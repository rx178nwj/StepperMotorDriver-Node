"""NEMA23 ステッピングモータドライバの USB 擬似デバイス（シミュレータ）。

実機なしで nemadbg CLI や PC 側 GUI を動かすためのもの。標準ライブラリのみで動く（PySide6 等に依存しない）。
`SimSerial` は pyserial 互換（write / read / in_waiting / close）で、`Link(SimSerial())` のように渡す。
プロトコル・レジスタ・状態遷移・コマンドの受付条件は docs/usb_interface.md 3〜8 章、名前表は docs/usb_symbols.md と
App/Src/*.c 末尾の SYM_ENTRY（104 件）に合わせてある。サイクル精度は求めない。

時間の進め方（遅延評価）
    内部状態は、要求・read・公開 API が呼ばれたとき、前回からの経過時間ぶん 1 ms 刻みで進める。
    一度に進める時間は MAX_ADVANCE_S（10 s）まで（それ以上空いたぶんは捨てる）。
    時計は `clock` で注入できる。既定は time.monotonic で、このとき read はデータが来るまで最大 `timeout`
    （既定 0.02 s）だけ実時間で待つ（Link._pump が CPU を空回りしないように）。偽の時計を渡すと待たない。

簡略化した点（実機との違い）
    - ファームの 1 ms / 10 ms 周期処理を 1 ms 刻みで再現するが、パルスは 1 個ずつ出さず速度の積分で位置を進める。
      加減速は時間基準（v += a*dt）で、ファームの「ステップごとに v^2 を更新」するプランナとは数 step ずれる。
      位置決めは「目標に着いたら即停止」（ファームは v<=START_SPEED まで落として止める）。
    - ローカルジョグ（SW1/SW2 長押し・同時押し）は再現しない。STATUS の bit3/bit4 を注入できるだけ。
    - 位置センサ AS5600 は「出力軸角度 = 位置から算出」。DIR（FLAGS bit2）による回転方向反転と I2C 異常は再現しない。
      センサの有無は set_sensor_present() で切り替える。MH/ML は常に 0、磁石検出（MD）は有効時に常に 1。
    - 原点復帰の状態機械は home.c の移植。HOME1/HOME2 スイッチは「その位置より外側で作動」する片側無限のスイッチ
      （HOME1 は POSITION >= home1_pos、HOME2 は POSITION <= home2_pos）。スイッチが無い側へ探索すると
      home_travel_limit（既定 100000 step）動いたところで失敗にする（実機は機械端まで動き続ける）。
    - エンコーダ（J10_MODE=1）の ENC_COUNT は位置に追従する（モード切替時に 0 から数え始める）。
    - 通信は CAN バスを持たない。comm_stats.* は常に 0。USB の受信タイムアウト（20 ms）は再現するが、
      送信詰まり（50 ms）は再現しない。SAVE_SETTINGS の応答遅れ（約 25 ms）は再現しない（loop.max_us にだけ反映）。
    - 保存設定（Flash）はプロセス内のメモリで持つ。`blank_flash=True` なら未保存（LOADED=0）で始まる。
    - 基板温度・MCU 温度・VM には小さな揺らぎを付ける（noise=False で無効、seed で再現可能）。
    - リセット（ENTER_BOOTLOADER の後）は実機では DFU デバイスに変わる。sim では以後 write / read が OSError を
      投げ（ACK は読める）、replug() で電源再投入相当に戻す。
    - スタック・ループ周期（loop.*、stack.*）はそれらしい固定値。
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import random
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import protocol as p

# ---- 定数（App/Inc/config.h、app.h、comm.h、usb_proto.h に合わせる） ----

DEVICE_ID = 0xA3
FW_MAJOR = 0
FW_MINOR = 4
PROTO_MAJOR = 1
PROTO_MINOR = 0
MAX_LEN = 250

MAX_ADVANCE_S = 10.0          # 1 回に進める時間の上限
RX_TIMEOUT_MS = 20            # フレーム途中でこれ以上途切れたら捨てる

STATE_NO_VM, STATE_DISABLED, STATE_ENABLED, STATE_FAULT = 0, 1, 2, 3
RESULT_OK, RESULT_REJECTED, RESULT_INVALID, RESULT_FAILED = 0, 1, 2, 3
FAULT_OPD, FAULT_ISD, FAULT_TSD, FAULT_VM_UV, FAULT_VM_OV, FAULT_OVERTEMP = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20

CFG_ST_LOADED, CFG_ST_DIRTY, CFG_ST_SAVE_FAILED, CFG_ST_SANITIZED = 0x01, 0x02, 0x04, 0x08

FLAG_INVERT_DIR, FLAG_LOCAL_JOG, FLAG_SENSOR_DIR, FLAG_AGC, FLAG_HOME_ACTIVE_HIGH = 0x01, 0x02, 0x04, 0x08, 0x10
FLAGS_MASK = 0x1F

CMD_ENABLE, CMD_DISABLE, CMD_STOP, CMD_ESTOP, CMD_CLEAR_FAULT = 1, 2, 3, 4, 5
CMD_RESET_ELEC_ANGLE, CMD_SAVE_SETTINGS, CMD_LOAD_SETTINGS, CMD_FACTORY_SETTINGS = 6, 7, 8, 9
CMD_SYNC_POSITION, CMD_HOME_NEG, CMD_HOME_POS = 10, 11, 12

J10_NONE, J10_ENC, J10_HOME = 0, 1, 2
PA7_AD_IN, PA7_DIR, PA7_Z = 0, 1, 2

VM_OK_MIN_MV, VM_OK_MAX_MV, VM_HYST_MV = 10500, 28000, 500
VM_STABLE_MS = 50
VM_FAULT_SAMPLES = 2
BOARD_TEMP_WARN_DC, BOARD_TEMP_FAULT_DC = 700, 800
TEMP_INVALID = -32768
CURRENT_MIN_MA, CURRENT_LIMIT_MA = 250, 3000
STEP_RATE_MAX = 100000
CURRENT_SETTLE_MS = 5
LO_ARM_DELAY_MS = 5
HOME_CONFIRM_SAMPLES = 2
POS_FAIL_LIMIT = 3
POS_RETRY_MS = 1000

CAN_ID_BROADCAST = 0x000
CAN_ID_FAULT_BASE = 0x080
CAN_ID_REQUEST_BASE = 0x100
CAN_ID_RESPONSE_BASE = 0x180

REG_MAP_SIZE = 0x60
BOOT_KEY = p.BOOT_KEY
RESET_CSR_POWER_ON = 0x0C000000    # BORRSTF | PINRSTF
BUILD_ID = hashlib.sha1(b"nemadbg-sim").digest()

# メモリ読み出しの許可領域（usb_proto.c の regions）
_MEM_REGIONS = [
    (0x08000000, 0x0803FFFF),   # Flash
    (0x20000000, 0x2000BFFF),   # SRAM1
    (0x2000C000, 0x2000FFFF),   # SRAM2
    (0x10000000, 0x10003FFF),   # SRAM2 別名
    (0x1FFF7000, 0x1FFF73FF),   # OTP
    (0x1FFF7500, 0x1FFF75FF),   # UID 等
]
RAM_BASE = 0x20000000
RAM_SIZE = 0x10000


class SimUnplugged(OSError):
    """ケーブル抜去中（または ROM ブートローダへ移行後）に write / read を呼んだ。"""


# ---------------------------------------------------------------- 設定

@dataclass
class Settings:
    """settings_t（パディングなし 40 B）。"""

    run_current_ma: int = 1000
    hold_current_ma: int = 500
    microstep: int = 16
    flags: int = 0x02
    comm_timeout_ms: int = 0
    max_speed: int = 3200
    accel: int = 16000
    decel: int = 16000
    start_speed: int = 200
    jog_speed: int = 1600
    hold_delay_ms: int = 500
    steps_per_rev: int = 200
    gear_ratio: int = 20
    sensor_offset: int = 0
    pa7_mode: int = 1
    j10_mode: int = 0
    home_speed: int = 800

    _FMT = "<HHBBHIIIIIHHHHBBH"

    def pack(self) -> bytes:
        return struct.pack(
            self._FMT, self.run_current_ma, self.hold_current_ma, self.microstep, self.flags, self.comm_timeout_ms,
            self.max_speed, self.accel, self.decel, self.start_speed, self.jog_speed, self.hold_delay_ms,
            self.steps_per_rev, self.gear_ratio, self.sensor_offset, self.pa7_mode, self.j10_mode, self.home_speed)


assert struct.calcsize(Settings._FMT) == 40


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)


def sanitize(s: Settings) -> bool:
    """範囲外を丸める（settings_sanitize）。丸めた項目があれば True。"""
    o = dataclasses.replace(s)
    s.run_current_ma = _clamp(s.run_current_ma, CURRENT_MIN_MA, CURRENT_LIMIT_MA)
    s.hold_current_ma = _clamp(s.hold_current_ma, CURRENT_MIN_MA, CURRENT_LIMIT_MA)
    if s.microstep not in (1, 2, 4, 8, 16, 32):
        s.microstep = 16
    s.flags &= FLAGS_MASK
    s.max_speed = _clamp(s.max_speed, 1, STEP_RATE_MAX)
    s.start_speed = _clamp(s.start_speed, 1, STEP_RATE_MAX)
    s.jog_speed = _clamp(s.jog_speed, 1, STEP_RATE_MAX)
    s.accel = _clamp(s.accel, 1, 10000000)
    s.decel = _clamp(s.decel, 1, 10000000)
    s.steps_per_rev = _clamp(s.steps_per_rev, 1, 0xFFFF)
    s.gear_ratio = _clamp(s.gear_ratio, 1, 1000)
    s.sensor_offset = _clamp(s.sensor_offset, 0, 4095)
    if s.pa7_mode > PA7_Z:
        s.pa7_mode = 1
    if s.j10_mode > J10_HOME:
        s.j10_mode = 0
    s.home_speed = _clamp(s.home_speed, 1, 0xFFFF)
    return s != o


# 書き込み可能レジスタ（昇順）: addr -> (サイズ, 設定のフィールド名。None は動作指令・COMMAND)
_WRITABLE: List[Tuple[int, int, Optional[str]]] = [
    (0x20, 2, "run_current_ma"), (0x22, 2, "hold_current_ma"), (0x24, 1, "microstep"), (0x25, 1, "flags"),
    (0x26, 2, "comm_timeout_ms"), (0x28, 4, "max_speed"), (0x2C, 4, "accel"), (0x30, 4, "decel"),
    (0x34, 4, "start_speed"), (0x38, 4, "jog_speed"), (0x3C, 2, "hold_delay_ms"), (0x3E, 2, "steps_per_rev"),
    (0x40, 4, None), (0x44, 4, None), (0x48, 4, None), (0x4C, 4, None), (0x50, 1, None),
    (0x54, 2, "gear_ratio"), (0x56, 2, "sensor_offset"), (0x58, 1, "pa7_mode"), (0x59, 1, "j10_mode"),
    (0x5A, 2, "home_speed"),
]


# ---------------------------------------------------------------- 名前表（RAM 配置）

T_U8, T_I8, T_U16, T_I16, T_U32, T_I32, T_F32, T_BYTES = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x10
_ELEM: Dict[int, Tuple[str, int]] = {
    T_U8: ("B", 1), T_I8: ("b", 1), T_U16: ("H", 2), T_I16: ("h", 2),
    T_U32: ("I", 4), T_I32: ("i", 4), T_F32: ("f", 4),
}

# (構造体名, パックするか, 構造体全体の登録名, [(名前, TYPE)...])。名前が "_" 始まりは登録しない（RAM 上だけの欄）
_GROUPS: List[Tuple[str, bool, Optional[str], List[Tuple[str, int]]]] = [
    ("app", False, None, [
        ("state", T_U8), ("fault", T_U8), ("status", T_U8), ("cmd_result", T_U8), ("cfg_status", T_U8),
        ("isr_fault", T_U8), ("vm_ok", T_U8), ("hold_active", T_U8), ("temp_warn", T_U8), ("lo_armed", T_U8)]),
    ("mon", False, None, [
        ("mon.vdda_mv", T_U32), ("mon.vm_mv", T_U32), ("mon.board_temp_dc", T_I16), ("mon.mcu_temp_dc", T_I16),
        ("mon.ad_in_mv", T_U32)]),
    ("adc_raw", False, None, [
        ("adc_raw.vrefint", T_U16), ("adc_raw.vm", T_U16), ("adc_raw.board_temp", T_U16), ("adc_raw.ad_in", T_U16),
        ("adc_raw.mcu_temp", T_U16)]),
    ("cfg", True, "cfg", [
        ("cfg.run_current_ma", T_U16), ("cfg.hold_current_ma", T_U16), ("cfg.microstep", T_U8), ("cfg.flags", T_U8),
        ("cfg.comm_timeout_ms", T_U16), ("cfg.max_speed", T_U32), ("cfg.accel", T_U32), ("cfg.decel", T_U32),
        ("cfg.start_speed", T_U32), ("cfg.jog_speed", T_U32), ("cfg.hold_delay_ms", T_U16),
        ("cfg.steps_per_rev", T_U16), ("cfg.gear_ratio", T_U16), ("cfg.sensor_offset", T_U16),
        ("cfg.pa7_mode", T_U8), ("cfg.j10_mode", T_U8), ("_cfg.home_speed", T_U16)]),
    ("sensor", False, None, [
        ("sensor.raw", T_U16), ("sensor.as_status", T_U8), ("sensor.fail_streak", T_U8), ("_sensor.magnet", T_U8),
        ("sensor.z_level", T_U8), ("_sensor.next_try_ms", T_U32), ("sensor.i2c_ok", T_U32), ("sensor.i2c_fail", T_U32),
        ("pa7_applied", T_U8), ("sensor_status", T_U8), ("shaft_angle", T_U16)]),
    ("stepper", False, None, [
        ("position", T_I32), ("cur_speed", T_F32), ("target_pos", T_I32), ("target_vel", T_F32), ("mode", T_U8),
        ("running", T_U8), ("touched", T_U8), ("dir", T_I8), ("plan_speed", T_F32), ("commit_pos", T_I32),
        ("prm.max_speed", T_F32), ("prm.accel", T_F32), ("prm.decel", T_F32), ("prm.start_speed", T_F32),
        ("prm.invert_dir", T_U8)]),
    ("j10", False, None, [
        ("input_status", T_U8), ("limit_stopped", T_U8), ("agc_out", T_U8), ("enc_count", T_I32),
        ("home.state", T_U8), ("home.side", T_I8), ("home.seek_speed", T_I32), ("home.back_speed", T_I32),
        ("home.limit_steps", T_U32), ("home.start_pos", T_I32), ("home.opp_prev", T_U8),
        ("hm1.active", T_U8), ("hm1.cand", T_U8), ("hm1.count", T_U8),
        ("hm2.active", T_U8), ("hm2.cand", T_U8), ("hm2.count", T_U8)]),
    ("comm_stats", False, "comm_stats", [
        ("comm_stats.rx_frames", T_U32), ("comm_stats.rx_dropped", T_U32), ("comm_stats.tx_frames", T_U32),
        ("comm_stats.tx_dropped", T_U32), ("comm_stats.err_events", T_U32), ("comm_stats.err_passive", T_U32),
        ("comm_stats.err_busoff", T_U32), ("comm_stats.err_rx_overrun", T_U32), ("comm_stats.err_last_code", T_U32)]),
    # 配列は (名前, TYPE, 要素数)。CAN を持たないので init_stage 以外は常に 0
    ("comm_diag", False, "comm_diag", [
        ("comm_diag.init_stage", T_U32), ("comm_diag.esr", T_U32), ("comm_diag.tec", T_U32),
        ("comm_diag.rec", T_U32), ("comm_diag.tec_max", T_U32), ("comm_diag.rec_max", T_U32),
        ("comm_diag.ewg_count", T_U32), ("comm_diag.lec_count", T_U32, 6), ("comm_diag.last_rx_id", T_U32),
        ("comm_diag.last_rx_dlc", T_U32), ("comm_diag.last_rx_data", T_U8, 8)]),
    ("usb_stats", False, "usb_stats", [
        ("usb_stats.rx_frames", T_U32), ("usb_stats.crc_errors", T_U32), ("usb_stats.dropped", T_U32),
        ("usb_stats.rx_overflow", T_U32), ("usb_stats.rx_timeouts", T_U32), ("usb_stats.bad_len", T_U32),
        ("usb_stats.tx_frames", T_U32), ("usb_stats.tx_dropped", T_U32)]),
    ("diag", False, None, [
        ("uwTick", T_U32), ("reset_csr", T_U32), ("loop.max_us", T_U32), ("loop.max_1s_us", T_U32),
        ("loop.rate_hz", T_U32), ("stack.used_max", T_U32), ("stack.size", T_U32)]),
]


@dataclass(frozen=True)
class SymEntry:
    name: str
    addr: int
    type: int
    num: int

    @property
    def size(self) -> int:
        return _ELEM[self.type][1] * self.num if self.type != T_BYTES else self.num


def _build_symbols() -> Tuple[List[SymEntry], List[Tuple[str, str, int]]]:
    """名前表（登録順）と、RAM へ書く欄の一覧 [(名前, struct 書式, アドレス)] を作る。"""
    entries: List[SymEntry] = []
    fields: List[Tuple[str, str, int]] = []
    cursor = RAM_BASE + 0x40
    for _gname, packed, whole, members in _GROUPS:
        cursor = (cursor + 3) & ~3
        base = cursor
        first = len(entries)
        for member in members:
            name, typ = member[0], member[1]
            num = member[2] if len(member) > 2 else 1
            fmt, size = _ELEM[typ]
            if not packed:
                cursor = (cursor + size - 1) & ~(size - 1)
            fields.append((name, fmt if num == 1 else str(num) + fmt, cursor))
            if not name.startswith("_"):
                entries.append(SymEntry(name, cursor, typ, num))
            cursor += size * num
        if whole is not None:
            entries.insert(first, SymEntry(whole, base, T_BYTES, cursor - base))
    return entries, fields


SYMBOLS, _RAM_FIELDS = _build_symbols()
SYM_INDEX: Dict[str, int] = {e.name: i for i, e in enumerate(SYMBOLS)}
# 構造体全体の登録の大きさ（ファームの sizeof）
assert len(SYMBOLS) == 116
assert {e.name: e.num for e in SYMBOLS if e.type == T_BYTES} == {
    "cfg": 40, "comm_stats": 36, "comm_diag": 68, "usb_stats": 32}


# ---------------------------------------------------------------- 原点復帰（home.c の移植）

H_IDLE, H_SEEK, H_SEEK_STOP, H_BACK, H_BACK_STOP, H_DONE, H_FAILED = range(7)
A_NONE, A_RUN, A_ESTOP, A_ZERO = range(4)


class _Home:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = H_IDLE
        self.side = 0
        self.seek_speed = 0
        self.back_speed = 0
        self.limit_steps = 0
        self.start_pos = 0
        self.opp_prev = 0
        self.seek_origin = 0     # sim 専用: 探索を始めた位置（見つからない探索を打ち切るため）

    @staticmethod
    def _clamp_speed(v: int) -> int:
        return max(1, min(STEP_RATE_MAX, v))

    def _side_active(self, h1: bool, h2: bool) -> bool:
        return h1 if self.side > 0 else h2

    def _opp_active(self, h1: bool, h2: bool) -> bool:
        return h2 if self.side > 0 else h1

    def is_active(self) -> bool:
        return H_SEEK <= self.state <= H_BACK_STOP

    def abort(self) -> None:
        if self.is_active():
            self.state = H_FAILED

    def start(self, side: int, home_speed: int, max_speed: int, start_speed: int, limit_steps: int,
              h1: bool, h2: bool, position: int) -> Tuple[int, int]:
        self.side = 1 if side > 0 else -1
        seek = min(home_speed, max_speed)
        back = min(start_speed, seek)
        self.seek_speed = self._clamp_speed(seek)
        self.back_speed = self._clamp_speed(back)
        self.limit_steps = limit_steps
        self.opp_prev = 1 if self._opp_active(h1, h2) else 0
        self.seek_origin = position
        if self._side_active(h1, h2):
            if self._opp_active(h1, h2):
                self.state = H_FAILED     # 両側が作動（配線の誤り・固着）
                return A_NONE, 0
            self.state = H_BACK
            self.start_pos = position
            return A_RUN, -self.side * self.back_speed
        self.state = H_SEEK
        return A_RUN, self.side * self.seek_speed

    def step(self, h1: bool, h2: bool, moving: bool, position: int) -> Tuple[int, int]:
        st = self.state
        if st == H_SEEK:
            if not moving:
                self.state = H_FAILED
                return A_NONE, 0
            opp = self._opp_active(h1, h2)
            if opp and not self.opp_prev:
                self.state = H_FAILED
                return A_ESTOP, 0
            self.opp_prev = 1 if opp else 0
            if self._side_active(h1, h2):
                self.state = H_SEEK_STOP
                return A_ESTOP, 0
            return A_NONE, 0
        if st == H_SEEK_STOP:
            if moving:
                return A_NONE, 0
            self.state = H_BACK
            self.start_pos = position
            return A_RUN, -self.side * self.back_speed
        if st == H_BACK:
            if not moving:
                self.state = H_FAILED
                return A_NONE, 0
            if not self._side_active(h1, h2):
                self.state = H_BACK_STOP
                return A_ESTOP, 0
            if self._opp_active(h1, h2) or abs(position - self.start_pos) >= self.limit_steps:
                self.state = H_FAILED
                return A_ESTOP, 0
            return A_NONE, 0
        if st == H_BACK_STOP:
            if moving:
                return A_NONE, 0
            self.state = H_DONE
            return A_ZERO, 0
        return A_NONE, 0


class _Debounce:
    """j10.c の pin_deb_t。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.cand = False
        self.count = 0

    def sample(self, s: bool) -> None:
        if self.count != 0 and s == self.cand:
            if self.count < 0xFF:
                self.count += 1
        else:
            self.cand = s
            self.count = 1
        if self.count >= HOME_CONFIRM_SAMPLES:
            self.active = s


