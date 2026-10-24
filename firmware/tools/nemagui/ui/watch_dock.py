"""ウォッチ。任意の項目（名前表・レジスタ）を集めて常時表示するドック。

ポーリング組 "watch" の登録は MainWindow が行う（keys が変わると changed、周期が変わると period_changed を出す）。
G5 のグラフは keys() / changed を系列の候補に使える。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDockWidget, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import summary as sm
from .. import viewstate as vs
from ..items import Item
from ..model import Model

HEADERS = ["表示名", "値", "単位", "毎秒", ""]
COL_LABEL, COL_VALUE, COL_UNIT, COL_RATE, COL_DELETE = range(5)


class WatchDock(QDockWidget):
    changed = Signal(list)          # 一覧が変わった（引数は key のリスト）。復元・接続時の取り込みでも出る
    period_changed = Signal(float)  # ポーリング周期 [s] が変わった

    def __init__(self, items: List[Item], saved_keys: List[str], period_s: float = sm.WATCH_PERIOD_S,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("ウォッチ", parent)
        self.setObjectName("watch_dock")
        self.watch = sm.WatchList(items, saved_keys)
        self._cells: List[List[str]] = []
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)

        add_row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(24)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.add_button = QPushButton("追加")
        self.add_button.clicked.connect(self._add_selected)
        add_row.addWidget(self.combo, 1)
        add_row.addWidget(self.add_button)
        lay.addLayout(add_row)

        per = QHBoxLayout()
        per.addWidget(QLabel("周期"))
        self.period_spin = QSpinBox()
        self.period_spin.setRange(int(sm.WATCH_PERIOD_MIN_S * 1000), int(sm.WATCH_PERIOD_MAX_S * 1000))
        self.period_spin.setSingleStep(50)
        self.period_spin.setSuffix(" ms")
        self.period_spin.setValue(int(round(sm.clamp_watch_period(period_s) * 1000)))
        self.period_spin.valueChanged.connect(lambda _v: self.period_changed.emit(self.period_s))
        per.addWidget(self.period_spin)
        per.addStretch(1)
        lay.addLayout(per)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(COL_VALUE, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.setWidget(body)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance("0" * 54))
        self._fill_combo()
        self._rebuild()

    # ---- 公開 API（G5 のグラフから使う） ----

    def keys(self) -> List[str]:
        """ウォッチ中の項目の key（追加順）。"""
        return self.watch.keys

    @property
    def period_s(self) -> float:
        return self.period_spin.value() / 1000.0

    def add(self, key: str) -> bool:
        """追加する。無い key・登録済みは False。"""
        if not self.watch.add(key):
            return False
        self._rebuild()
        self.changed.emit(self.keys())
        return True

    def remove(self, key: str) -> bool:
        if not self.watch.remove(key):
            return False
        self._rebuild()
        self.changed.emit(self.keys())
        return True

    def set_items(self, items: List[Item]) -> None:
        """接続ごとに項目を差し替える。保存から復元した key のうち存在するものを取り込み、無いものは捨てる。"""
        before = self.keys()
        self.watch.set_items(items)
        self._fill_combo()
        self._rebuild()
        if self.keys() != before:
            self.changed.emit(self.keys())

    def to_text(self) -> str:
        return self.watch.to_text()

    # ---- 内部 ----

    def _fill_combo(self) -> None:
        self.combo.clear()
        for it in sorted(self.watch.all_items, key=lambda i: i.key):
            name = it.sym if it.sym else (it.reg.name if it.reg else it.key)
            self.combo.addItem(f"{it.label}  [{name}]", it.key)

    def _add_selected(self) -> None:
        key = self.combo.currentData()
        if key:
            self.add(str(key))

    def _rebuild(self) -> None:
        items = self.watch.items
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))
        self._cells = [["—", ""] for _ in items]
        for r, it in enumerate(items):
            name = it.sym if it.sym else (it.reg.name if it.reg else it.key)
            for c, text in ((COL_LABEL, it.label), (COL_VALUE, "—"), (COL_UNIT, it.unit), (COL_RATE, "")):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled)
                if c == COL_LABEL:
                    cell.setToolTip(f"{name}（{it.key}）")
                self.table.setItem(r, c, cell)
            b = QPushButton("削除")
            b.clicked.connect(lambda _c=False, k=it.key: self.remove(k))
            self.table.setCellWidget(r, COL_DELETE, b)
        self.table.resizeColumnToContents(COL_LABEL)
        self.table.resizeColumnToContents(COL_UNIT)
        self.table.resizeColumnToContents(COL_RATE)
        self.table.resizeColumnToContents(COL_DELETE)
        self.table.setColumnWidth(COL_RATE, max(self.table.columnWidth(COL_RATE),
                                                self.fontMetrics().horizontalAdvance("+00000/s") + 12))
        self.table.setUpdatesEnabled(True)

    def refresh(self, model: Model) -> None:
        """値セルと毎秒だけ更新する。変わったセルだけ setText する。"""
        for r, it in enumerate(self.watch.items):
            s = model.sample(it.key)
            text = "—" if s is None else vs.plain_text(it, s.value)
            rate = sm.format_rate(model.rate(it.key)) if it.counter else ""
            cur = self._cells[r]
            if text != cur[0]:
                cur[0] = text
                self.table.item(r, COL_VALUE).setText(text)
            if rate != cur[1]:
                cur[1] = rate
                self.table.item(r, COL_RATE).setText(rate)
