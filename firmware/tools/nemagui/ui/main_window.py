"""メインウィンドウ。out_q を QTimer で取り出して表示を更新する。表示内容の決定は nemagui.viewstate。"""

from __future__ import annotations

import html
import queue
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from nemadbg.link import Link, find_ports, open_serial

from .. import items as itemsmod
from .. import viewstate as vs
from .. import worker as wk
from ..items import Item
from ..model import Model
from .conn_bar import ConnectionBar
from .items_table import GroupTable
from .overview import OverviewPanel, overview_keys
from .sim_dock import SimDock

LOG_MAX_LINES = 5000
PUMP_INTERVAL_MS = 20
PUMP_MAX_MESSAGES = 500


@dataclass
class AppConfig:
    sim: Any = None                              # SimSerial。指定すると実機は使わない
    port: Optional[str] = None                   # --port
    timeout_ms: int = 200
    reconnect_interval_s: float = 1.0
    stats_period_s: float = 1.0
    find_ports: Callable[[], List[str]] = find_ports


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, settings: Optional[QSettings] = None) -> None:
        super().__init__()
        self.setWindowTitle("nemagui - NEMA23 ステッピングモータドライバ デバッグ" + ("（シミュレータ）" if config.sim else ""))
        self._config = config
        self._settings = settings if settings is not None else QSettings("nemadbg", "nemagui")
        self.model = Model(itemsmod.build_items())
        self._items: List[Item] = itemsmod.build_items()
        self._kind = vs.CONN_DISCONNECTED
        self._dirty = False
        self._started = False
        self._closed = False

        self.conn_bar = ConnectionBar(config.sim is not None, config.port, config.find_ports)
        self.worker = wk.Worker(self._make_link, auto_reconnect=True, reconnect_interval_s=config.reconnect_interval_s,
                                stats_period_s=config.stats_period_s)
        self._build_ui()
        self._build_shortcuts()
        if config.sim is not None:
            self.sim_dock: Optional[SimDock] = SimDock(config.sim, self.log_info, self)
            self.addDockWidget(Qt.RightDockWidgetArea, self.sim_dock)
        else:
            self.sim_dock = None

        self.worker.set_poll(vs.OVERVIEW_POLL, overview_keys(), vs.OVERVIEW_PERIOD_S)
        self._restore_settings()
        self._apply_tab_poll()

        self._timer = QTimer(self)
        self._timer.setInterval(PUMP_INTERVAL_MS)
        self._timer.timeout.connect(self.pump)
        self._timer.start()

        # 接続先が決まっているとき（--sim、--port、見つかったポートが 1 つ）は自動で接続する
        if config.sim is not None or config.port or self.conn_bar.selected_port():
            self._start_connect()

    # ---- 通信 ----

    def _make_link(self) -> Link:
        """Worker スレッドから呼ばれる。--sim は毎回同じ SimSerial を包む（unplug/replug 後も再接続できる）。"""
        t = self._config.timeout_ms / 1000.0
        if self._config.sim is not None:
            return Link(self._config.sim, timeout_s=t)
        port = self._port
        if not port:
            raise OSError("ポートが選択されていません")
        return Link(open_serial(port), timeout_s=t)

    @property
    def _port(self) -> Optional[str]:
        return self._config.port or self.conn_bar.selected_port()

    def _start_connect(self) -> None:
        if self._config.sim is None and not self._port:
            self.log_warn("ポートを選択してください")
            return
        if not self._started:
            self._started = True
            self.worker.start()
        else:
            self.worker.send(wk.Connect())

    def _disconnect(self) -> None:
        if self._started:
            self.worker.send(wk.Disconnect())
        self._set_kind(vs.CONN_DISCONNECTED)

    def send_command(self, code: wk.Cmd) -> None:
        """COMMAND を送る。ESTOP は Worker が最優先で処理する。"""
        self.worker.send(wk.Command(code))

    # ---- 組み立て ----

    def _build_ui(self) -> None:
        self.conn_bar.connect_clicked.connect(self._start_connect)
        self.conn_bar.disconnect_clicked.connect(self._disconnect)

        # 左: 非常停止 + 状態概要
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        h = self.fontMetrics().height()
        self.estop_button = QPushButton("ESTOP")
        self.estop_button.setFocusPolicy(Qt.NoFocus)
        self.estop_button.setMinimumHeight(h * 4)
        self.estop_button.setToolTip("非常停止（Esc / Space）")
        self.estop_button.setStyleSheet(
            "QPushButton{background:#d32f2f;color:white;font-weight:bold;font-size:%dpx;border:2px solid #7f0000;"
            "border-radius:6px;}QPushButton:pressed{background:#9a0007;}" % int(h * 1.9))
        self.estop_button.clicked.connect(lambda: self.send_command(wk.Cmd.ESTOP))
        self.stop_button = QPushButton("STOP")
        self.stop_button.setFocusPolicy(Qt.NoFocus)
        self.stop_button.setMinimumHeight(h * 4)
        self.stop_button.setToolTip("減速停止")
        self.stop_button.setStyleSheet(
            "QPushButton{background:#ef8f00;color:white;font-weight:bold;font-size:%dpx;border:2px solid #8a5200;"
            "border-radius:6px;}QPushButton:pressed{background:#b36b00;}" % int(h * 1.4))
        self.stop_button.clicked.connect(lambda: self.send_command(wk.Cmd.STOP))
        row = QHBoxLayout()
        row.addWidget(self.estop_button, 2)
        row.addWidget(self.stop_button, 1)
        lv.addLayout(row)
        self.overview = OverviewPanel()
        self.overview.setEnabled(False)
        lv.addWidget(self.overview, 1)

        # 中央: タブ
        self.tabs = QTabWidget()
        self._specs = vs.tab_specs()
        self._tables: Dict[str, GroupTable] = {}
        for spec in self._specs:
            if spec.group is None:
                w: QWidget = QLabel(spec.note)
                w.setAlignment(Qt.AlignCenter)
            else:
                t = GroupTable()
                t.set_items([i for i in self._items if i.group == spec.group])
                self._tables[spec.group] = t
                w = t
            self.tabs.addTab(w, spec.label)
        self.tabs.currentChanged.connect(lambda _i: self._apply_tab_poll())

        upper = QSplitter(Qt.Horizontal)
        upper.addWidget(left)
        upper.addWidget(self.tabs)
        upper.setStretchFactor(0, 0)
        upper.setStretchFactor(1, 1)
        upper.setSizes([self.fontMetrics().horizontalAdvance("0" * 40), 900])

        # 下: ログ
        logbox = QWidget()
        lg = QVBoxLayout(logbox)
        lg.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("ログ"))
        top.addStretch(1)
        self.clear_button = QPushButton("クリア")
        top.addWidget(self.clear_button)
        lg.addLayout(top)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_MAX_LINES)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.clear_button.clicked.connect(self.log.clear)
        lg.addWidget(self.log)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(upper)
        vsplit.addWidget(logbox)
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 0)
        vsplit.setSizes([620, 160])

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.addWidget(self.conn_bar)
        cv.addWidget(vsplit, 1)
        self.setCentralWidget(central)

        self.stats_label = QLabel("")
        self.statusBar().addWidget(self.stats_label, 1)

    def _build_shortcuts(self) -> None:
        # どのウィジェットにフォーカスがあっても効くようにアプリケーション全体で受ける
        # （テキスト入力中の Space は入力欄が先に処理するため ESTOP にならない）
        self._shortcuts = []
        for key in (Qt.Key_Escape, Qt.Key_Space):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(lambda: self.send_command(wk.Cmd.ESTOP))
            self._shortcuts.append(sc)

    # ---- 設定の保存 ----

    def _restore_settings(self) -> None:
        geo = self._settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1280, 800)
        try:
            idx = int(self._settings.value("tab", 0))
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    def _save_settings(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("tab", self.tabs.currentIndex())
        self._settings.sync()

    # ---- タブとポーリング ----

    @property
    def active_group(self) -> Optional[str]:
        """表示中のタブが項目表のとき、そのグループ名。"""
        return self._specs[self.tabs.currentIndex()].group

    def _apply_tab_poll(self) -> None:
        """表示中の項目表のグループだけを "tab" としてポーリングする。離れたら解除する。"""
        g = self.active_group
        keys = vs.group_keys(self._items, g) if g else []
        if g and keys:
            self.worker.set_poll(vs.TAB_POLL, keys, vs.POLL_PERIODS[g])
            self._tables[g].refresh(self.model)
        else:
            self.worker.clear_poll(vs.TAB_POLL)

    # ---- out_q の処理 ----

    def pump(self) -> None:
        for _ in range(PUMP_MAX_MESSAGES):
            try:
                msg = self.worker.out_q.get_nowait()
            except queue.Empty:
                break
            self.handle(msg)
        if self._dirty:
            self._dirty = False
            self.overview.refresh(self.model)
            g = self.active_group
            if g:
                self._tables[g].refresh(self.model)

    def handle(self, msg: Any) -> None:
        if isinstance(msg, wk.Values):
            self.model.update(msg.timestamp, msg.values)
            self._dirty = True
            return
        if isinstance(msg, wk.Stats):
            self.stats_label.setText(vs.format_stats(msg))
            return
        if isinstance(msg, wk.Connected):
            self.model.reset(msg.items)
            self._items = list(msg.items)
            for g, t in self._tables.items():
                t.set_items([i for i in self._items if i.group == g])
            self._set_kind(vs.CONN_CONNECTED)
            self.conn_bar.set_info(vs.device_info_text(msg))
            self._apply_tab_poll()
            self._dirty = True
        elif isinstance(msg, wk.Disconnected):
            self._set_kind(vs.conn_kind(msg))
        for e in vs.log_entries(msg):
            self._append_log(e)

    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        self.conn_bar.set_kind(kind)
        self.overview.setEnabled(kind == vs.CONN_CONNECTED)

    @property
    def conn_kind(self) -> str:
        return self._kind

    # ---- ログ ----

    def _append_log(self, e: vs.LogEntry) -> None:
        color = vs.LEVEL_COLORS[e.level]
        self.log.appendHtml(f'<span style="color:{color}">[{vs.stamp()}] {html.escape(e.text)}</span>')

    def log_info(self, text: str) -> None:
        self._append_log(vs.LogEntry(text))

    def log_warn(self, text: str) -> None:
        self._append_log(vs.LogEntry(text, vs.LEVEL_WARN))

    # ---- 終了 ----

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self._save_settings()
            self.worker.stop(join_timeout=3)
        super().closeEvent(event)
