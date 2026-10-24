"""内部状態タブの要点パネル・名前表ブラウザ・ウォッチの表示ロジック。Qt には依存せず、単体で試験できる。

色は nemagui.viewstate の定数を使う。値の取得元は Model（value(key) / rate(key)）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from nemadbg import protocol as p

from . import items as itemsmod
from .items import Item
from .viewstate import GRAY, GREEN, ORANGE, RED

# ---------------------------------------------------------------- 稼働時間・リセット要因

def format_uptime(ms: Optional[int]) -> str:
    """uwTick [ms] を「N 日 hh:mm:ss.mmm」にする。"""
    if ms is None:
        return "—"
    ms = int(ms)
    days, rest = divmod(ms, 86_400_000)
    hours, rest = divmod(rest, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{days} 日 {hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


# 主要因の優先順（上ほど優先）。(RCC_CSR のビット名, 日本語)
#   - IWDG・WWDG・低消費電力は、立っていれば他に原因がない（PINRSTF が一緒に立つことはあるが原因ではない）。
#   - ソフトウェアリセット（ENTER_BOOTLOADER の NVIC_SystemReset など）は NRST ピンのフラグも立てる。
#   - 電源投入では BORRSTF が立ち、PINRSTF も一緒に立つ（0x0C000000）。BORRSTF はブラウンアウトでも立つので
#     「電源投入（POR/BOR）」とまとめる。
#   - オプションバイトのロードも PINRSTF を伴うため、PINRSTF より先に判定する。
#   - NRST ピンは他のフラグが何も無い（PINRSTF だけ）ときの要因。
RESET_CAUSES: Tuple[Tuple[str, str], ...] = (
    ("IWDGRSTF", "IWDG（独立ウォッチドッグ）"),
    ("WWDGRSTF", "WWDG（ウィンドウウォッチドッグ）"),
    ("LPWRRSTF", "低消費電力モード移行"),
    ("SFTRSTF", "ソフトウェア（ENTER_BOOTLOADER 等）"),
    ("FWRSTF", "ファイアウォール"),
    ("BORRSTF", "電源投入（POR/BOR）"),
    ("OBLRSTF", "オプションバイトのロード"),
    ("PINRSTF", "NRST ピン"),
)
_RESET_BIT = {name: bit for bit, name in itemsmod.RESET_CSR_BITS}


@dataclass(frozen=True)
class ResetInfo:
    flags: Tuple[str, ...]      # 立っているフラグ名（ビット番号の昇順）
    primary: str                # 主要因（日本語）。フラグが無ければ「不明（フラグなし）」


def decode_reset_csr(value: Optional[int]) -> Optional[ResetInfo]:
    if value is None:
        return None
    v = int(value)
    flags = tuple(n for b, n in itemsmod.RESET_CSR_BITS if v & (1 << b))
    primary = "不明（フラグなし）"
    for name, text in RESET_CAUSES:
        if v & (1 << _RESET_BIT[name]):
            primary = text
            break
    return ResetInfo(flags, primary)


# ---------------------------------------------------------------- スタック

STACK_WARN_PCT = 75.0
STACK_ERROR_PCT = 90.0


def stack_percent(used: Optional[int], size: Optional[int]) -> Optional[float]:
    if used is None or size is None or int(size) <= 0:
        return None
    return 100.0 * int(used) / int(size)


def stack_color(pct: Optional[float]) -> str:
    """75% 以上で橙、90% 以上で赤。それ未満は緑、不明は灰。"""
    if pct is None:
        return GRAY
    if pct >= STACK_ERROR_PCT:
        return RED
    if pct >= STACK_WARN_PCT:
        return ORANGE
    return GREEN


# ---------------------------------------------------------------- 通信統計

# 0 でなければ異常の疑いがあるカウンタ（sym 名）。err_last_code は値であってカウンタではないので含めない
ANOMALY_COUNTERS: Tuple[str, ...] = (
    "comm_stats.rx_dropped", "comm_stats.tx_dropped", "comm_stats.err_events", "comm_stats.err_passive",
    "comm_stats.err_busoff", "comm_stats.err_rx_overrun",
    "usb_stats.crc_errors", "usb_stats.dropped", "usb_stats.rx_overflow", "usb_stats.rx_timeouts",
    "usb_stats.bad_len", "usb_stats.tx_dropped",
)
COMM_CAN_KEYS: Tuple[str, ...] = tuple("sym." + n for n in (
    "comm_stats.rx_frames", "comm_stats.rx_dropped", "comm_stats.tx_frames", "comm_stats.tx_dropped",
    "comm_stats.err_events", "comm_stats.err_passive", "comm_stats.err_busoff", "comm_stats.err_rx_overrun",
    "comm_stats.err_last_code"))
COMM_USB_KEYS: Tuple[str, ...] = tuple("sym." + n for n in (
    "usb_stats.rx_frames", "usb_stats.crc_errors", "usb_stats.dropped", "usb_stats.rx_overflow",
    "usb_stats.rx_timeouts", "usb_stats.bad_len", "usb_stats.tx_frames", "usb_stats.tx_dropped"))
_ANOMALY_KEYS = frozenset("sym." + n for n in ANOMALY_COUNTERS)
LAST_CODE_KEY = "sym.comm_stats.err_last_code"


def is_anomaly_counter(key: str) -> bool:
    return key in _ANOMALY_KEYS


def anomaly_color(key: str, value: Optional[int], rate: Optional[float]) -> Optional[str]:
    """異常系カウンタの色。0 または異常系でなければ None（通常色）、0 でなければ橙、増加中なら赤。"""
    if key not in _ANOMALY_KEYS or value is None:
        return None
    if rate is not None and rate > 0:
        return RED
    if int(value) != 0:
        return ORANGE
    return None


def format_counter_value(key: str, value: Optional[int]) -> str:
    if value is None:
        return "—"
    if key == LAST_CODE_KEY:
        return f"0x{int(value):08X}"
    return str(int(value))


def format_rate(rate: Optional[float]) -> str:
    return "—" if rate is None else f"+{rate:.0f}/s"


# ---------------------------------------------------------------- モーション

DIR_ARROWS = {1: "→", -1: "←"}


def dir_arrow(direction: Optional[int]) -> str:
    return "—" if direction is None else DIR_ARROWS.get(int(direction), "・")


def remaining_steps(position: Optional[int], other: Optional[int]) -> Optional[int]:
    """other（target_pos / commit_pos）までの残り step（符号付き）。"""
    if position is None or other is None:
        return None
    return int(other) - int(position)


# ---------------------------------------------------------------- センサ

SENSOR_FAIL_LIMIT = 3          # sensor.fail_streak がこの値以上で位置センサ無効
AS_STATUS_BITS: Tuple[Tuple[int, str], ...] = ((3, "MH"), (4, "ML"), (5, "MD"))
AS_STATUS_REG = "AS_STATUS"    # BitLamps の色テーブル用の名前（viewstate._LAMP_COLORS 参照）


def fail_streak_text(streak: Optional[int]) -> Tuple[str, str]:
    """(文言, 色)。3 回以上の連続失敗で無効（赤）、1〜2 は橙。"""
    if streak is None:
        return "—", GRAY
    s = int(streak)
    if s >= SENSOR_FAIL_LIMIT:
        return f"{s}（無効）", RED
    return str(s), (ORANGE if s else GREEN)


# ---------------------------------------------------------------- J10・原点復帰

HOME_CONFIRM_SAMPLES = 2       # App/Inc/config.h の HOME_CONFIRM_SAMPLES（HOME 入力の確定に必要な連続一致回数）
HOME_STAGES: Tuple[str, ...] = ("IDLE", "SEEK", "SEEK_STOP", "BACK", "BACK_STOP", "DONE")
HOME_FAILED = 6
HOME_SIDE_TEXT = {1: "HOME1（HOME_POS）", -1: "HOME2（HOME_NEG）"}


def home_stage(state: Optional[int]) -> Tuple[Optional[int], bool]:
    """(HOME_STAGES 内の現在段階の添字, FAILED か)。FAILED は段階列に載らないので添字 None。"""
    if state is None:
        return None, False
    s = int(state)
    if s == HOME_FAILED:
        return None, True
    if 0 <= s < len(HOME_STAGES):
        return s, False
    return None, False


def home_side_text(side: Optional[int]) -> str:
    if side is None:
        return "—"
    return HOME_SIDE_TEXT.get(int(side), f"なし（{int(side)}）")


def debounce_progress(count: Optional[int]) -> Tuple[int, int]:
    """(連続回数（確定回数で頭打ち）, 確定に必要な回数)。"""
    n = 0 if count is None else min(int(count), HOME_CONFIRM_SAMPLES)
    return n, HOME_CONFIRM_SAMPLES


# ---------------------------------------------------------------- 名前表ブラウザ

def sym_type_name(type_code: int) -> str:
    return p.SYM_TYPES.get(type_code, ("?", 1, ""))[0]


def filter_names(names: Iterable[str], text: str) -> List[str]:
    """名前の部分一致（大文字小文字を区別しない）。空なら全件。"""
    t = text.strip().lower()
    return [n for n in names if t in n.lower()]


def hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hexes = " ".join(f"{b:02X}" for b in chunk)
        asc = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{off:04X}: {hexes:<{width * 3 - 1}}  {asc}")
    return "\n".join(lines)


def read_result_text(item: Item, raw: Optional[bytes]) -> str:
    """名前表ブラウザの読み出し結果。bytes 型は 16 進ダンプ、それ以外は値（と生の 16 進）。"""
    if raw is None:
        return f"{item.sym or item.key}: 読めませんでした"
    name = item.sym or item.key
    if item.ctype == "bytes":
        return f"{name} ({len(raw)} B)\n{hex_dump(raw)}"
    value = itemsmod.decode(item, raw)
    return f"{name} = {itemsmod.format_value(item, value)}   [{raw.hex(' ')}]"


# ---------------------------------------------------------------- ウォッチ

WATCH_POLL = "watch"
WATCH_PERIOD_S = 0.2
WATCH_PERIOD_MIN_S = 0.05
WATCH_PERIOD_MAX_S = 5.0


def encode_watch_keys(keys: Sequence[str]) -> str:
    """QSettings に保存する形式（JSON 文字列。1 要素のリストが文字列に化ける QSettings の癖を避ける）。"""
    return json.dumps(list(keys), ensure_ascii=False)


def decode_watch_keys(text: Any) -> List[str]:
    """保存した文字列を key の列に戻す。壊れていれば空。重複は除く。"""
    if not isinstance(text, str) or not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: List[str] = []
    if isinstance(data, list):
        for k in data:
            if isinstance(k, str) and k not in out:
                out.append(k)
    return out


def clamp_watch_period(seconds: Any) -> float:
    try:
        v = float(seconds)
    except (TypeError, ValueError):
        return WATCH_PERIOD_S
    return min(WATCH_PERIOD_MAX_S, max(WATCH_PERIOD_MIN_S, v))


class WatchList:
    """ウォッチする key の並び。接続前に復元した key のうち、まだ項目の無いものは保留し、項目が揃ったら取り込む。"""

    def __init__(self, items: Iterable[Item] = (), saved: Sequence[str] = ()) -> None:
        self._items = itemsmod.items_by_key(items)
        self._keys: List[str] = []
        self._pending: List[str] = []
        for k in saved:
            if k in self._items:
                self.add(k)
            elif k not in self._pending:
                self._pending.append(k)

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    @property
    def pending(self) -> List[str]:
        return list(self._pending)

    @property
    def items(self) -> List[Item]:
        return [self._items[k] for k in self._keys]

    @property
    def all_items(self) -> List[Item]:
        """ウォッチに追加できる項目（全項目）。"""
        return list(self._items.values())

    def item(self, key: str) -> Optional[Item]:
        return self._items.get(key)

    def add(self, key: str) -> bool:
        if key not in self._items or key in self._keys:
            return False
        self._keys.append(key)
        return True

    def remove(self, key: str) -> bool:
        if key in self._keys:
            self._keys.remove(key)
            return True
        return False

    def set_items(self, items: Iterable[Item]) -> bool:
        """接続時に項目を差し替える。保留の key は存在すれば取り込み、無ければ捨てる。
        現在の key のうち項目が無くなったものも外す。変化があれば True。"""
        self._items = itemsmod.items_by_key(items)
        before = list(self._keys)
        self._keys = [k for k in self._keys if k in self._items]
        pend, self._pending = self._pending, []
        for k in pend:
            if k in self._items:
                self.add(k)
        return self._keys != before

    def to_text(self) -> str:
        """保存形式。保留中の key も残す（接続するまで消さない）。"""
        return encode_watch_keys(self._keys + [k for k in self._pending if k not in self._keys])
