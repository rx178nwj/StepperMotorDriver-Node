"""メインウィンドウ。out_q を QTimer で取り出して表示を更新する。表示内容の決定は nemagui.viewstate。"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import (QCloseEvent, QColor, QFont, QFontDatabase, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from nemadbg.link import Link, find_ports, open_serial

from .. import graphmodel as gm
from .. import items as itemsmod
from .. import summary as sm
from .. import viewstate as vs
from .. import worker as wk
from ..items import Item
from ..model import Model
from .conn_bar import ConnectionBar
from .graph_tab import GraphTab, SaveDialog
from .items_table import GroupTable
from .ops_tab import OpsTab
from .overview import OverviewPanel, overview_keys
from .panels import PANEL_FACTORIES, SummaryPanel
from .settings_tab import CONFIG_STATUS_KEY, SettingsTab
from .sim_dock import SimDock
from .sym_browser import SymBrowser
from .watch_dock import WatchDock

LOG_MAX_LINES = 5000
PUMP_INTERVAL_MS = 20
PUMP_MAX_MESSAGES = 500


LOG_FONT_CANDIDATES = ("Yu Gothic UI", "Meiryo UI", "MS UI Gothic", "Segoe UI")


def log_font(base: QFont) -> QFont:
    """ログ用フォント。英数字と日本語を 1 つの実在するフォントで描かせる。

    app.font() の先頭が実在しない総称名（"Sans Serif"。offscreen など）だと、ASCII だけの行は小さな代替フォント、
    日本語を含む行は日本語フォント（先頭の記号・数字もこちら）で描かれ、行ごとに大きさが変わって見える。
    """
    have = set(QFontDatabase.families())
    f = QFont(base)
    for fam in LOG_FONT_CANDIDATES:
        if fam in have:
            f.setFamilies([fam])
            break
    return f


@dataclass
class AppConfig:
    sim: Any = None                              # SimSerial。指定すると実機は使わない
    port: Optional[str] = None                   # --port
    timeout_ms: int = 200
    reconnect_interval_s: float = 1.0
    stats_period_s: float = 1.0
    find_ports: Callable[[], List[str]] = find_ports
    confirm: Optional[Callable[[str, str], bool]] = None   # 設定タブの確認ダイアログ（試験で差し替える）
    save_dialog: Optional[SaveDialog] = None               # グラフタブの保存先ダイアログ（試験で差し替える）


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
        self.watch_dock = WatchDock(self._items, sm.decode_watch_keys(self._settings.value("watch/keys", "")),
                                    sm.clamp_watch_period(self._settings.value("watch/period_s", sm.WATCH_PERIOD_S)),
                                    self)
        self.watch_dock.changed.connect(self._on_watch_changed)
        self.graph_tab.set_watch_keys(self.watch_dock.keys())
        self.watch_dock.period_changed.connect(lambda _p: self._apply_watch_poll())
        self.addDockWidget(Qt.RightDockWidgetArea, self.watch_dock)
        if config.sim is not None:
            self.sim_dock: Optional[SimDock] = SimDock(config.sim, self.log_info, self)
            self.addDockWidget(Qt.RightDockWidgetArea, self.sim_dock)
        else:
            self.sim_dock = None
        view = self.menuBar().addMenu("表示")
        view.addAction(self.watch_dock.toggleViewAction())
        if self.sim_dock is not None:
            view.addAction(self.sim_dock.toggleViewAction())
        for t in self._tables.values():
            t.add_to_watch.connect(self.add_to_watch)
        self.sym_browser.add_to_watch.connect(self.add_to_watch)

        self.worker.set_poll(vs.OVERVIEW_POLL, overview_keys(), vs.OVERVIEW_PERIOD_S)
        self._restore_settings()
        self._apply_tab_poll()
        self._apply_watch_poll()

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
        self.ops_tab = OpsTab(self.worker.send)
        self.ops_tab.setEnabled(False)
        self.settings_tab = SettingsTab(self.worker.send, [i for i in self._items if i.group == "settings"],
                                        self._config.confirm)
        self.tabs = QTabWidget()
        self._specs = vs.tab_specs()
        self.sym_browser = SymBrowser(self.worker.send)
        self.graph_tab = GraphTab(self.model, self._items, self._config.save_dialog)
        self.graph_tab.poll_changed.connect(self._apply_graph_poll)
        self.graph_tab.recording_changed.connect(lambda _r: self._apply_graph_poll())
        self.graph_tab.rec_status_changed.connect(self._on_rec_status)
        self._tables: Dict[str, GroupTable] = {}
        self._panels: Dict[str, SummaryPanel] = {}
        for spec in self._specs:
            if spec.kind == vs.TAB_OPS:
                w: QWidget = self.ops_tab
            elif spec.kind == vs.TAB_SETTINGS:
                w = self.settings_tab
            elif spec.kind == vs.TAB_SYMS:
                w = self.sym_browser
            elif spec.kind == vs.TAB_GRAPH:
                w = self.graph_tab
            elif spec.group is None:
                w = QLabel(spec.note)
                w.setAlignment(Qt.AlignCenter)
            else:
                t = GroupTable()
                t.set_items([i for i in self._items if i.group == spec.group])
                self._tables[spec.group] = t
                factory = PANEL_FACTORIES.get(spec.group)
                if factory is None:
                    w = t
                else:
                    # 上部に要点パネル、下に詳細の汎用項目表
                    panel = factory()
                    self._panels[spec.group] = panel
                    sp = QSplitter(Qt.Vertical)
                    sp.setChildrenCollapsible(False)
                    sp.addWidget(panel)
                    sp.addWidget(t)
                    sp.setStretchFactor(0, 0)
                    sp.setStretchFactor(1, 1)
                    w = sp
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
        self.log.setFont(log_font(self.log.font()))
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
        self.rec_label = QLabel("")
        self.rec_label.setStyleSheet("color:#d32f2f;font-weight:bold;")
        self.rec_label.setVisible(False)
        self.statusBar().addPermanentWidget(self.rec_label)

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
        self._save_watch()
        self._settings.sync()

    # ---- タブとポーリング ----

    @property
    def active_group(self) -> Optional[str]:
        """表示中のタブが項目表のとき、そのグループ名。"""
        return self._specs[self.tabs.currentIndex()].group

    @property
    def active_kind(self) -> str:
        """表示中のタブの種類（vs.TAB_OPS など。項目表なら空）。"""
        return self._specs[self.tabs.currentIndex()].kind

    def _apply_tab_poll(self) -> None:
        """表示中のタブに応じたポーリングを設定し、離れたものは解除する。

        項目表は "tab"、操作タブは "ops"、設定タブは "settings"。
        """
        kind = self.active_kind
        if kind == vs.TAB_OPS:
            self.worker.set_poll(vs.OPS_POLL, vs.OPS_POLL_KEYS, vs.OPS_PERIOD_S)
        else:
            self.worker.clear_poll(vs.OPS_POLL)
        if kind == vs.TAB_SETTINGS:
            keys = [i.key for i in self.settings_tab.edit.items] + [CONFIG_STATUS_KEY]
            self.worker.set_poll(vs.SETTINGS_POLL, keys, vs.SETTINGS_PERIOD_S)
            self.settings_tab.refresh(self.model)
        else:
            self.worker.clear_poll(vs.SETTINGS_POLL)
        if kind == vs.TAB_OPS:
            self.ops_tab.refresh(self.model, self._kind == vs.CONN_CONNECTED)
        g = self.active_group
        keys = self._tab_poll_keys(g) if g else []
        if g and keys:
            self.worker.set_poll(vs.TAB_POLL, keys, vs.POLL_PERIODS[g])
            self._tables[g].refresh(self.model)
            if g in self._panels:
                self._panels[g].refresh(self.model)
        else:
            self.worker.clear_poll(vs.TAB_POLL)
        self._apply_graph_poll()

    def _apply_graph_poll(self) -> None:
        """ポーリング組 "graph"。グラフタブの表示中、または CSV 記録中に、選択中の系列を読む。

        タブを離れても記録中なら続ける。表示も記録もなければ、または系列が空なら外す。
        """
        keys = self.graph_tab.selected_keys()
        if keys and (self.active_kind == vs.TAB_GRAPH or self.graph_tab.recording):
            self.worker.set_poll(gm.GRAPH_POLL, keys, self.graph_tab.period_s)
        else:
            self.worker.clear_poll(gm.GRAPH_POLL)

    def _on_rec_status(self, text: str) -> None:
        self.rec_label.setText(text)
        self.rec_label.setVisible(bool(text))

    def _tab_poll_keys(self, group: str) -> List[str]:
        """グループの項目に、要点パネルが他のグループから使う項目（hold_active など）を足した key。"""
        keys = vs.group_keys(self._items, group)
        panel = self._panels.get(group)
        if panel is not None:
            have = {i.key for i in self._items}
            keys += [k for k in panel.keys if k not in keys and k in have]
        return keys

    # ---- ウォッチ ----

    def add_to_watch(self, key: str) -> None:
        """項目をウォッチに追加する（汎用項目表の右クリック・名前表ブラウザから）。"""
        self.watch_dock.add(key)

    def _on_watch_changed(self, keys: list) -> None:
        self._apply_watch_poll()
        self.graph_tab.set_watch_keys(keys)
        self._save_watch()
        self._dirty = True

    def _apply_watch_poll(self) -> None:
        """ポーリング組 "watch" を設定する。ウォッチが空なら外す。"""
        keys = self.watch_dock.keys()
        if keys:
            self.worker.set_poll(sm.WATCH_POLL, keys, self.watch_dock.period_s)
        else:
            self.worker.clear_poll(sm.WATCH_POLL)

    def _save_watch(self) -> None:
        self._settings.setValue("watch/keys", self.watch_dock.to_text())
        self._settings.setValue("watch/period_s", self.watch_dock.period_s)

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
            self._refresh_active()
            self.watch_dock.refresh(self.model)

    def _refresh_active(self) -> None:
        kind = self.active_kind
        if kind == vs.TAB_OPS:
            self.ops_tab.refresh(self.model, self._kind == vs.CONN_CONNECTED)
        elif kind == vs.TAB_SETTINGS:
            self.settings_tab.refresh(self.model)
        g = self.active_group
        if g:
            self._tables[g].refresh(self.model)
            if g in self._panels:
                self._panels[g].refresh(self.model)

    def handle(self, msg: Any) -> None:
        if isinstance(msg, wk.Values):
            self.model.update(msg.timestamp, msg.values)
            self._dirty = True
            return
        if isinstance(msg, wk.ReadResult):
            if msg.values:
                self.model.update(msg.timestamp, msg.values)
                self._dirty = True
            if SettingsTab.owns(msg.request.tag):
                self.settings_tab.on_read_result(msg, self.model)
            elif SymBrowser.owns(msg.request.tag):
                self.sym_browser.on_read_result(msg)
            for text in msg.errors:
                self.log_warn("読み出し失敗: " + text)
            return
        if isinstance(msg, wk.Stats):
            self.stats_label.setText(vs.format_stats(msg))
            return
        if isinstance(msg, wk.Connected):
            self.model.reset(msg.items)
            self._items = list(msg.items)
            for g, t in self._tables.items():
                t.set_items([i for i in self._items if i.group == g])
            self.settings_tab.set_items([i for i in self._items if i.group == "settings"])
            self.sym_browser.set_symtab(msg.symtab, self._items)
            self.watch_dock.set_items(self._items)
            self.graph_tab.set_items(self._items)
            self._set_kind(vs.CONN_CONNECTED)
            self.conn_bar.set_info(vs.device_info_text(msg))
            self._apply_tab_poll()
            self._dirty = True
        elif isinstance(msg, wk.Disconnected):
            self._set_kind(vs.conn_kind(msg))
        elif isinstance(msg, wk.WriteResult) and SettingsTab.owns(msg.request.tag):
            self.settings_tab.on_write_result(msg)
        for e in vs.log_entries(msg):
            self._append_log(e)

    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        self.conn_bar.set_kind(kind)
        connected = kind == vs.CONN_CONNECTED
        self.overview.setEnabled(connected)
        self.ops_tab.setEnabled(connected)
        self.settings_tab.set_connected(connected)
        self.sym_browser.set_connected(connected)
        self.graph_tab.set_connected(connected)
        self._dirty = True

    @property
    def conn_kind(self) -> str:
        return self._kind

    # ---- ログ ----

    def _append_log(self, e: vs.LogEntry) -> None:
        """1 行追加する。HTML は使わず、色（前景色）だけを書式に持たせる。

        appendHtml だと HTML 取り込みでフォントの扱いが行ごとに変わりうる。ここでは文字書式にフォントを含めない
        ので、全行が log のフォント（log_font）で描かれる。
        """
        sb = self.log.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 2
        cur = QTextCursor(self.log.document())
        cur.movePosition(QTextCursor.End)
        if cur.position() > 0:
            cur.insertBlock(QTextBlockFormat(), QTextCharFormat())
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(vs.LEVEL_COLORS[e.level]))
        cur.insertText(f"[{vs.stamp()}] {e.text}", fmt)
        if follow:
            sb.setValue(sb.maximum())

    def log_info(self, text: str) -> None:
        self._append_log(vs.LogEntry(text))

    def log_warn(self, text: str) -> None:
        self._append_log(vs.LogEntry(text, vs.LEVEL_WARN))

    # ---- 終了 ----

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self.pump()                      # 届いている分を取り込んでから記録を閉じる
            self.graph_tab.stop_recording()
            self._save_settings()
            self.worker.stop(join_timeout=3)
        super().closeEvent(event)