# ---------------------------------------------------------------- 基板本体（状態機械・運動・レジスタ）

MODE_IDLE, MODE_POS, MODE_VEL = 0, 1, 2


class _Board:
    """ファームの状態を 1 ms 刻みで進める。I/O（フレーム・時計・排他）は持たない。"""

    def __init__(self, board_id: int, noise: bool, seed: Optional[int], blank_flash: bool) -> None:
        self.board_id = board_id & 0x07
        self.noise = noise
        self.rng = random.Random(seed)
        self.saved: Optional[Settings] = None if blank_flash else Settings()
        # 注入できる外部条件（reboot でも保つ）
        self.vm_mv_set = 24000
        self.board_temp_set: Optional[int] = 250       # 0.1 ℃。None は無効（NTC 断線）
        self.ad_in_mv_set = 1650
        self.home1_pos: Optional[int] = 20000
        self.home2_pos: Optional[int] = -20000
        self.home_travel_limit = 100000
        self.sensor_present = True
        self.ext_5v = True
        self.sw1 = False
        self.sw2 = False
        self.z_level = False
        self.mech_offset = 0           # 出力軸の機械位置 = position + mech_offset
        self.save_fails = False
        self.reset(RESET_CSR_POWER_ON)

    # ---- 初期化 ----

    def reset(self, reset_csr: int) -> None:
        self.reset_csr = reset_csr
        self.now = 0
        self.state = STATE_NO_VM
        self.prev_state = STATE_NO_VM
        self.fault_bits = 0
        self.isr_fault = 0
        self.cmd_result = 0
        self.fault_events: List[Tuple[int, int]] = []
        self.cfg = dataclasses.replace(self.saved) if self.saved is not None else Settings()
        self.cfg_status = CFG_ST_LOADED if self.saved is not None else 0
        self.sensor_synced = False
        self.sensor_auto_done = False
        self.vm_ok = False
        self.vm_good_ms = 0
        self.vm_bad_count = 0
        self.vm_bad_reason = 0
        self.temp_warn = False
        self.lo_armed = False
        self.lo_arm_at = 0
        self.hold_active = False
        self.idle_since = 0
        self.start_allowed_at = 0
        self.limit_stopped = False
        self.last_request_ms = 0
        self.agc_pin = False
        self.pa7_applied = PA7_AD_IN
        self.j10_applied = 0xFF
        self.enc_origin = 0
        self.temp_walk = 0.0
        self.max_loop_us = 260
        # 運動
        self.position = 0
        self.frac = 0.0
        self.target_pos = 0
        self.target_vel = 0.0
        self.mode = MODE_IDLE
        self.running = False
        self.dir = 1
        self.cur_speed = 0.0
        self.touched = False
        self.v = 0.0
        self.braking = False
        self.prm_max = 3200.0
        self.prm_accel = 16000.0
        self.prm_decel = 16000.0
        self.prm_start = 200.0
        self.prm_invert = False
        # 監視
        self.mon_vdda = 3300
        self.mon_vm = 0
        self.mon_board_temp = TEMP_INVALID
        self.mon_mcu_temp = 0
        self.mon_ad_in = 0xFFFF
        self.adc = {"vrefint": 0, "vm": 0, "board_temp": 0, "ad_in": 0, "mcu_temp": 0}
        # センサ
        self.s_raw = 0
        self.s_as_status = 0
        self.s_fail = POS_FAIL_LIMIT
        self.s_magnet = False
        self.s_next_try = 0
        self.s_i2c_ok = 0
        self.s_i2c_fail = 0
        # J10・原点
        self.home = _Home()
        self.hm1 = _Debounce()
        self.hm2 = _Debounce()
        # スナップショット（名前表で公開）
        self.status_snapshot = 0
        self.sensor_status_snapshot = 0
        self.shaft_angle_snapshot = 0
        self.input_status_snapshot = 0
        self.enc_snapshot = 0
        self.agc_snapshot = 0
        # app_init
        self._apply_stepper_params()
        self.pa7_applied = self.cfg.pa7_mode
        self._j10_set_mode(self.cfg.j10_mode)

    # ---- 補助 ----

    def _awake(self) -> bool:
        return self.state in (STATE_DISABLED, STATE_ENABLED)

    def _pending(self) -> bool:
        if self.running:
            return False
        if self.mode == MODE_POS:
            return self.target_pos != self.position
        if self.mode == MODE_VEL:
            return self.target_vel != 0.0
        return False

    def _motion_idle(self) -> bool:
        return not self.running and not self._pending() and not self.home.is_active()

    def _mech(self) -> int:
        return self.position + self.mech_offset

    def _apply_stepper_params(self) -> None:
        c = self.cfg
        mx = max(1.0, min(float(c.max_speed), float(STEP_RATE_MAX)))
        self.prm_max = mx
        self.prm_start = min(max(1.0, float(c.start_speed)), mx)
        self.prm_accel = max(1.0, float(c.accel))
        self.prm_decel = max(1.0, float(c.decel))
        self.prm_invert = (c.flags & FLAG_INVERT_DIR) != 0

    def _agc_apply(self) -> None:
        self.agc_pin = (self.cfg.flags & FLAG_AGC) != 0

    def _j10_set_mode(self, mode: int) -> None:
        if mode > J10_HOME or mode == self.j10_applied:
            return
        self.hm1.reset()
        self.hm2.reset()
        if mode == J10_ENC:
            self.enc_origin = self._mech()
        self.j10_applied = mode

    def _apply_settings(self) -> None:
        self._apply_stepper_params()
        self.pa7_applied = self.cfg.pa7_mode
        self._j10_set_mode(self.cfg.j10_mode)
        if self.state != STATE_ENABLED:
            self._agc_apply()

    # ---- 状態遷移 ----

    def _driver_wake(self) -> None:
        self._agc_apply()
        self.lo_armed = False
        self.lo_arm_at = self.now + LO_ARM_DELAY_MS

    def _driver_sleep(self) -> None:
        self._stepper_estop()
        self.home.abort()
        self._agc_apply()
        self.lo_armed = False

    def _enter_fault(self, bits: int) -> None:
        self._driver_sleep()
        self.fault_bits |= bits
        self.state = STATE_FAULT

    def inject_fault(self, bits: int) -> None:
        """LO1/LO2 割り込み相当（OPD/ISD/TSD）。ISR は即座にパルスを止める。"""
        self._stepper_estop()
        self.isr_fault |= bits & 0x07

    def _cmd_enable(self) -> int:
        if self.state != STATE_DISABLED:
            return RESULT_REJECTED
        self.hold_active = False
        self.idle_since = self.now
        self.start_allowed_at = self.now + CURRENT_SETTLE_MS
        self.state = STATE_ENABLED
        return RESULT_OK

    def _cmd_disable(self) -> int:
        if self.state != STATE_ENABLED:
            return RESULT_REJECTED
        self._stepper_estop()
        self.home.abort()
        self._agc_apply()
        self.state = STATE_DISABLED
        return RESULT_OK

    def _cmd_clear_fault(self) -> int:
        if self.state != STATE_FAULT:
            return RESULT_REJECTED
        if self.mon_board_temp != TEMP_INVALID and self.mon_board_temp >= BOARD_TEMP_WARN_DC:
            return RESULT_REJECTED
        self.fault_bits = 0
        if self.vm_ok:
            self._driver_wake()
            self.state = STATE_DISABLED
        else:
            self.state = STATE_NO_VM
        return RESULT_OK

    def _cmd_sync_position(self) -> int:
        if self.running or self.home.is_active():
            return RESULT_REJECTED
        if not self._position_valid():
            return RESULT_FAILED
        c = self.cfg
        total = c.gear_ratio * c.steps_per_rev * c.microstep
        if total > 0x7FFFFFFF:
            return RESULT_INVALID
        a = (self._position_raw() + 4096 - c.sensor_offset) & 0x0FFF
        n = (a * total + 2048) >> 12
        if n >= total:
            n = 0
        if not self._stepper_set_position(n):
            return RESULT_REJECTED
        self.sensor_synced = True
        return RESULT_OK

    def _cmd_save(self) -> int:
        if not self._motion_idle():
            return RESULT_REJECTED
        if self.save_fails:
            self.cfg_status |= CFG_ST_SAVE_FAILED
            return RESULT_FAILED
        self.saved = dataclasses.replace(self.cfg)
        self.cfg_status &= ~(CFG_ST_DIRTY | CFG_ST_SAVE_FAILED) & 0xFF
        self.max_loop_us = max(self.max_loop_us, 25400)    # Flash 書き込みで CPU が止まる
        return RESULT_OK

    def _cmd_load(self, factory: bool) -> int:
        if not self._motion_idle():
            return RESULT_REJECTED
        if factory:
            t = Settings()
        elif self.saved is None:
            return RESULT_FAILED
        else:
            t = dataclasses.replace(self.saved)
            if sanitize(t):
                self.cfg_status |= CFG_ST_SANITIZED
        self.cfg = t
        if factory:
            self.cfg_status |= CFG_ST_DIRTY
        else:
            self.cfg_status &= ~CFG_ST_DIRTY & 0xFF
        self._apply_settings()
        return RESULT_OK

    def _limit_active(self, d: int) -> bool:
        if self.cfg.j10_mode != J10_HOME:
            return False
        return self.hm1.active if d > 0 else (self.hm2.active if d < 0 else False)

    def _move_allowed(self, d: int) -> bool:
        return not self.home.is_active() and not self._limit_active(d)

    def _cmd_home(self, side: int) -> int:
        if self.state != STATE_ENABLED or not self._motion_idle() or self.cfg.j10_mode != J10_HOME:
            return RESULT_REJECTED
        c = self.cfg
        act = self.home.start(side, c.home_speed, c.max_speed, c.start_speed, c.steps_per_rev * c.microstep,
                              self.hm1.active, self.hm2.active, self.position)
        self._home_apply(act)
        self.limit_stopped = False
        return RESULT_OK

    def _home_apply(self, act: Tuple[int, int]) -> None:
        kind, speed = act
        if kind == A_RUN:
            self._stepper_run(speed)
        elif kind == A_ESTOP:
            self._stepper_estop()
        elif kind == A_ZERO:
            if not self._stepper_set_position(0):
                self.home.abort()

    def command(self, c: int) -> int:
        if c == CMD_ENABLE:
            return self._cmd_enable()
        if c == CMD_DISABLE:
            return self._cmd_disable()
        if c == CMD_STOP:
            self._stepper_run(0)
            self.home.abort()
            return RESULT_OK
        if c == CMD_ESTOP:
            self._stepper_estop()
            self.home.abort()
            return RESULT_OK
        if c == CMD_CLEAR_FAULT:
            return self._cmd_clear_fault()
        if c == CMD_RESET_ELEC_ANGLE:
            if not self._awake() or self.running or self.home.is_active():
                return RESULT_REJECTED
            return RESULT_OK
        if c == CMD_SAVE_SETTINGS:
            return self._cmd_save()
        if c == CMD_LOAD_SETTINGS:
            return self._cmd_load(False)
        if c == CMD_FACTORY_SETTINGS:
            return self._cmd_load(True)
        if c == CMD_SYNC_POSITION:
            return self._cmd_sync_position()
        if c == CMD_HOME_NEG:
            return self._cmd_home(-1)
        if c == CMD_HOME_POS:
            return self._cmd_home(1)
        return RESULT_INVALID

    # ---- ステッパ ----

    def _stepper_move_to(self, target: int) -> None:
        self.target_pos = target
        self.braking = False
        self.mode = MODE_POS

    def _stepper_move_by(self, delta: int) -> None:
        base = self.target_pos if self.mode == MODE_POS else self.position
        self.target_pos = base + delta
        self.braking = False
        self.mode = MODE_POS

    def _stepper_run(self, speed: int) -> None:
        self.target_vel = float(speed)
        self.braking = False
        self.mode = MODE_VEL

    def _stepper_estop(self) -> None:
        self.running = False
        self.cur_speed = 0.0
        self.v = 0.0
        self.frac = 0.0
        self.target_vel = 0.0
        self.braking = False
        self.mode = MODE_IDLE

    def _stepper_set_position(self, pos: int) -> bool:
        if self.running:
            return False
        mech = self._mech()
        self.touched = True
        self.position = pos
        self.mech_offset = mech - pos     # 出力軸は動かない
        self.target_pos = pos
        self.target_vel = 0.0
        self.braking = False
        self.mode = MODE_IDLE
        return True

    def _stepper_service(self) -> None:
        """停止中に指令があるとき、最初のパルスを出して走り始める。"""
        if self.mode == MODE_POS:
            d = 1 if self.target_pos > self.position else -1
            vt = self.prm_max
        else:
            d = 1 if self.target_vel > 0 else -1
            vt = min(abs(self.target_vel), self.prm_max)
        self.dir = d
        self.braking = False
        self.frac = 0.0
        self.v = min(self.prm_start, vt)
        self.cur_speed = self.v * d
        self.running = True
        self.touched = True

    def _stepper_advance(self) -> None:
        """1 ms ぶん走らせる（台形加減速、速度の積分で位置を進める）。"""
        if not self.running:
            return
        dt = 0.001
        d = self.dir
        vs = self.prm_start
        v = self.v
        rem = 0
        if self.mode == MODE_POS:
            rem = (self.target_pos - self.position) * d
            if rem == 0:
                self._stop_running()
                return
            vt = self.prm_max if rem > 0 else 0.0
        elif self.mode == MODE_VEL:
            vt = min(max(self.target_vel * d, 0.0), self.prm_max)
        else:
            vt = 0.0

        if vt <= 0.0:
            v -= self.prm_decel * dt
            if v <= vs:
                self._stop_running()
                return
        else:
            stop_steps = (v * v - vs * vs) / (2.0 * self.prm_decel) if v > vs else 0.0
            if self.braking or (self.mode == MODE_POS and rem <= stop_steps + v * dt + 1.0):
                self.braking = self.mode == MODE_POS
                v = max(v - self.prm_decel * dt, vs)
            elif v < vt:
                v = min(v + self.prm_accel * dt, vt)
            elif v > vt:
                v = max(v - self.prm_decel * dt, vt)

        dist = v * dt + self.frac
        n = int(dist)
        self.frac = dist - n
        self.v = v
        if self.mode == MODE_POS and n >= rem:
            self.position += d * rem
            self._stop_running()
            return
        self.position += d * n
        self.cur_speed = v * d

    def _stop_running(self) -> None:
        self.running = False
        self.cur_speed = 0.0
        self.v = 0.0
        self.frac = 0.0
        self.braking = False

    # ---- 周期処理 ----

    def step_ms(self) -> None:
        self.now += 1
        self._tick_1ms()
        if self.now % 10 == 0:
            self._tick_10ms()

    def _tick_1ms(self) -> None:
        self._handle_driver_fault()
        # J10 の HOME 入力。作動したかどうかを直接サンプルする（FLAGS bit4 の論理は端子レベルを反転するだけで結果は同じ）
        if self.j10_applied == J10_HOME:
            self.hm1.sample(self._home1_raw())
            self.hm2.sample(self._home2_raw())
        else:
            self.hm1.reset()
            self.hm2.reset()
        self.status_snapshot = self._status_bits()
        self._handle_motion()
        self._stepper_advance()
        self._handle_limit_and_home()
        self.input_status_snapshot = self._input_status_bits()
        self.enc_snapshot = self._enc_count()
        if self.state == STATE_FAULT and self.prev_state != STATE_FAULT:
            self.fault_events.append((self.state, self.fault_bits))
        self.prev_state = self.state

    def _home1_raw(self) -> bool:
        return self.home1_pos is not None and self.position >= self.home1_pos

    def _home2_raw(self) -> bool:
        return self.home2_pos is not None and self.position <= self.home2_pos

    def _handle_driver_fault(self) -> None:
        f = self.isr_fault
        self.isr_fault = 0
        if self.state == STATE_FAULT or not self._awake():
            return
        if f:
            self._enter_fault(f)
            return
        if not self.lo_armed and self.now >= self.lo_arm_at:
            self.lo_armed = True

    def _handle_motion(self) -> None:
        if self.state != STATE_ENABLED:
            return
        pending = self._pending()
        if pending:
            if self.hold_active:
                self.hold_active = False
                self.start_allowed_at = self.now + CURRENT_SETTLE_MS
            if self.now >= self.start_allowed_at:
                self._stepper_service()
        if self.running or pending or self.home.is_active():
            self.idle_since = self.now
        elif not self.hold_active and (self.now - self.idle_since) >= self.cfg.hold_delay_ms:
            self.hold_active = True
        # 通信タイムアウト（ジョグは再現しないので常に対象）
        t = self.cfg.comm_timeout_ms
        if t != 0 and (self.running or pending) and (self.now - self.last_request_ms) > t:
            self._stepper_run(0)
            self.home.abort()

    def _handle_limit_and_home(self) -> None:
        if self.home.is_active():
            moving = self.running or self._pending()
            self._home_apply(self.home.step(self.hm1.active, self.hm2.active, moving, self.position))
            if self.home.state == H_SEEK and abs(self.position - self.home.seek_origin) > self.home_travel_limit:
                self.home.state = H_FAILED       # sim 専用: スイッチが見つからない
                self._stepper_estop()
            return
        d = self.dir if self.running else 0
        if d != 0 and self._limit_active(d):
            self._stepper_estop()
            self.limit_stopped = True

    def _ntc_raw(self, dc: int) -> int:
        t = dc / 10.0
        r = 10000.0 * math.exp(3434.0 * (1.0 / (t + 273.15) - 1.0 / 298.15))
        return max(20, min(4075, int(round(4095.0 * r / (r + 4700.0)))))

    def _monitor_sample(self) -> None:
        wob = 0
        if self.noise:
            self.temp_walk = max(-3.0, min(3.0, self.temp_walk + self.rng.uniform(-0.4, 0.4)))
            wob = int(round(self.temp_walk))
        vm = self.vm_mv_set
        if self.noise and vm > 0:
            vm += self.rng.randint(-10, 10)
        self.mon_vm = max(0, vm)
        if self.board_temp_set is None:
            self.mon_board_temp = TEMP_INVALID
            self.adc["board_temp"] = 4095
            mcu = 300
        else:
            self.mon_board_temp = self.board_temp_set + wob
            self.adc["board_temp"] = self._ntc_raw(self.mon_board_temp)
            mcu = self.board_temp_set + 50
        self.mon_mcu_temp = mcu + wob
        measure = self.cfg.pa7_mode == PA7_AD_IN
        self.mon_ad_in = self.ad_in_mv_set if measure else 0xFFFF
        self.adc["vrefint"] = 1356
        self.adc["vm"] = max(0, min(4095, int(round(self.mon_vm / 19.0 / 3300.0 * 4095.0))))
        self.adc["ad_in"] = max(0, min(4095, int(round(self.ad_in_mv_set * 4095.0 / 3300.0)))) if measure else 0
        self.adc["mcu_temp"] = max(0, min(4095, int(round(1000 + (self.mon_mcu_temp / 10.0 - 30.0) * 2.5))))

    def _position_poll(self) -> None:
        if self.s_fail >= POS_FAIL_LIMIT and self.now - self.s_next_try < 0:
            return
        if self.sensor_present:
            self.s_i2c_ok += 1
            self.s_fail = 0
            self.s_as_status = 0x20
            self.s_magnet = True
            self.s_raw = self._sensor_raw_now()
        else:
            self.s_i2c_fail += 1
            if self.s_fail < POS_FAIL_LIMIT:
                self.s_fail += 1
            if self.s_fail >= POS_FAIL_LIMIT:
                self.s_next_try = self.now + POS_RETRY_MS

    def _sensor_raw_now(self) -> int:
        c = self.cfg
        total = c.gear_ratio * c.steps_per_rev * c.microstep
        a = ((self._mech() % total) * 4096 + total // 2) // total
        return (a + c.sensor_offset) & 0x0FFF

    def _position_valid(self) -> bool:
        return self.s_fail < POS_FAIL_LIMIT and self.s_magnet

    def _position_raw(self) -> int:
        return self.s_raw if self._position_valid() else 0xFFFF

    def _shaft_angle(self) -> int:
        raw = self._position_raw()
        if raw > 4095:
            return 0xFFFF
        a = (raw + 4096 - self.cfg.sensor_offset) & 0x0FFF
        return (a * 36000 + 2048) >> 12

    def _sensor_status_bits(self) -> int:
        s = 0
        responding = self.s_fail < POS_FAIL_LIMIT
        if self._position_valid():
            s |= 1 << 0
        if responding and self.s_as_status & 0x08:
            s |= 1 << 1
        if responding and self.s_as_status & 0x10:
            s |= 1 << 2
        if not responding:
            s |= 1 << 3
        if self.sensor_synced:
            s |= 1 << 4
        if self.pa7_applied == PA7_Z and self.z_level:
            s |= 1 << 5
        return s

    def _tick_10ms(self) -> None:
        self._monitor_sample()
        self._position_poll()
        if not self.sensor_auto_done and self._position_valid():
            self.sensor_auto_done = True
            if not self.touched:
                self._cmd_sync_position()
        self.sensor_status_snapshot = self._sensor_status_bits()
        self.shaft_angle_snapshot = self._shaft_angle()
        self.agc_snapshot = 1 if self.agc_pin else 0

        vm = self.mon_vm
        lo = VM_OK_MIN_MV if self.vm_ok else VM_OK_MIN_MV + VM_HYST_MV
        hi = VM_OK_MAX_MV if self.vm_ok else VM_OK_MAX_MV - VM_HYST_MV
        if lo <= vm <= hi:
            self.vm_bad_count = 0
            if not self.vm_ok:
                self.vm_good_ms += 10
                if self.vm_good_ms >= VM_STABLE_MS:
                    self.vm_ok = True
        else:
            self.vm_good_ms = 0
            if self.vm_bad_count < 0xFF:
                self.vm_bad_count += 1
            if self.vm_ok and self.vm_bad_count >= VM_FAULT_SAMPLES:
                self.vm_ok = False
                self.vm_bad_reason = FAULT_VM_UV if vm < lo else FAULT_VM_OV

        if self.state == STATE_NO_VM:
            if self.vm_ok:
                self._driver_wake()
                self.state = STATE_DISABLED
        elif self.state == STATE_DISABLED:
            if not self.vm_ok:
                self._driver_sleep()
                self.state = STATE_NO_VM
        elif self.state == STATE_ENABLED:
            if not self.vm_ok:
                self._enter_fault(self.vm_bad_reason)

        t = self.mon_board_temp
        valid = t != TEMP_INVALID
        self.temp_warn = valid and t >= BOARD_TEMP_WARN_DC
        if valid and t >= BOARD_TEMP_FAULT_DC and self.state in (STATE_DISABLED, STATE_ENABLED):
            self._enter_fault(FAULT_OVERTEMP)

    # ---- レジスタ ----

    def _status_bits(self) -> int:
        s = 0
        if self.running or self.home.is_active():
            s |= 1 << 0
        if self.ext_5v:
            s |= 1 << 2
        if self.sw1:
            s |= 1 << 3
        if self.sw2:
            s |= 1 << 4
        if self.temp_warn:
            s |= 1 << 5
        if self.hold_active and self.state == STATE_ENABLED:
            s |= 1 << 6
        if self.mode == MODE_POS and not self.running and self.position == self.target_pos:
            s |= 1 << 7
        return s

    def _input_status_bits(self) -> int:
        s = 0
        if self.hm1.active:
            s |= 1 << 0
        if self.hm2.active:
            s |= 1 << 1
        if self.home.is_active():
            s |= 1 << 2
        if self.home.state == H_DONE:
            s |= 1 << 3
        if self.home.state == H_FAILED:
            s |= 1 << 4
        if self.limit_stopped:
            s |= 1 << 5
        return s

    def _enc_count(self) -> int:
        if self.j10_applied != J10_ENC:
            return 0
        v = (self._mech() - self.enc_origin) & 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    def reg_read(self, reg: int, length: int) -> bytes:
        c = self.cfg
        m = bytearray(REG_MAP_SIZE)
        m[0x00] = DEVICE_ID
        m[0x01] = FW_MAJOR
        m[0x02] = FW_MINOR
        m[0x03] = self.board_id
        m[0x04] = self.state
        m[0x05] = self.fault_bits
        m[0x06] = self._status_bits()
        m[0x07] = self.cmd_result
        struct.pack_into("<i", m, 0x08, self.position)
        struct.pack_into("<i", m, 0x0C, int(self.cur_speed) if self.running else 0)
        struct.pack_into("<H", m, 0x10, min(self.mon_vm, 0xFFFF))
        struct.pack_into("<h", m, 0x12, self.mon_board_temp)
        struct.pack_into("<h", m, 0x14, self.mon_mcu_temp)
        struct.pack_into("<H", m, 0x16, min(self.mon_ad_in, 0xFFFF) if c.pa7_mode == PA7_AD_IN else 0xFFFF)
        m[0x18] = self.cfg_status
        m[0x19] = self._sensor_status_bits()
        struct.pack_into("<H", m, 0x1A, self._position_raw())
        struct.pack_into("<H", m, 0x1C, self._shaft_angle())
        m[0x1E] = self._input_status_bits()
        struct.pack_into("<H", m, 0x20, c.run_current_ma)
        struct.pack_into("<H", m, 0x22, c.hold_current_ma)
        m[0x24] = c.microstep
        m[0x25] = c.flags
        struct.pack_into("<H", m, 0x26, c.comm_timeout_ms)
        struct.pack_into("<I", m, 0x28, c.max_speed)
        struct.pack_into("<I", m, 0x2C, c.accel)
        struct.pack_into("<I", m, 0x30, c.decel)
        struct.pack_into("<I", m, 0x34, c.start_speed)
        struct.pack_into("<I", m, 0x38, c.jog_speed)
        struct.pack_into("<H", m, 0x3C, c.hold_delay_ms)
        struct.pack_into("<H", m, 0x3E, c.steps_per_rev)
        struct.pack_into("<H", m, 0x54, c.gear_ratio)
        struct.pack_into("<H", m, 0x56, c.sensor_offset)
        m[0x58] = c.pa7_mode
        m[0x59] = c.j10_mode
        struct.pack_into("<H", m, 0x5A, c.home_speed)
        struct.pack_into("<i", m, 0x5C, self._enc_count())
        return bytes(m[reg + i] if reg + i < REG_MAP_SIZE else 0xFF for i in range(length))

    def _write_setting(self, addr: int, data: bytes, field: str) -> int:
        t = dataclasses.replace(self.cfg)
        setattr(t, field, int.from_bytes(data, "little"))
        if sanitize(t):
            return RESULT_INVALID
        if t.microstep != self.cfg.microstep and not self._motion_idle():
            return RESULT_REJECTED
        if addr == 0x59 and not self._motion_idle():
            return RESULT_REJECTED
        if addr == 0x25 and ((t.flags ^ self.cfg.flags) & FLAG_AGC) and self.state == STATE_ENABLED:
            return RESULT_REJECTED
        if t != self.cfg:
            self.cfg = t
            self.cfg_status |= CFG_ST_DIRTY
            self._apply_settings()
        return RESULT_OK

    def _write_reg(self, addr: int, data: bytes, field: Optional[str]) -> int:
        if field is not None:
            return self._write_setting(addr, data, field)
        if addr == 0x50:
            return self.command(data[0])
        val = int.from_bytes(data[:4], "little", signed=True)
        if addr == 0x40:
            d = (val > self.position) - (val < self.position)
            if self.state != STATE_ENABLED or not self._move_allowed(d):
                return RESULT_REJECTED
            self._stepper_move_to(val)
            self.limit_stopped = False
            return RESULT_OK
        if addr == 0x44:
            d = (val > 0) - (val < 0)
            if self.state != STATE_ENABLED or not self._move_allowed(d):
                return RESULT_REJECTED
            self._stepper_move_by(val)
            self.limit_stopped = False
            return RESULT_OK
        if addr == 0x48:
            if val == 0:
                if self.state != STATE_ENABLED:
                    return RESULT_REJECTED
                self._stepper_run(0)
                self.home.abort()
                return RESULT_OK
            d = (val > 0) - (val < 0)
            if self.state != STATE_ENABLED or not self._move_allowed(d):
                return RESULT_REJECTED
            self._stepper_run(val)
            self.limit_stopped = False
            return RESULT_OK
        # 0x4C SET_POSITION
        if self.home.is_active():
            return RESULT_REJECTED
        return RESULT_OK if self._stepper_set_position(val) else RESULT_REJECTED

    def reg_write(self, reg: int, data: bytes) -> int:
        """完全に含まれるレジスタだけ反映する（32bit の部分書き込みは反映せず INVALID）。"""
        n = len(data)
        covered = 0
        result = RESULT_OK
        for a, size, field in _WRITABLE:
            if a >= reg and a + size <= reg + n:
                r = self._write_reg(a, data[a - reg: a - reg + size], field)
                if r != RESULT_OK:
                    result = r
                covered += size
        if covered != n:
            result = RESULT_INVALID
        self.cmd_result = result
        return result

    def broadcast(self, cmd: int) -> None:
        self.command(cmd)

    # ---- 名前表の値 ----

    def sym_values(self, usb_stats: Dict[str, int]) -> Dict[str, Any]:
        c = self.cfg
        h = self.home
        v: Dict[str, Any] = {
            "state": self.state, "fault": self.fault_bits, "status": self.status_snapshot,
            "cmd_result": self.cmd_result, "cfg_status": self.cfg_status, "isr_fault": self.isr_fault,
            "vm_ok": int(self.vm_ok), "hold_active": int(self.hold_active), "temp_warn": int(self.temp_warn),
            "lo_armed": int(self.lo_armed),
            "mon.vdda_mv": self.mon_vdda, "mon.vm_mv": self.mon_vm, "mon.board_temp_dc": self.mon_board_temp,
            "mon.mcu_temp_dc": self.mon_mcu_temp, "mon.ad_in_mv": self.mon_ad_in,
            "adc_raw.vrefint": self.adc["vrefint"], "adc_raw.vm": self.adc["vm"],
            "adc_raw.board_temp": self.adc["board_temp"], "adc_raw.ad_in": self.adc["ad_in"],
            "adc_raw.mcu_temp": self.adc["mcu_temp"],
            "cfg.run_current_ma": c.run_current_ma, "cfg.hold_current_ma": c.hold_current_ma,
            "cfg.microstep": c.microstep, "cfg.flags": c.flags, "cfg.comm_timeout_ms": c.comm_timeout_ms,
            "cfg.max_speed": c.max_speed, "cfg.accel": c.accel, "cfg.decel": c.decel,
            "cfg.start_speed": c.start_speed, "cfg.jog_speed": c.jog_speed, "cfg.hold_delay_ms": c.hold_delay_ms,
            "cfg.steps_per_rev": c.steps_per_rev, "cfg.gear_ratio": c.gear_ratio,
            "cfg.sensor_offset": c.sensor_offset, "cfg.pa7_mode": c.pa7_mode, "cfg.j10_mode": c.j10_mode,
            "_cfg.home_speed": c.home_speed,
            "sensor.raw": self.s_raw, "sensor.as_status": self.s_as_status, "sensor.fail_streak": self.s_fail,
            "_sensor.magnet": int(self.s_magnet), "sensor.z_level": int(self.z_level and self.pa7_applied == PA7_Z),
            "_sensor.next_try_ms": self.s_next_try & 0xFFFFFFFF, "sensor.i2c_ok": self.s_i2c_ok,
            "sensor.i2c_fail": self.s_i2c_fail, "pa7_applied": self.pa7_applied,
            "sensor_status": self.sensor_status_snapshot, "shaft_angle": self.shaft_angle_snapshot,
            "position": self.position, "cur_speed": self.cur_speed, "target_pos": self.target_pos,
            "target_vel": self.target_vel, "mode": self.mode, "running": int(self.running), "touched": int(self.touched),
            "dir": self.dir, "plan_speed": self.v, "commit_pos": self.position,
            "prm.max_speed": self.prm_max, "prm.accel": self.prm_accel, "prm.decel": self.prm_decel,
            "prm.start_speed": self.prm_start, "prm.invert_dir": int(self.prm_invert),
            "input_status": self.input_status_snapshot, "limit_stopped": int(self.limit_stopped),
            "agc_out": self.agc_snapshot, "enc_count": self.enc_snapshot,
            "home.state": h.state, "home.side": h.side, "home.seek_speed": h.seek_speed,
            "home.back_speed": h.back_speed, "home.limit_steps": h.limit_steps, "home.start_pos": h.start_pos,
            "home.opp_prev": h.opp_prev,
            "hm1.active": int(self.hm1.active), "hm1.cand": int(self.hm1.cand), "hm1.count": self.hm1.count,
            "hm2.active": int(self.hm2.active), "hm2.cand": int(self.hm2.cand), "hm2.count": self.hm2.count,
            "uwTick": self.now & 0xFFFFFFFF, "reset_csr": self.reset_csr,
            "loop.max_us": self.max_loop_us, "loop.max_1s_us": 180, "loop.rate_hz": 21500,
            "stack.used_max": 704, "stack.size": 12288,
        }
        for k in ("comm_stats.rx_frames", "comm_stats.rx_dropped", "comm_stats.tx_frames", "comm_stats.tx_dropped",
                  "comm_stats.err_events", "comm_stats.err_passive", "comm_stats.err_busoff",
                  "comm_stats.err_rx_overrun", "comm_stats.err_last_code"):
            v[k] = 0
        for k in ("esr", "tec", "rec", "tec_max", "rec_max", "ewg_count", "lec_count", "last_rx_id", "last_rx_dlc",
                  "last_rx_data"):
            v["comm_diag." + k] = 0
        v["comm_diag.init_stage"] = 4      # 稼働中（実機の comm_init 成功時に近い値）
        for k, val in usb_stats.items():
            v["usb_stats." + k] = val
        return v


# ---------------------------------------------------------------- USB 擬似デバイス

_KEEP: Any = object()      # set_home_switches の「変更しない」


class SimSerial:
    """serial 互換の擬似デバイス。`Link(SimSerial())` のように使う。

    clock       : 秒を返す時計（既定 time.monotonic）。偽の時計を渡すと read は実時間で待たない。
    board_id    : DIP スイッチ 0〜7。
    noise / seed: 温度・VM の揺らぎ（False で無効、seed で再現可能）。
    blank_flash : True なら保存設定が無い状態で始める（CONFIG_STATUS の LOADED=0、LOAD_SETTINGS は FAILED）。
    block       : read がデータを待つか。None なら clock が time.monotonic のときだけ待つ。
    timeout     : read の最大待ち [s]（pyserial の timeout 相当）。
    warmup_ms   : 生成直後に基板の時間を進めておく ms（電源投入後しばらく経った状態にする。CLI の --sim は 200）。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, board_id: int = 3, *, noise: bool = True,
                 seed: Optional[int] = None, blank_flash: bool = False, block: Optional[bool] = None,
                 timeout: float = 0.02, build_id: bytes = BUILD_ID, warmup_ms: int = 0) -> None:
        self._clock = clock
        self.timeout = timeout
        self._block = (clock is time.monotonic) if block is None else block
        self._build_id = build_id
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._board = _Board(board_id, noise, seed, blank_flash)
        self._last = clock()
        self._out = bytearray()
        self._unplugged = False
        self._gone = False                 # ENTER_BOOTLOADER 後（DFU デバイスに変わった）
        self.closed = False
        self.drop_responses = 0            # >0: 先頭からこの数だけ応答を捨てる（実行はする）。<0: 以後すべて捨てる
        self.requests: List[bytes] = []    # 受けた BODY（試験用）
        self.bootloader_entered = False
        self._session = False
        self._mta = 0
        self._ram = bytearray(RAM_SIZE)
        self._rx_state = 0                 # 0 SOF、1 LEN、2 DATA
        self._rx_frame = bytearray()
        self._rx_last_ms = 0
        for _ in range(warmup_ms):
            self._board.step_ms()
        self._stats = {"rx_frames": 0, "crc_errors": 0, "dropped": 0, "rx_overflow": 0, "rx_timeouts": 0,
                       "bad_len": 0, "tx_frames": 0, "tx_dropped": 0}

    # ---- 時間 ----

    def _advance(self) -> None:
        """前回から経過した時間ぶん、内部状態を 1 ms 刻みで進める。"""
        now = self._clock()
        elapsed = now - self._last
        if elapsed < 0:
            self._last = now
            return
        ms = int(elapsed * 1000 + 1e-6)
        if ms <= 0:
            return
        if ms > MAX_ADVANCE_S * 1000:
            ms = int(MAX_ADVANCE_S * 1000)
            self._last = now
        else:
            self._last += ms / 1000.0
        board = self._board
        for _ in range(ms):
            board.step_ms()
            if board.fault_events:
                self._flush_events()
        if self._rx_state != 0 and board.now - self._rx_last_ms >= RX_TIMEOUT_MS:
            self._rx_state = 0
            self._stats["rx_timeouts"] += 1
            self._stats["dropped"] += 1

    def _flush_events(self) -> None:
        events = self._board.fault_events
        self._board.fault_events = []
        if not self._session or self._unplugged or self._gone:
            return
        for state, fault in events:
            can = p.CanFrame(CAN_ID_FAULT_BASE + self._board.board_id, bytes([state, fault]))
            body = bytes([p.PID_EVENT, p.EVT_CAN_FRAME]) + p.encode_can(can)
            self._emit(body)

    def _emit(self, body: bytes) -> None:
        self._out += p.encode_frame(body)
        self._stats["tx_frames"] += 1
        self._cond.notify_all()

    def sync(self) -> None:
        """時計に追いつくまで内部状態を進める（試験・GUI から明示的に進めたいとき）。"""
        with self._lock:
            self._advance()

    @property
    def uptime_ms(self) -> int:
        with self._lock:
            self._advance()
            return self._board.now

    # ---- serial 互換 ----

    def _check_alive(self) -> None:
        if self._unplugged:
            raise SimUnplugged("シミュレータのケーブルが抜かれています")
        if self._gone:
            raise SimUnplugged("シミュレータは ROM ブートローダへ移行しました（COM ポートが消えています）")

    @property
    def in_waiting(self) -> int:
        with self._lock:
            if self._unplugged or (self._gone and not self._out):
                raise SimUnplugged("シミュレータのケーブルが抜かれています")
            self._advance()
            return len(self._out)

    def write(self, data: bytes) -> int:
        with self._lock:
            self._check_alive()
            self._advance()
            self._rx_feed(bytes(data))
            self._cond.notify_all()
            return len(data)

    def read(self, size: int = 1) -> bytes:
        with self._cond:
            if self._unplugged or (self._gone and not self._out):
                raise SimUnplugged("シミュレータのケーブルが抜かれています")
            self._advance()
            if not self._out and self._block and self.timeout > 0:
                end = time.monotonic() + self.timeout
                while not self._out:
                    rem = end - time.monotonic()
                    if rem <= 0:
                        break
                    self._cond.wait(min(rem, 0.005))
                    if self._unplugged or self._gone:
                        break
                    self._advance()
            if self._unplugged:
                raise SimUnplugged("シミュレータのケーブルが抜かれています")
            data = bytes(self._out[:size])
            del self._out[:size]
            return data

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    # ---- 注入 API ----

    def set_vm_mv(self, mv: int) -> None:
        with self._lock:
            self._advance()
            self._board.vm_mv_set = int(mv)

    def set_board_temp_dc(self, dc: Optional[int]) -> None:
        """基板温度 [0.1 ℃]。None は無効（NTC 断線、0x8000）。"""
        with self._lock:
            self._advance()
            self._board.board_temp_set = None if dc is None else int(dc)

    def set_board_temp_c(self, c: Optional[float]) -> None:
        self.set_board_temp_dc(None if c is None else int(round(c * 10)))

    def set_ad_in_mv(self, mv: int) -> None:
        with self._lock:
            self._advance()
            self._board.ad_in_mv_set = int(mv)

    def inject_fault(self, bits: int) -> None:
        """OPD(0x01)・ISD(0x02)・TSD(0x04) の LO 割り込み相当。パルスを止め、ENABLED/DISABLED なら FAULT へ。"""
        with self._lock:
            self._advance()
            self._board.inject_fault(bits)

    def set_home_switches(self, home1_pos: Any = _KEEP, home2_pos: Any = _KEEP) -> None:
        """HOME1（＋側、POSITION >= home1_pos で作動）・HOME2（−側、POSITION <= home2_pos で作動）の位置。None はスイッチ無し。"""
        with self._lock:
            self._advance()
            if home1_pos is not _KEEP:
                self._board.home1_pos = home1_pos
            if home2_pos is not _KEEP:
                self._board.home2_pos = home2_pos

    def set_home_travel_limit(self, steps: int) -> None:
        with self._lock:
            self._board.home_travel_limit = int(steps)

    def set_sensor_present(self, present: bool) -> None:
        with self._lock:
            self._advance()
            self._board.sensor_present = bool(present)
            if not present:
                self._board.s_magnet = False

    def set_shaft_offset(self, steps: int) -> None:
        """出力軸の機械位置 = POSITION + steps（SET_POSITION とセンサ角度のずれを作る）。"""
        with self._lock:
            self._advance()
            self._board.mech_offset = int(steps)

    def set_switches(self, sw1: bool, sw2: bool) -> None:
        with self._lock:
            self._advance()
            self._board.sw1, self._board.sw2 = bool(sw1), bool(sw2)

    def set_external_5v(self, on: bool) -> None:
        with self._lock:
            self._advance()
            self._board.ext_5v = bool(on)

    def set_save_fails(self, fails: bool) -> None:
        """True にすると SAVE_SETTINGS が FAILED になる（CONFIG_STATUS の SAVE_FAILED を試す）。"""
        with self._lock:
            self._board.save_fails = bool(fails)

    def unplug(self) -> None:
        """ケーブル抜去。以後 write / read が SimUnplugged（OSError）を投げる。CONNECT 状態は解除される。"""
        with self._cond:
            self._advance()
            self._unplugged = True
            self._reset_link()
            self._cond.notify_all()

    def replug(self) -> None:
        """ケーブルを挿し直す。CONNECT 状態は解除されており、最初の要求は ERR 0x35 になる。
        ROM ブートローダへ移行した後なら、電源再投入相当に基板を再起動する。"""
        with self._cond:
            if self._gone:
                self._reboot_locked()
            self._advance()
            self._unplugged = False
            self._reset_link()

    def reboot(self) -> None:
        """基板の電源再投入相当（保存設定と注入した外部条件は保つ）。"""
        with self._cond:
            self._reboot_locked()

    def _reboot_locked(self) -> None:
        self._board.reset(RESET_CSR_POWER_ON)
        self._last = self._clock()
        self._gone = False
        self._reset_link()
        self._stats = {k: 0 for k in self._stats}

    def _reset_link(self) -> None:
        self._session = False
        self._mta = 0
        self._out.clear()
        self._rx_state = 0
        self._board.fault_events = []

    # ---- 受信 ----

    def _rx_feed(self, data: bytes) -> None:
        board = self._board
        if self._rx_state != 0 and board.now - self._rx_last_ms >= RX_TIMEOUT_MS:
            self._rx_state = 0
            self._stats["rx_timeouts"] += 1
            self._stats["dropped"] += 1
        self._rx_last_ms = board.now
        for b in data:
            if self._rx_state == 0:
                if b == p.SOF:
                    self._rx_state = 1
            elif self._rx_state == 1:
                if p.LEN_MIN <= b <= p.LEN_MAX:
                    self._rx_frame = bytearray([b])
                    self._rx_state = 2
                else:
                    self._stats["bad_len"] += 1
                    self._rx_state = 1 if b == p.SOF else 0
            else:
                f = self._rx_frame
                f.append(b)
                n = f[0]
                if len(f) == n + 3:
                    self._rx_state = 0
                    if p.crc16(bytes(f[: n + 1])) == (f[n + 1] | (f[n + 2] << 8)):
                        self._stats["rx_frames"] += 1
                        self._handle_frame(bytes(f[1: n + 1]))
                    else:
                        self._stats["crc_errors"] += 1

    def _handle_frame(self, body: bytes) -> None:
        self.requests.append(body)
        err, data = self._exec(body)
        if err:
            data = b""
        rsp = bytes([p.PID_RESPONSE, err, body[1]]) + data
        if self.drop_responses != 0:
            if self.drop_responses > 0:
                self.drop_responses -= 1
            self._stats["tx_dropped"] += 1
            self._stats["dropped"] += 1
            return
        self._emit(rsp)

    # ---- コマンド ----

    def _refresh_ram(self) -> None:
        values = self._board.sym_values(self._stats)
        ram = self._ram
        for name, fmt, addr in _RAM_FIELDS:
            val = values[name]
            if fmt == "f":
                struct.pack_into("<f", ram, addr - RAM_BASE, float(val))
            elif len(fmt) > 1:     # 配列（"6I" など）。値 0 を全要素に入れる
                struct.pack_into("<" + fmt, ram, addr - RAM_BASE, *([val] * int(fmt[:-1])))
            else:
                struct.pack_into("<" + fmt, ram, addr - RAM_BASE, val)

    def _mem_allowed(self, addr: int, size: int) -> bool:
        if size == 0:
            return False
        for lo, hi in _MEM_REGIONS:
            if lo <= addr <= hi:
                return size <= hi - addr + 1
        return False

    def _mem_read(self, addr: int, size: int) -> bytes:
        self._refresh_ram()
        out = bytearray()
        for a in range(addr, addr + size):
            if RAM_BASE <= a < RAM_BASE + RAM_SIZE:
                out.append(self._ram[a - RAM_BASE])
            elif 0x10000000 <= a <= 0x10003FFF:
                out.append(self._ram[0xC000 + a - 0x10000000])
            elif 0x08000000 <= a <= 0x0803FFFF:
                out.append(0xFF)
            else:
                out.append((a * 7) & 0xFF)       # OTP・UID は適当な固定値
        return bytes(out)

    def _read_mem(self, size: int, addr: int) -> Tuple[int, bytes]:
        if size < 1 or size > p.UPLOAD_MAX:
            return 0x32, b""
        if not self._mem_allowed(addr, size):
            return 0x33, b""
        return 0, self._mem_read(addr, size)

    def _sym_lookup(self, req: bytes, base: int, tail: int) -> Tuple[int, int]:
        ln = len(req)
        if ln < base + 1:
            return 0x31, -1
        nlen = req[base]
        if ln != base + 1 + nlen + tail:
            return 0x31, -1
        if nlen < 1 or nlen > 31:
            return 0x32, -1
        try:
            name = req[base + 1: base + 1 + nlen].decode("ascii")
        except UnicodeDecodeError:
            return 0x36, -1
        i = SYM_INDEX.get(name)
        if i is None:
            return 0x36, -1
        return 0, i

    def _exec(self, req: bytes) -> Tuple[int, bytes]:
        cmd = req[0]
        ln = len(req)
        if cmd == p.CMD_GET_VERSION:
            return (0x31, b"") if ln != 2 else (0, bytes([PROTO_MAJOR, PROTO_MINOR]))
        if cmd == p.CMD_CONNECT:
            if ln != 2:
                return 0x31, b""
            self._session = True
            self._mta = 0
            return 0, bytes([PROTO_MAJOR, PROTO_MINOR, DEVICE_ID, FW_MAJOR, FW_MINOR, self._board.board_id, MAX_LEN])
        if not self._session:
            return 0x35, b""

        if cmd == p.CMD_DISCONNECT:
            if ln != 2:
                return 0x31, b""
            self._session = False
            return 0, b""
        if cmd == p.CMD_EXCHANGE_ID:
            if ln != 2:
                return 0x31, b""
            return 0, bytes([len(self._build_id)]) + self._build_id
        if cmd == p.CMD_SET_MTA:
            if ln != 6:
                return 0x31, b""
            self._mta = struct.unpack_from("<I", req, 2)[0]
            return 0, b""
        if cmd == p.CMD_UPLOAD:
            if ln != 3:
                return 0x31, b""
            size = req[2]
            err, data = self._read_mem(size, self._mta)
            if err == 0:
                self._mta = (self._mta + size) & 0xFFFFFFFF
            return err, data
        if cmd == p.CMD_SHORT_UP:
            if ln != 7:
                return 0x31, b""
            size, addr = struct.unpack_from("<BI", req, 2)
            return self._read_mem(size, addr)
        if cmd == p.CMD_SYM_COUNT:
            return (0x31, b"") if ln != 2 else (0, struct.pack("<H", len(SYMBOLS)))
        if cmd == p.CMD_SYM_INFO:
            if ln != 4:
                return 0x31, b""
            i = struct.unpack_from("<H", req, 2)[0]
            if i >= len(SYMBOLS):
                return 0x32, b""
            e = SYMBOLS[i]
            raw = e.name.encode("ascii")
            return 0, struct.pack("<IBHB", e.addr, e.type, e.num, len(raw)) + raw
        if cmd == p.CMD_SYM_FIND:
            err, i = self._sym_lookup(req, 2, 0)
            if err:
                return err, b""
            e = SYMBOLS[i]
            return 0, struct.pack("<HIBH", i, e.addr, e.type, e.num)
        if cmd == p.CMD_SYM_UPLOAD:
            err, i = self._sym_lookup(req, 2, 3)
            if err:
                return err, b""
            offset, size = struct.unpack_from("<HB", req, 3 + req[2])
            e = SYMBOLS[i]
            total = e.size
            if offset >= total:
                return 0x32, b""
            if size == 0:
                size = total - offset
            if size > p.UPLOAD_MAX or offset + size > total:
                return 0x32, b""
            return 0, self._mem_read(e.addr + offset, size)
        if cmd == p.CMD_CAN_REQUEST:
            return self._can_request(req)
        if cmd == p.CMD_ENTER_BOOTLOADER:
            if ln != 6:
                return 0x31, b""
            if struct.unpack_from("<I", req, 2)[0] != BOOT_KEY:
                return 0x32, b""
            if self._board.state == STATE_ENABLED:
                return 0x33, b""
            self.bootloader_entered = True
            self._gone = True          # ACK を返したあと、ポートは消える
            self._session = False
            return 0, b""
        return 0x30, b""

    def _can_request(self, req: bytes) -> Tuple[int, bytes]:
        if len(req) < 5:
            return 0x31, b""
        can_id, dlc = struct.unpack_from("<HB", req, 2)
        if can_id > 0x7FF or dlc > 8:
            return 0x32, b""
        if len(req) != 5 + dlc:
            return 0x31, b""
        data = req[5:]
        b = self._board
        if can_id == CAN_ID_BROADCAST:
            if dlc == 1 and data[0] in (CMD_DISABLE, CMD_STOP, CMD_ESTOP):
                b.broadcast(data[0])
            return 0, b"\x00"
        if can_id != CAN_ID_REQUEST_BASE + b.board_id:
            return 0, b"\x00"
        b.last_request_ms = b.now
        op = data[0] if dlc >= 1 else 0
        reg = data[1] if dlc >= 2 else 0
        if op == 0x01 and 3 <= dlc <= 8:
            rsp = bytes([0x81, reg, b.reg_write(reg, data[2:])])
        elif op == 0x02 and dlc == 3 and 1 <= data[2] <= 6:
            rsp = bytes([0x82, reg]) + b.reg_read(reg, data[2])
        else:
            rsp = bytes([0xFF, reg, RESULT_INVALID])
        return 0, b"\x01" + p.encode_can(p.CanFrame(CAN_ID_RESPONSE_BASE + b.board_id, rsp))
