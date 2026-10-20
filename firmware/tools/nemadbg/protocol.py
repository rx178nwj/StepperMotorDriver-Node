"""USB CDC デバッグプロトコル（requirements.md 3.9 節）の純粋ロジック。シリアルには依存しない。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple

SOF = 0xA5
LEN_MIN = 2
LEN_MAX = 250
UPLOAD_MAX = 240

PID_RESPONSE = 0xFF
PID_EVENT = 0xFE
EVT_CAN_FRAME = 0x01

CMD_CONNECT = 0x01
CMD_SET_MTA = 0x02
CMD_UPLOAD = 0x04
CMD_DISCONNECT = 0x07
CMD_SHORT_UP = 0x0F
CMD_EXCHANGE_ID = 0x17
CMD_GET_VERSION = 0x1B
CMD_SYM_COUNT = 0x20
CMD_SYM_INFO = 0x21
CMD_SYM_FIND = 0x22
CMD_SYM_UPLOAD = 0x23
CMD_CAN_REQUEST = 0x40

ERR_NAMES: Dict[int, str] = {
    0x00: "OK",
    0x30: "未定義コマンド",
    0x31: "コマンド構文エラー",
    0x32: "パラメータ範囲外",
    0x33: "アクセス拒否",
    0x35: "アクセスロック（未接続）",
    0x36: "リソースなし",
}

# 名前表 TYPE -> (名前, 要素サイズ, struct 書式)。0x10 のバイト列は書式なし
SYM_TYPES: Dict[int, Tuple[str, int, str]] = {
    0x01: ("u8", 1, "<B"),
    0x02: ("i8", 1, "<b"),
    0x03: ("u16", 2, "<H"),
    0x04: ("i16", 2, "<h"),
    0x05: ("u32", 4, "<I"),
    0x06: ("i32", 4, "<i"),
    0x07: ("f32", 4, "<f"),
    0x10: ("bytes", 1, ""),
}


def err_name(err: int) -> str:
    return ERR_NAMES.get(err, "不明なエラー")


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE（poly 0x1021、init 0xFFFF、反転なし）。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_frame(body: bytes) -> bytes:
    """BODY をフレーム `[SOF][LEN][BODY][CRC lo][CRC hi]` にする。"""
    if not LEN_MIN <= len(body) <= LEN_MAX:
        raise ValueError(f"BODY 長が範囲外です: {len(body)}（{LEN_MIN}〜{LEN_MAX}）")
    payload = bytes([len(body)]) + body
    return bytes([SOF]) + payload + struct.pack("<H", crc16(payload))


class FrameParser:
    """バイトストリームからフレームを取り出す。

    LEN 範囲外・CRC 不一致のときは SOF の次のバイトから SOF を探し直す。
    途切れたフレームは続きのバイトが来るまで保留し、次のフレームで CRC 不一致になって再同期する。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.bad_len = 0

    def reset(self) -> None:
        """保留中の途中フレームを捨てる。"""
        self._buf.clear()

    def feed(self, data: bytes) -> List[bytes]:
        """バイト列を渡し、完成したフレームの BODY を順に返す。"""
        self._buf.extend(data)
        bodies: List[bytes] = []
        buf = self._buf
        while True:
            start = buf.find(bytes([SOF]))
            if start < 0:
                buf.clear()
                break
            del buf[:start]
            if len(buf) < 2:
                break
            length = buf[1]
            if not LEN_MIN <= length <= LEN_MAX:
                self.bad_len += 1
                del buf[:1]
                continue
            total = length + 4
            if len(buf) < total:
                break
            payload = bytes(buf[1 : 2 + length])
            (got,) = struct.unpack_from("<H", buf, 2 + length)
            if crc16(payload) != got:
                self.crc_errors += 1
                del buf[:1]
                continue
            bodies.append(bytes(buf[2 : 2 + length]))
            del buf[:total]
        return bodies


@dataclass
class Response:
    err: int
    ctr: int
    data: bytes


@dataclass
class Event:
    evt: int
    data: bytes


def encode_request(cmd: int, ctr: int, params: bytes = b"") -> bytes:
    return encode_frame(bytes([cmd, ctr & 0xFF]) + params)


def decode_body(body: bytes) -> "Response | Event":
    """BODY を応答またはイベントに分類する。形式不正は ValueError。"""
    if body and body[0] == PID_RESPONSE and len(body) >= 3:
        return Response(err=body[1], ctr=body[2], data=body[3:])
    if body and body[0] == PID_EVENT and len(body) >= 2:
        return Event(evt=body[1], data=body[2:])
    raise ValueError(f"不明な BODY: {body.hex()}")


@dataclass
class CanFrame:
    can_id: int
    data: bytes


def encode_can(frame: CanFrame) -> bytes:
    return struct.pack("<HB", frame.can_id, len(frame.data)) + frame.data


def parse_can_frames(data: bytes, count: int) -> List[CanFrame]:
    """`[CAN_ID u16][DLC][DATA]` を count 個読む。"""
    frames: List[CanFrame] = []
    pos = 0
    for _ in range(count):
        if pos + 3 > len(data):
            raise ValueError("CAN フレームが途中で切れています")
        can_id, dlc = struct.unpack_from("<HB", data, pos)
        pos += 3
        if pos + dlc > len(data):
            raise ValueError("CAN フレームの DATA が途中で切れています")
        frames.append(CanFrame(can_id, bytes(data[pos : pos + dlc])))
        pos += dlc
    return frames
