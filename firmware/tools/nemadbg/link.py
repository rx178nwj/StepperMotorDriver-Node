"""シリアル（CDC）上で要求→応答を行うトランスポート。serial オブジェクトは注入できる。"""

from __future__ import annotations

import struct
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, List, Optional

from . import protocol as p

DEFAULT_TIMEOUT_S = 0.2
MAX_RETRIES = 3          # 初回送信に加えて再送する最大回数
USB_VID = 0x0483
USB_PID = 0x5740


class LinkError(Exception):
    """通信路の異常（タイムアウト・形式不正）。"""


class DeviceError(Exception):
    """基板が ERR≠0 を返した。"""

    def __init__(self, cmd: int, err: int) -> None:
        self.cmd = cmd
        self.err = err
        super().__init__(f"コマンド 0x{cmd:02X} がエラー 0x{err:02X}（{p.err_name(err)}）で失敗しました")


@dataclass
class ConnectInfo:
    proto_major: int
    proto_minor: int
    device_id: int
    fw_major: int
    fw_minor: int
    board_id: int
    max_len: int


@dataclass
class SymInfo:
    index: int
    addr: int
    type: int
    num: int
    name: str = ""

    @property
    def elem_size(self) -> int:
        return p.SYM_TYPES.get(self.type, ("?", 1, ""))[1]

    @property
    def size(self) -> int:
        return self.elem_size * self.num


def find_ports() -> List[str]:
    """VID/PID が一致するポート名の一覧。"""
    from serial.tools import list_ports

    return [x.device for x in list_ports.comports() if x.vid == USB_VID and x.pid == USB_PID]


def open_serial(port: str) -> Any:
    import serial

    return serial.Serial(port, baudrate=115200, timeout=0.02, write_timeout=1.0)


class Link:
    """1 要求ずつ送って応答を待つ。ser は write / read / in_waiting / close を持つこと。"""

    def __init__(self, ser: Any, timeout_s: float = DEFAULT_TIMEOUT_S, retries: int = MAX_RETRIES) -> None:
        self._ser = ser
        self.timeout_s = timeout_s
        self.retries = retries
        self._ctr = 0
        self._parser = p.FrameParser()
        self._responses: Deque[p.Response] = deque()
        self.events: Deque[p.Event] = deque()
        self.retransmits = 0

    def close(self) -> None:
        self._ser.close()

    # ---- 低レベル ----

    def _pump(self, deadline: float) -> Optional[p.Response]:
        """deadline までバイトを読み、応答が来たら返す。イベントはキューに積む。"""
        while True:
            if not self._responses:
                n = getattr(self._ser, "in_waiting", 0) or 1
                data = self._ser.read(n)
                for body in self._parser.feed(data) if data else []:
                    try:
                        msg = p.decode_body(body)
                    except ValueError:
                        continue
                    if isinstance(msg, p.Event):
                        self.events.append(msg)
                    else:
                        self._responses.append(msg)
            if self._responses:
                return self._responses.popleft()
            if time.monotonic() >= deadline:
                return None

    def request(self, cmd: int, params: bytes = b"") -> bytes:
        """要求を送り、応答の DATA を返す。ERR≠0 は DeviceError、無応答は LinkError。"""
        self._ctr = (self._ctr + 1) & 0xFF
        ctr = self._ctr
        frame = p.encode_request(cmd, ctr, params)
        for attempt in range(self.retries + 1):
            if attempt > 0:
                self.retransmits += 1
            self._responses.clear()
            self._parser.reset()   # 前の試行の途中フレームで次の応答を塞がない
            self._ser.write(frame)
            deadline = time.monotonic() + self.timeout_s
            while True:
                rsp = self._pump(deadline)
                if rsp is None:
                    break
                if rsp.ctr != ctr:
                    continue   # 再送前の遅延応答は読み捨てる
                if rsp.err != 0:
                    raise DeviceError(cmd, rsp.err)
                return rsp.data
        raise LinkError(f"コマンド 0x{cmd:02X} に応答がありません（{self.retries + 1} 回送信、タイムアウト {self.timeout_s * 1000:.0f} ms）")

    def wait_events(self, seconds: float) -> List[p.Event]:
        """seconds 秒間イベントを待ち、受け取った分を返す。途中で届いた応答は捨てる。"""
        deadline = time.monotonic() + seconds
        while self._pump(deadline) is not None:
            pass
        out = list(self.events)
        self.events.clear()
        return out

    # ---- コマンド ----

    def connect(self) -> ConnectInfo:
        return ConnectInfo(*self.request(p.CMD_CONNECT)[:7])

    def disconnect(self) -> None:
        self.request(p.CMD_DISCONNECT)

    def get_version(self) -> "tuple[int, int]":
        d = self.request(p.CMD_GET_VERSION)
        return d[0], d[1]

    def exchange_id(self) -> bytes:
        d = self.request(p.CMD_EXCHANGE_ID)
        n = d[0]
        if len(d) < 1 + n:
            raise LinkError("EXCHANGE_ID の応答が短すぎます")
        return bytes(d[1 : 1 + n])

    def short_up(self, addr: int, size: int) -> bytes:
        """addr から size バイトを読む（240 B ずつ分割）。"""
        out = bytearray()
        while size > 0:
            n = min(size, p.UPLOAD_MAX)
            out += self.request(p.CMD_SHORT_UP, struct.pack("<BI", n, addr))
            addr += n
            size -= n
        return bytes(out)

    def sym_count(self) -> int:
        return struct.unpack("<H", self.request(p.CMD_SYM_COUNT)[:2])[0]

    def sym_info(self, index: int) -> SymInfo:
        d = self.request(p.CMD_SYM_INFO, struct.pack("<H", index))
        addr, typ, num, nlen = struct.unpack_from("<IBHB", d, 0)
        return SymInfo(index, addr, typ, num, d[8 : 8 + nlen].decode("ascii", "replace"))

    def sym_find(self, name: str) -> SymInfo:
        raw = name.encode("ascii")
        d = self.request(p.CMD_SYM_FIND, bytes([len(raw)]) + raw)
        index, addr, typ, num = struct.unpack_from("<HIBH", d, 0)
        return SymInfo(index, addr, typ, num, name)

    def sym_upload(self, info: SymInfo) -> bytes:
        """名前表の変数全体を読む（240 B 超は OFFSET で分割）。"""
        raw = info.name.encode("ascii")
        out = bytearray()
        total = info.size
        while len(out) < total:
            n = min(total - len(out), p.UPLOAD_MAX)
            params = bytes([len(raw)]) + raw + struct.pack("<HB", len(out), n)
            out += self.request(p.CMD_SYM_UPLOAD, params)
        return bytes(out)

    def can_request(self, can_id: int, data: bytes = b"") -> List[p.CanFrame]:
        params = p.encode_can(p.CanFrame(can_id, data))
        d = self.request(p.CMD_CAN_REQUEST, params)
        if not d:
            raise LinkError("CAN_REQUEST の応答が空です")
        try:
            return p.parse_can_frames(d[1:], d[0])
        except ValueError as e:
            raise LinkError(str(e)) from e
