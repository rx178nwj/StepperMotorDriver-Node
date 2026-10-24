"""操作タブ。状態遷移・位置決め・速度指令・位置・原点復帰のボタンと、現在値の小さな表示。

ボタンの可否は viewstate.op_availability（GUI 側の目安。最終判断は基板）で決める。
要求は send（Worker.send 相当）に渡すだけで、結果は MainWindow が受けてログに出す。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from nemadbg import registers as regs

from .. import items as itemsmod
from .. import viewstate as vs
from .. import worker as wk
from ..model import Model
from .inputs import INT_MAX, INT_MIN, int_value, make_int_spin
from .overview import _OFF_STYLE

QUICK_STEPS = (-6400, -200, 200, 6400)

# 有効なときのツールチップ
_TIPS: Dict[str, str] = {
    vs.OP_ENABLE: "励磁して運転可能にする（DISABLED のとき）",
    vs.OP_DISABLE: "即停止して脱力する（ENABLED のとき）",
    vs.OP_CLEAR_FAULT: "異常を解除する（FAULT のとき。基板温度 70 °C 以上は拒否される）",
    vs.OP_MOVE_ABS: "入力した位置へ移動する",
    vs.OP_MOVE_REL: "入力した量だけ現在位置から移動する",
    vs.OP_RUN_SPEED: "入力した速度で連続運転する",
    vs.OP_RUN_SPEED_ZERO: "速度 0 を指令して減速停止する",
    vs.OP_SET_POSITION: "現在位置の値を書き換える（モータは動かない）",
    vs.OP_SYNC_POSITION: "センサ角度から現在位置を合わせる",
    vs.OP_RESET_ELEC_ANGLE: "電気角をリセットする",
    vs.OP_HOME_NEG: "−側へ原点復帰する",
    vs.OP_HOME_POS: "＋側へ原点復帰する",
}


class Lamp(QLabel):
    """オン・オフの小さなランプ。"""

    def __init__(self, text: str, on_color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._on_color = on_color
        self._on: Optional[bool] = None
        self.setAlignment(Qt.AlignCenter)
        self.set_on(False)

    @property
    def is_on(self) -> bool:
        return bool(self._on)

    def set_on(self, on: bool) -> None:
        if self._on == on:
            return
        self._on = on
        if on:
            self.setStyleSheet(f"color:#ffffff;background:{self._on_color};font-weight:bold;"
                               "border:1px solid #444444;border-radius:3px;padding:1px 6px;")
        else:
            self.setStyleSheet(_OFF_STYLE)


class OpsTab(QWidget):
    def __init__(self, send: Callable[[Any], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._send = send
        self.buttons: Dict[str, QPushButton] = {}
        self.quick_buttons: List[QPushButton] = []
        self._ctx = vs.OpsContext(False)

        root = QVBoxLayout(self)
        root.addWidget(self._build_readout())
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self._build_state())
        left.addWidget(self._build_position())
        left.addWidget(self._build_home())
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(self._build_motion())
        right.addWidget(self._build_speed())
        right.addStretch(1)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        root.addLayout(row, 1)
        self.apply_context(self._ctx)

    # ---- 組み立て ----

    def _button(self, op: str, text: str, action: Callable[[], None]) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda _c=False: action())
        self.buttons[op] = b
        return b

    def _command(self, code: wk.Cmd) -> Callable[[], None]:
        return lambda: self._send(wk.Command(code))

    def _write(self, target: str, get: Callable[[], int]) -> Callable[[], None]:
        return lambda: self._send(wk.WriteReg(target, get()))

    def _build_readout(self) -> QGroupBox:
        box = QGroupBox("現在値")
        g = QGridLayout(box)
        fm = self.fontMetrics()
        self.state_label = QLabel("—")
        self.state_label.setAlignment(Qt.AlignCenter)
        f = self.state_label.font()
        f.setBold(True)
        self.state_label.setFont(f)
        self.state_label.setMinimumWidth(fm.horizontalAdvance("DISABLED") + 24)
        self.pos_label = QLabel("—")
        self.speed_label = QLabel("—")
        self.j10_label = QLabel("—")
        for lab in (self.pos_label, self.speed_label):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setMinimumWidth(fm.horizontalAdvance("-2147483648 step/s"))
        self.lamps: Dict[str, Lamp] = {
            "TARGET_REACHED": Lamp("目標到達", vs.GREEN),
            "HOMING": Lamp("HOMING", vs.GREEN),
            "HOMED": Lamp("HOMED", vs.GREEN),
            "HOME_FAILED": Lamp("HOME_FAILED", vs.lamp_color("INPUT_STATUS", "HOME_FAILED")),
            "LIMIT_STOP": Lamp("LIMIT_STOP", vs.lamp_color("INPUT_STATUS", "LIMIT_STOP")),
        }
        g.addWidget(QLabel("状態"), 0, 0)
        g.addWidget(self.state_label, 0, 1)
        g.addWidget(QLabel("位置"), 0, 2)
        g.addWidget(self.pos_label, 0, 3)
        g.addWidget(QLabel("速度"), 0, 4)
        g.addWidget(self.speed_label, 0, 5)
        g.addWidget(QLabel("J10 の機能"), 0, 6)
        g.addWidget(self.j10_label, 0, 7)
        lamps = QHBoxLayout()
        for lamp in self.lamps.values():
            lamps.addWidget(lamp)
        lamps.addStretch(1)
        g.addLayout(lamps, 1, 0, 1, 8)
        g.setColumnStretch(8, 1)
        return box

    def _build_state(self) -> QGroupBox:
        box = QGroupBox("状態")
        lay = QHBoxLayout(box)
        lay.addWidget(self._button(vs.OP_ENABLE, "ENABLE", self._command(wk.Cmd.ENABLE)))
        lay.addWidget(self._button(vs.OP_DISABLE, "DISABLE", self._command(wk.Cmd.DISABLE)))
        lay.addWidget(self._button(vs.OP_CLEAR_FAULT, "CLEAR_FAULT", self._command(wk.Cmd.CLEAR_FAULT)))
        return box

    def _build_motion(self) -> QGroupBox:
        box = QGroupBox("位置決め")
        g = QGridLayout(box)
        self.target_spin = make_int_spin(INT_MIN, INT_MAX, 0)
        self.target_spin.setSuffix(" step")
        g.addWidget(QLabel("目標値"), 0, 0)
        g.addWidget(self.target_spin, 0, 1)

        def get() -> int:
            return int_value(self.target_spin)

        g.addWidget(self._button(vs.OP_MOVE_ABS, "絶対移動（MOVE_ABS）", self._write("MOVE_ABS", get)), 1, 0, 1, 2)
        g.addWidget(self._button(vs.OP_MOVE_REL, "相対移動（MOVE_REL）", self._write("MOVE_REL", get)), 2, 0, 1, 2)
        quick = QHBoxLayout()
        for d in QUICK_STEPS:
            b = QPushButton(f"{d:+d}")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, d=d: self._send(wk.WriteReg("MOVE_REL", d)))
            self.quick_buttons.append(b)
            quick.addWidget(b)
        g.addWidget(QLabel("クイック相対"), 3, 0)
        g.addLayout(quick, 3, 1)
        g.setColumnStretch(1, 1)
        return box

    def _build_speed(self) -> QGroupBox:
        box = QGroupBox("速度指令")
        g = QGridLayout(box)
        self.speed_spin = make_int_spin(INT_MIN, INT_MAX, 0)
        self.speed_spin.setSuffix(" step/s")
        self.speed_spin.valueChanged.connect(lambda _v: self.apply_context(self._ctx))
        g.addWidget(QLabel("速度"), 0, 0)
        g.addWidget(self.speed_spin, 0, 1)
        g.addWidget(self._button(vs.OP_RUN_SPEED, "速度指令（RUN_SPEED）",
                                 self._write("RUN_SPEED", lambda: int_value(self.speed_spin))), 1, 0, 1, 2)
        g.addWidget(self._button(vs.OP_RUN_SPEED_ZERO, "速度 0（減速停止）", self._write("RUN_SPEED", lambda: 0)),
                    2, 0, 1, 2)
        g.setColumnStretch(1, 1)
        return box

    def _build_position(self) -> QGroupBox:
        box = QGroupBox("位置")
        g = QGridLayout(box)
        self.setpos_spin = make_int_spin(INT_MIN, INT_MAX, 0)
        self.setpos_spin.setSuffix(" step")
        g.addWidget(QLabel("値"), 0, 0)
        g.addWidget(self.setpos_spin, 0, 1)
        g.addWidget(self._button(vs.OP_SET_POSITION, "SET_POSITION",
                                 self._write("SET_POSITION", lambda: int_value(self.setpos_spin))), 0, 2)
        g.addWidget(self._button(vs.OP_SYNC_POSITION, "SYNC_POSITION", self._command(wk.Cmd.SYNC_POSITION)),
                    1, 0, 1, 3)
        g.addWidget(self._button(vs.OP_RESET_ELEC_ANGLE, "RESET_ELEC_ANGLE",
                                 self._command(wk.Cmd.RESET_ELEC_ANGLE)), 2, 0, 1, 3)
        g.setColumnStretch(1, 1)
        return box

    def _build_home(self) -> QGroupBox:
        box = QGroupBox("原点復帰")
        lay = QHBoxLayout(box)
        lay.addWidget(self._button(vs.OP_HOME_NEG, "HOME_NEG（−側）", self._command(wk.Cmd.HOME_NEG)))
        lay.addWidget(self._button(vs.OP_HOME_POS, "HOME_POS（＋側）", self._command(wk.Cmd.HOME_POS)))
        return box

    # ---- 更新 ----

    def apply_context(self, ctx: vs.OpsContext) -> None:
        """受付条件に従ってボタンの有効・無効とツールチップを更新する。"""
        self._ctx = ctx
        av = vs.op_availability(ctx)
        for op, b in self.buttons.items():
            st = av[op]
            if op == vs.OP_RUN_SPEED and int_value(self.speed_spin) == 0:
                st = av[vs.OP_RUN_SPEED_ZERO]       # 速度 0 は減速停止（ENABLED なら常に可）
                tip = "速度 0 は減速停止になります（「速度 0」と同じ）" if st.enabled else st.reason
            else:
                tip = _TIPS[op] if st.enabled else st.reason
            b.setEnabled(st.enabled)
            b.setToolTip(tip)
        mv = av[vs.OP_MOVE_REL]
        for b in self.quick_buttons:
            b.setEnabled(mv.enabled)
            b.setToolTip(f"現在位置から {b.text()} step 移動する" if mv.enabled else mv.reason)

    def refresh(self, model: Model, connected: bool) -> None:
        """model の最新値で現在値の表示と、ボタンの可否を更新する。"""
        ctx = vs.ops_context(model, connected)
        if ctx != self._ctx:
            self.apply_context(ctx)
        st = ctx.state if connected else None
        self.state_label.setText(vs.state_name(st))
        self.state_label.setStyleSheet(f"background:{vs.state_color(st)};color:{vs.state_text_color(st)};"
                                       "border-radius:4px;padding:3px;")
        for label, key in ((self.pos_label, "reg.POSITION"), (self.speed_label, "reg.SPEED")):
            s = model.sample(key)
            label.setText("—" if s is None or not connected else itemsmod.format_value(model.items[key], s.value))
        j = ctx.j10_mode if connected else None
        self.j10_label.setText("—" if j is None else f"{j} ({regs.J10_MODE_NAMES.get(j, '不明')})")
        status = ctx.status if connected else None
        inp = ctx.input_status if connected else None
        self.lamps["TARGET_REACHED"].set_on(status is not None and bool(status & vs.STATUS_TARGET_REACHED))
        for name, bit in (("HOMING", vs.INPUT_HOMING), ("HOMED", vs.INPUT_HOMED),
                          ("HOME_FAILED", vs.INPUT_HOME_FAILED), ("LIMIT_STOP", vs.INPUT_LIMIT_STOP)):
            self.lamps[name].set_on(inp is not None and bool(inp & bit))
