"""設定タブ。RW レジスタ（設定）の表示・編集・書き込みと、Flash への保存・読み直し・工場出荷値。

編集状態の管理は nemagui.settings_edit（Qt 非依存）。ここは表示と操作の受け渡しだけを行う。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import items as itemsmod
from .. import viewstate as vs
from .. import worker as wk
from ..items import Item
from ..model import Model
from ..settings_edit import SettingsEdit
from .inputs import IntSpin, int_value, make_int_spin, set_int_value
from .overview import BitLamps

HEADERS = ["表示名", "基板の値", "編集値", "単位", "範囲", "名前", "書き込み結果"]
COL_LABEL, COL_BOARD, COL_EDIT, COL_UNIT, COL_RANGE, COL_NAME, COL_RESULT = range(7)

DIFF_BG = "#ffe08a"          # 編集値が基板の値と違う行
CONFIG_STATUS_KEY = "reg.CONFIG_STATUS"

TAG_WRITE = "write"
TAG_CMD = "cmd"
TAG_RELOAD = "reload"        # 読み直し。終わったら編集値を基板の値に戻す
TAG_AFTER = "after"          # 書き込み・保存のあとの確認読み出し。編集値はそのまま


def confirm_dialog(parent: Optional[QWidget], title: str, text: str) -> bool:
    """既定の確認関数。試験では SettingsTab.confirm を差し替える。"""
    ans = QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return ans == QMessageBox.Yes


class ValueEditor(QWidget):
    """設定 1 項目の編集ウィジェット。整数はスピン、離散値・列挙はコンボ、ビット項目はチェックボックス。"""

    edited = Signal()

    def __init__(self, item: Item, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self._spin: Optional[IntSpin] = None
        self._combo: Optional[QComboBox] = None
        self._checks: List[QCheckBox] = []
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QGridLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(4)
        lo = int(item.min if item.min is not None else 0)
        hi = int(item.max if item.max is not None else 0)
        if item.bits:
            for n, (_b, name) in enumerate(item.bits):
                c = QCheckBox(name)
                c.toggled.connect(lambda _on: self.edited.emit())
                self._checks.append(c)
                lay.addWidget(c, n // 3, n % 3)
        elif item.choices or item.enum:
            self._combo = QComboBox()
            if item.choices:
                for v in item.choices:
                    self._combo.addItem(str(v), v)
            else:
                for v, name in (item.enum or {}).items():
                    self._combo.addItem(f"{v} ({name})", v)
            self._combo.currentIndexChanged.connect(lambda _i: self.edited.emit())
            lay.addWidget(self._combo, 0, 0)
        else:
            self._spin = make_int_spin(lo, hi, lo)
            self._spin.valueChanged.connect(lambda _v: self.edited.emit())
            lay.addWidget(self._spin, 0, 0)

    def value(self) -> int:
        if self._checks:
            return sum(1 << b for (b, _n), c in zip(self.item.bits, self._checks) if c.isChecked())
        if self._combo is not None:
            return int(self._combo.currentData())
        assert self._spin is not None
        return int_value(self._spin)

    def set_value(self, v: int) -> None:
        """値を設定する（edited は出さない）。"""
        v = int(v)
        if self.value() == v:
            return
        self.blockSignals(True)
        try:
            for w in [*self._checks, *([self._combo] if self._combo else []), *([self._spin] if self._spin else [])]:
                w.blockSignals(True)
            if self._checks:
                for (b, _n), c in zip(self.item.bits, self._checks):
                    c.setChecked(bool(v & (1 << b)))
            elif self._combo is not None:
                i = self._combo.findData(v)
                if i >= 0:
                    self._combo.setCurrentIndex(i)
            elif self._spin is not None:
                set_int_value(self._spin, v)
        finally:
            for w in [*self._checks, *([self._combo] if self._combo else []), *([self._spin] if self._spin else [])]:
                w.blockSignals(False)
            self.blockSignals(False)

    def set_diff(self, diff: bool) -> None:
        self.setStyleSheet(f"background:{DIFF_BG};" if diff else "")


class SettingsTab(QWidget):
    def __init__(self, send: Callable[[Any], None], items: List[Item],
                 confirm: Optional[Callable[[str, str], bool]] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._send = send
        self.confirm: Callable[[str, str], bool] = confirm or (lambda t, m: confirm_dialog(self, t, m))
        self.edit = SettingsEdit(items)
        self._connected = False
        self._status: Optional[int] = None
        self.editors: Dict[str, ValueEditor] = {}
        self._rows: Dict[str, int] = {}
        self._board_text: Dict[str, str] = {}
        self._status_item = itemsmod.items_by_key(itemsmod.build_items())[CONFIG_STATUS_KEY]

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("設定状態"))
        self.lamps = BitLamps("CONFIG_STATUS", self._status_item)
        top.addWidget(self.lamps, 1)
        self.unsaved_label = QLabel("")
        top.addWidget(self.unsaved_label)
        root.addLayout(top)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.write_button = QPushButton("変更を書き込み")
        self.write_button.clicked.connect(self.write_changes)
        self.reload_button = QPushButton("読み直し")
        self.reload_button.setToolTip("基板の値を読み直し、編集値を基板の値に戻す")
        self.reload_button.clicked.connect(self.reload)
        self.save_button = QPushButton("Flash に保存（SAVE_SETTINGS）")
        self.save_button.clicked.connect(self._save)
        self.load_button = QPushButton("Flash から読み直し（LOAD_SETTINGS）")
        self.load_button.clicked.connect(self._load)
        self.factory_button = QPushButton("工場出荷値（FACTORY_SETTINGS、RAM のみ）")
        self.factory_button.clicked.connect(self._factory)
        for b in (self.write_button, self.reload_button):
            row.addWidget(b)
        row.addStretch(1)
        for b in (self.save_button, self.load_button, self.factory_button):
            row.addWidget(b)
        root.addLayout(row)

        self._build_rows()
        self._update_buttons()

    # ---- 行 ----

    def set_items(self, items: List[Item]) -> None:
        """接続ごと（項目が変わりうるとき）にだけ呼ぶ。同じ項目なら何もしない。"""
        before = [i.key for i in self.edit.items]
        self.edit.set_items(items)
        if [i.key for i in self.edit.items] != before:
            self._build_rows()

    def _build_rows(self) -> None:
        items = self.edit.items
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))
        self.editors.clear()
        self._rows.clear()
        self._board_text.clear()
        for r, it in enumerate(items):
            name = it.reg.name if it.reg else it.key
            cells = {COL_LABEL: it.label, COL_BOARD: "—", COL_UNIT: it.unit, COL_RANGE: vs.range_text(it),
                     COL_NAME: name, COL_RESULT: ""}
            for c, text in cells.items():
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(r, c, cell)
            ed = ValueEditor(it)
            ed.edited.connect(lambda key=it.key: self._on_edited(key))
            self.table.setCellWidget(r, COL_EDIT, ed)
            self.table.setItem(r, COL_EDIT, self._blank_cell())
            self.editors[it.key] = ed
            self._rows[it.key] = r
            self.table.setRowHeight(r, max(ed.sizeHint().height(), self.fontMetrics().height() + 10))
        self.table.resizeColumnsToContents()
        em = self.fontMetrics().horizontalAdvance("0")
        for col, chars in ((COL_BOARD, 22), (COL_EDIT, 42), (COL_UNIT, 7), (COL_RANGE, 22), (COL_NAME, 15)):
            self.table.setColumnWidth(col, max(self.table.columnWidth(col) if col == COL_UNIT else 0, em * chars))
        self.table.setUpdatesEnabled(True)
        self._set_editors_enabled()

    @staticmethod
    def _blank_cell() -> QTableWidgetItem:
        cell = QTableWidgetItem("")
        cell.setFlags(Qt.ItemIsEnabled)
        return cell

    def _set_editors_enabled(self) -> None:
        for ed in self.editors.values():
            ed.setEnabled(self._connected)

    def _on_edited(self, key: str) -> None:
        self.edit.edit(key, self.editors[key].value())
        self._paint_row(key)
        self._update_buttons()

    def _paint_row(self, key: str) -> None:
        r = self._rows[key]
        diff = self.edit.is_changed(key)
        brush = QBrush(QColor(DIFF_BG)) if diff else QBrush()
        for c in (COL_LABEL, COL_BOARD, COL_EDIT, COL_UNIT, COL_RANGE, COL_NAME, COL_RESULT):
            self.table.item(r, c).setBackground(brush)
        self.editors[key].set_diff(diff)

    # ---- 接続・更新 ----

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.edit.abort("切断されました")
            self._show_results()
        self._set_editors_enabled()
        self._update_buttons()

    def refresh(self, model: Model) -> None:
        """model の最新値で更新する。編集中の行の編集値は触らず、基板の値の列だけ更新する。"""
        board = {}
        for it in self.edit.items:
            s = model.sample(it.key)
            board[it.key] = None if s is None else s.value
        self.edit.update_board(board)
        for it in self.edit.items:
            key = it.key
            v = board[key]
            text = "—" if v is None else vs.plain_text(it, v)
            if self._board_text.get(key) != text:
                self._board_text[key] = text
                self.table.item(self._rows[key], COL_BOARD).setText(text)
            if not self.edit.is_editing(key) and v is not None:
                self.editors[key].set_value(int(v))
            self._paint_row(key)
        s = model.sample(CONFIG_STATUS_KEY)
        cs = None if s is None else s.value
        self.lamps.set_value(cs)
        unsaved = cs is not None and bool(int(cs) & 0x02)
        self.unsaved_label.setText("未保存の変更があります（Flash に保存されていません）" if unsaved else "")
        self.unsaved_label.setStyleSheet(
            f"color:#ffffff;background:{vs.ORANGE};font-weight:bold;border-radius:3px;padding:2px 8px;"
            if unsaved else "")
        self._show_results()
        self._update_buttons(model)

    def _show_results(self) -> None:
        for key, r in self._rows.items():
            text = self.edit.results.get(key, "")
            cell = self.table.item(r, COL_RESULT)
            if cell.text() != text:
                cell.setText(text)

    def _update_buttons(self, model: Optional[Model] = None) -> None:
        if model is not None:
            v = model.value("reg.STATUS")
            self._status = None if v is None else int(v)
        stopped = vs.settings_cmd_availability(self._connected, self._status)
        for b, tip in ((self.save_button, "RAM の設定を Flash に保存する"),
                       (self.load_button, "Flash の設定で RAM の設定を置き換える（未保存の変更は失われる）"),
                       (self.factory_button, "RAM の設定を工場出荷値に戻す（Flash は変わらない。保存は別途）")):
            b.setEnabled(stopped.enabled)
            b.setToolTip(tip if stopped.enabled else stopped.reason)
        can = self._connected and bool(self.edit.changed()) and not self.edit.busy
        self.write_button.setEnabled(can)
        self.write_button.setToolTip("編集値が基板の値と違う行だけを書き込む" if can else
                                     ("未接続です" if not self._connected else
                                      "書き込み中です" if self.edit.busy else "変更された行がありません"))
        self.reload_button.setEnabled(self._connected)

    # ---- 操作 ----

    def _read_keys(self) -> List[str]:
        return [i.key for i in self.edit.items] + [CONFIG_STATUS_KEY]

    def write_changes(self) -> None:
        """変更行だけを項目の並び順に WriteReg で送る。全結果が返ったら読み直す。"""
        todo = self.edit.begin_write()
        for it, value in todo:
            self._send(wk.WriteReg(it.key, value, tag=("settings", TAG_WRITE, it.key)))
        self._show_results()
        self._update_buttons()

    def reload(self) -> None:
        self._send(wk.ReadOnce(self._read_keys(), tag=("settings", TAG_RELOAD)))

    def _command(self, code: wk.Cmd) -> None:
        self._send(wk.Command(code, tag=("settings", TAG_CMD, code)))

    def _save(self) -> None:
        self._command(wk.Cmd.SAVE_SETTINGS)

    def _load(self) -> None:
        if self.confirm("Flash から読み直し",
                        "Flash に保存されている設定で現在の設定を置き換えます。\n未保存の変更は失われます。実行しますか？"):
            self._command(wk.Cmd.LOAD_SETTINGS)

    def _factory(self) -> None:
        if self.confirm("工場出荷値",
                        "設定を工場出荷値に戻します（RAM のみ。Flash は SAVE_SETTINGS まで変わりません）。\n"
                        "未保存の変更は失われます。実行しますか？"):
            self._command(wk.Cmd.FACTORY_SETTINGS)

    # ---- 結果 ----

    @staticmethod
    def owns(tag: Any) -> bool:
        return isinstance(tag, tuple) and len(tag) >= 2 and tag[0] == "settings"

    def on_write_result(self, r: wk.WriteResult) -> None:
        tag = r.request.tag
        if tag[1] == TAG_WRITE:
            if self.edit.record_result(tag[2], vs.write_result_text(r)):
                self._send(wk.ReadOnce(self._read_keys(), tag=("settings", TAG_AFTER)))
            self._show_results()
            self._update_buttons()
        elif tag[1] == TAG_CMD:
            replaced = tag[2] in (wk.Cmd.LOAD_SETTINGS, wk.Cmd.FACTORY_SETTINGS) and r.ok
            self._send(wk.ReadOnce(self._read_keys(), tag=("settings", TAG_RELOAD if replaced else TAG_AFTER)))

    def on_read_result(self, r: wk.ReadResult, model: Model) -> None:
        """MainWindow が model に取り込んだあとに呼ぶ。TAG_RELOAD なら編集値を基板の値に戻す。"""
        if r.request.tag[1] == TAG_RELOAD and not r.errors:
            board = {}
            for it in self.edit.items:
                s = model.sample(it.key)
                board[it.key] = None if s is None else s.value
            self.edit.update_board(board)
            self.edit.clear_edits()
            for it in self.edit.items:
                v = board[it.key]
                if v is not None:
                    self.editors[it.key].set_value(int(v))
        self.refresh(model)
