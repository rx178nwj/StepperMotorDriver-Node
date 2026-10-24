"""値と履歴。Worker の Values を受けて最新値・履歴・カウンタの毎秒増加量・CSV 記録を持つ。Qt には依存しない。

スレッド安全ではない。UI スレッドだけから使うこと（Worker の out_q を UI が取り出して update() に渡す）。
"""

from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from . import items as itemsmod
from .items import Item


@dataclass
class Sample:
    raw: bytes
    value: Any            # decode 済み（無効は None）
    time: float


class Model:
    """history_s    : 履歴を残す秒数。max_points: 1 項目あたりの点数の上限。
    rate_window_s: カウンタの毎秒増加量を出す最小の間隔（短い間隔で更新されても、この時間ぶんためて割る）。
    """

    def __init__(self, items: Optional[Iterable[Item]] = None, history_s: float = 120.0, max_points: int = 20000,
                 rate_window_s: float = 1.0) -> None:
        self.history_s = history_s
        self.max_points = max_points
        self.rate_window_s = rate_window_s
        self._items: Dict[str, Item] = {}
        self._latest: Dict[str, Sample] = {}
        self._hist: Dict[str, Tuple[Deque[float], Deque[float]]] = {}
        self._rate_ref: Dict[str, Tuple[float, int]] = {}
        self._rates: Dict[str, float] = {}
        self._csv_file: Optional[IO[str]] = None
        self._csv_writer: Any = None
        self._csv_keys: List[str] = []
        self._csv_t0: Optional[float] = None
        self.reset(items)

    # ---- 状態 ----

    def reset(self, items: Optional[Iterable[Item]] = None) -> None:
        """接続ごとに呼ぶ。最新値・履歴・増加量を捨てる。CSV 記録は止めない（未取得の欄は空欄になる）。"""
        if items is not None:
            self._items = itemsmod.items_by_key(items)
        self._latest.clear()
        self._hist.clear()
        self._rate_ref.clear()
        self._rates.clear()

    @property
    def items(self) -> Dict[str, Item]:
        return self._items

    def update(self, timestamp: float, values: Dict[str, bytes]) -> None:
        """Values（時刻と {key: 生バイト列}）を取り込む。項目に無い key・短すぎるデータは無視する。"""
        for key, raw in values.items():
            item = self._items.get(key)
            if item is None:
                continue
            try:
                v = itemsmod.decode(item, raw)
            except ValueError:
                continue
            self._latest[key] = Sample(bytes(raw), v, timestamp)
            if item.numeric and v is not None:
                self._push(key, timestamp, float(v))
            if item.counter and v is not None:
                self._update_rate(item, timestamp, int(v))
        if self._csv_writer is not None:
            self._write_csv_row(timestamp)

    def _push(self, key: str, t: float, v: float) -> None:
        ts, vs = self._hist.setdefault(key, (deque(maxlen=self.max_points), deque(maxlen=self.max_points)))
        ts.append(t)
        vs.append(v)
        limit = t - self.history_s
        while ts and ts[0] < limit:
            ts.popleft()
            vs.popleft()

    def _update_rate(self, item: Item, t: float, v: int) -> None:
        ref = self._rate_ref.get(item.key)
        if ref is None:
            self._rate_ref[item.key] = (t, v)
            return
        dt = t - ref[0]
        if dt < self.rate_window_s:
            return
        mod = 1 << (8 * item.nbytes)
        self._rates[item.key] = ((v - ref[1]) % mod) / dt     # ラップ（2^32 など）を考慮
        self._rate_ref[item.key] = (t, v)

    # ---- 参照 ----

    def sample(self, key: str) -> Optional[Sample]:
        return self._latest.get(key)

    def value(self, key: str) -> Any:
        s = self._latest.get(key)
        return None if s is None else s.value

    def raw(self, key: str) -> Optional[bytes]:
        s = self._latest.get(key)
        return None if s is None else s.raw

    def text(self, key: str) -> str:
        """表示用の文字列。未取得は「—」、無効値は「無効」。"""
        s = self._latest.get(key)
        if s is None:
            return "—"
        return itemsmod.format_value(self._items[key], s.value)

    def history(self, key: str) -> Tuple[List[float], List[float]]:
        """(時刻の列, 値の列)。時刻は Values の timestamp。数値項目で、無効値の点は含まない。"""
        h = self._hist.get(key)
        if h is None:
            return [], []
        return list(h[0]), list(h[1])

    def rate(self, key: str) -> Optional[float]:
        """カウンタ項目の毎秒の増加量。まだ rate_window_s ぶんたまっていなければ None。"""
        return self._rates.get(key)

    # ---- CSV ----

    @property
    def csv_active(self) -> bool:
        return self._csv_writer is not None

    def start_csv(self, path: str, keys: Sequence[str]) -> None:
        """記録を始める。UTF-8 BOM 付き（Excel 用）。ヘッダは表示名と単位。Values を取り込むたびに 1 行書く。"""
        self.stop_csv()
        keys = [k for k in keys if k in self._items]
        f = open(path, "w", newline="", encoding="utf-8-sig")
        w = csv.writer(f)
        head = ["時刻", "経過[s]"]
        for k in keys:
            it = self._items[k]
            head.append(f"{it.label}[{it.unit}]" if it.unit else it.label)
        w.writerow(head)
        self._csv_file, self._csv_writer, self._csv_keys, self._csv_t0 = f, w, keys, None

    def stop_csv(self) -> None:
        if self._csv_file is not None:
            self._csv_file.close()
        self._csv_file = None
        self._csv_writer = None
        self._csv_keys = []

    def _write_csv_row(self, t: float) -> None:
        if self._csv_t0 is None:
            self._csv_t0 = t
        row: List[Any] = [datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], f"{t - self._csv_t0:.3f}"]
        for k in self._csv_keys:
            s = self._latest.get(k)
            if s is None or s.value is None:
                row.append("")
                continue
            it = self._items[k]
            v = s.value
            if it.ctype == "bytes":
                v = bytes(v).hex(" ")
            elif isinstance(v, float):
                d = itemsmod.display_decimals(it)
                v = round(v, d) if d >= 0 else f"{v:.7g}"
            row.append(v)
        self._csv_writer.writerow(row)
        self._csv_file.flush()   # type: ignore[union-attr]
