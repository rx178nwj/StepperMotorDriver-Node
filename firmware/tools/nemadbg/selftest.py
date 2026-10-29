"""動作確認モード（selftest サブコマンド）の本体: 試験項目・結果モデル・レポート出力。

グループ
    A 立ち上げ（電源・温度・スタック・ループ・USB エラー）  B 角度センサ（AS5600）
    C モーター（正逆転・連続回転。実際に回す）            D オシロ（C の実行中に DRV_VREF / CLK / DIR を計測）
    E CAN（アダプタから要求フレームを送って応答と統計を確認）

方針
    - 値の読み出しは USB の名前表（SYM_UPLOAD）、設定・指令の書き込みは CAN_REQUEST（レジスタ書き込み）で行う。
    - 変更する設定（RUN_CURRENT・MAX_SPEED）は元値を読んで保存し、どの経路でも最後に STOP -> DISABLE して元へ戻す。
      SAVE_SETTINGS は送らない（不揮発設定は変えない）。
    - pyvisa（オシロ）と python-can（CAN）は該当グループの実行時だけ import する。無ければそのグループは SKIP。
    - 時計と sleep、オシロ・CAN の生成は SelftestEnv で差し替えられる（tests/test_selftest.py）。
"""

from __future__ import annotations

import json
import statistics
import struct
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import canbus as canmod
from . import protocol as p
from . import registers as regs
from . import scope as scopemod
from .link import ConnectInfo, Link

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

GROUP_NAMES = {"A": "立ち上げ", "B": "角度センサ", "C": "モーター", "D": "オシロ", "E": "CAN", "X": "実行制御"}
GROUP_ORDER = "ABCDEX"
SELECTABLE = "ABCDE"

EXPECTED_DEVICE_ID = 0xA3
MOTOR_FULL_STEPS = 200            # 23HS8430: 1.8 deg/step
CAN_CMD_WRITE_OK = 0

# ---- 判定しきい値 ----
VM_RANGE_MV = (10800, 13200)      # VM 12 V +-10 %
VDDA_RANGE_MV = (3200, 3400)
TEMP_RANGE_C = (0.0, 60.0)
STACK_LIMIT_PCT = 80.0
ANGLE_TOL_DEG = 5.0               # 1 回転（360 deg）に対する許容
SPEED_TOL = 0.05
VREF_TOL = 0.10
CLK_FREQ_TOL = 0.02
IOUT_PER_V = 1.25                 # config.h: IOUT = VREF x 1.25
DIR_LOW_V, DIR_HIGH_V = 1.0, 2.3
USB_ERROR_FIELDS = ("crc_errors", "dropped", "rx_overflow", "rx_timeouts", "bad_len", "tx_dropped")
SIM_VM_MV = 12000                 # --sim のとき擬似デバイスの VM を 12 V にする

R_RUN_CURRENT, R_MAX_SPEED = 0x20, 0x28
R_MOVE_REL, R_RUN_SPEED, R_COMMAND = 0x44, 0x48, 0x50
CMD_ENABLE, CMD_DISABLE, CMD_STOP, CMD_CLEAR_FAULT = 1, 2, 3, 5
CAN_OP_WRITE, CAN_OP_WRITE_RSP = 0x01, 0x81


class SelftestError(Exception):
    """試験の実行自体ができない異常（DUT の応答不正など）。"""


# ---------------------------------------------------------------- 結果モデル

@dataclass
class Item:
    id: str
    group: str
    name: str
    result: str
    value: Any = None
    unit: str = ""
    limit: str = ""
    note: str = ""


@dataclass
class SelftestOptions:
    current_ma: int = 1500
    speed_rev_s: float = 1.0
    no_scope: bool = False
    scope_visa: str = scopemod.DEFAULT_VISA
    no_can: bool = False
    can_interface: str = canmod.DEFAULT_INTERFACE
    can_channel: Optional[str] = None
    can_bitrate: int = canmod.DEFAULT_BITRATE
    can_node: Optional[int] = None
    no_motion: bool = False
    yes: bool = False
    only: str = SELECTABLE
    out_dir: Optional[str] = None
    sim: bool = False


