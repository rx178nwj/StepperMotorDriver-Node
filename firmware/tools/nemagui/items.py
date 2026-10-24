"""デバッグ項目（Item）の定義と、生バイト列⇄値の変換。Qt には依存しない。

レジスタ項目は nemadbg.registers.REGISTERS から作り、アドレス・サイズ・符号・ビット名・列挙は重複して持たない。
ここで足すのは表示名・単位・倍率・無効値・範囲といった装飾だけ。
名前表の項目は usb_symbols.md の名前に合わせた静的定義で、未定義の名前は build_items() が "other" に足す。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from nemadbg import registers as regs
from nemadbg.link import SymInfo

GROUPS: Tuple[str, ...] = ("overview", "settings", "motion", "state", "monitor", "sensor", "j10", "comm", "diag", "other")
GROUP_LABELS: Dict[str, str] = {
    "overview": "状態概要",
    "settings": "設定",
    "motion": "モーション内部",
    "state": "状態遷移・異常",
    "monitor": "監視・ADC",
    "sensor": "位置センサ",
    "j10": "J10・原点復帰",
    "comm": "通信統計",
    "diag": "診断",
    "other": "その他",
}

MODE_NAMES = {0: "IDLE", 1: "POS", 2: "VEL"}
HOME_STATE_NAMES = {0: "IDLE", 1: "SEEK", 2: "SEEK_STOP", 3: "BACK", 4: "BACK_STOP", 5: "DONE", 6: "FAILED"}
RESET_CSR_BITS = ((24, "FWRSTF"), (25, "OBLRSTF"), (26, "PINRSTF"), (27, "BORRSTF"),
                  (28, "SFTRSTF"), (29, "IWDGRSTF"), (30, "WWDGRSTF"), (31, "LPWRRSTF"))

_CTYPE_INFO: Dict[str, Tuple[int, bool]] = {   # ctype -> (サイズ, 符号付き)
    "u8": (1, False), "i8": (1, True), "u16": (2, False), "i16": (2, True), "u32": (4, False), "i32": (4, True),
}
_TYPE_TO_CTYPE = {0x01: "u8", 0x02: "i8", 0x03: "u16", 0x04: "i16", 0x05: "u32", 0x06: "i32", 0x07: "f32", 0x10: "bytes"}


@dataclass(frozen=True, eq=False)
class Item:
    """1 つの表示・読み出し単位。eq=False なので同一性で比較・ハッシュされる（辞書のキーにしてよい）。"""

    key: str                                   # 一意。"reg.POSITION" / "sym.position"
    label: str                                 # 日本語の短い表示名
    group: str
    source: str                                # "reg" / "sym"
    ctype: str                                 # u8 i8 u16 i16 u32 i32 f32 bytes
    reg: Optional[regs.Reg] = None
    sym: Optional[str] = None
    size: int = 0                              # バイト数（0 なら ctype から決まる。bytes は必ず指定）
    scale: float = 1.0
    unit: str = ""
    bits: Tuple[Tuple[int, str], ...] = ()
    enum: Optional[Dict[int, str]] = None
    invalid: Optional[int] = None              # 無効を表す生の整数値（符号は ctype に従う。例 i16 の 0x8000 は -32768）
    counter: bool = False                      # 累積カウンタ（Model.rate が毎秒の増加量を出す）
    atomic: bool = False                       # True なら SYM_UPLOAD で読む（SHORT_UPLOAD は値が割れうる）
    writable: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Tuple[int, ...] = ()              # 許される値が離散のとき（MICROSTEP）

    @property
    def nbytes(self) -> int:
        if self.size:
            return self.size
        if self.ctype == "f32":
            return 4
        return _CTYPE_INFO.get(self.ctype, (1, False))[0]

    @property
    def numeric(self) -> bool:
        return self.ctype != "bytes"


# ---------------------------------------------------------------- 静的定義

_REG = {r.name: r for r in regs.REGISTERS}


def _ctype_of_reg(r: regs.Reg) -> str:
    return ("i" if r.signed else "u") + str(r.size * 8)


# (レジスタ名, 表示名, 単位, 追加指定)
_OVERVIEW_REGS: List[Tuple[str, str, str, Dict[str, Any]]] = [
    ("STATE", "状態", "", {}),
    ("FAULT", "異常要因", "", {}),
    ("STATUS", "ステータス", "", {}),
    ("CMD_RESULT", "直前の結果", "", {}),
    ("POSITION", "位置", "step", {}),
    ("SPEED", "速度", "step/s", {}),
    ("VM_MV", "モータ電圧", "mV", {}),
    ("BOARD_TEMP", "基板温度", "°C", {"scale": 0.1, "invalid": -32768}),
    ("MCU_TEMP", "MCU 温度", "°C", {"scale": 0.1}),
    ("AD_IN_MV", "AD 入力", "mV", {"invalid": 0xFFFF}),
    ("CONFIG_STATUS", "設定状態", "", {}),
    ("SENSOR_STATUS", "センサ状態", "", {}),
    ("SENSOR_RAW", "センサ生値", "", {"invalid": 0xFFFF}),
    ("SHAFT_ANGLE", "軸角度", "°", {"scale": 0.01, "invalid": 0xFFFF}),
    ("INPUT_STATUS", "入力状態", "", {}),
    ("ENC_COUNT", "エンコーダ", "count", {}),
]

# (レジスタ名, 表示名, 単位, 最小, 最大, 離散値)。範囲は usb_interface.md 8.5 章
_SETTING_REGS: List[Tuple[str, str, str, int, int, Tuple[int, ...]]] = [
    ("RUN_CURRENT", "運転電流", "mA", 250, 3000, ()),
    ("HOLD_CURRENT", "保持電流", "mA", 250, 3000, ()),
    ("MICROSTEP", "分割", "", 1, 32, (1, 2, 4, 8, 16, 32)),
    ("FLAGS", "フラグ", "", 0, 0x1F, ()),
    ("COMM_TIMEOUT", "通信タイムアウト", "ms", 0, 0xFFFF, ()),
    ("MAX_SPEED", "最高速度", "step/s", 1, 100000, ()),
    ("ACCEL", "加速度", "step/s²", 1, 10_000_000, ()),
    ("DECEL", "減速度", "step/s²", 1, 10_000_000, ()),
    ("START_SPEED", "開始速度", "step/s", 1, 100000, ()),
    ("JOG_SPEED", "ジョグ速度", "step/s", 1, 100000, ()),
    ("HOLD_DELAY", "保持移行時間", "ms", 0, 0xFFFF, ()),
    ("STEPS_PER_REV", "1 回転のステップ数", "step", 1, 0xFFFF, ()),
    ("GEAR_RATIO", "ギヤ比", "", 1, 1000, ()),
    ("SENSOR_OFFSET", "センサ原点オフセット", "", 0, 4095, ()),
    ("PA7_MODE", "PA7 の機能", "", 0, 2, ()),
    ("J10_MODE", "J10 の機能", "", 0, 2, ()),
    ("HOME_SPEED", "原点復帰速度", "step/s", 1, 0xFFFF, ()),
]

_FAULT_BITS = _REG["FAULT"].bits
_STATUS_BITS = _REG["STATUS"].bits
_CFG_STATUS_BITS = _REG["CONFIG_STATUS"].bits
_SENSOR_STATUS_BITS = _REG["SENSOR_STATUS"].bits
_INPUT_STATUS_BITS = _REG["INPUT_STATUS"].bits

# 速く変わる 2 B 超の数値。SHORT_UPLOAD はバイト単位のコピーで値が割れうるため、SYM_UPLOAD（8 B 以下は
# 割り込み禁止でコピー。usb_proto.c の cmd_sym_upload）で読む
_ATOMIC_NAMES = {"position", "cur_speed", "target_pos", "target_vel", "plan_speed", "commit_pos", "uwTick",
                 "enc_count", "home.start_pos"}

# 名前表の項目: (グループ, 名前, ctype, 表示名, 単位, 追加指定)
_T = Tuple[str, str, str, str, str, Dict[str, Any]]
_SYM_DEFS: List[_T] = [
    # 状態遷移・異常
    ("state", "state", "u8", "状態", "", {"enum": regs.STATE_NAMES}),
    ("state", "fault", "u8", "異常要因", "", {"bits": _FAULT_BITS}),
    ("state", "status", "u8", "ステータス", "", {"bits": _STATUS_BITS}),
    ("state", "cmd_result", "u8", "直前の結果", "", {"enum": regs.CMD_RESULT_NAMES}),
    ("state", "cfg_status", "u8", "設定状態", "", {"bits": _CFG_STATUS_BITS}),
    ("state", "isr_fault", "u8", "割り込みで検出した異常", "", {"bits": _FAULT_BITS}),
    ("state", "vm_ok", "u8", "VM 正常", "", {}),
    ("state", "temp_warn", "u8", "基板過熱警告", "", {}),
    ("state", "lo_armed", "u8", "LO 異常検出有効", "", {}),
    # モーション内部
    ("motion", "position", "i32", "現在位置", "step", {}),
    ("motion", "cur_speed", "f32", "現在速度", "step/s", {}),
    ("motion", "target_pos", "i32", "目標位置", "step", {}),
    ("motion", "target_vel", "f32", "目標速度", "step/s", {}),
    ("motion", "mode", "u8", "動作モード", "", {"enum": MODE_NAMES}),
    ("motion", "running", "u8", "パルス出力中", "", {}),
    ("motion", "touched", "u8", "位置が確定済み", "", {}),
    ("motion", "dir", "i8", "移動方向", "", {}),
    ("motion", "plan_speed", "f32", "プランナ速度", "step/s", {}),
    ("motion", "commit_pos", "i32", "確定済み先行位置", "step", {}),
    ("motion", "prm.max_speed", "f32", "最高速度(適用値)", "step/s", {}),
    ("motion", "prm.accel", "f32", "加速度(適用値)", "step/s²", {}),
    ("motion", "prm.decel", "f32", "減速度(適用値)", "step/s²", {}),
    ("motion", "prm.start_speed", "f32", "開始速度(適用値)", "step/s", {}),
    ("motion", "prm.invert_dir", "u8", "方向反転(適用値)", "", {}),
    ("motion", "hold_active", "u8", "保持電流中", "", {}),
    # 監視・ADC
    ("monitor", "mon.vdda_mv", "u32", "VDDA", "mV", {}),
    ("monitor", "mon.vm_mv", "u32", "VM", "mV", {}),
    ("monitor", "mon.board_temp_dc", "i16", "基板温度", "°C", {"scale": 0.1, "invalid": -32768}),
    ("monitor", "mon.mcu_temp_dc", "i16", "MCU 温度", "°C", {"scale": 0.1}),
    ("monitor", "mon.ad_in_mv", "u32", "AD 入力", "mV", {"invalid": 0xFFFF}),
    ("monitor", "adc_raw.vrefint", "u16", "ADC 生値 VREFINT", "", {}),
    ("monitor", "adc_raw.vm", "u16", "ADC 生値 VM", "", {}),
    ("monitor", "adc_raw.board_temp", "u16", "ADC 生値 基板温度", "", {}),
    ("monitor", "adc_raw.ad_in", "u16", "ADC 生値 AD 入力", "", {}),
    ("monitor", "adc_raw.mcu_temp", "u16", "ADC 生値 MCU 温度", "", {}),
    # 位置センサ
    ("sensor", "sensor.raw", "u16", "センサ RAW ANGLE", "", {}),
    ("sensor", "sensor.as_status", "u8", "AS5600 STATUS", "", {}),
    ("sensor", "sensor.fail_streak", "u8", "連続失敗回数", "", {}),
    ("sensor", "sensor.z_level", "u8", "Z 入力レベル", "", {}),
    ("sensor", "sensor.i2c_ok", "u32", "I2C 成功回数", "", {"counter": True}),
    ("sensor", "sensor.i2c_fail", "u32", "I2C 失敗回数", "", {"counter": True}),
    ("sensor", "pa7_applied", "u8", "PA7 の適用機能", "", {"enum": regs.PA7_MODE_NAMES}),
    ("sensor", "sensor_status", "u8", "センサ状態", "", {"bits": _SENSOR_STATUS_BITS}),
    ("sensor", "shaft_angle", "u16", "軸角度", "°", {"scale": 0.01, "invalid": 0xFFFF}),
    # J10・原点復帰
    ("j10", "input_status", "u8", "入力状態", "", {"bits": _INPUT_STATUS_BITS}),
    ("j10", "enc_count", "i32", "エンコーダ", "count", {}),
    ("j10", "agc_out", "u8", "AGC 出力", "", {}),
    ("j10", "limit_stopped", "u8", "リミット停止", "", {}),
    ("j10", "home.state", "u8", "原点復帰状態", "", {"enum": HOME_STATE_NAMES}),
    ("j10", "home.side", "i8", "原点復帰の側", "", {}),
    ("j10", "home.seek_speed", "i32", "探索速度", "step/s", {}),
    ("j10", "home.back_speed", "i32", "戻り速度", "step/s", {}),
    ("j10", "home.limit_steps", "u32", "戻り上限", "step", {}),
    ("j10", "home.start_pos", "i32", "戻り開始位置", "step", {}),
    ("j10", "home.opp_prev", "u8", "反対側の前回入力", "", {}),
    ("j10", "hm1.active", "u8", "HOME1 確定", "", {}),
    ("j10", "hm1.cand", "u8", "HOME1 サンプル", "", {}),
    ("j10", "hm1.count", "u8", "HOME1 連続回数", "", {}),
    ("j10", "hm2.active", "u8", "HOME2 確定", "", {}),
    ("j10", "hm2.cand", "u8", "HOME2 サンプル", "", {}),
    ("j10", "hm2.count", "u8", "HOME2 連続回数", "", {}),
    # 通信統計（累積カウンタ）
    ("comm", "comm_stats.rx_frames", "u32", "CAN 受信フレーム", "", {"counter": True}),
    ("comm", "comm_stats.rx_dropped", "u32", "CAN 受信あふれ", "", {"counter": True}),
    ("comm", "comm_stats.tx_frames", "u32", "CAN 送信フレーム", "", {"counter": True}),
    ("comm", "comm_stats.tx_dropped", "u32", "CAN 送信破棄", "", {"counter": True}),
    ("comm", "comm_stats.err_events", "u32", "CAN エラー割り込み", "", {"counter": True}),
    ("comm", "comm_stats.err_passive", "u32", "CAN エラーパッシブ", "", {"counter": True}),
    ("comm", "comm_stats.err_busoff", "u32", "CAN バスオフ", "", {"counter": True}),
    ("comm", "comm_stats.err_rx_overrun", "u32", "CAN 受信オーバーラン", "", {"counter": True}),
    ("comm", "comm_stats.err_last_code", "u32", "CAN 直近エラーコード", "", {}),
    ("comm", "usb_stats.rx_frames", "u32", "USB 受信フレーム", "", {"counter": True}),
    ("comm", "usb_stats.crc_errors", "u32", "USB CRC エラー", "", {"counter": True}),
    ("comm", "usb_stats.dropped", "u32", "USB 破棄", "", {"counter": True}),
    ("comm", "usb_stats.rx_overflow", "u32", "USB 受信あふれ", "B", {"counter": True}),
    ("comm", "usb_stats.rx_timeouts", "u32", "USB 受信途切れ", "", {"counter": True}),
    ("comm", "usb_stats.bad_len", "u32", "USB LEN 不正", "", {"counter": True}),
    ("comm", "usb_stats.tx_frames", "u32", "USB 送信フレーム", "", {"counter": True}),
    ("comm", "usb_stats.tx_dropped", "u32", "USB 送信破棄", "", {"counter": True}),
    # 診断
    ("diag", "uwTick", "u32", "稼働時間", "ms", {}),
    ("diag", "reset_csr", "u32", "リセット要因", "", {"bits": RESET_CSR_BITS}),
    ("diag", "loop.max_us", "u32", "ループ最大周期", "µs", {}),
    ("diag", "loop.max_1s_us", "u32", "ループ最大周期(直近1秒)", "µs", {}),
    ("diag", "loop.rate_hz", "u32", "ループ回数(直近1秒)", "Hz", {}),
    ("diag", "stack.used_max", "u32", "スタック使用最大", "B", {}),
    ("diag", "stack.size", "u32", "スタック領域", "B", {}),
]


def _reg_items() -> List[Item]:
    out: List[Item] = []
    for name, label, unit, extra in _OVERVIEW_REGS:
        r = _REG[name]
        out.append(Item(key="reg." + name, label=label, group="overview", source="reg", ctype=_ctype_of_reg(r),
                        reg=r, size=r.size, unit=unit, bits=r.bits, enum=r.enum, **extra))
    for name, label, unit, lo, hi, choices in _SETTING_REGS:
        r = _REG[name]
        out.append(Item(key="reg." + name, label=label, group="settings", source="reg", ctype=_ctype_of_reg(r),
                        reg=r, size=r.size, unit=unit, bits=r.bits, enum=r.enum, writable=r.access == "RW",
                        min=lo, max=hi, choices=choices))
    return out


def _sym_items() -> List[Item]:
    out: List[Item] = []
    for group, name, ctype, label, unit, extra in _SYM_DEFS:
        out.append(Item(key="sym." + name, label=label, group=group, source="sym", ctype=ctype, sym=name,
                        unit=unit, atomic=name in _ATOMIC_NAMES, **extra))
    return out


_STATIC_ITEMS: List[Item] = _reg_items() + _sym_items()
STATIC_SYM_NAMES = frozenset(i.sym for i in _STATIC_ITEMS if i.sym)


def build_items(sym_infos: "Optional[Sequence[SymInfo]]" = None) -> List[Item]:
    """静的定義の全項目と、名前表にあって未定義の名前（"other" グループ）を返す。

    返す順序はグループ表示順（GROUPS）で、同じグループ内は定義順（other は名前表の順）。
    """
    others: List[Item] = []
    for info in sym_infos or ():
        if info.name in STATIC_SYM_NAMES:
            continue
        ctype = _TYPE_TO_CTYPE.get(info.type, "bytes")
        if info.num != 1:
            ctype = "bytes"       # 配列と構造体全体は生のバイト列として扱う
        others.append(Item(key="sym." + info.name, label=info.name, group="other", source="sym", ctype=ctype,
                           sym=info.name, size=info.size if ctype == "bytes" else 0))
    order = {g: n for n, g in enumerate(GROUPS)}
    return sorted(_STATIC_ITEMS + others, key=lambda i: order[i.group])   # sorted は安定


def items_by_key(items: Iterable[Item]) -> Dict[str, Item]:
    return {i.key: i for i in items}


# ---------------------------------------------------------------- 変換

def _int_range(ctype: str) -> Tuple[int, int]:
    size, signed = _CTYPE_INFO[ctype]
    if signed:
        return -(1 << (size * 8 - 1)), (1 << (size * 8 - 1)) - 1
    return 0, (1 << (size * 8)) - 1


def decode(item: Item, raw: bytes) -> Any:
    """生バイト列を値にする。invalid に一致すれば None。倍率（scale）を適用する。bytes は bytes のまま。"""
    if item.ctype == "bytes":
        return bytes(raw)
    n = item.nbytes
    if len(raw) < n:
        raise ValueError(f"{item.key}: 生データが短すぎます（{len(raw)} < {n}）")
    if item.ctype == "f32":
        return struct.unpack_from("<f", raw, 0)[0]
    v = int.from_bytes(raw[:n], "little", signed=_CTYPE_INFO[item.ctype][1])
    if item.invalid is not None and v == item.invalid:
        return None
    return v * item.scale if item.scale != 1.0 else v


def display_decimals(item: Item) -> int:
    """倍率から決まる小数桁数（0.1 なら 1、0.01 なら 2）。整数なら 0。f32 は -1（有効数字で表示）。"""
    if item.ctype == "f32":
        return -1
    if item.scale == 1.0:
        return 0
    return max(0, int(round(-math.log10(item.scale))))


def format_value(item: Item, value: Any) -> str:
    """表示用の文字列。単位・列挙名・ビット名・無効表示を付ける。"""
    if value is None:
        return "無効"
    if item.ctype == "bytes":
        return bytes(value).hex(" ")
    if item.enum is not None:
        return f"{int(value)} ({item.enum.get(int(value), '不明')})"
    if item.bits:
        v = int(value)
        names = [n for b, n in item.bits if v & (1 << b)]
        return f"0x{v:0{item.nbytes * 2}X} [" + (", ".join(names) if names else "-") + "]"
    d = display_decimals(item)
    if d < 0:
        text = f"{value:.6g}"
    elif d == 0:
        text = str(int(value))
    else:
        text = f"{value:.{d}f}"
    return f"{text} {item.unit}" if item.unit else text


def _pack_int(ctype: str, v: int, what: str) -> bytes:
    lo, hi = _int_range(ctype)
    if not lo <= v <= hi:
        raise ValueError(f"{what}: {v} は {ctype} の範囲外です（{lo}〜{hi}）")
    return v.to_bytes(_CTYPE_INFO[ctype][0], "little", signed=lo < 0)


def _to_int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    raise ValueError(f"{what}: 整数ではありません: {value!r}")


def encode(item: Item, value: Any) -> bytes:
    """書き込み用に値を生バイト列にする（倍率の逆変換つき）。書き込めない項目・範囲外は ValueError。"""
    if not item.writable:
        raise ValueError(f"{item.key}: 書き込みできない項目です")
    if item.ctype == "bytes":
        b = bytes(value)
        if len(b) != item.nbytes:
            raise ValueError(f"{item.key}: {item.nbytes} バイトが必要です")
        return b
    if (item.min is not None and value < item.min) or (item.max is not None and value > item.max):
        raise ValueError(f"{item.label}: {value} は範囲外です（{item.min}〜{item.max} {item.unit}）")
    if item.ctype == "f32":
        return struct.pack("<f", float(value))
    if item.scale != 1.0:
        v = int(round(value / item.scale))
    else:
        v = _to_int(value, item.label)
    if item.choices and v not in item.choices:
        raise ValueError(f"{item.label}: {v} は指定できません（{', '.join(map(str, item.choices))} のいずれか）")
    return _pack_int(item.ctype, v, item.label)


def encode_reg(reg: regs.Reg, value: Any) -> bytes:
    """項目を持たないレジスタ（MOVE_ABS・COMMAND など）に書く値を生バイト列にする。範囲外は ValueError。"""
    if reg.access == "R":
        raise ValueError(f"{reg.name}: 読み出し専用のレジスタです")
    return _pack_int(("i" if reg.signed else "u") + str(reg.size * 8), _to_int(value, reg.name), reg.name)
