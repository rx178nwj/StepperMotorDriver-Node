"""グラフの判断ロジック。Qt にも pyqtgraph にも依存しない。

系列のプロットへの振り分け（単位ごと＋状態）、プリセットの解決、相対時刻への変換と時間窓の切り出し、
階段状データの生成、カーソル位置の値、記録ファイル名など。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .items import Item

GRAPH_POLL = "graph"
GRAPH_PERIOD_S = 0.05
GRAPH_PERIOD_MIN_S = 0.02
GRAPH_PERIOD_MAX_S = 1.0

WINDOWS_S: Tuple[int, ...] = (10, 30, 60, 120)
DEFAULT_WINDOW_S = 30
STATE_LABEL = "状態"

# (名前, key の並び)。存在しない key は resolve_preset が黙って除く
PRESETS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("運動", ("reg.POSITION", "reg.SPEED", "sym.cur_speed", "sym.plan_speed", "sym.target_pos")),
    ("速度プロファイル", ("sym.cur_speed", "sym.plan_speed", "sym.target_vel")),
    ("電源・温度", ("reg.VM_MV", "reg.BOARD_TEMP", "reg.MCU_TEMP")),
    ("原点復帰", ("reg.POSITION", "sym.home.state", "sym.input_status", "sym.hm1.active", "sym.hm2.active")),
    ("ループ・診断", ("sym.loop.max_1s_us", "sym.loop.rate_hz", "sym.stack.used_max")),
)

PALETTE: Tuple[str, ...] = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                            "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f")


def clamp_period(period_s: float) -> float:
    return min(GRAPH_PERIOD_MAX_S, max(GRAPH_PERIOD_MIN_S, float(period_s)))


def preset_names() -> List[str]:
    return [n for n, _k in PRESETS]


def resolve_preset(name: str, valid_keys: Iterable[str]) -> List[str]:
    """プリセットの key の並び。valid_keys に無いもの（名前表に無い項目など）は除く。未知の名前は空。"""
    valid = set(valid_keys)
    for n, keys in PRESETS:
        if n == name:
            return [k for k in keys if k in valid]
    return []


def numeric_items(items: Iterable[Item]) -> List[Item]:
    """グラフにできる項目（数値のみ。bytes は除く）。"""
    return [i for i in items if i.numeric]


def candidate_order(items: Iterable[Item], watch_keys: Sequence[str]) -> List[Item]:
    """候補の並び。ウォッチ中の項目（ウォッチの順）、続いて残りの数値項目（key 順）。"""
    num = {i.key: i for i in numeric_items(items)}
    out = [num[k] for k in watch_keys if k in num]
    seen = {i.key for i in out}
    out += sorted((i for i in num.values() if i.key not in seen), key=lambda i: i.key)
    return out


def matches_filter(item: Item, text: str) -> bool:
    """検索フィルタ。空白区切りの語がすべて、表示名・key・単位のどれかに含まれる（大文字小文字は区別しない）。"""
    hay = f"{item.label} {item.key} {item.unit}".lower()
    return all(w in hay for w in text.lower().split())


@dataclass(frozen=True)
class PlotSpec:
    unit: str                  # 空なら状態プロット
    keys: Tuple[str, ...]

    @property
    def is_state(self) -> bool:
        return self.unit == ""

    @property
    def title(self) -> str:
        return self.unit if self.unit else STATE_LABEL


def plot_layout(items: Dict[str, Item], keys: Sequence[str]) -> List[PlotSpec]:
    """選択中の key を、単位ごとのプロットに振り分ける。

    単位のあるものは単位ごと（初出順）に 1 プロット、単位なし（状態・列挙・ビット値）は最後の「状態」プロット 1 つ。
    items に無い key・bytes は除く。
    """
    by_unit: Dict[str, List[str]] = {}
    for k in keys:
        it = items.get(k)
        if it is None or not it.numeric:
            continue
        lst = by_unit.setdefault(it.unit, [])
        if k not in lst:
            lst.append(k)
    out = [PlotSpec(u, tuple(ks)) for u, ks in by_unit.items() if u]
    if "" in by_unit:
        out.append(PlotSpec("", tuple(by_unit[""])))
    return out


def series_color(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


def window_slice(ts: Sequence[float], vs: Sequence[float], now: float, window_s: float,
                 start: Optional[float] = None) -> Tuple[List[float], List[float]]:
    """(時刻列, 値列) を、now を 0 とする相対秒（過去は負）にして、直近 window_s 秒だけ切り出す。

    窓の左端の直前の 1 点も含める（線が左端まで届くように）。start（クリア時刻など）より前の点は含めない。
    """
    lo = now - window_s
    clamped = start is not None and start > lo
    if clamped:
        lo = start   # type: ignore[assignment]
    i = bisect_left(ts, lo)
    if not clamped and i > 0:
        i -= 1
    hi = bisect_right(ts, now)
    return [t - now for t in ts[i:hi]], list(vs[i:hi])


def step_xy(xs: Sequence[float], ys: Sequence[float], x_end: Optional[float] = None) -> Tuple[List[float], List[float]]:
    """階段状（値が次の点まで保持される）の折れ線にする。x_end を指定すると最後の値をそこまで延ばす。"""
    ox: List[float] = []
    oy: List[float] = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        if i > 0:
            ox.append(x)
            oy.append(ys[i - 1])
        ox.append(x)
        oy.append(y)
    if x_end is not None and ox and x_end > ox[-1]:
        ox.append(x_end)
        oy.append(oy[-1])
    return ox, oy


def value_at(xs: Sequence[float], ys: Sequence[float], x: float) -> Optional[float]:
    """時刻 x 以前で最も新しい点の値（保持値）。x より前に点が無ければ None。"""
    i = bisect_right(xs, x)
    return ys[i - 1] if i > 0 else None


def default_csv_name(now: datetime) -> str:
    return now.strftime("nemagui_%Y%m%d_%H%M%S.csv")


def default_png_name(now: datetime) -> str:
    return now.strftime("nemagui_%Y%m%d_%H%M%S.png")


def format_elapsed(sec: float) -> str:
    s = int(max(0.0, sec))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"
