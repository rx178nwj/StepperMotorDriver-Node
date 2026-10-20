"""テスト用の擬似デバイス。serial の代わりに bytes を受けて FW と同等の応答を返す。"""

from __future__ import annotations

import struct
from typing import Dict, List, Tuple

from nemadbg import protocol as p

BUILD_ID = bytes(range(0x10, 0x24))
BOARD_ID = 3
RAM_BASE = 0x20000000
FLASH_BASE = 0x08000000

# 名前 -> (アドレス, TYPE, NUM)
SYMBOLS: List[Tuple[str, int, int, int]] = [
    ("state", RAM_BASE + 0x00, 0x01, 1),
    ("position", RAM_BASE + 0x04, 0x06, 1),
    ("speed_f", RAM_BASE + 0x08, 0x07, 1),
    ("arr16", RAM_BASE + 0x10, 0x03, 3),
    ("big", RAM_BASE + 0x100, 0x10, 300),
]


class FakeSerial:
    def __init__(self) -> None:
        self.ram = bytearray(0x1000)
        self.ram[0] = 2
        struct.pack_into("<i", self.ram, 4, -1234)
        struct.pack_into("<f", self.ram, 8, 1.5)
        struct.pack_into("<3H", self.ram, 0x10, 1, 2, 65535)
        for i in range(300):
            self.ram[0x100 + i] = i & 0xFF
        self.regs = bytearray(0x60)
        self.regs[0x00] = 0xA3
        self.regs[0x03] = BOARD_ID
        self.regs[0x04] = 2
        self.regs[0x06] = 0x84
        struct.pack_into("<i", self.regs, 0x08, -100)
        struct.pack_into("<H", self.regs, 0x10, 24000)
        self.connected = False
        self.parser = p.FrameParser()
        self.out = bytearray()
        self.requests: List[bytes] = []     # 受けた BODY
        self.drop_responses = 0             # 先頭からこの数だけ応答を捨てる（タイムアウト試験）
        self.stale_first = False            # 最初の応答の前に CTR 違いの応答を挟む
        self.closed = False

    # ---- serial 互換 ----
    @property
    def in_waiting(self) -> int:
        return len(self.out)

    def write(self, data: bytes) -> int:
        for body in self.parser.feed(data):
            self.requests.append(body)
            rsp = self._handle(body)
            if self.drop_responses > 0:
                self.drop_responses -= 1
                continue
            if self.stale_first:
                self.stale_first = False
                self.out += p.encode_frame(bytes([p.PID_RESPONSE, 0, (body[1] + 100) & 0xFF]))
            self.out += rsp
        return len(data)

    def read(self, size: int = 1) -> bytes:
        data = bytes(self.out[:size])
        del self.out[:size]
        return data

    def close(self) -> None:
        self.closed = True

    # ---- デバイス側 ----
    def push_event(self, can_id: int, data: bytes) -> None:
        body = bytes([p.PID_EVENT, p.EVT_CAN_FRAME]) + p.encode_can(p.CanFrame(can_id, data))
        self.out += p.encode_frame(body)

    def _mem(self, addr: int, size: int) -> Tuple[int, bytes]:
        if size < 1 or size > p.UPLOAD_MAX:
            return 0x32, b""
        if RAM_BASE <= addr and addr + size <= RAM_BASE + len(self.ram):
            return 0, bytes(self.ram[addr - RAM_BASE : addr - RAM_BASE + size])
        if FLASH_BASE <= addr and addr + size <= FLASH_BASE + 0x40000:
            return 0, bytes([0xEE]) * size
        return 0x33, b""

    def _handle(self, body: bytes) -> bytes:
        cmd, ctr = body[0], body[1]
        err, data = self._exec(cmd, body)
        if err:
            data = b""
        return p.encode_frame(bytes([p.PID_RESPONSE, err, ctr]) + data)

    def _exec(self, cmd: int, body: bytes) -> Tuple[int, bytes]:
        if cmd == p.CMD_GET_VERSION:
            return 0, bytes([1, 0])
        if cmd == p.CMD_CONNECT:
            self.connected = True
            return 0, bytes([1, 0, 0xA3, 1, 2, BOARD_ID, 250])
        if not self.connected:
            return 0x35, b""
        if cmd == p.CMD_DISCONNECT:
            self.connected = False
            return 0, b""
        if cmd == p.CMD_EXCHANGE_ID:
            return 0, bytes([20]) + BUILD_ID
        if cmd == p.CMD_SHORT_UP:
            if len(body) != 7:
                return 0x31, b""
            size, addr = struct.unpack_from("<BI", body, 2)
            return self._mem(addr, size)
        if cmd == p.CMD_SYM_COUNT:
            return 0, struct.pack("<H", len(SYMBOLS))
        if cmd == p.CMD_SYM_INFO:
            (i,) = struct.unpack_from("<H", body, 2)
            if i >= len(SYMBOLS):
                return 0x32, b""
            name, addr, typ, num = SYMBOLS[i]
            raw = name.encode()
            return 0, struct.pack("<IBHB", addr, typ, num, len(raw)) + raw
        if cmd in (p.CMD_SYM_FIND, p.CMD_SYM_UPLOAD):
            nlen = body[2]
            name = body[3 : 3 + nlen].decode()
            for i, (n, addr, typ, num) in enumerate(SYMBOLS):
                if n == name:
                    break
            else:
                return 0x36, b""
            if cmd == p.CMD_SYM_FIND:
                return 0, struct.pack("<HIBH", i, addr, typ, num)
            offset, size = struct.unpack_from("<HB", body, 3 + nlen)
            total = p.SYM_TYPES[typ][1] * num
            if offset >= total:
                return 0x32, b""
            if size == 0:
                size = total - offset
            if size > p.UPLOAD_MAX or offset + size > total:
                return 0x32, b""
            return self._mem(addr + offset, size)
        if cmd == p.CMD_CAN_REQUEST:
            return self._can(body)
        return 0x30, b""

    def _can(self, body: bytes) -> Tuple[int, bytes]:
        if len(body) < 5:
            return 0x31, b""
        can_id, dlc = struct.unpack_from("<HB", body, 2)
        if can_id > 0x7FF or dlc > 8:
            return 0x32, b""
        if len(body) != 5 + dlc:
            return 0x31, b""
        payload = body[5:]
        if can_id != 0x100 + BOARD_ID:
            return 0, b"\x00"
        if len(payload) == 3 and payload[0] == 0x02 and 1 <= payload[2] <= 6:
            reg, n = payload[1], payload[2]
            vals = bytes(self.regs[reg + i] if reg + i < len(self.regs) else 0xFF for i in range(n))
            rsp = p.CanFrame(0x180 + BOARD_ID, bytes([0x82, reg]) + vals)
        else:
            rsp = p.CanFrame(0x180 + BOARD_ID, bytes([0xFF, payload[1] if len(payload) > 1 else 0, 2]))
        return 0, b"\x01" + p.encode_can(rsp)
