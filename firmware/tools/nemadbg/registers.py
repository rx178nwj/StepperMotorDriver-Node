"""CAN レジスタマップ（requirements.md 3.8 節）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

BOARD_REQ_BASE = 0x100
BOARD_RSP_BASE = 0x180
OP_READ = 0x02
OP_READ_RSP = 0x82
READ_MAX = 6


@dataclass(frozen=True)
class Reg:
    addr: int
    name: str
    size: int
    signed: bool
    access: str                                   # "R" / "RW" / "W"
    bits: Tuple[Tuple[int, str], ...] = ()        # (ビット位置, 名前)
    enum: Optional[Dict[int, str]] = None         # 値 -> 名前


STATE_NAMES = {0: "NO_VM", 1: "DISABLED", 2: "ENABLED", 3: "FAULT"}
CMD_RESULT_NAMES = {0: "OK", 1: "状態により拒否", 2: "値・アドレス不正", 3: "実行失敗"}
PA7_MODE_NAMES = {0: "AD_IN", 1: "DIR", 2: "Z"}
J10_MODE_NAMES = {0: "未使用", 1: "ENC", 2: "HOME"}

REGISTERS: List[Reg] = [
    Reg(0x00, "DEVICE_ID", 1, False, "R"),
    Reg(0x01, "FW_MAJOR", 1, False, "R"),
    Reg(0x02, "FW_MINOR", 1, False, "R"),
    Reg(0x03, "BOARD_ID", 1, False, "R"),
    Reg(0x04, "STATE", 1, False, "R", enum=STATE_NAMES),
    Reg(0x05, "FAULT", 1, False, "R", bits=(
        (0, "OPD"), (1, "ISD"), (2, "TSD"), (3, "VM_UV"), (4, "VM_OV"), (5, "BOARD_OT"))),
    Reg(0x06, "STATUS", 1, False, "R", bits=(
        (0, "MOVING"), (2, "EXT_5V"), (3, "SWITCH1"), (4, "SWITCH2"),
        (5, "OVERTEMP_WARN"), (6, "HOLDING"), (7, "TARGET_REACHED"))),
    Reg(0x07, "CMD_RESULT", 1, False, "R", enum=CMD_RESULT_NAMES),
    Reg(0x08, "POSITION", 4, True, "R"),
    Reg(0x0C, "SPEED", 4, True, "R"),
    Reg(0x10, "VM_MV", 2, False, "R"),
    Reg(0x12, "BOARD_TEMP", 2, True, "R"),
    Reg(0x14, "MCU_TEMP", 2, True, "R"),
    Reg(0x16, "AD_IN_MV", 2, False, "R"),
    Reg(0x18, "CONFIG_STATUS", 1, False, "R", bits=(
        (0, "LOADED"), (1, "UNSAVED"), (2, "SAVE_FAILED"), (3, "CORRECTED"))),
    Reg(0x19, "SENSOR_STATUS", 1, False, "R", bits=(
        (0, "VALID"), (1, "MH"), (2, "ML"), (3, "NO_RESPONSE"), (4, "SYNCED"), (5, "Z_LEVEL"))),
    Reg(0x1A, "SENSOR_RAW", 2, False, "R"),
    Reg(0x1C, "SHAFT_ANGLE", 2, False, "R"),
    Reg(0x1E, "INPUT_STATUS", 1, False, "R", bits=(
        (0, "HOME1"), (1, "HOME2"), (2, "HOMING"), (3, "HOMED"), (4, "HOME_FAILED"), (5, "LIMIT_STOP"))),
    Reg(0x20, "RUN_CURRENT", 2, False, "RW"),
    Reg(0x22, "HOLD_CURRENT", 2, False, "RW"),
    Reg(0x24, "MICROSTEP", 1, False, "RW"),
    Reg(0x25, "FLAGS", 1, False, "RW", bits=((0, "DIR_INVERT"), (1, "LOCAL_JOG"), (2, "SENSOR_DIR"), (3, "AGC"), (4, "HOME_ACTIVE_HIGH"))),
    Reg(0x26, "COMM_TIMEOUT", 2, False, "RW"),
    Reg(0x28, "MAX_SPEED", 4, False, "RW"),
    Reg(0x2C, "ACCEL", 4, False, "RW"),
    Reg(0x30, "DECEL", 4, False, "RW"),
    Reg(0x34, "START_SPEED", 4, False, "RW"),
    Reg(0x38, "JOG_SPEED", 4, False, "RW"),
    Reg(0x3C, "HOLD_DELAY", 2, False, "RW"),
    Reg(0x3E, "STEPS_PER_REV", 2, False, "RW"),
    Reg(0x40, "MOVE_ABS", 4, True, "W"),
    Reg(0x44, "MOVE_REL", 4, True, "W"),
    Reg(0x48, "RUN_SPEED", 4, True, "W"),
    Reg(0x4C, "SET_POSITION", 4, True, "W"),
    Reg(0x50, "COMMAND", 1, False, "W"),
    Reg(0x54, "GEAR_RATIO", 2, False, "RW"),
    Reg(0x56, "SENSOR_OFFSET", 2, False, "RW"),
    Reg(0x58, "PA7_MODE", 1, False, "RW", enum=PA7_MODE_NAMES),
    Reg(0x59, "J10_MODE", 1, False, "RW", enum=J10_MODE_NAMES),
    Reg(0x5A, "HOME_SPEED", 2, False, "RW"),
    Reg(0x5C, "ENC_COUNT", 4, True, "R"),
]

INVALID_FFFF = ('AD_IN_MV', 'SENSOR_RAW', 'SHAFT_ANGLE')   # 無効時 0xFFFF

REG_END = 0x60   # レジスタ空間の終端（含まない）。regs は 0x5F まで読む


def decode_value(reg: Reg, raw: bytes) -> int:
    return int.from_bytes(raw, "little", signed=reg.signed)


def format_value(reg: Reg, raw: bytes) -> str:
    """値を整形する。ビット・列挙があれば名前を付ける。"""
    v = decode_value(reg, raw)
    if reg.enum is not None:
        return f"{v} ({reg.enum.get(v, '不明')})"
    if reg.bits:
        names = [n for b, n in reg.bits if v & (1 << b)]
        return f"0x{v:02X} [" + (", ".join(names) if names else "-") + "]"
    if reg.name in INVALID_FFFF and v == 0xFFFF:
        return f"{v} (無効)"
    return str(v)
