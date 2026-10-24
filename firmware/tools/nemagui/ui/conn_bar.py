"""上部の接続バー。"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from .. import viewstate as vs

SIM_LABEL = "シミュレータ"


class ConnectionBar(QWidget):
    connect_clicked = Signal()
    disconnect_clicked = Signal()

    def __init__(self, sim: bool, fixed_port: Optional[str], find_ports: Callable[[], List[str]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._find_ports = find_ports
        self._fixed = sim or bool(fixed_port)
        self._kind = vs.CONN_DISCONNECTED
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("ポート"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(self.fontMetrics().horizontalAdvance("0" * 16))
        lay.addWidget(self.port_combo)
        self.refresh_button = QPushButton("再検索")
        self.refresh_button.clicked.connect(self.refresh_ports)
        lay.addWidget(self.refresh_button)
        self.connect_button = QPushButton("接続")
        self.connect_button.clicked.connect(self._on_button)
        lay.addWidget(self.connect_button)
        self.state_label = QLabel()
        lay.addWidget(self.state_label)
        self.info_label = QLabel("")
        lay.addWidget(self.info_label, 1)

        if sim:
            self.port_combo.addItem(SIM_LABEL, None)
        elif fixed_port:
            self.port_combo.addItem(fixed_port, fixed_port)
        if self._fixed:
            self.port_combo.setEnabled(False)
            self.refresh_button.setEnabled(False)
        else:
            self.refresh_ports()
        self.set_kind(vs.CONN_DISCONNECTED)

    # ---- ポート ----

    def refresh_ports(self) -> None:
        """VID/PID 一致のポートを探し直す。1 つなら選び、複数なら未選択にしてユーザーに選ばせる。"""
        if self._fixed:
            return
        try:
            ports = self._find_ports()
        except Exception:
            ports = []
        prev = self.selected_port()
        self.port_combo.clear()
        if not ports:
            self.port_combo.addItem("（ポートが見つかりません）", None)
            return
        if len(ports) > 1:
            self.port_combo.addItem("（選択してください）", None)
        for p in ports:
            self.port_combo.addItem(p, p)
        if len(ports) == 1:
            self.port_combo.setCurrentIndex(0)
        elif prev in ports:
            self.port_combo.setCurrentIndex(ports.index(prev) + 1)

    def selected_port(self) -> Optional[str]:
        data = self.port_combo.currentData()
        return data if isinstance(data, str) else None

    # ---- 状態 ----

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.state_label.setText(vs.CONN_TEXT[kind])
        self.state_label.setStyleSheet(
            f"background:{vs.CONN_COLORS[kind]};color:#ffffff;font-weight:bold;border-radius:4px;padding:3px 10px;")
        self.connect_button.setText("接続" if kind == vs.CONN_DISCONNECTED else "切断")
        if kind != vs.CONN_CONNECTED:
            self.info_label.setText("")

    @property
    def kind(self) -> str:
        return self._kind

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)

    def _on_button(self) -> None:
        if self._kind == vs.CONN_DISCONNECTED:
            self.connect_clicked.emit()
        else:
            self.disconnect_clicked.emit()
