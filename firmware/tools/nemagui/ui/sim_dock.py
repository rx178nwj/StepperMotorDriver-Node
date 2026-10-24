"""--sim のときだけ出す、擬似デバイスの操作ドック。SimSerial はスレッド安全なので UI スレッドから直接呼ぶ。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import (QCheckBox, QDockWidget, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

FAULT_BITS = (("OPD", 0x01), ("ISD", 0x02), ("TSD", 0x04))


class SimDock(QDockWidget):
    def __init__(self, sim: Any, log: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__("シミュレータ", parent)
        self.setObjectName("simDock")
        self._sim = sim
        self._log = log
        body = QWidget()
        root = QVBoxLayout(body)

        # 入力値
        g = QGroupBox("入力")
        f = QFormLayout(g)
        self.vm_spin = QSpinBox()
        self.vm_spin.setRange(0, 60000)
        self.vm_spin.setSingleStep(500)
        self.vm_spin.setValue(24000)
        self.vm_spin.setSuffix(" mV")
        b = QPushButton("適用")
        b.clicked.connect(self._apply_vm)
        f.addRow("VM", _row(self.vm_spin, b))

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(-40.0, 150.0)
        self.temp_spin.setDecimals(1)
        self.temp_spin.setValue(25.0)
        self.temp_spin.setSuffix(" °C")
        self.ntc_check = QCheckBox("NTC 断線")
        b = QPushButton("適用")
        b.clicked.connect(self._apply_temp)
        f.addRow("基板温度", _row(self.temp_spin, b))
        f.addRow("", self.ntc_check)
        root.addWidget(g)

        # FAULT 注入
        g = QGroupBox("FAULT 注入")
        h = QHBoxLayout(g)
        for name, bit in FAULT_BITS:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name, v=bit: self._inject(n, v))
            h.addWidget(b)
        root.addWidget(g)

        # HOME スイッチ
        g = QGroupBox("HOME スイッチ位置 [step]")
        f = QFormLayout(g)
        self.home1_spin, self.home1_none = _home_row(f, "HOME1（＋側）", 20000)
        self.home2_spin, self.home2_none = _home_row(f, "HOME2（−側）", -20000)
        b = QPushButton("適用")
        b.clicked.connect(self._apply_home)
        f.addRow("", b)
        root.addWidget(g)

        # 通信
        g = QGroupBox("通信")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.unplug_button = QPushButton("ケーブル抜去")
        self.unplug_button.clicked.connect(self._unplug)
        self.replug_button = QPushButton("再接続")
        self.replug_button.clicked.connect(self._replug)
        h.addWidget(self.unplug_button)
        h.addWidget(self.replug_button)
        v.addLayout(h)
        self.reboot_button = QPushButton("基板を再起動")
        self.reboot_button.clicked.connect(self._reboot)
        v.addWidget(self.reboot_button)
        self.drop_spin = QSpinBox()
        self.drop_spin.setRange(0, 100)
        b = QPushButton("設定")
        b.clicked.connect(self._apply_drop)
        f2 = QFormLayout()
        f2.addRow("応答を捨てる回数", _row(self.drop_spin, b))
        v.addLayout(f2)
        root.addWidget(g)
        root.addStretch(1)
        self.setWidget(body)

    # ---- 操作 ----

    def _apply_vm(self) -> None:
        self._sim.set_vm_mv(self.vm_spin.value())
        self._log(f"VM を {self.vm_spin.value()} mV にしました")

    def _apply_temp(self) -> None:
        if self.ntc_check.isChecked():
            self._sim.set_board_temp_c(None)
            self._log("基板温度: NTC 断線にしました")
        else:
            self._sim.set_board_temp_c(self.temp_spin.value())
            self._log(f"基板温度を {self.temp_spin.value():.1f} °C にしました")

    def _inject(self, name: str, bit: int) -> None:
        self._sim.inject_fault(bit)
        self._log(f"FAULT {name} を注入しました")

    def _apply_home(self) -> None:
        h1 = None if self.home1_none.isChecked() else self.home1_spin.value()
        h2 = None if self.home2_none.isChecked() else self.home2_spin.value()
        self._sim.set_home_switches(h1, h2)
        self._log(f"HOME スイッチ位置: HOME1={h1}、HOME2={h2}")

    def _unplug(self) -> None:
        self._sim.unplug()
        self._log("ケーブルを抜きました")

    def _replug(self) -> None:
        self._sim.replug()
        self._log("ケーブルを挿しました")

    def _reboot(self) -> None:
        self._sim.reboot()
        self._log("基板を再起動しました")

    def _apply_drop(self) -> None:
        self._sim.drop_responses = self.drop_spin.value()
        self._log(f"以後 {self.drop_spin.value()} 回の応答を捨てます")


def _row(*widgets: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        h.addWidget(x)
    return w


def _home_row(form: QFormLayout, label: str, default: int):
    spin = QSpinBox()
    spin.setRange(-10_000_000, 10_000_000)
    spin.setSingleStep(100)
    spin.setValue(default)
    none = QCheckBox("なし")
    form.addRow(label, _row(spin, none))
    return spin, none
