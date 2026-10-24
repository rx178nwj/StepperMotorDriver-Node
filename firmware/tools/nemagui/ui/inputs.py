"""数値入力ウィジェットの共通部品。"""

from __future__ import annotations

from typing import Optional, Union

from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QWidget

INT_MIN = -(1 << 31)
INT_MAX = (1 << 31) - 1

IntSpin = Union[QSpinBox, QDoubleSpinBox]


def make_int_spin(lo: int, hi: int, value: int = 0, parent: Optional[QWidget] = None) -> IntSpin:
    """整数用のスピンボックス。範囲外は入力できない（範囲に丸められる）。

    QSpinBox は int32 の範囲までしか扱えないので、範囲がそれを超える（u32 の上限など）ときは
    小数 0 桁の QDoubleSpinBox にする（2^53 までの整数は正確）。値の読み書きは int_value / set_int_value を使う。
    """
    if INT_MIN <= lo and hi <= INT_MAX:
        w: IntSpin = QSpinBox(parent)
        w.setRange(int(lo), int(hi))
    else:
        w = QDoubleSpinBox(parent)
        w.setDecimals(0)
        w.setRange(float(lo), float(hi))
    w.setKeyboardTracking(False)
    set_int_value(w, value)
    return w


def int_value(w: IntSpin) -> int:
    return int(round(w.value()))


def set_int_value(w: IntSpin, v: int) -> None:
    if isinstance(w, QDoubleSpinBox):
        w.setValue(float(v))
    else:
        w.setValue(int(v))
