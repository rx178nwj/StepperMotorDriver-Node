"""モーター角度とセンサ角度を同じ円上に描くダイヤル。0° は真上、時計回りに増加する。"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import summary as sm

MOTOR_COLOR = QColor("#1976d2")    # モーター（青）
SENSOR_COLOR = QColor("#e65100")   # センサ（橙）。明暗どちらのテーマでも見える中間的な色
_LEGEND_LINES = 3                  # モーター・センサ・差


def _fmt(angle: Optional[float]) -> str:
    return "—" if angle is None else f"{angle:.2f} °"


class AngleDial(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._motor: Optional[float] = None
        self._sensor: Optional[float] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(self.minimumSizeHint())

    @property
    def motor_angle(self) -> Optional[float]:
        return self._motor

    @property
    def sensor_angle(self) -> Optional[float]:
        return self._sensor

    def set_angles(self, motor: Optional[float], sensor: Optional[float]) -> None:
        """角度 [°]（不明は None）を設定する。変わったときだけ再描画する。"""
        if motor == self._motor and sensor == self._sensor:
            return
        self._motor = motor
        self._sensor = sensor
        self.update()

    def _legend_height(self) -> int:
        return self.fontMetrics().lineSpacing() * _LEGEND_LINES + 8

    def minimumSizeHint(self) -> QSize:
        return QSize(150, 150 + self._legend_height())

    def sizeHint(self) -> QSize:
        return QSize(220, 220 + self._legend_height())

    def _needle(self, p: QPainter, center: QPointF, radius: float, angle: float, color: QColor, length: float,
                width: float) -> None:
        rad = math.radians(angle)
        dx, dy = math.sin(rad), -math.cos(rad)          # 0° は真上、時計回り
        tip = QPointF(center.x() + dx * radius * length, center.y() + dy * radius * length)
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(center, tip)
        # 先端の三角
        px, py = -dy, dx
        head = radius * 0.1
        base = QPointF(tip.x() - dx * head * 1.6, tip.y() - dy * head * 1.6)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([tip, QPointF(base.x() + px * head * 0.7, base.y() + py * head * 0.7),
                                 QPointF(base.x() - px * head * 0.7, base.y() - py * head * 0.7)]))

    def paintEvent(self, _event) -> None:   # noqa: N802
        pal = self.palette()
        fg = pal.color(QPalette.WindowText)
        face = pal.color(QPalette.Base)
        legend_h = self._legend_height()
        side = max(10, min(self.width(), self.height() - legend_h) - 4)
        center = QPointF(self.width() / 2, 2 + side / 2)
        radius = side / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 円盤
        p.setPen(QPen(fg, 1.5))
        p.setBrush(face)
        p.drawEllipse(center, radius, radius)

        # 目盛り（30° ごと。90° ごとは長く）
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            dx, dy = math.sin(rad), -math.cos(rad)
            major = deg % 90 == 0
            mid = deg % 30 == 0
            inner = radius * (0.84 if major else 0.90 if mid else 0.94)
            col = QColor(fg)
            col.setAlpha(255 if major or mid else 110)
            p.setPen(QPen(col, 1.5 if major else 1))
            p.drawLine(QPointF(center.x() + dx * inner, center.y() + dy * inner),
                       QPointF(center.x() + dx * radius * 0.98, center.y() + dy * radius * 0.98))
        font = p.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.9))
        p.setFont(font)
        p.setPen(fg)
        fm = p.fontMetrics()
        for deg in (0, 90, 180, 270):
            rad = math.radians(deg)
            r = radius * 0.70
            text = f"{deg}"
            x = center.x() + math.sin(rad) * r - fm.horizontalAdvance(text) / 2
            y = center.y() - math.cos(rad) * r + (fm.ascent() - fm.descent()) / 2
            p.drawText(QPointF(x, y), text)

        # 針（センサを細く長く、モーターを太く少し短く。値が無いものは描かない）
        if self._sensor is not None:
            self._needle(p, center, radius, self._sensor, SENSOR_COLOR, 0.88, 2.0)
        if self._motor is not None:
            self._needle(p, center, radius, self._motor, MOTOR_COLOR, 0.76, 4.0)
        p.setPen(Qt.NoPen)
        p.setBrush(fg)
        p.drawEllipse(center, radius * 0.04, radius * 0.04)

        # 凡例
        font.setPointSizeF(self.font().pointSizeF())
        p.setFont(font)
        fm = p.fontMetrics()
        diff = sm.angle_diff_deg(self._motor, self._sensor)
        rows = [(MOTOR_COLOR, f"モーター  {_fmt(self._motor)}"), (SENSOR_COLOR, f"センサ  {_fmt(self._sensor)}"),
                (None, "差（モーター − センサ）  " + ("—" if diff is None else f"{diff:+.2f} °"))]
        y = 2 + side + 6
        x0 = max(4.0, (self.width() - max(fm.horizontalAdvance(t) for _, t in rows) - 18) / 2)
        for col, text in rows:
            if col is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawRect(QRectF(x0, y + fm.lineSpacing() / 2 - 4, 12, 8))
            p.setPen(fg)
            p.drawText(QPointF(x0 + 18, y + fm.ascent()), text)
            y += fm.lineSpacing()
        p.end()
