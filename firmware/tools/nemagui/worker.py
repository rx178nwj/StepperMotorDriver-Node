"""通信スレッド。Link を持つのはこのスレッドだけで、UI とは 2 本の queue.Queue で話す。Qt には依存しない。

    UI -> Worker : in_q に要求（Connect / Disconnect / WriteReg / Command / Broadcast / ReadOnce / RawCan /
                   EnterBootloader）を put する。ポーリングは set_poll / clear_poll（スレッド安全）。
    Worker -> UI : out_q に結果・通知（Connected / Disconnected / Values / WriteResult / ReadResult / RawCanResult /
                   BootloaderResult / Event / Stats / Notice）を put する。UI が定期的に get_nowait() で取り出す。

方針（usb_interface.md 10.2 章）:
- 要求は 1 つずつ直列に処理する。書き込み系の要求はポーリングより先に処理し、ESTOP は要求の中でも最優先。
- 書き込み・ブロードキャストは再送しない（Link の方針）。無応答は「実行されたか不明」（unknown=True）として返す。
- ERR=0x35 は CONNECT からやり直す。読み出しの無応答が連続 fail_limit 回なら切断扱い。OSError（ポート消失）も切断扱い。
- COMM_TIMEOUT の生存通知はポーリング自体が兼ねる。専用の送信はしない。
- 何もポーリングしていないとき、イベントは次の要求まで取り出さない（ポーリングを常時 1 組は置くこと）。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from nemadbg import protocol as p
from nemadbg import registers as regs
from nemadbg.link import ConnectInfo, DeviceError, Link, LinkError, SymInfo, UnsafeRetryError

from . import items as itemsmod
from . import readplan
from .items import Item

DEVICE_ID = 0xA3
PROTO_MAJOR = 1
ERR_LOCKED = 0x35
ERR_DENIED = 0x33
ERR_NO_BUILD_ID = 0x36
CAN_ID_FAULT_BASE = 0x080
BROADCAST_CODES = {2, 3, 4}

RESULT_NAMES = {0: "OK", 1: "REJECTED", 2: "INVALID", 3: "FAILED"}


class Cmd(IntEnum):
    """COMMAND レジスタ（0x50）の値。"""

    ENABLE = 1
    DISABLE = 2
    STOP = 3
    ESTOP = 4
    CLEAR_FAULT = 5
    RESET_ELEC_ANGLE = 6
    SAVE_SETTINGS = 7
    LOAD_SETTINGS = 8
    FACTORY_SETTINGS = 9
    SYNC_POSITION = 10
    HOME_NEG = 11
    HOME_POS = 12


# ---------------------------------------------------------------- 要求（UI -> Worker）

@dataclass
class Connect:
    """接続する（切断中、または再接続したいとき）。"""


@dataclass
class Disconnect:
    """DISCONNECT を送って閉じる。以後 Connect が来るまで自動再接続しない。"""


@dataclass
class WriteReg:
    """レジスタ書き込み。target は項目の key（"reg.RUN_CURRENT"）、レジスタ名（"MOVE_REL"）、アドレスのどれか。"""

    target: Union[str, int]
    value: Any
    tag: Any = None


@dataclass
class Command:
    """COMMAND レジスタへの書き込み。code は Cmd または整数または名前（"ENABLE"）。"""

    code: Union[Cmd, int, str]
    tag: Any = None


@dataclass
class Broadcast:
    """ブロードキャスト（CAN_ID 0x000）。code は 2 DISABLE、3 STOP、4 ESTOP のみ有効。応答なし。"""

    code: Union[Cmd, int, str]
    tag: Any = None


@dataclass
class ReadOnce:
    """key の項目を 1 回だけ読む（名前表ブラウザ・設定の読み直し用）。結果は ReadResult。"""

    keys: Sequence[str]
    tag: Any = None


@dataclass
class RawCan:
    can_id: int
    data: bytes = b""
    tag: Any = None


@dataclass
class EnterBootloader:
    """ENTER_BOOTLOADER。ENABLED 中は基板が 0x33 で拒否する。成功したら切断扱いにし、自動再接続しない。"""

    tag: Any = None


# ---------------------------------------------------------------- 結果・通知（Worker -> UI）

@dataclass
class Connected:
    info: ConnectInfo
    build_id: bytes
    symtab: Dict[str, SymInfo]
    items: List[Item]


@dataclass
class Disconnected:
    reason: str
    will_retry: bool = False


@dataclass
class Values:
    timestamp: float                 # time.time()
    values: Dict[str, bytes]         # key -> 生バイト列


@dataclass
class WriteResult:
    request: Any
    result: Optional[int] = None     # CMD_RESULT。応答が無い（ブロードキャスト・失敗）ときは None
    result_name: str = ""
    error: str = ""                  # 送れなかった・不明などの説明
    unknown: bool = False            # 実行されたか不明（無応答・切断）

    @property
    def ok(self) -> bool:
        return self.result == 0 and not self.error


@dataclass
class ReadResult:
    request: Any
    timestamp: float
    values: Dict[str, bytes]
    missing: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class RawCanResult:
    request: Any
    frames: List[Tuple[int, bytes]] = field(default_factory=list)
    error: str = ""
    unknown: bool = False


@dataclass
class BootloaderResult:
    request: Any
    accepted: bool
    error: str = ""
    unknown: bool = False


@dataclass
class Event:
    timestamp: float
    evt: int
    data: bytes
    can_id: Optional[int] = None
    state: Optional[int] = None      # FAULT 通知のとき
    fault: Optional[int] = None
    text: str = ""


@dataclass
class Notice:
    """個別の読み出し失敗など、接続を切るほどではない問題（同じ内容は接続ごとに 1 回だけ）。"""

    text: str


@dataclass
class Stats:
    timestamp: float
    connected: bool
    requests: int                    # 累積（Link への要求数）
    timeouts: int                    # 累積（無応答で LinkError になった数）
    retransmits: int                 # 累積（Link の再送数）
    errors: int                      # 累積（基板の ERR・応答形式不正の数）
    rtt_avg_ms: float                # 直前の統計周期内の往復時間（要求がなければ 0）
    rtt_max_ms: float
    poll_periods_s: Dict[str, float] # ポーリング組ごとの実効周期（直前の統計周期内。実行が無ければ含めない）


# ---------------------------------------------------------------- 内部例外

class _Lost(Exception):
    """接続断として扱う。"""


class _Relink(Exception):
    """ERR=0x35。CONNECT からやり直す。"""


class _Abort(Exception):
    """本機ではない・プロトコルが違う。自動再接続しない。"""


class ProtocolError(LinkError):
    """応答は来たが形式が想定と違う。"""


def command_code(code: Union[Cmd, int, str]) -> int:
    if isinstance(code, str):
        try:
            return int(Cmd[code.upper()])
        except KeyError:
            raise ValueError(f"不明なコマンド名: {code}")
    return int(code)


_FAULT_BITS = next(r for r in regs.REGISTERS if r.name == "FAULT").bits


def _fault_text(fault: int) -> str:
    names = [n for b, n in _FAULT_BITS if fault & (1 << b)]
    return ",".join(names) if names else "-"


class Worker(threading.Thread):
    """通信スレッド。start() で接続を試み始める。

    link_factory       : ポートを開いて Link を返す（接続のたびに呼ぶ。失敗は例外）。
    auto_reconnect     : 切断・接続失敗のあと reconnect_interval_s ごとにやり直す。
    stats_period_s     : Stats を出す周期。
    fail_limit         : 読み出しの無応答がこの回数連続したら切断扱い。
    """

    def __init__(self, link_factory: Callable[[], Link], auto_reconnect: bool = True,
                 reconnect_interval_s: float = 1.0, stats_period_s: float = 1.0, fail_limit: int = 3) -> None:
        super().__init__(name="nemagui-worker", daemon=True)
        self.in_q: "queue.Queue[Any]" = queue.Queue()
        self.out_q: "queue.Queue[Any]" = queue.Queue()
        self._factory = link_factory
        self._auto = auto_reconnect
        self._interval = reconnect_interval_s
        self._stats_period = stats_period_s
        self._fail_limit = fail_limit

        self._lock = threading.Lock()
        self._polls: Dict[str, Tuple[FrozenSet[str], float]] = {}
        self._stop_evt = threading.Event()

        # 以下はワーカースレッドだけが触る
        self._pending: List[Any] = []
        self._link: Optional[Link] = None
        self._board_id = 0
        self._symtab: Dict[str, SymInfo] = {}
        self._items: Dict[str, Item] = {}
        self._want = True
        self._next_connect = 0.0
        self._down_announced = False
        self._plans: Dict[FrozenSet[str], List[readplan.ReadOp]] = {}
        self._due: Dict[str, float] = {}
        self._runs: Dict[str, int] = {}
        self._noticed: set = set()
        self._consec = 0
        self._requests = 0
        self._timeouts = 0
        self._errors = 0
        self._retx_closed = 0
        self._rtt_sum = 0.0
        self._rtt_n = 0
        self._rtt_max = 0.0
        self._stats_t0 = time.monotonic()
        self._stats_due = self._stats_t0 + stats_period_s

    # ---- UI 側から呼ぶ ----

    def set_poll(self, name: str, keys: Iterable[str], period_s: float) -> None:
        """ポーリング組を登録（同名は置き換え）。keys の項目を period_s ごとに読む。"""
        with self._lock:
            self._polls[name] = (frozenset(keys), max(0.0, float(period_s)))
        self.in_q.put(None)   # 待機中のスレッドを起こす

    def clear_poll(self, name: str) -> None:
        with self._lock:
            self._polls.pop(name, None)

    def send(self, request: Any) -> None:
        self.in_q.put(request)

    def stop(self, join_timeout: Optional[float] = None) -> None:
        """終了を要求する。join_timeout を渡すと（別スレッドからなら）終了を待つ。"""
        self._stop_evt.set()
        self.in_q.put(None)
        if join_timeout is not None and threading.current_thread() is not self and self.is_alive():
            self.join(join_timeout)

    # ---- スレッド本体 ----

    def run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                try:
                    if self._link is None:
                        self._disconnected_step()
                    else:
                        self._connected_step()
                except _Relink:
                    self._relink()
                except _Lost as e:
                    self._drop(str(e))
        finally:
            self._graceful_close()

    def _out(self, msg: Any) -> None:
        self.out_q.put(msg)

    def _wait_request(self, timeout: float) -> None:
        try:
            req = self.in_q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return
        if req is not None:
            self._pending.append(req)

    def _drain(self) -> None:
        while True:
            try:
                req = self.in_q.get_nowait()
            except queue.Empty:
                return
            if req is not None:
                self._pending.append(req)

    # ---- 切断中 ----

    def _disconnected_step(self) -> None:
        now = time.monotonic()
        if self._want and now >= self._next_connect:
            self._try_connect()
        else:
            wake = self._next_connect - now if self._want else 0.2
            self._wait_request(min(0.2, wake, self._stats_due - now))
        self._service_requests()
        self._maybe_stats()

    def _try_connect(self) -> None:
        try:
            link = self._factory()
        except Exception as e:
            self._connect_failed(f"ポートを開けません: {e}")
            return
        try:
            info, build_id, symtab, infos = self._handshake(link)
        except _Abort as e:
            self._close_link(link)
            self._want = False
            self._announce(Disconnected(str(e), False))
            return
        except (DeviceError, LinkError, OSError) as e:
            self._close_link(link)
            self._connect_failed(f"接続できません: {e}")
            return
        self._link = link
        self._board_id = info.board_id
        self._after_connect(info, build_id, symtab, infos)

    def _handshake(self, link: Link) -> Tuple[ConnectInfo, bytes, Dict[str, SymInfo], List[SymInfo]]:
        info = link.connect()
        if info.device_id != DEVICE_ID:
            raise _Abort(f"DEVICE_ID が 0x{info.device_id:02X} です（本機は 0x{DEVICE_ID:02X}）。別の機器につながっています")
        major, minor = link.get_version()
        if major != PROTO_MAJOR:
            raise _Abort(f"プロトコル {major}.{minor} は未対応です（対応: {PROTO_MAJOR}.x）")
        try:
            build_id = link.exchange_id()
        except DeviceError as e:
            if e.err != ERR_NO_BUILD_ID:
                raise
            build_id = b""
        infos = [link.sym_info(i) for i in range(link.sym_count())]
        return info, build_id, {s.name: s for s in infos}, infos

    def _after_connect(self, info: ConnectInfo, build_id: bytes, symtab: Dict[str, SymInfo],
                       infos: List[SymInfo]) -> None:
        items = itemsmod.build_items(infos)
        self._symtab = symtab
        self._items = itemsmod.items_by_key(items)
        self._plans.clear()
        self._noticed.clear()
        self._due.clear()
        self._consec = 0
        self._down_announced = False
        self._out(Connected(info, build_id, symtab, items))
        self._emit_events()

    def _connect_failed(self, reason: str) -> None:
        if not self._auto:
            self._want = False
        self._next_connect = time.monotonic() + self._interval
        self._announce(Disconnected(reason, self._want))

    def _announce(self, msg: Disconnected) -> None:
        """同じ切断状態の間は、最初の 1 回だけ Disconnected を出す。"""
        if not self._down_announced:
            self._down_announced = True
            self._out(msg)

    # ---- 切断 ----

    def _close_link(self, link: Link) -> None:
        self._retx_closed += link.retransmits
        try:
            link.close()
        except Exception:
            pass

    def _graceful_close(self) -> None:
        link, self._link = self._link, None
        if link is None:
            return
        try:
            link.disconnect()
        except Exception:
            pass
        self._close_link(link)

    def _drop(self, reason: str) -> None:
        """接続断。DISCONNECT は送らずに閉じる。"""
        link, self._link = self._link, None
        if link is not None:
            self._close_link(link)
        if not self._auto:
            self._want = False
        self._next_connect = time.monotonic() + self._interval
        self._down_announced = True
        self._out(Disconnected(reason, self._want))

    def _relink(self) -> None:
        """ERR=0x35。同じ Link で CONNECT からやり直す。"""
        link = self._link
        assert link is not None
        try:
            info, build_id, symtab, infos = self._handshake(link)
        except (_Abort, DeviceError, LinkError, OSError) as e:
            self._drop(f"再接続に失敗しました: {e}")
            return
        self._board_id = info.board_id
        self._after_connect(info, build_id, symtab, infos)

    # ---- 接続中 ----

    def _connected_step(self) -> None:
        self._service_requests()
        if self._link is None:
            return
        now = time.monotonic()
        due = self._due_polls(now)
        if due:
            self._run_polls(due, now)
        else:
            wake = self._stats_due
            with self._lock:
                names = list(self._polls)
            for n in names:
                wake = min(wake, self._due.get(n, now))
            self._wait_request(min(0.2, wake - now))
        self._maybe_stats()

    def _due_polls(self, now: float) -> List[Tuple[str, FrozenSet[str], float]]:
        with self._lock:
            polls = dict(self._polls)
        for gone in [n for n in self._due if n not in polls]:
            del self._due[gone]
        return [(n, k, per) for n, (k, per) in polls.items() if self._due.get(n, 0.0) <= now]

    def _plan(self, keys: FrozenSet[str]) -> List[readplan.ReadOp]:
        ops = self._plans.get(keys)
        if ops is None:
            items = [self._items[k] for k in sorted(keys) if k in self._items]
            ops = readplan.plan_reads(items, self._symtab)
            if len(self._plans) > 32:
                self._plans.clear()
            self._plans[keys] = ops
        return ops

    def _run_polls(self, due: List[Tuple[str, FrozenSet[str], float]], now: float) -> None:
        keys: FrozenSet[str] = frozenset().union(*(k for _, k, _ in due))
        values: Dict[str, bytes] = {}
        for op in self._plan(keys):
            if op.kind == readplan.KIND_MISSING:
                continue
            self._service_requests()          # 書き込みは読み出しの合間にも割り込ませる
            if self._link is None:
                return
            try:
                values.update(readplan.extract(op, self._read_op(op)))
            except (DeviceError, LinkError) as e:
                self._notice(f"読み出しに失敗しました（{', '.join(op.keys[:3])}…）: {e}")
        self._emit_events()
        if values:
            self._out(Values(time.time(), values))
        for name, _k, period in due:
            # 遅れを溜めない: 次の期限は「前回の期限+周期」だが、今回の開始時刻より前にはしない
            self._due[name] = max(self._due.get(name, now) + period, now)
            self._runs[name] = self._runs.get(name, 0) + 1

    def _notice(self, text: str) -> None:
        if text not in self._noticed:
            self._noticed.add(text)
            self._out(Notice(text))

    # ---- Link 操作 ----

    def _io(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Link の操作を 1 つ実行し、統計と異常処理（0x35 → _Relink、連続無応答・OSError → _Lost）を行う。"""
        t0 = time.perf_counter()
        self._requests += 1
        try:
            r = fn(*args)
        except DeviceError as e:
            if e.err == ERR_LOCKED:
                raise _Relink()
            self._errors += 1
            self._consec = 0         # 基板は応答している
            raise
        except ProtocolError:
            self._errors += 1
            self._consec += 1
            self._check_lost()
            raise
        except LinkError:            # UnsafeRetryError を含む
            self._timeouts += 1
            self._consec += 1
            self._check_lost()
            raise
        except OSError as e:
            raise _Lost(f"ポートが使えません: {e}")
        self._consec = 0
        dt = time.perf_counter() - t0
        self._rtt_sum += dt
        self._rtt_n += 1
        self._rtt_max = max(self._rtt_max, dt)
        return r

    def _check_lost(self) -> None:
        if self._consec >= self._fail_limit:
            raise _Lost(f"応答が {self._consec} 回連続でありません（通信タイムアウト）")

    def _can(self, can_id: int, data: bytes) -> List[p.CanFrame]:
        assert self._link is not None
        return self._io(self._link.can_request, can_id, data)

    def _can_read(self, addr: int, size: int) -> bytes:
        bid = self._board_id
        link = self._link
        assert link is not None

        def call() -> bytes:
            frames = link.can_request(regs.BOARD_REQ_BASE + bid, bytes([regs.OP_READ, addr, size]))
            rsp = [f for f in frames if f.can_id == regs.BOARD_RSP_BASE + bid]
            if not rsp:
                raise ProtocolError(f"レジスタ 0x{addr:02X} の読み出しに CAN 応答がありません")
            d = rsp[0].data
            if len(d) != 2 + size or d[0] != regs.OP_READ_RSP or d[1] != addr:
                raise ProtocolError(f"レジスタ 0x{addr:02X} の応答が不正です: {d.hex(' ')}")
            return d[2:]

        return self._io(call)

    def _read_op(self, op: readplan.ReadOp) -> bytes:
        link = self._link
        assert link is not None
        if op.kind == readplan.KIND_REG:
            return self._can_read(op.addr, op.size)
        if op.kind == readplan.KIND_SHORT:
            return self._io(link.short_up, op.addr, op.size)
        assert op.sym is not None
        return self._io(link.sym_upload, op.sym)

    def _emit_events(self) -> None:
        link = self._link
        if link is None:
            return
        while link.events:
            e = link.events.popleft()
            ev = Event(time.time(), e.evt, bytes(e.data))
            if e.evt == p.EVT_CAN_FRAME:
                try:
                    f = p.parse_can_frames(e.data, 1)[0]
                except ValueError:
                    f = None
                if f is not None:
                    ev.can_id = f.can_id
                    if f.can_id == CAN_ID_FAULT_BASE + self._board_id and len(f.data) == 2:
                        ev.state, ev.fault = f.data[0], f.data[1]
                        sname = regs.STATE_NAMES.get(ev.state, "?")
                        ev.text = f"異常通知: 状態 {sname}、要因 {_fault_text(ev.fault)}"
            if not ev.text:
                ev.text = f"イベント 0x{e.evt:02X}: {e.data.hex(' ')}"
            self._out(ev)

    # ---- 要求の処理 ----

    @staticmethod
    def _urgent(req: Any) -> bool:
        try:
            if isinstance(req, (Command, Broadcast)):
                return command_code(req.code) == Cmd.ESTOP
        except ValueError:
            pass
        return False

    def _service_requests(self) -> None:
        """溜まっている要求を処理する（ESTOP が最優先、他は到着順）。処理中に来た要求も続けて処理する。"""
        while True:
            self._drain()
            if not self._pending:
                return
            self._pending.sort(key=lambda r: 0 if self._urgent(r) else 1)   # sort は安定
            req = self._pending.pop(0)
            try:
                self._handle_request(req)
            except (_Lost, _Relink):
                self._fail(req, "接続が切れました", unknown=True)
                raise

    def _fail(self, req: Any, text: str, unknown: bool = False) -> None:
        if isinstance(req, (WriteReg, Command, Broadcast)):
            self._out(WriteResult(req, None, "", text, unknown))
        elif isinstance(req, ReadOnce):
            self._out(ReadResult(req, time.time(), {}, [], [text]))
        elif isinstance(req, RawCan):
            self._out(RawCanResult(req, [], text, unknown))
        elif isinstance(req, EnterBootloader):
            self._out(BootloaderResult(req, False, text, unknown))

    def _handle_request(self, req: Any) -> None:
        if isinstance(req, Connect):
            if self._link is not None:
                self._graceful_close()
            self._want = True
            self._next_connect = 0.0
            self._down_announced = False
            return
        if isinstance(req, Disconnect):
            was = self._link is not None
            self._graceful_close()
            self._want = False
            self._down_announced = True
            if was:
                self._out(Disconnected("切断しました", False))
            return
        if self._link is None:
            self._fail(req, "未接続です")
            return
        if isinstance(req, WriteReg):
            self._do_write(req)
        elif isinstance(req, Command):
            self._do_command(req)
        elif isinstance(req, Broadcast):
            self._do_broadcast(req)
        elif isinstance(req, ReadOnce):
            self._do_read_once(req)
        elif isinstance(req, RawCan):
            self._do_raw(req)
        elif isinstance(req, EnterBootloader):
            self._do_bootloader(req)
        self._emit_events()

    def _resolve(self, target: Union[str, int]) -> Tuple[regs.Reg, Optional[Item]]:
        by_addr = {r.addr: r for r in regs.REGISTERS}
        by_name = {r.name: r for r in regs.REGISTERS}
        if isinstance(target, int):
            if target not in by_addr:
                raise ValueError(f"レジスタ 0x{target:02X} は定義されていません")
            reg = by_addr[target]
        else:
            it = self._items.get(target)
            if it is not None:
                if it.reg is None:
                    raise ValueError(f"{target} はレジスタ項目ではありません")
                reg = it.reg
            elif target in by_name:
                reg = by_name[target]
            else:
                raise ValueError(f"不明な書き込み先: {target}")
        item = self._items.get("reg." + reg.name)
        return reg, item

    def _write_frame(self, reg: regs.Reg, data: bytes) -> int:
        """レジスタに書き、CMD_RESULT を返す。"""
        bid = self._board_id
        frames = self._can(regs.BOARD_REQ_BASE + bid, bytes([0x01, reg.addr]) + data)
        rsp = [f for f in frames if f.can_id == regs.BOARD_RSP_BASE + bid]
        if not rsp:
            raise ProtocolError("書き込みに CAN 応答がありません")
        d = rsp[0].data
        if len(d) == 3 and d[0] == 0x81 and d[1] == reg.addr:
            return d[2]
        if len(d) == 3 and d[0] == 0xFF:
            return d[2]                   # 不正要求（INVALID）
        raise ProtocolError(f"書き込みの応答が不正です: {d.hex(' ')}")

    def _write_result(self, req: Any, reg: regs.Reg, data: bytes) -> None:
        try:
            r = self._write_frame(reg, data)
        except UnsafeRetryError as e:
            self._out(WriteResult(req, None, "不明", str(e), True))
        except (DeviceError, LinkError) as e:
            self._out(WriteResult(req, None, "", str(e)))
        else:
            self._out(WriteResult(req, r, RESULT_NAMES.get(r, f"不明({r})")))

    def _do_write(self, req: WriteReg) -> None:
        try:
            reg, item = self._resolve(req.target)
            data = itemsmod.encode(item, req.value) if item is not None and item.writable \
                else itemsmod.encode_reg(reg, req.value)
        except (ValueError, TypeError) as e:
            self._out(WriteResult(req, None, "", str(e)))
            return
        self._write_result(req, reg, data)

    def _do_command(self, req: Command) -> None:
        try:
            code = command_code(req.code)
            reg = next(r for r in regs.REGISTERS if r.name == "COMMAND")
            data = itemsmod.encode_reg(reg, code)
        except (ValueError, TypeError) as e:
            self._out(WriteResult(req, None, "", str(e)))
            return
        self._write_result(req, reg, data)

    def _do_broadcast(self, req: Broadcast) -> None:
        try:
            code = command_code(req.code)
            if code not in BROADCAST_CODES:
                raise ValueError(f"ブロードキャストできるのは 2 DISABLE・3 STOP・4 ESTOP だけです（{code}）")
        except ValueError as e:
            self._out(WriteResult(req, None, "", str(e)))
            return
        try:
            self._can(0x000, bytes([code]))
        except UnsafeRetryError as e:
            self._out(WriteResult(req, None, "不明", str(e), True))
        except (DeviceError, LinkError) as e:
            self._out(WriteResult(req, None, "", str(e)))
        else:
            self._out(WriteResult(req, None, "送信済み（応答なし）"))

    def _do_read_once(self, req: ReadOnce) -> None:
        items = [self._items[k] for k in req.keys if k in self._items]
        ops = readplan.plan_reads(items, self._symtab)
        values: Dict[str, bytes] = {}
        errors = [f"不明な項目: {k}" for k in req.keys if k not in self._items]
        for op in ops:
            if op.kind == readplan.KIND_MISSING:
                continue
            try:
                values.update(readplan.extract(op, self._read_op(op)))
            except (DeviceError, LinkError) as e:
                errors.append(str(e))
        self._out(ReadResult(req, time.time(), values, readplan.missing_keys(ops), errors))

    def _do_raw(self, req: RawCan) -> None:
        try:
            frames = self._can(req.can_id, bytes(req.data))
        except UnsafeRetryError as e:
            self._out(RawCanResult(req, [], str(e), True))
        except (DeviceError, LinkError) as e:
            self._out(RawCanResult(req, [], str(e)))
        else:
            self._out(RawCanResult(req, [(f.can_id, f.data) for f in frames]))

    def _do_bootloader(self, req: EnterBootloader) -> None:
        link = self._link
        assert link is not None
        try:
            self._io(link.enter_bootloader)
        except DeviceError as e:
            if e.err == ERR_DENIED:
                self._out(BootloaderResult(req, False, "励磁中（ENABLED）のため移行できません。DISABLE してから実行してください"))
            else:
                self._out(BootloaderResult(req, False, str(e)))
            return
        except UnsafeRetryError as e:
            self._out(BootloaderResult(req, False, str(e), True))
            return
        except LinkError as e:
            self._out(BootloaderResult(req, False, str(e)))
            return
        self._out(BootloaderResult(req, True))
        # 以後 COM ポートは消える。DISCONNECT は送らずに閉じ、自動再接続もしない
        self._link = None
        self._close_link(link)
        self._want = False
        self._down_announced = True
        self._out(Disconnected("ROM ブートローダへ移行しました", False))

    # ---- 統計 ----

    def _maybe_stats(self) -> None:
        now = time.monotonic()
        if now < self._stats_due:
            return
        window = max(now - self._stats_t0, 1e-6)
        retx = self._retx_closed + (self._link.retransmits if self._link is not None else 0)
        periods = {n: window / c for n, c in self._runs.items() if c > 0}
        self._out(Stats(time.time(), self._link is not None, self._requests, self._timeouts, retx, self._errors,
                        self._rtt_sum / self._rtt_n * 1000.0 if self._rtt_n else 0.0, self._rtt_max * 1000.0, periods))
        self._runs.clear()
        self._rtt_sum = 0.0
        self._rtt_n = 0
        self._rtt_max = 0.0
        self._stats_t0 = now
        self._stats_due = now + self._stats_period
