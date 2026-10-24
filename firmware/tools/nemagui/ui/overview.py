"""左の状態概要パネル。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from .. import items as itemsmod
from .. import viewstate as vs
from ..items import Item
from ..model import Model

_OFF_STYLE = "color:#8a8a8a;background:#ececec;border:1px solid #d0d0d0;border-radius:3px;padding:1px 4px;"


class BitLamps(QWidget):
    """ビットごとのランプ。立っているビットは色付きの太字、それ以外は灰色。"""

    COLUMNS = 3

    def __init__(self, reg_name: str, item: Item, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._reg = reg_name
        self._item = item
        self._labels: List[QLabel] = []
        self._shown: List[Optional[bool]] = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for n, (_b, name) in enumerate(item.bits):
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet(_OFF_STYLE)
            grid.addWidget(lab, n // self.COLUMNS, n % self.COLUMNS)
            self._labels.append(lab)
            self._shown.append(False)
        for c in range(self.COLUMNS):
            grid.setColumnStretch(c, 1)

    def set_value(self, value: Optional[int]) -> None:
        for n, (name, on) in enumerate(vs.lamp_states(self._item, value)):
            if self._shown[n] == on:
                continue
            self._shown[n] = on
            if on:
                self._labels[n].setStyleSheet(
                    f"color:#ffffff;background:{vs.lamp_color(self._reg, name)};font-weight:bold;"
                    "border:1px solid #444444;border-radius:3px;padding:1px 4px;")
            else:
                self._labels[n].setStyleSheet(_OFF_STYLE)


# (キー, 種類) 種類: state 色付き状態、lamps ビットランプ、vm V 表示、text 通常
_ROWS: List[Tuple[str, str]] = [
    ("reg.STATE", "state"),
    ("reg.FAULT", "lamps"),
    ("reg.STATUS", "lamps"),
    ("reg.INPUT_STATUS", "lamps"),
    ("reg.CONFIG_STATUS", "lamps"),
    ("reg.CMD_RESULT", "text"),
    ("reg.POSITION", "text"),
    ("reg.SPEED", "text"),
    ("reg.VM_MV", "vm"),
    ("reg.BOARD_TEMP", "text"),
    ("reg.MCU_TEMP", "text"),
    ("reg.SENSOR_STATUS", "text"),
    ("reg.SHAFT_ANGLE", "text"),
    ("reg.ENC_COUNT", "text"),
]


def overview_keys() -> List[str]:
    """概要パネルが表示する項目の key（"overview" ポーリング用）。"""
    return [k for k, _ in _ROWS]


class OverviewPanel(QGroupBox):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("状態概要", parent)
        by_key = itemsmod.items_by_key(itemsmod.build_items())
        self._items: Dict[str, Item] = {k: by_key[k] for k, _ in _ROWS}
        self._kinds = dict(_ROWS)
        self._widgets: Dict[str, QWidget] = {}
        self._texts: Dict[str, str] = {}
        grid = QGridLayout(self)
        grid.setVerticalSpacing(6)
        for row, (key, kind) in enumerate(_ROWS):
            item = self._items[key]
            name = QLabel(item.label)
            name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            if kind == "lamps":
                w: QWidget = BitLamps(item.reg.name if item.reg else key, item)
            else:
                w = QLabel("—")
                w.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if kind == "state":
                    w.setAlignment(Qt.AlignCenter)
                    f = w.font()
                    f.setBold(True)
                    f.setPointSizeF(f.pointSizeF() * 1.4)
                    w.setFont(f)
                else:
                    w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._widgets[key] = w
            grid.addWidget(name, row, 0)
            grid.addWidget(w, row, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(len(_ROWS), 1)
        self.refresh(None)

    def _set_text(self, key: str, text: str) -> None:
        if self._texts.get(key) != text:
            self._texts[key] = text
            self._widgets[key].setText(text)   # type: ignore[attr-defined]

    def refresh(self, model: Optional[Model]) -> None:
        """model の最新値で更新する。値が変わっていないウィジェットには触らない。"""
        for key, kind in _ROWS:
            sample = model.sample(key) if model is not None else None
            value = None if sample is None else sample.value
            w = self._widgets[key]
            if kind == "lamps":
                w.set_value(value)   # type: ignore[attr-defined]
            elif kind == "state":
                st = None if value is None else int(value)
                self._set_text(key, vs.state_name(st))
                style = (f"background:{vs.state_color(st)};color:{vs.state_text_color(st)};"
                         "border-radius:4px;padding:4px;")
                if self._texts.get(key + "#style") != style:
                    self._texts[key + "#style"] = style
                    w.setStyleSheet(style)
            elif kind == "vm":
                self._set_text(key, vs.format_vm(None if value is None else float(value)))
            else:
                self._set_text(key, "—" if value is None else itemsmod.format_value(self._items[key], value))
