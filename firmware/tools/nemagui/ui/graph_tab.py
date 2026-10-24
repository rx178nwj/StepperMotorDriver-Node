"""グラフタブ。選んだ項目の履歴（Model.history）を pyqtgraph で描き、CSV 記録も受け持つ。

ポーリング組 "graph" の登録は MainWindow が行う（選択や周期が変わると poll_changed、記録の開始・停止で
recording_changed を出す）。系列の振り分け・時間窓・プリセットなどの判断は nemagui.graphmodel。
"""

from __future__ import annotations

import time
from datetime import datetime
from html import escape
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import graphmodel as gm
from .. import items as itemsmod
from ..items import Item
from ..model import Model

RENDER_INTERVAL_MS = 50          # 20 fps
PLOT_MIN_HEIGHT = 90
LEFT_AXIS_WIDTH = 70

# 保存ダイアログ: (題名, 既定のファイル名, フィルタ) -> パス（取り消しは None）。試験で差し替える
SaveDialog = Callable[[str, str, str], Optional[str]]


def default_save_dialog(parent: QWidget) -> SaveDialog:
    def ask(title: str, name: str, filt: str) -> Optional[str]:
        path, _f = QFileDialog.getSaveFileName(parent, title, name, filt)
        return path or None
    return ask


class GraphTab(QWidget):
    poll_changed = Signal()               # 選択中の系列または周期が変わった
    recording_changed = Signal(bool)      # CSV 記録を開始した／止めた
    rec_status_changed = Signal(str)      # 記録中の表示文字列（止めると空）

    def __init__(self, model: Model, items: Sequence[Item], save_dialog: Optional[SaveDialog] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = model
        self._items: Dict[str, Item] = itemsmod.items_by_key(items)
        self._watch_keys: List[str] = []
        self._selected: List[str] = []
        self._save_dialog: SaveDialog = save_dialog or default_save_dialog(self)
        self._window_s = float(gm.DEFAULT_WINDOW_S)
        self._paused = False
        self._pause_now = 0.0
        self._t_clear: Optional[float] = None
        self._connected = False
        self._recording = False
        self._rec_t0 = 0.0
        self._rec_path = ""
        self._rec_text = ""
        self._cursor_x: Optional[float] = None
        self._plots: List[pg.PlotItem] = []
        self._lines: List[pg.InfiniteLine] = []
        self._specs: List[gm.PlotSpec] = []
        self._curves: Dict[str, pg.PlotDataItem] = {}
        self._cache: Dict[str, Tuple[List[float], List[float]]] = {}
        self._list_items: Dict[str, QListWidgetItem] = {}
        self.last_render_ms = 0.0
        self.render_count = 0

        pg.setConfigOptions(background="w", foreground="k", antialias=False)
        self._build_ui()
        self._rebuild_list()
        self._rebuild_plots()
        self._timer = QTimer(self)
        self._timer.setInterval(RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---- 組み立て ----

    def _build_ui(self) -> None:
        # 左: 系列の選択
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.select_box = QWidget()
        sv = QVBoxLayout(self.select_box)
        sv.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("プリセット…")
        for n in gm.preset_names():
            self.preset_combo.addItem(n)
        self.preset_combo.activated.connect(self._on_preset)
        self.clear_sel_button = QPushButton("選択解除")
        self.clear_sel_button.clicked.connect(lambda: self.set_selected([]))
        row.addWidget(self.preset_combo, 1)
        row.addWidget(self.clear_sel_button)
        sv.addLayout(row)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("検索（表示名・key・単位）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        sv.addWidget(self.filter_edit)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._on_list_changed)
        sv.addWidget(self.list, 1)
        lv.addWidget(self.select_box, 1)

        # 記録
        self.rec_button = QPushButton("記録開始")
        self.rec_button.setToolTip("選択中の系列を CSV に記録する（記録中は系列を変更できない）")
        self.rec_button.clicked.connect(self._toggle_recording)
        self.rec_label = QLabel("")
        self.rec_label.setWordWrap(True)
        lv.addWidget(self.rec_button)
        lv.addWidget(self.rec_label)

        # 右: 操作列 + グラフ + 読み取り
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("時間窓"))
        self.window_combo = QComboBox()
        for w in gm.WINDOWS_S:
            self.window_combo.addItem(f"{w} s", w)
        self.window_combo.setCurrentIndex(gm.WINDOWS_S.index(gm.DEFAULT_WINDOW_S))
        self.window_combo.currentIndexChanged.connect(self._on_window)
        bar.addWidget(self.window_combo)
        bar.addWidget(QLabel("周期"))
        self.period_spin = QSpinBox()
        self.period_spin.setRange(int(gm.GRAPH_PERIOD_MIN_S * 1000), int(gm.GRAPH_PERIOD_MAX_S * 1000))
        self.period_spin.setSingleStep(10)
        self.period_spin.setSuffix(" ms")
        self.period_spin.setValue(int(gm.GRAPH_PERIOD_S * 1000))
        self.period_spin.valueChanged.connect(lambda _v: self.poll_changed.emit())
        bar.addWidget(self.period_spin)
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("表示を止める（データ取得は続く）。停止中はドラッグ・ホイールで移動・拡大縮小できる")
        self.pause_button.toggled.connect(self._on_pause_toggled)
        bar.addWidget(self.pause_button)
        self.clear_button = QPushButton("クリア")
        self.clear_button.setToolTip("表示の開始時刻を今にする（履歴そのものは消さない）")
        self.clear_button.clicked.connect(self.clear)
        bar.addWidget(self.clear_button)
        self.png_button = QPushButton("PNG 保存")
        self.png_button.clicked.connect(self.save_png)
        bar.addWidget(self.png_button)
        bar.addStretch(1)
        rv.addLayout(bar)

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("w")
        self.glw.scene().sigMouseMoved.connect(self._on_mouse)
        rv.addWidget(self.glw, 1)
        self.readout = QLabel("")
        self.readout.setWordWrap(True)
        self.readout.setTextFormat(Qt.RichText)
        self.readout.setMinimumHeight(self.fontMetrics().height() * 2 + 4)
        rv.addWidget(self.readout)

        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)
        sp.setSizes([self.fontMetrics().horizontalAdvance("0" * 36), 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sp)
        self._update_rec_ui()

    # ---- 公開 API ----

    def selected_keys(self) -> List[str]:
        return list(self._selected)

    @property
    def period_s(self) -> float:
        return self.period_spin.value() / 1000.0

    @property
    def window_s(self) -> float:
        return self._window_s

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def plot_specs(self) -> List[gm.PlotSpec]:
        return list(self._specs)

    def curve_points(self, key: str) -> int:
        """key の曲線に今描かれている点の数（試験・確認用）。"""
        c = self._curves.get(key)
        if c is None or c.xData is None:
            return 0
        return len(c.xData)

    def set_items(self, items: Sequence[Item]) -> None:
        """接続ごとに項目を差し替える。無くなった項目は選択から外す。記録中でなければ選択も更新する。"""
        self._items = itemsmod.items_by_key(items)
        before = list(self._selected)
        self._selected = [k for k in self._selected if k in self._items and self._items[k].numeric]
        self._rebuild_list()
        self._rebuild_plots()
        if self._selected != before:
            self.poll_changed.emit()

    def set_watch_keys(self, keys: Sequence[str]) -> None:
        self._watch_keys = list(keys)
        self._rebuild_list()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.stop_recording()
        self._update_rec_ui()

    def apply_preset(self, name: str) -> List[str]:
        keys = gm.resolve_preset(name, self._items)
        keys = [k for k in keys if self._items[k].numeric]
        self.set_selected(keys)
        return keys

    def set_selected(self, keys: Sequence[str]) -> bool:
        """選択を置き換える。記録中は変更できず False。"""
        if self._recording:
            return False
        new: List[str] = []
        for k in keys:
            it = self._items.get(k)
            if it is not None and it.numeric and k not in new:
                new.append(k)
        if new == self._selected:
            return True
        self._selected = new
        self._sync_checks()
        self._rebuild_plots()
        self._update_rec_ui()
        self.poll_changed.emit()
        return True

    def set_window(self, seconds: int) -> None:
        if seconds in gm.WINDOWS_S:
            self.window_combo.setCurrentIndex(gm.WINDOWS_S.index(seconds))

    def set_paused(self, paused: bool) -> None:
        self.pause_button.setChecked(paused)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._pause_now = time.time()
            self._render()                                   # 履歴の全体を、停止時刻を 0 として描く
        for p in self._plots:
            p.setMouseEnabled(x=paused, y=paused)
            p.enableAutoRange("y", True)
        self._apply_x_range()
        if not paused:
            self._render()

    def clear(self) -> None:
        """表示の開始時刻を今にする。model の履歴は消さない。"""
        self._t_clear = time.time()
        if self._paused:
            self._pause_now = self._t_clear
        self._render()

    def set_cursor(self, x: Optional[float]) -> None:
        """カーソルの縦線を相対時刻 x [s] に置く（None で消す）。"""
        self._cursor_x = x
        for ln in self._lines:
            if x is None:
                ln.hide()
            else:
                ln.setPos(x)
                ln.show()
        self._update_readout()

    def readout_text(self) -> str:
        return self.readout.text()

    # ---- 描画 ----

    def _tick(self) -> None:
        if self._recording:
            self._update_rec_ui()
        if not self.isVisible() or self._paused or not self._selected:
            return
        self._render()

    def _hist_span(self) -> float:
        return float(self.model.history_s)

    def _render(self) -> None:
        t0 = time.perf_counter()
        now = self._pause_now if self._paused else time.time()
        window = self._hist_span() if self._paused else self._window_s
        for spec in self._specs:
            for k in spec.keys:
                ts, vs_ = self.model.history(k)
                xs, ys = gm.window_slice(ts, vs_, now, window, self._t_clear)
                self._cache[k] = (xs, ys)
                if spec.is_state:
                    px, py = gm.step_xy(xs, ys, 0.0)
                else:
                    px, py = xs, ys
                self._curves[k].setData(px, py)
        self._update_readout()
        self.last_render_ms = (time.perf_counter() - t0) * 1000.0
        self.render_count += 1

    def _apply_x_range(self) -> None:
        if self._plots:
            self._plots[0].setXRange(-self._window_s, 0, padding=0)

    def _on_window(self, _i: int) -> None:
        self._window_s = float(self.window_combo.currentData())
        self._apply_x_range()
        if self._paused:
            self._render()

    def _series_name(self, key: str) -> str:
        it = self._items[key]
        dup = sum(1 for k in self._selected if self._items[k].label == it.label) > 1
        return f"{it.label} ({key})" if dup else it.label

    def _rebuild_plots(self) -> None:
        self.glw.clear()
        self._plots, self._lines, self._curves, self._cache = [], [], {}, {}
        self._specs = gm.plot_layout(self._items, self._selected)
        n = len(self._specs)
        for r, spec in enumerate(self._specs):
            p = self.glw.addPlot(row=r, col=0)
            p.setMinimumHeight(PLOT_MIN_HEIGHT)
            p.showGrid(x=True, y=True, alpha=0.3)
            p.setLabel("left", spec.title)
            p.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
            p.getAxis("left").enableAutoSIPrefix(False)     # 「(x0.001)」のような接頭辞を付けない
            p.setDownsampling(auto=True, mode="peak")
            p.setClipToView(True)
            p.hideButtons()
            p.vb.setAutoVisible(y=True)
            p.enableAutoRange("y", True)
            p.setMouseEnabled(x=self._paused, y=self._paused)
            p.addLegend(offset=(5, 5))
            if r < n - 1:
                p.getAxis("bottom").setStyle(showValues=False)
            else:
                p.setLabel("bottom", "時刻（現在からの相対）[s]")
            if r > 0:
                p.setXLink(self._plots[0])
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#555555", width=1, style=Qt.DashLine))
            p.addItem(line, ignoreBounds=True)
            line.setVisible(self._cursor_x is not None)
            if self._cursor_x is not None:
                line.setPos(self._cursor_x)
            self._plots.append(p)
            self._lines.append(line)
            for k in spec.keys:
                color = gm.series_color(self._selected.index(k))
                self._curves[k] = p.plot(pen=pg.mkPen(color, width=1), name=self._series_name(k))
        self._apply_x_range()
        if self._specs:
            self._render()
        else:
            self.readout.setText("")

    # ---- カーソル ----

    def _on_mouse(self, pos) -> None:   # type: ignore[no-untyped-def]
        for p in self._plots:
            if p.vb.sceneBoundingRect().contains(pos):
                self.set_cursor(float(p.vb.mapSceneToView(pos).x()))
                return

    def _update_readout(self) -> None:
        x = self._cursor_x
        if x is None or not self._specs:
            self.readout.setText("")
            return
        parts = [f"t = {x:+.2f} s"]
        for k in self._selected:
            c = self._cache.get(k)
            if c is None:
                continue
            v = gm.value_at(c[0], c[1], x)
            it = self._items[k]
            text = "—" if v is None else itemsmod.format_value(it, v)
            color = gm.series_color(self._selected.index(k))
            parts.append(f'<span style="color:{color}">■</span> {escape(self._series_name(k))}: {escape(text)}')
        self.readout.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    # ---- 選択リスト ----

    def _rebuild_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._list_items = {}
        watch = set(self._watch_keys)
        for it in gm.candidate_order(self._items.values(), self._watch_keys):
            star = "★ " if it.key in watch else ""
            unit = f" [{it.unit}]" if it.unit else ""
            li = QListWidgetItem(f"{star}{it.label}{unit}   {it.key}")
            li.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            li.setCheckState(Qt.Checked if it.key in self._selected else Qt.Unchecked)
            li.setData(Qt.UserRole, it.key)
            self.list.addItem(li)
            self._list_items[it.key] = li
        self.list.blockSignals(False)
        self._apply_filter(self.filter_edit.text())

    def _apply_filter(self, text: str) -> None:
        for key, li in self._list_items.items():
            li.setHidden(not gm.matches_filter(self._items[key], text))

    def _sync_checks(self) -> None:
        self.list.blockSignals(True)
        for key, li in self._list_items.items():
            li.setCheckState(Qt.Checked if key in self._selected else Qt.Unchecked)
        self.list.blockSignals(False)

    def _on_list_changed(self, li: QListWidgetItem) -> None:
        key = li.data(Qt.UserRole)
        if self._recording:
            self._sync_checks()
            return
        sel = [k for k in self._selected if k != key]
        if li.checkState() == Qt.Checked:
            sel.append(key)
        self.set_selected(sel)

    def _on_preset(self, index: int) -> None:
        if index > 0:
            self.apply_preset(self.preset_combo.itemText(index))
        self.preset_combo.setCurrentIndex(0)

    # ---- 記録 ----

    def _toggle_recording(self) -> None:
        if self._recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> bool:
        """保存先を尋ねて CSV 記録を始める。取り消し・系列なし・未接続・書き込めないときは False。"""
        if self._recording or not self._selected or not self._connected:
            return False
        path = self._save_dialog("CSV 記録", gm.default_csv_name(datetime.now()), "CSV (*.csv)")
        if not path:
            return False
        try:
            self.model.start_csv(path, self._selected)
        except OSError as e:
            self.rec_label.setText(f"記録を開始できません: {e}")
            return False
        self._recording = True
        self._rec_path = path
        self._rec_t0 = time.monotonic()
        self.select_box.setEnabled(False)
        self._update_rec_ui()
        self.recording_changed.emit(True)
        return True

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self.model.stop_csv()
        self._recording = False
        self.select_box.setEnabled(True)
        self._update_rec_ui()
        self.recording_changed.emit(False)

    def _update_rec_ui(self) -> None:
        if self._recording:
            name = self._rec_path.replace("\\", "/").rsplit("/", 1)[-1]
            text = (f"記録中 {gm.format_elapsed(time.monotonic() - self._rec_t0)}  {self.model.csv_rows} 行\n{name}")
            self.rec_button.setText("記録停止")
            self.rec_button.setEnabled(True)
            self.rec_label.setText(text)
            self.rec_label.setToolTip(self._rec_path)
            status = text.split("\n")[0].replace("記録中", "● 記録中")
        else:
            self.rec_button.setText("記録開始")
            self.rec_button.setEnabled(bool(self._selected) and self._connected)
            if not self.rec_label.text().startswith("記録を開始できません"):
                self.rec_label.setText("")
            status = ""
        if status != self._rec_text:
            self._rec_text = status
            self.rec_status_changed.emit(status)

    # ---- 画像 ----

    def save_png(self) -> bool:
        path = self._save_dialog("PNG 保存", gm.default_png_name(datetime.now()), "PNG (*.png)")
        if not path:
            return False
        return bool(self.glw.grab().save(path, "PNG"))