@dataclass
class SelftestEnv:
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    scope_factory: Callable[[str], Any] = scopemod.open_scope
    can_factory: Callable[[str, Optional[str], int], Any] = canmod.open_bus


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _fmt_value(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def format_item(it: Item) -> str:
    val = _fmt_value(it.value) + (f" {it.unit}" if it.unit and it.value is not None else "")
    line = f"{_pad(it.result, 4)}  {it.group}  {_pad(it.id, 14)} {_pad(it.name, 26)} {_pad(val, 22)} 基準: {it.limit or '-'}"
    return line + (f"  # {it.note}" if it.note else "")


# ---------------------------------------------------------------- 基板アクセス

class Dut:
    """基板の読み書き。読み出しは名前表、書き込みは CAN_REQUEST。"""

    def __init__(self, link: Link, ci: ConnectInfo) -> None:
        self.link = link
        self.ci = ci
        self._syms: Dict[str, Any] = {}

    def get(self, name: str) -> Any:
        info = self._syms.get(name)
        if info is None:
            info = self._syms[name] = self.link.sym_find(name)
        raw = self.link.sym_upload(info)
        fmt = p.SYM_TYPES[info.type][2]
        if not fmt or info.num != 1:
            raise SelftestError(f"{name} は単一の数値ではありません")
        return struct.unpack_from(fmt, raw)[0]

    def reg_write(self, reg: int, data: bytes) -> int:
        bid = self.ci.board_id
        frames = self.link.can_request(regs.BOARD_REQ_BASE + bid, bytes([CAN_OP_WRITE, reg]) + data)
        rsp = [f for f in frames if f.can_id == regs.BOARD_RSP_BASE + bid]
        if not rsp:
            raise SelftestError(f"レジスタ 0x{reg:02X} の書き込みに応答がありません")
        d = rsp[0].data
        if len(d) != 3 or d[0] != CAN_OP_WRITE_RSP or d[1] != reg:
            raise SelftestError(f"レジスタ 0x{reg:02X} の書き込み応答が不正です: {d.hex(' ')}")
        return d[2]

    def command(self, code: int) -> int:
        return self.reg_write(R_COMMAND, bytes([code]))


def fold_total_deg(angles: List[int]) -> float:
    """shaft_angle（0.01 deg、0xFFFF は無効）の列から、折り返しを補正した回転量 [deg]（符号付き）を求める。"""
    total, prev = 0, None
    for a in angles:
        if a == 0xFFFF:
            continue
        if prev is not None:
            total += (a - prev + 18000) % 36000 - 18000
        prev = a
    return total / 100.0


@dataclass
class MoveResult:
    start_pos: int
    end_pos: int
    angle_deg: Optional[float]     # None はセンサ無効
    done: bool
    elapsed: float
    write_result: int
    fault: int = 0


# ---------------------------------------------------------------- 本体

class SelfTest:
    def __init__(self, link: Link, ci: ConnectInfo, opts: SelftestOptions, env: Optional[SelftestEnv] = None,
                 prompt: Callable[[str], str] = input, out: Callable[[str], None] = print) -> None:
        self.link, self.ci, self.o = link, ci, opts
        self.env = env or SelftestEnv()
        self.prompt = prompt
        self._out = out
        self.dut = Dut(link, ci)
        self.groups: Set[str] = set(opts.only.upper())
        self.items: List[Item] = []
        self.lines: List[str] = []
        self.started = datetime.now()
        self.finished: Optional[datetime] = None
        self.out_dir = Path(opts.out_dir) if opts.out_dir else None
        self.extra_files: List[str] = []
        self._usb0: Dict[str, int] = {}
        self._scope: Any = None
        self._scope_err = ""

    # ---- 時間 ----

    def sleep(self, s: float) -> None:
        self.env.sleep(s)

    def now(self) -> float:
        return self.env.clock()

    def wait_until(self, fn: Callable[[], Any], timeout: float, step: float = 0.02) -> bool:
        end = self.now() + timeout
        while True:
            if fn():
                return True
            if self.now() >= end:
                return False
            self.sleep(step)

    # ---- 出力 ----

    def say(self, text: str = "") -> None:
        self.lines.append(text)
        self._out(text)

    def add(self, id_: str, name: str, result: str, value: Any = None, unit: str = "", limit: str = "",
            note: str = "") -> Item:
        it = Item(id_, id_.split(".")[0], name, result, value, unit, limit, note)
        if it.group not in self.groups and it.group != "X":
            return it                  # --only で選ばれていないグループの項目（D だけ選んだときの C など）は出さない
        self.items.append(it)
        self.say(format_item(it))
        return it

    def range_item(self, id_: str, name: str, v: float, lo: float, hi: float, unit: str, note: str = "") -> None:
        self.add(id_, name, PASS if lo <= v <= hi else FAIL, v, unit, f"{lo:g}..{hi:g} {unit}", note)

    def skip_group(self, g: str, reason: str) -> None:
        self.add(f"{g}.all", f"{GROUP_NAMES[g]}の試験全体", SKIP, note=reason)

    def heading(self, g: str) -> None:
        self.say("")
        self.say(f"[{g}] {GROUP_NAMES[g]}")

    # ---- 全体 ----

    def run(self) -> int:
        o = self.o
        self.say(f"nemadbg selftest  {self.started:%Y-%m-%d %H:%M:%S}")
        ci = self.ci
        self.say(f"DUT: DEVICE_ID=0x{ci.device_id:02X} FW {ci.fw_major}.{ci.fw_minor} BOARD_ID={ci.board_id}"
                 + ("  (--sim 擬似デバイス)" if o.sim else ""))
        self.say(f"条件: 試験電流 {o.current_ma} mA / 速度 {o.speed_rev_s:g} rev/s / グループ {''.join(sorted(self.groups))}"
                 f" / モーター {'無し' if o.no_motion else '有り'} / オシロ {'無し' if o.no_scope else o.scope_visa}"
                 f" / CAN {'無し' if o.no_can else o.can_interface}")
        try:
            if o.sim:
                self._prepare_sim()
            if "A" in self.groups:
                try:
                    self._usb0 = self._read_usb_stats()
                except Exception:
                    self._usb0 = {}
            for g, fn in (("A", self.group_a), ("B", self.group_b)):
                if g in self.groups:
                    self._guard(g, fn)
            if "C" in self.groups or "D" in self.groups:
                self._guard("C", self.group_motion)
            if "E" in self.groups:
                self._guard("E", self.group_e)
        except KeyboardInterrupt:
            self.add("X.abort", "ユーザー中断", FAIL, note="Ctrl+C で中断（モーターは STOP -> DISABLE 済み）")
        if "A" in self.groups:
            self.say("")
            self.say("[A] 立ち上げ（続き: 試験全体を通した確認）")
            self._guard("A", self.usb_errors_final)
        return self.finish()

    def _guard(self, g: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.add(f"{g}.error", f"{GROUP_NAMES[g]}の実行", FAIL, note=f"例外: {type(e).__name__}: {e}")

    def _prepare_sim(self) -> None:
        """--sim: 擬似デバイスの VM を 12 V にする（既定は 24 V で A の VM 判定に合わない）。"""
        ser = getattr(self.link, "_ser", None)
        if hasattr(ser, "set_vm_mv"):
            ser.set_vm_mv(SIM_VM_MV)
            self.sleep(0.3)

    # ---- A 立ち上げ ----

    def _read_usb_stats(self) -> Dict[str, int]:
        return {k: int(self.dut.get("usb_stats." + k)) for k in USB_ERROR_FIELDS}

    def group_a(self) -> None:
        d, ci = self.dut, self.ci
        self.heading("A")
        self.add("A.device_id", "DEVICE_ID", PASS if ci.device_id == EXPECTED_DEVICE_ID else FAIL,
                 f"0x{ci.device_id:02X}", limit=f"0x{EXPECTED_DEVICE_ID:02X}")
        self.add("A.fw", "FW 版数 / BOARD_ID", INFO, f"{ci.fw_major}.{ci.fw_minor} / {ci.board_id}")
        self.range_item("A.vm", "VM 電圧", d.get("mon.vm_mv"), *VM_RANGE_MV, "mV")
        self.range_item("A.vdda", "VDDA", d.get("mon.vdda_mv"), *VDDA_RANGE_MV, "mV")
        mcu = d.get("mon.mcu_temp_dc")
        self.range_item("A.mcu_temp", "MCU 温度", mcu / 10.0, *TEMP_RANGE_C, "degC")
        brd = d.get("mon.board_temp_dc")
        if brd == -32768:
            self.add("A.board_temp", "基板温度", FAIL, "無効", limit="0..60 degC", note="NTC 断線の疑い")
        else:
            self.range_item("A.board_temp", "基板温度", brd / 10.0, *TEMP_RANGE_C, "degC")
        used, size = d.get("stack.used_max"), d.get("stack.size")
        pct = 100.0 * used / size if size else 100.0
        self.add("A.stack", "スタック使用率", PASS if size and pct < STACK_LIMIT_PCT else FAIL, round(pct, 1), "%",
                 f"<{STACK_LIMIT_PCT:g} %", f"{used}/{size} B")
        self.add("A.loop_rate", "ループ回数 loop.rate_hz", INFO, d.get("loop.rate_hz"), "Hz")
        self.add("A.loop_max", "ループ最大周期 loop.max_us", INFO, d.get("loop.max_us"), "us")
        self.add("A.reset_csr", "起動要因 reset_csr", INFO, f"0x{d.get('reset_csr'):08X}")

    def usb_errors_final(self) -> None:
        if not self._usb0:
            self.add("A.usb_errors", "USB エラー増加なし", FAIL, note="開始時の usb_stats を読めなかった")
            return
        now = self._read_usb_stats()
        grown = {k: now[k] - self._usb0[k] for k in USB_ERROR_FIELDS if now[k] != self._usb0[k]}
        self.add("A.usb_errors", "USB エラー増加なし", PASS if not grown else FAIL,
                 "増加なし" if not grown else ", ".join(f"{k}+{v}" for k, v in grown.items()), limit="増加 0",
                 note="対象: " + ",".join(USB_ERROR_FIELDS))

    # ---- B 角度センサ ----

    def group_b(self) -> None:
        d = self.dut
        self.heading("B")
        ok1, fail1 = d.get("sensor.i2c_ok"), d.get("sensor.i2c_fail")
        self.sleep(1.0)
        ok2, fail2 = d.get("sensor.i2c_ok"), d.get("sensor.i2c_fail")
        streak, st = d.get("sensor.fail_streak"), d.get("sensor.as_status")
        self.add("B.i2c_ok", "I2C 成功回数が増加", PASS if ok2 > ok1 else FAIL, f"{ok1}->{ok2}", limit="増加")
        self.add("B.i2c_fail", "I2C 失敗回数が増えない", PASS if fail2 == fail1 else FAIL, f"{fail1}->{fail2}",
                 limit="増加 0")
        self.add("B.fail_streak", "連続失敗回数", PASS if streak == 0 else FAIL, streak, limit="0")
        # AS5600 STATUS: bit3 MH（磁石が強すぎ）、bit4 ML（弱すぎ）、bit5 MD（検出）。position.c の AS5600_ST_* に合わせる
        md, ml, mh = bool(st & 0x20), bool(st & 0x10), bool(st & 0x08)
        self.add("B.magnet", "磁石検出 MD=1 / ML=0 / MH=0", PASS if md and not ml and not mh and streak == 0 else FAIL,
                 f"0x{st:02X} (MD={int(md)} ML={int(ml)} MH={int(mh)})", limit="MD=1, ML=0, MH=0",
                 note="" if streak == 0 else "センサ無応答のため値は最後に読めたもの")
        self.add("B.agc", "AGC 端子レベル agc_out", INFO, d.get("agc_out"),
                 note="AS5600 の AGC レジスタ値は名前表に無い。agc_out は AGC0/AGC1 端子の出力")
        ang = d.get("shaft_angle")
        self.add("B.angle", "軸角度 shaft_angle", INFO, "無効" if ang == 0xFFFF else ang / 100.0,
                 "" if ang == 0xFFFF else "deg")

    # ---- C モーター / D オシロ ----

    def _confirm_motion(self) -> bool:
        o = self.o
        if o.yes:
            return True
        msg = (f"モーターを回します（電流 {o.current_ma} mA、1 回転の正逆転と {o.speed_rev_s:g} rev/s の連続回転）。"
               "無負荷で接続し、軸や周囲に触れないでください。実行しますか？ [y/N] ")
        try:
            ans = self.prompt(msg)
        except EOFError:
            return False
        return ans.strip().lower() in ("y", "yes")

    def group_motion(self) -> None:
        o = self.o
        want_c, want_d = "C" in self.groups, "D" in self.groups
        if want_c:
            self.heading("C")
        if o.no_motion or not self._confirm_motion():
            reason = "--no-motion" if o.no_motion else "確認プロンプトで中止"
            if want_c:
                self.skip_group("C", reason)
            if want_d:
                self.heading("D")
                self.skip_group("D", f"C が SKIP（{reason}）")
            return
        reason_d = ""
        if want_d:
            if o.no_scope:
                reason_d = "--no-scope"
            elif o.sim:
                reason_d = "--sim（擬似デバイスにはオシロで測る信号が無い）"
            else:
                try:
                    self._scope = self.env.scope_factory(o.scope_visa)
                except scopemod.ScopeUnavailable as e:
                    reason_d = str(e)
        dres: Dict[str, Any] = {}
        try:
            self._motion_body(dres)
        finally:
            try:
                self._shutdown(dres)
            finally:
                try:
                    if want_d:
                        self.heading("D")
                        if reason_d:
                            self.skip_group("D", reason_d)
                        else:
                            self._emit_d(dres)
                finally:
                    sc, self._scope = self._scope, None
                    if sc is not None and hasattr(sc, "close"):
                        sc.close()

    # -- オシロ呼び出し（失敗しても試験は続ける） --

    def _sc_alive(self) -> bool:
        return self._scope is not None and not isinstance(self._scope, _DeadScope)

    def _sc(self, fn: Callable[..., Any], *a: Any) -> Any:
        if not self._sc_alive():
            return None
        try:
            return fn(*a)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self._scope_err = f"{type(e).__name__}: {e}"
            self._scope = _DeadScope(self._scope)
            return None

    def _sc_measure(self, ch: int, item: str, n: int = 3) -> Optional[float]:
        if not self._sc_alive():
            return None
        vals = []
        for _ in range(n):
            v = self._sc(self._scope.measure, ch, item)
            if v is None:
                return None
            vals.append(v)
            self.sleep(0.05)
        return statistics.median(vals)

    # -- 本体 --

    def _wr_u(self, reg: int, size: int, v: int, signed: bool = False) -> int:
        return self.dut.reg_write(reg, int(v).to_bytes(size, "little", signed=signed))

    def _state(self) -> int:
        return self.dut.get("state")

    def _motion_body(self, dres: Dict[str, Any]) -> None:
        o, d = self.o, self.dut
        want_c = "C" in self.groups
        ms = d.get("cfg.microstep")
        gear, cfg_spr = d.get("cfg.gear_ratio"), d.get("cfg.steps_per_rev")
        dres["orig_run"] = d.get("cfg.run_current_ma")
        dres["orig_max"] = d.get("cfg.max_speed")
        n_rev = MOTOR_FULL_STEPS * ms
        speed_sps = int(round(n_rev * o.speed_rev_s))
        dres["n_rev"], dres["speed_sps"] = n_rev, speed_sps
        exp_deg = 360.0 * MOTOR_FULL_STEPS / (gear * cfg_spr)
        tol_deg = max(ANGLE_TOL_DEG * exp_deg / 360.0, 0.5)
        dres["restore"] = True
        if want_c:
            self.add("C.orig", "試験前の設定（終了時に復元）", INFO,
                     f"RUN_CURRENT={dres['orig_run']}mA MAX_SPEED={dres['orig_max']} MICROSTEP={ms} "
                     f"GEAR_RATIO={gear} STEPS_PER_REV={cfg_spr}")
        if not 1 <= speed_sps <= 100000:
            self.add("C.setup", "試験設定", FAIL, speed_sps, "step/s", "1..100000", "--speed が範囲外")
            return
        r1 = self._wr_u(R_RUN_CURRENT, 2, o.current_ma)
        r2 = self._wr_u(R_MAX_SPEED, 4, speed_sps)
        self.add("C.setup", "試験電流・速度の設定", PASS if r1 == 0 and r2 == 0 else FAIL,
                 f"RUN_CURRENT={o.current_ma}mA MAX_SPEED={speed_sps}", note=f"CMD_RESULT={r1},{r2}")
        if r1 or r2:
            return
        d.command(CMD_CLEAR_FAULT)    # FAULT でなければ REJECTED になるが無害
        res = d.command(CMD_ENABLE)
        ok = self.wait_until(lambda: self._state() == 2, 1.0)
        self.add("C.enable", "ENABLE で励磁", PASS if ok and res == 0 else FAIL, f"STATE={self._state()}",
                 limit="STATE=2", note=f"CMD_RESULT={res}")
        if not ok:
            return
        self._sc(lambda: self._scope.setup_live())
        # ---- 正転 1 回転（CLK のエッジ数を単発取り込み） ----
        dur = n_rev / speed_sps
        if self._sc_alive():
            self._sc(lambda: self._scope.arm_capture(2, dur + 1.0))
        fwd = self._move(n_rev, speed_sps)
        if self._sc_alive():
            wf = self._sc(lambda: self._scope.read_capture(2))
            if wf is not None:
                dres["wave"] = wf
            self._sc(lambda: self._scope.setup_live())
            self.sleep(0.3)
            dres["dir_fwd"] = self._sc_measure(3, "VAVG")
        self._judge_move("fwd", "正転", fwd, n_rev, fwd.start_pos + n_rev, exp_deg, tol_deg, gear)
        if not fwd.done or fwd.fault:
            return
        # ---- 逆転 1 回転 ----
        rev = self._move(-n_rev, speed_sps)
        if self._sc_alive():
            self.sleep(0.3)
            dres["dir_rev"] = self._sc_measure(3, "VAVG")
        self._judge_move("rev", "逆転", rev, -n_rev, fwd.start_pos, exp_deg, tol_deg, gear)
        if not rev.done or rev.fault:
            return
        # ---- 連続回転 ----
        self._run_speed(dres, speed_sps)
        dres["completed"] = True

    def _move(self, delta: int, speed_sps: int) -> MoveResult:
        d = self.dut
        start = d.get("position")
        ang0 = d.get("shaft_angle")
        target = start + delta
        timeout = abs(delta) / speed_sps * 3 + 2.0
        res = d.reg_write(R_MOVE_REL, int(delta).to_bytes(4, "little", signed=True))
        t0 = self.now()
        angles = [ang0]
        done, fault, pos = False, 0, start
        while res == 0:
            pos = d.get("position")
            st = d.get("status")
            angles.append(d.get("shaft_angle"))
            if d.get("state") == 3:
                fault = d.get("fault") or 0x100
                break
            if pos == target and not (st & 1):
                done = True
                break
            if self.now() - t0 > timeout:
                break
            self.sleep(0.02)
        elapsed = self.now() - t0
        if done:
            self.sleep(0.1)
            angles.append(d.get("shaft_angle"))
            pos = d.get("position")
        valid = [a for a in angles if a != 0xFFFF]
        ang = fold_total_deg(angles) if len(valid) >= 2 and angles[0] != 0xFFFF and angles[-1] != 0xFFFF else None
        return MoveResult(start, pos, ang, done, elapsed, res, fault)

    def _judge_move(self, key: str, label: str, m: MoveResult, delta: int, expect_pos: int,
                    exp_deg: float, tol_deg: float, gear: int) -> None:
        if "C" not in self.groups:
            return
        why = ""
        if m.write_result:
            why = f"MOVE_REL の CMD_RESULT={m.write_result}"
        elif m.fault:
            why = f"FAULT 0x{m.fault:02X}"
        elif not m.done:
            why = f"{m.elapsed:.1f} s で完了せず"
        diff = m.end_pos - m.start_pos
        self.add(f"C.{key}_pos", f"{label} position 差分", PASS if m.done and m.end_pos == expect_pos else FAIL,
                 diff, "step", f"={delta}" if key == "fwd" else f"={delta}（元位置に戻る）",
                 why or f"{m.elapsed:.2f} s")
        if m.angle_deg is None:
            self.add(f"C.{key}_angle", f"{label} 軸角度の回転量", FAIL, "センサ無効", limit=f"|{exp_deg:g}|±{tol_deg:g} deg")
        else:
            ok = abs(abs(m.angle_deg) - exp_deg) <= tol_deg
            note = ""
            if not ok and gear != 1:
                note = f"GEAR_RATIO={gear}（無負荷直結なら 1 が必要）"
            self.add(f"C.{key}_angle", f"{label} 軸角度の回転量（絶対値）", PASS if ok and m.done else FAIL,
                     abs(m.angle_deg), "deg", f"{exp_deg:g}±{tol_deg:g} deg", note)
            self.add(f"C.{key}_sign", f"{label} 軸角度の符号", INFO, "+" if m.angle_deg >= 0 else "-",
                     note=f"{m.angle_deg:+.2f} deg")

    def _run_speed(self, dres: Dict[str, Any], speed_sps: int) -> None:
        d = self.dut
        res = self._wr_u(R_RUN_SPEED, 4, speed_sps, signed=True)
        if res:
            self.add("C.run_speed", "連続回転の速度", FAIL, note=f"RUN_SPEED の CMD_RESULT={res}")
            return
        t0 = self.now()
        while self.now() - t0 < 1.0:      # 加速が終わるまで
            d.get("cur_speed")
            self.sleep(0.1)
        if self._sc_alive():
            vref = self._sc_measure(1, "VAVG")
            freq = self._sc_measure(2, "FREQ")
            dres["vref_v"], dres["freq_hz"] = vref, freq
        speeds = []
        for _ in range(10):
            speeds.append(d.get("cur_speed"))
            self.sleep(0.1)
        mean = sum(speeds) / len(speeds)
        err = abs(mean - speed_sps) / speed_sps
        self.add("C.run_speed", "連続回転の cur_speed", PASS if err <= SPEED_TOL else FAIL, round(mean, 1), "step/s",
                 f"{speed_sps}±{SPEED_TOL * 100:g} %", f"誤差 {err * 100:.2f} %")
        d.command(CMD_STOP)
        ok = self.wait_until(lambda: d.get("running") == 0, 3.0)
        self.add("C.stop", "STOP で停止（running=0）", PASS if ok else FAIL, d.get("running"), limit="0")

    def _shutdown(self, dres: Dict[str, Any]) -> None:
        """どの経路でも最後に STOP -> DISABLE を送り、変更した設定を元に戻す。"""
        d = self.dut
        errs: List[str] = []
        for code in (CMD_STOP, CMD_DISABLE):
            try:
                d.command(code)
            except Exception as e:
                errs.append(f"コマンド {code}: {e}")
        if not dres.get("restore"):
            if errs:
                self.add("C.restore", "停止・設定の復元", FAIL, note="; ".join(errs))
            return
        for reg, size, key in ((R_RUN_CURRENT, 2, "orig_run"), (R_MAX_SPEED, 4, "orig_max")):
            if key not in dres:
                continue
            try:
                r = self._wr_u(reg, size, dres[key])
                if r:
                    errs.append(f"0x{reg:02X} の復元が CMD_RESULT={r}")
            except Exception as e:
                errs.append(f"0x{reg:02X} の復元: {e}")
        try:
            now_run, now_max = d.get("cfg.run_current_ma"), d.get("cfg.max_speed")
            state = d.get("state")
            ok = (now_run == dres.get("orig_run") and now_max == dres.get("orig_max") and state != 2 and not errs)
            val = f"RUN_CURRENT={now_run}mA MAX_SPEED={now_max} STATE={state}"
        except Exception as e:
            ok, val = False, "確認不能"
            errs.append(f"確認: {e}")
        if "C" in self.groups or not ok:
            self.add("C.restore", "STOP/DISABLE と設定の復元", PASS if ok else FAIL, val,
                     limit="元値に一致・STATE≠ENABLED", note="; ".join(errs) or "SAVE_SETTINGS は送っていない")

    # -- D --

    def _emit_d(self, dres: Dict[str, Any]) -> None:
        o = self.o
        sc = self._scope
        idn = ""
        if sc is not None and hasattr(sc, "idn"):
            try:
                idn = sc.idn()
            except Exception:
                idn = ""
        self.add("D.scope", "オシロ", INFO, idn or o.scope_visa)
        if "n_rev" not in dres or "freq_hz" not in dres and "wave" not in dres and "dir_fwd" not in dres \
                and "vref_v" not in dres and not self._scope_err:
            why = "C が途中で中断"
        else:
            why = ""
        err = f"オシロ通信エラー: {self._scope_err}" if self._scope_err else ""

        def missing(k: str) -> str:
            return err or why or "測定値が取れなかった（9.9E37 など）"

        # VREF
        v = dres.get("vref_v")
        lo, hi = o.current_ma * (1 - VREF_TOL), o.current_ma * (1 + VREF_TOL)
        if v is None:
            self._d_none("D.vref", "DRV_VREF 平均 x1.25", f"{lo:.0f}..{hi:.0f} mA", missing("vref_v"), bool(err or why))
        else:
            cur = v * IOUT_PER_V * 1000.0
            self.add("D.vref", "DRV_VREF 平均 x1.25 = 電流", PASS if lo <= cur <= hi else FAIL, round(cur, 1), "mA",
                     f"{lo:.0f}..{hi:.0f} mA", f"VREF 平均 {v:.3f} V（期待 {o.current_ma / 1000 / IOUT_PER_V:.3f} V）")
        # 周波数
        f = dres.get("freq_hz")
        exp_f = dres.get("speed_sps")
        if f is None or not exp_f:
            self._d_none("D.clk_freq", "DRV_CLK 周波数（連続回転）", f"{exp_f}±{CLK_FREQ_TOL * 100:g} %",
                         missing("freq_hz"), bool(err or why))
        else:
            e = abs(f - exp_f) / exp_f
            self.add("D.clk_freq", "DRV_CLK 周波数（連続回転）", PASS if e <= CLK_FREQ_TOL else FAIL, round(f, 1), "Hz",
                     f"{exp_f}±{CLK_FREQ_TOL * 100:g} %", f"誤差 {e * 100:.2f} %")
        # エッジ数
        wf = dres.get("wave")
        n_rev = dres.get("n_rev")
        if wf is None:
            self._d_none("D.clk_edges", "DRV_CLK 立ち上がり数（正転 1 回転）", f"={n_rev}", missing("wave"),
                         bool(err or why))
        else:
            edges = scopemod.rising_edges(wf)
            self.add("D.clk_edges", "DRV_CLK 立ち上がり数（正転 1 回転）", PASS if len(edges) == n_rev else FAIL,
                     len(edges), "個", f"={n_rev}", f"サンプル間隔 {wf.xinc * 1e6:.3g} us、{len(wf.raw)} 点")
            self._save_wave(wf, edges)
        # DIR
        a, b = dres.get("dir_fwd"), dres.get("dir_rev")
        if a is None or b is None:
            self._d_none("D.dir", "DRV_DIR 論理レベル（正転/逆転）", "反転", missing("dir"), bool(err or why))
        else:
            def lvl(x: float) -> Optional[int]:
                return 0 if x <= DIR_LOW_V else (1 if x >= DIR_HIGH_V else None)
            la, lb = lvl(a), lvl(b)
            ok = la is not None and lb is not None and la != lb
            self.add("D.dir", "DRV_DIR 論理レベル（正転/逆転）", PASS if ok else FAIL,
                     f"正転 {a:.2f} V / 逆転 {b:.2f} V", limit=f"<={DIR_LOW_V:g} V と >={DIR_HIGH_V:g} V の反転")

    def _d_none(self, id_: str, name: str, limit: str, note: str, skip: bool) -> None:
        self.add(id_, name, SKIP if skip else FAIL, limit=limit, note=note)

    def _save_wave(self, wf: scopemod.Waveform, edges: List[int]) -> None:
        if self.out_dir is None:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        pe = self.out_dir / "scope_clk_edges.csv"
        with open(pe, "w", encoding="utf-8", newline="") as fh:
            fh.write("index,sample,time_s\n")
            for i, s in enumerate(edges, 1):
                fh.write(f"{i},{s},{wf.time_of(s):.9g}\n")
        pw = self.out_dir / "scope_clk_wave.csv"
        stride = max(1, len(wf.raw) // 20000)
        with open(pw, "w", encoding="utf-8", newline="") as fh:
            fh.write("time_s,volts\n")
            for i in range(0, len(wf.raw), stride):
                fh.write(f"{wf.time_of(i):.9g},{wf.volts(i):.4f}\n")
        self.extra_files += [pe.name, pw.name]

    # ---- E CAN ----

    def group_e(self) -> None:
        o = self.o
        self.heading("E")
        if o.no_can:
            return self.skip_group("E", "--no-can")
        if o.sim:
            return self.skip_group("E", "--sim（擬似デバイスは CAN バスを持たない）")
        try:
            bus = self.env.can_factory(o.can_interface, o.can_channel, o.can_bitrate)
        except canmod.CanUnavailable as e:
            return self.skip_group("E", str(e))
        try:
            self._e_body(bus)
        finally:
            bus.close()

    def _e_body(self, bus: Any) -> None:
        d = self.dut
        node = self.o.can_node if self.o.can_node is not None else self.ci.board_id
        req, rsp = regs.BOARD_REQ_BASE + node, regs.BOARD_RSP_BASE + node
        keys = ("rx_frames", "tx_frames", "err_busoff", "err_passive", "err_rx_overrun")
        before = {k: d.get("comm_stats." + k) for k in keys}
        sent = 0
        got = 0

        def xfer(data: bytes) -> Optional[bytes]:
            nonlocal sent, got
            for _ in range(3):             # 読み出しだけ（冪等）なので最大 3 回まで再送してよい
                sent += 1
                r = bus.request(req, data, rsp, 0.05)
                if r is not None:
                    got += 1
                    return r
            return None

        def check(id_: str, name: str, data: bytes, expect: Callable[[bytes], bool], want: str) -> None:
            r = xfer(data)
            if r is None:
                self.add(id_, name, FAIL, "応答なし", limit=want, note=f"送信 {req:#05x} {data.hex(' ')}")
            else:
                self.add(id_, name, PASS if expect(r) else FAIL, r.hex(" "), limit=want,
                         note=f"送信 {req:#05x} {data.hex(' ')}")

        check("E.device_id", "DEVICE_ID 読み出し", bytes([2, 0x00, 1]),
              lambda r: r == bytes([0x82, 0x00, EXPECTED_DEVICE_ID]), "82 00 A3")
        check("E.board_id", "BOARD_ID 読み出し", bytes([2, 0x03, 1]),
              lambda r: r == bytes([0x82, 0x03, node]), f"82 03 {node:02X}")
        check("E.state", "STATE・FAULT 読み出し", bytes([2, 0x04, 2]),
              lambda r: len(r) == 4 and r[:2] == b"\x82\x04" and r[2] <= 3, "82 04 <STATE> <FAULT>")
        pos = d.get("position")
        check("E.position", "POSITION 読み出し（USB の値と一致）", bytes([2, 0x08, 4]),
              lambda r: r == b"\x82\x08" + struct.pack("<i", pos), f"82 08 {struct.pack('<i', pos).hex(' ')}")
        check("E.invalid", "不正要求（未定義 OP=0x05）", bytes([5, 0x04, 1]),
              lambda r: r == bytes([0xFF, 0x04, 0x02]), "FF 04 02")
        after = {k: d.get("comm_stats." + k) for k in keys}
        for k in ("err_busoff", "err_passive", "err_rx_overrun"):
            ok = after[k] == 0 and after[k] == before[k]
            self.add(f"E.{k}", f"comm_stats.{k}", PASS if ok else FAIL, f"{before[k]}->{after[k]}", limit="0 のまま")
        for k in ("rx_frames", "tx_frames"):
            self.add(f"E.{k}", f"comm_stats.{k} 増加", PASS if after[k] > before[k] else FAIL,
                     f"{before[k]}->{after[k]}", limit="増加", note=f"送信 {sent} 件、応答 {got} 件")

    # ---- 集計・出力 ----

    def counts(self) -> Dict[str, int]:
        c = {PASS: 0, FAIL: 0, SKIP: 0, INFO: 0}
        for it in self.items:
            c[it.result] += 1
        return c

    def overall(self) -> str:
        return FAIL if any(i.result == FAIL for i in self.items) else PASS

    def finish(self) -> int:
        self.finished = datetime.now()
        self.items.sort(key=lambda i: GROUP_ORDER.index(i.group))   # 同じグループ内は実行順
        c = self.counts()
        self.say("")
        self.say(f"集計: PASS {c[PASS]} / FAIL {c[FAIL]} / SKIP {c[SKIP]} / INFO {c[INFO]}  =>  総合 {self.overall()}")
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / "report.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
            (self.out_dir / "report.json").write_text(
                json.dumps(self.to_json(), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            self._out(f"レポート: {self.out_dir / 'report.txt'} / {self.out_dir / 'report.json'}")
        return 1 if self.overall() == FAIL else 0

    def to_json(self) -> Dict[str, Any]:
        ci = self.ci
        cond = asdict(self.o)
        return {
            "tool": "nemadbg selftest",
            "started": self.started.isoformat(timespec="seconds"),
            "finished": (self.finished or datetime.now()).isoformat(timespec="seconds"),
            "device": {"device_id": f"0x{ci.device_id:02X}", "fw": f"{ci.fw_major}.{ci.fw_minor}",
                       "board_id": ci.board_id, "sim": self.o.sim},
            "conditions": cond,
            "items": [asdict(i) for i in self.items],
            "summary": {**self.counts(), "overall": self.overall()},
            "files": list(self.extra_files),
        }


class _DeadScope:
    """通信エラーを起こしたオシロ。以後は何も呼ばない（close だけ元へ流す）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def close(self) -> None:
        if hasattr(self._inner, "close"):
            self._inner.close()

    def idn(self) -> str:
        return ""
