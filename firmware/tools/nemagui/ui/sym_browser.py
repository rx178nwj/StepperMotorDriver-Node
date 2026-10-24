"""名前表ブラウザ。接続時に得た名前表の全件を表で見て、選んだ項目を 1 回読む／ウォッチに追加する。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from nemadbg.link import SymInfo

from .. import items as itemsmod
from .. import summary as sm
from .. import worker as wk
from ..items import Item

HEADERS = ["名前", "アドレス", "TYPE", "NUM", "サイズ", "グループ"]
TAG = "syms"


class SymBrowser(QWidget):
    add_to_watch = Signal(str)      # 選択した項目の key

    def __init__(self, send: Callable[[Any], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._send = send
        self._infos: List[SymInfo] = []
        self._items: Dict[str, Item] = {}
        self._connected = False

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("名前の絞り込み"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("名前の一部（大文字小文字は区別しない）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, 1)
        self.count_label = QLabel("")
        top.addWidget(self.count_label)
        root.addLayout(top)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _it: self.read_selected())

        buttons = QHBoxLayout()
        self.read_button = QPushButton("読む")
        self.read_button.setToolTip("選択した項目を 1 回読んで、下に値を表示する")
        self.read_button.clicked.connect(self.read_selected)
        self.watch_button = QPushButton("ウォッチに追加")
        self.watch_button.clicked.connect(self.add_selected_to_watch)
        buttons.addWidget(self.read_button)
        buttons.addWidget(self.watch_button)
        buttons.addStretch(1)

        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.result.setPlaceholderText("読んだ値がここに出ます（bytes 型は 16 進ダンプ）")
        self.result.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))   # 16 進ダンプの桁を揃える

        lower = QWidget()
        lv = QVBoxLayout(lower)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(buttons)
        lv.addWidget(self.result, 1)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(lower)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        self._update_buttons()

    # ---- 名前表 ----

    def set_symtab(self, symtab: Mapping[str, SymInfo], items: List[Item]) -> None:
        """接続ごとに呼ぶ。名前表の全件を行にする（アドレス順）。"""
        infos = sorted(symtab.values(), key=lambda i: (i.addr, i.name))
        by_key = itemsmod.items_by_key(items)
        if [i.name for i in infos] == [i.name for i in self._infos] and by_key.keys() == self._items.keys():
            return
        self._infos = infos
        self._items = by_key
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(infos))
        for r, info in enumerate(infos):
            it = by_key.get("sym." + info.name)
            group = itemsmod.GROUP_LABELS.get(it.group, it.group) if it else "—"
            cells = [info.name, f"0x{info.addr:08X}", sm.sym_type_name(info.type), str(info.num), str(info.size), group]
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c in (1, 3, 4):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()
        self.table.setUpdatesEnabled(True)
        self._apply_filter()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_buttons()

    @property
    def visible_names(self) -> List[str]:
        return [i.name for r, i in enumerate(self._infos) if not self.table.isRowHidden(r)]

    def _apply_filter(self) -> None:
        shown = set(sm.filter_names((i.name for i in self._infos), self.filter_edit.text()))
        for r, info in enumerate(self._infos):
            self.table.setRowHidden(r, info.name not in shown)
        self.count_label.setText(f"{len(shown)} / {len(self._infos)} 件")
        self._update_buttons()

    def selected_keys(self) -> List[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return ["sym." + self._infos[r].name for r in rows
                if r < len(self._infos) and not self.table.isRowHidden(r) and ("sym." + self._infos[r].name) in self._items]

    def _update_buttons(self) -> None:
        has = bool(self.table.selectedIndexes())
        self.read_button.setEnabled(self._connected and has)
        self.watch_button.setEnabled(has)

    # ---- 操作 ----

    def read_selected(self) -> None:
        keys = self.selected_keys()
        if keys and self._connected:
            self._send(wk.ReadOnce(keys, tag=(TAG, keys)))

    def add_selected_to_watch(self) -> None:
        for k in self.selected_keys():
            self.add_to_watch.emit(k)

    @staticmethod
    def owns(tag: Any) -> bool:
        return isinstance(tag, tuple) and len(tag) >= 2 and tag[0] == TAG

    def on_read_result(self, r: wk.ReadResult) -> None:
        """MainWindow が model に取り込んだあとに呼ぶ。要求した順に値（bytes 型は 16 進ダンプ）を並べる。"""
        parts: List[str] = []
        for key in r.request.tag[1]:
            it = self._items.get(key)
            if it is None:
                continue
            raw = r.values.get(key)
            parts.append(sm.read_result_text(it, raw) if raw is not None else f"{it.sym}: 読めませんでした")
        for text in r.errors:
            parts.append("読み出し失敗: " + text)
        self.result.setPlainText("\n\n".join(parts))
