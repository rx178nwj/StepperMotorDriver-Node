"""グループごとの汎用項目表（表示名・値・単位・名前・取得元）。詳細表示として各内部タブの下に残す（上部には要点パネル）。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableWidget, QTableWidgetItem, QWidget

from .. import viewstate as vs
from ..items import Item
from ..model import Model

HEADERS = ["表示名", "値", "単位", "名前", "取得元"]
SOURCE_TEXT = {"reg": "CAN レジスタ", "sym": "名前表"}


class GroupTable(QTableWidget):
    add_to_watch = Signal(str)     # 右クリックメニューの「ウォッチに追加」。引数は項目の key

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(HEADERS), parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._items: List[Item] = []
        self._cells: List[str] = []
        self.setHorizontalHeaderLabels(HEADERS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.setAlternatingRowColors(True)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)

    @property
    def keys(self) -> List[str]:
        return [i.key for i in self._items]

    def _context_menu(self, pos: QPoint) -> None:
        r = self.rowAt(pos.y())
        if not 0 <= r < len(self._items):
            return
        menu = QMenu(self)
        act = menu.addAction("ウォッチに追加")
        act.triggered.connect(lambda: self.add_to_watch.emit(self._items[r].key))
        menu.exec(self.viewport().mapToGlobal(pos))

    def set_items(self, items: List[Item]) -> None:
        """行を作り直す。接続ごと（項目が変わりうるとき）にだけ呼ぶ。同じ項目なら何もしない。"""
        if [i.key for i in items] == self.keys:
            return
        self._items = list(items)
        self._cells = ["—"] * len(items)
        self.setUpdatesEnabled(False)
        self.setRowCount(len(items))
        for r, it in enumerate(items):
            name = it.sym if it.sym else (it.reg.name if it.reg else it.key)
            for c, text in enumerate([it.label, "—", it.unit, name, SOURCE_TEXT.get(it.source, it.source)]):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.setItem(r, c, cell)
        self.resizeColumnsToContents()
        self.setColumnWidth(1, max(self.columnWidth(1), self.fontMetrics().horizontalAdvance("0" * 28)))
        self.setUpdatesEnabled(True)

    def refresh(self, model: Model) -> None:
        """値セルだけ更新する。変わったセルだけ setText する。"""
        for r, it in enumerate(self._items):
            s = model.sample(it.key)
            text = "—" if s is None else vs.plain_text(it, s.value)
            if it.counter:
                text += vs.counter_suffix(model.rate(it.key))
            if text != self._cells[r]:
                self._cells[r] = text
                self.item(r, 1).setText(text)
