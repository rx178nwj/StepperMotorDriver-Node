"""内部状態タブの上部に置く要点パネル（モーション・状態遷移・センサ・J10/原点・通信統計・診断）。

判定と整形は nemagui.summary / nemagui.viewstate（Qt 非依存）。ここはウィジェットの組み立てと値の受け渡しだけを行う。
各パネルは keys（そのパネルが使う項目の key。表示中に読む）と refresh(model) を持つ。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import items as itemsmod
from .. import summary as sm
from .. import viewstate as vs
from ..model import Model
from .ops_tab import Lamp
from .overview import BitLamps

ITEMS = itemsmod.items_by_key(itemsmod.build_items())

_CAPTION_STYLE = "color:#555555;"


def _num(model: Model, key: str) -> Optional[int]:
    v = model.value(key)
    return None if v is None else int(v)


def _flt(model: Model, key: str) -> Optional[float]:
    v = model.value(key)
    return None if v is None else float(v)


def fmt_speed(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f} step/s"


def fmt_step(v: Optional[int]) -> str:
    return "—" if v is None else f"{v} step"


class SummaryPanel(QGroupBox):
    """要点パネルの基底。値が変わらないラベルには触らない。"""

    def __init__(self, title: str, keys: List[str]) -> None:
        super().__init__(title)
        self._keys = list(keys)
        self._cache: Dict[int, Tuple[str, str]] = {}

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def refresh(self, model: Model) -> None:
        raise NotImplementedError

    def _set(self, label: QLabel, text: str, color: str = "", bold: bool = False) -> None:
        sig = (text, f"{color}|{bold}")
        if self._cache.get(id(label)) == sig:
            return
        self._cache[id(label)] = sig
        label.setText(text)
        style = (f"color:{color};" if color else "") + ("font-weight:bold;" if bold else "")
        label.setStyleSheet(style)

    @staticmethod
    def _field(grid: QGridLayout, row: int, col: int, caption: str, big: bool = False) -> QLabel:
        """caption: value の 1 組を grid の (row, col*2) に置く。"""
        cap = QLabel(caption)
        cap.setStyleSheet(_CAPTION_STYLE)
        val = QLabel("—")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if big:
            f = val.font()
            f.setPointSizeF(f.pointSizeF() * 1.4)
            f.setBold(True)
            val.setFont(f)
        grid.addWidget(cap, row, col * 2, Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(val, row, col * 2 + 1, Qt.AlignLeft | Qt.AlignVCenter)
        return val


def _item(key: str):   # type: ignore[no-untyped-def]
    return ITEMS[key]


# ---------------------------------------------------------------- モーション

class MotionPanel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("モーション要点", ["sym." + n for n in (
            "mode", "running", "dir", "cur_speed", "plan_speed", "target_vel", "position", "target_pos",
            "commit_pos", "hold_active")])
        g = QGridLayout(self)
        top = QHBoxLayout()
        self.mode = QLabel("—")
        f = self.mode.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        self.mode.setFont(f)
        self.mode.setMinimumWidth(self.fontMetrics().horizontalAdvance("0" * 6))
        self.running = Lamp("パルス出力中", vs.GREEN)
        self.hold = Lamp("保持電流", vs.BLUE)
        self.dir = QLabel("—")
        self.dir.setFont(f)
        self.dir.setMinimumWidth(self.fontMetrics().horizontalAdvance("0" * 3))
        self.dir.setAlignment(Qt.AlignCenter)
        top.addWidget(QLabel("モード"))
        top.addWidget(self.mode)
        top.addSpacing(12)
        top.addWidget(self.running)
        top.addWidget(self.hold)
        top.addSpacing(12)
        top.addWidget(QLabel("方向"))
        top.addWidget(self.dir)
        top.addStretch(1)
        g.addLayout(top, 0, 0, 1, 6)
        self.cur_speed = self._field(g, 1, 0, "現在速度")
        self.plan_speed = self._field(g, 1, 1, "プランナ速度")
        self.target_vel = self._field(g, 1, 2, "目標速度")
        self.position = self._field(g, 2, 0, "現在位置")
        self.target_pos = self._field(g, 2, 1, "目標位置")
        self.remain = self._field(g, 2, 2, "残り（目標まで）")
        self.commit = self._field(g, 3, 1, "確定済み先行位置")
        self.lead = self._field(g, 3, 2, "先行分（確定位置−現在）")
        for c in (1, 3, 5):
            g.setColumnStretch(c, 1)

    def refresh(self, model: Model) -> None:
        mode = _num(model, "sym.mode")
        self._set(self.mode, "—" if mode is None else itemsmod.MODE_NAMES.get(mode, f"不明({mode})"),
                  vs.BLUE if mode else "", True)
        running = _num(model, "sym.running")
        self.running.set_on(bool(running))
        self.hold.set_on(bool(_num(model, "sym.hold_active")))
        d = _num(model, "sym.dir")
        self._set(self.dir, sm.dir_arrow(d), vs.BLUE if running else vs.GRAY, True)
        self._set(self.cur_speed, fmt_speed(_flt(model, "sym.cur_speed")))
        self._set(self.plan_speed, fmt_speed(_flt(model, "sym.plan_speed")))
        self._set(self.target_vel, fmt_speed(_flt(model, "sym.target_vel")),
                  "" if mode == 2 else vs.GRAY)
        pos = _num(model, "sym.position")
        tgt = _num(model, "sym.target_pos")
        com = _num(model, "sym.commit_pos")
        self._set(self.position, fmt_step(pos))
        self._set(self.target_pos, fmt_step(tgt), "" if mode == 1 else vs.GRAY)
        rem = sm.remaining_steps(pos, tgt)
        self._set(self.remain, fmt_step(rem) if mode == 1 else "—")
        self._set(self.commit, fmt_step(com))
        self._set(self.lead, fmt_step(sm.remaining_steps(pos, com)))


# ---------------------------------------------------------------- 状態遷移・異常

class StatePanel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("状態遷移・異常 要点", ["sym." + n for n in (
            "state", "fault", "isr_fault", "vm_ok", "temp_warn", "lo_armed", "hold_active", "cmd_result",
            "cfg_status")])
        g = QGridLayout(self)
        self.state = QLabel("—")
        self.state.setAlignment(Qt.AlignCenter)
        f = self.state.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        self.state.setFont(f)
        self.state.setMinimumWidth(self.fontMetrics().horizontalAdvance("0" * 12))
        g.addWidget(QLabel("状態"), 0, 0)
        g.addWidget(self.state, 0, 1)
        self.fault = BitLamps("FAULT", _item("sym.fault"))
        self.isr_fault = BitLamps("FAULT", _item("sym.isr_fault"))
        self.cfg = BitLamps("CONFIG_STATUS", _item("sym.cfg_status"))
        g.addWidget(QLabel("fault"), 1, 0)
        g.addWidget(self.fault, 1, 1, 1, 3)
        g.addWidget(QLabel("isr_fault"), 2, 0)
        g.addWidget(self.isr_fault, 2, 1, 1, 3)
        g.addWidget(QLabel("cfg_status"), 3, 0)
        g.addWidget(self.cfg, 3, 1, 1, 3)
        flags = QHBoxLayout()
        self.vm_ok = Lamp("vm_ok", vs.GREEN)
        self.temp_warn = Lamp("temp_warn", vs.ORANGE)
        self.lo_armed = Lamp("lo_armed", vs.GREEN)
        self.hold = Lamp("hold_active", vs.BLUE)
        for w in (self.vm_ok, self.temp_warn, self.lo_armed, self.hold):
            flags.addWidget(w)
        flags.addStretch(1)
        g.addLayout(flags, 4, 0, 1, 4)
        self.cmd_result = QLabel("—")
        g.addWidget(QLabel("cmd_result"), 5, 0)
        g.addWidget(self.cmd_result, 5, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)

    def refresh(self, model: Model) -> None:
        st = _num(model, "sym.state")
        text = vs.state_name(st)
        style = (f"background:{vs.state_color(st)};color:{vs.state_text_color(st)};"
                 "font-weight:bold;border-radius:4px;padding:4px;")
        if self._cache.get(id(self.state)) != (text, style):
            self._cache[id(self.state)] = (text, style)
            self.state.setText(text)
            self.state.setStyleSheet(style)
        self.fault.set_value(_num(model, "sym.fault"))
        self.isr_fault.set_value(_num(model, "sym.isr_fault"))
        self.cfg.set_value(_num(model, "sym.cfg_status"))
        self.vm_ok.set_on(bool(_num(model, "sym.vm_ok")))
        self.temp_warn.set_on(bool(_num(model, "sym.temp_warn")))
        self.lo_armed.set_on(bool(_num(model, "sym.lo_armed")))
        self.hold.set_on(bool(_num(model, "sym.hold_active")))
        self._set(self.cmd_result, model.text("sym.cmd_result"))


# ---------------------------------------------------------------- センサ

def _as_status_item():   # type: ignore[no-untyped-def]
    import dataclasses
    return dataclasses.replace(_item("sym.sensor.as_status"), bits=sm.AS_STATUS_BITS)


class SensorPanel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("センサ要点", ["sym." + n for n in (
            "sensor_status", "sensor.as_status", "sensor.raw", "shaft_angle", "sensor.i2c_ok", "sensor.i2c_fail",
            "sensor.fail_streak", "pa7_applied")])
        g = QGridLayout(self)
        g.addWidget(QLabel("SENSOR_STATUS"), 0, 0)
        self.status = BitLamps("SENSOR_STATUS", _item("sym.sensor_status"))
        g.addWidget(self.status, 0, 1, 1, 5)
        g.addWidget(QLabel("AS5600 STATUS"), 1, 0)
        self.as_status = BitLamps(sm.AS_STATUS_REG, _as_status_item())
        g.addWidget(self.as_status, 1, 1, 1, 5)
        self.raw = self._field(g, 2, 0, "RAW ANGLE")
        self.angle = self._field(g, 2, 1, "軸角度")
        self.pa7 = self._field(g, 2, 2, "PA7 の機能")
        self.ok = self._field(g, 3, 0, "I2C 成功")
        self.fail = self._field(g, 3, 1, "I2C 失敗")
        self.fail_rate = self._field(g, 3, 2, "失敗/秒")
        self.streak = self._field(g, 4, 0, "連続失敗")
        for c in (1, 3, 5):
            g.setColumnStretch(c, 1)

    def refresh(self, model: Model) -> None:
        self.status.set_value(_num(model, "sym.sensor_status"))
        as_st = _num(model, "sym.sensor.as_status")
        self.as_status.set_value(as_st)
        raw = _num(model, "sym.sensor.raw")
        self._set(self.raw, "—" if raw is None else str(raw))
        self._set(self.angle, model.text("sym.shaft_angle"))
        self._set(self.pa7, model.text("sym.pa7_applied"))
        self._set(self.ok, model.text("sym.sensor.i2c_ok") + vs.counter_suffix(model.rate("sym.sensor.i2c_ok")))
        fail = _num(model, "sym.sensor.i2c_fail")
        frate = model.rate("sym.sensor.i2c_fail")
        self._set(self.fail, "—" if fail is None else str(fail), vs.ORANGE if fail else "")
        self._set(self.fail_rate, sm.format_rate(frate), vs.RED if frate else "", bool(frate))
        text, color = sm.fail_streak_text(_num(model, "sym.sensor.fail_streak"))
        self._set(self.streak, text, color if color != vs.GREEN else "", color == vs.RED)


# ---------------------------------------------------------------- J10・原点

class _HomeInput(QGroupBox):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        lay = QHBoxLayout(self)
        self.active = Lamp("確定", vs.GREEN)
        self.cand = Lamp("サンプル", vs.BLUE)
        self.bar = QProgressBar()
        self.bar.setRange(0, sm.HOME_CONFIRM_SAMPLES)
        self.bar.setFormat("%v/%m")
        self.bar.setMaximumWidth(self.fontMetrics().horizontalAdvance("0" * 10))
        self.bar.setTextVisible(True)
        lay.addWidget(self.active)
        lay.addWidget(self.cand)
        lay.addWidget(QLabel("連続"))
        lay.addWidget(self.bar)
        lay.addStretch(1)

    def update_values(self, active: Optional[int], cand: Optional[int], count: Optional[int]) -> None:
        self.active.set_on(bool(active))
        self.cand.set_on(bool(cand))
        n, total = sm.debounce_progress(count)
        self.bar.setRange(0, total)
        if self.bar.value() != n:
            self.bar.setValue(n)


class J10Panel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("J10・原点復帰 要点", ["sym." + n for n in (
            "home.state", "home.side", "input_status", "hm1.active", "hm1.cand", "hm1.count", "hm2.active",
            "hm2.cand", "hm2.count", "enc_count", "agc_out", "limit_stopped")])
        g = QGridLayout(self)
        stages = QHBoxLayout()
        stages.addWidget(QLabel("原点復帰"))
        self.stage_labels: List[QLabel] = []
        for i, name in enumerate(sm.HOME_STAGES):
            if i:
                stages.addWidget(QLabel("→"))
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignCenter)
            self.stage_labels.append(lab)
            stages.addWidget(lab)
        self.failed = QLabel("FAILED")
        self.failed.setAlignment(Qt.AlignCenter)
        stages.addSpacing(12)
        stages.addWidget(self.failed)
        stages.addStretch(1)
        g.addLayout(stages, 0, 0, 1, 6)
        self.side = self._field(g, 1, 0, "対象の側")
        g.addWidget(QLabel("INPUT_STATUS"), 2, 0)
        self.input_status = BitLamps("INPUT_STATUS", _item("sym.input_status"))
        g.addWidget(self.input_status, 2, 1, 1, 5)
        self.hm1 = _HomeInput("HOME1（PA15）")
        self.hm2 = _HomeInput("HOME2（PA1）")
        g.addWidget(self.hm1, 3, 0, 1, 3)
        g.addWidget(self.hm2, 3, 3, 1, 3)
        self.enc = self._field(g, 4, 0, "enc_count")
        self.agc = self._field(g, 4, 1, "agc_out")
        self.limit = Lamp("limit_stopped", vs.ORANGE)
        g.addWidget(self.limit, 4, 4, 1, 2)
        for c in (1, 3, 5):
            g.setColumnStretch(c, 1)
        self._stage_sig: Optional[Tuple[Optional[int], bool]] = None

    _ON = "color:#ffffff;background:%s;font-weight:bold;border-radius:3px;padding:2px 6px;"
    _PASSED = "color:#1b5e20;background:#c8e6c9;border-radius:3px;padding:2px 6px;"
    _OFF = "color:#8a8a8a;background:#ececec;border-radius:3px;padding:2px 6px;"

    def _paint_stages(self, cur: Optional[int], failed: bool) -> None:
        if self._stage_sig == (cur, failed):
            return
        self._stage_sig = (cur, failed)
        for i, lab in enumerate(self.stage_labels):
            if cur is not None and i == cur:
                lab.setStyleSheet(self._ON % vs.BLUE)
            elif cur is not None and i < cur:
                lab.setStyleSheet(self._PASSED)
            else:
                lab.setStyleSheet(self._OFF)
        self.failed.setStyleSheet(self._ON % vs.RED if failed else self._OFF)

    def refresh(self, model: Model) -> None:
        cur, failed = sm.home_stage(_num(model, "sym.home.state"))
        self._paint_stages(cur, failed)
        self._set(self.side, sm.home_side_text(_num(model, "sym.home.side")))
        self.input_status.set_value(_num(model, "sym.input_status"))
        self.hm1.update_values(_num(model, "sym.hm1.active"), _num(model, "sym.hm1.cand"),
                               _num(model, "sym.hm1.count"))
        self.hm2.update_values(_num(model, "sym.hm2.active"), _num(model, "sym.hm2.cand"),
                               _num(model, "sym.hm2.count"))
        self._set(self.enc, model.text("sym.enc_count"))
        self._set(self.agc, model.text("sym.agc_out"))
        self.limit.set_on(bool(_num(model, "sym.limit_stopped")))


# ---------------------------------------------------------------- 通信統計

class CounterTable(QTableWidget):
    """カウンタの表（名前・値・毎秒）。異常系は 0 でなければ橙、増加中なら赤。"""

    def __init__(self, title: str, keys: Tuple[str, ...]) -> None:
        super().__init__(len(keys), 3)
        self.title = title
        self._keys = list(keys)
        self._sig: Dict[int, Tuple[str, str, str]] = {}
        self.setHorizontalHeaderLabels([title, "値", "毎秒"])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for r, k in enumerate(keys):
            it = ITEMS[k]
            for c, text in enumerate([f"{it.label}（{it.sym}）", "—", "—"]):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled)
                if c:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(r, c, cell)
        self.setFixedHeight(self.horizontalHeader().height() + (self.fontMetrics().height() + 6) * len(keys) + 4)

    def refresh(self, model: Model) -> None:
        for r, k in enumerate(self._keys):
            v = _num(model, k)
            rate = model.rate(k) if ITEMS[k].counter else None
            vt = sm.format_counter_value(k, v)
            rt = sm.format_rate(rate) if ITEMS[k].counter else ""
            color = sm.anomaly_color(k, v, rate) or ""
            if self._sig.get(r) == (vt, rt, color):
                continue
            self._sig[r] = (vt, rt, color)
            brush = QBrush(QColor(color)) if color else QBrush(self.palette().text().color())
            for c, text in ((1, vt), (2, rt)):
                cell = self.item(r, c)
                cell.setText(text)
                cell.setForeground(brush)
                f = cell.font()
                f.setBold(bool(color))
                cell.setFont(f)


class CommPanel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("通信統計 要点", list(sm.COMM_CAN_KEYS) + list(sm.COMM_USB_KEYS))
        lay = QHBoxLayout(self)
        self.can_table = CounterTable("CAN", sm.COMM_CAN_KEYS)
        self.usb_table = CounterTable("USB", sm.COMM_USB_KEYS)
        lay.addWidget(self.can_table, 0, Qt.AlignTop)
        lay.addWidget(self.usb_table, 0, Qt.AlignTop)

    def refresh(self, model: Model) -> None:
        self.can_table.refresh(model)
        self.usb_table.refresh(model)


# ---------------------------------------------------------------- 診断

class DiagPanel(SummaryPanel):
    def __init__(self) -> None:
        super().__init__("診断 要点", ["sym." + n for n in (
            "uwTick", "reset_csr", "loop.max_us", "loop.max_1s_us", "loop.rate_hz", "stack.used_max", "stack.size")])
        g = QGridLayout(self)
        self.uptime = self._field(g, 0, 0, "稼働時間", big=True)
        self.reset_main = self._field(g, 1, 0, "リセット要因")
        self.reset_flags = self._field(g, 2, 0, "立っているフラグ")
        self.loop_max = self._field(g, 3, 0, "ループ最大周期")
        self.loop_1s = self._field(g, 4, 0, "ループ最大周期(直近1秒)")
        self.loop_rate = self._field(g, 5, 0, "ループ回数(直近1秒)")
        self.stack_text = self._field(g, 6, 0, "スタック使用量")
        self.stack_bar = QProgressBar()
        self.stack_bar.setRange(0, 1000)
        self.stack_bar.setTextVisible(True)
        g.addWidget(self.stack_bar, 7, 0, 1, 2)
        g.setColumnStretch(1, 1)
        self._bar_color = ""

    def refresh(self, model: Model) -> None:
        self._set(self.uptime, sm.format_uptime(_num(model, "sym.uwTick")))
        info = sm.decode_reset_csr(_num(model, "sym.reset_csr"))
        self._set(self.reset_main, "—" if info is None else info.primary, "", True)
        self._set(self.reset_flags, "—" if info is None else (", ".join(info.flags) or "なし"))
        self._set(self.loop_max, model.text("sym.loop.max_us"))
        self._set(self.loop_1s, model.text("sym.loop.max_1s_us"))
        self._set(self.loop_rate, model.text("sym.loop.rate_hz"))
        used = _num(model, "sym.stack.used_max")
        size = _num(model, "sym.stack.size")
        pct = sm.stack_percent(used, size)
        color = sm.stack_color(pct)
        if pct is None:
            self._set(self.stack_text, "—")
            self.stack_bar.setValue(0)
            self.stack_bar.setFormat("—")
        else:
            self._set(self.stack_text, f"{used} / {size} B（{pct:.1f} %）", color if color != vs.GREEN else "",
                      color != vs.GREEN)
            self.stack_bar.setValue(min(1000, int(round(pct * 10))))
            self.stack_bar.setFormat(f"{pct:.1f} %")
        if color != self._bar_color:
            self._bar_color = color
            self.stack_bar.setStyleSheet(
                f"QProgressBar{{border:1px solid #888;border-radius:3px;text-align:center;}}"
                f"QProgressBar::chunk{{background:{color};}}")


# グループ名 -> パネルの生成関数。ここに無いグループ（監視・その他）は汎用表だけ
PANEL_FACTORIES: Dict[str, Callable[[], SummaryPanel]] = {
    "motion": MotionPanel,
    "state": StatePanel,
    "sensor": SensorPanel,
    "j10": J10Panel,
    "comm": CommPanel,
    "diag": DiagPanel,
}
