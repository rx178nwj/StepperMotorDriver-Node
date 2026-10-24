"""nemagui の画面の試験（PySide6 が無ければスキップ）。offscreen で擬似デバイス（SimSerial）につなぐ。"""

import os
import tempfile
import time
import unittest
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QTextFormat
    from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSpinBox
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from nemadbg.sim import SimSerial
from nemagui import summary as sm
from nemagui import viewstate as vs
from nemagui import worker as wk


@unittest.skipUnless(HAVE_QT, "PySide6 がありません")
class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from nemagui.ui.main_window import AppConfig, MainWindow

        self.tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self.tmp.name, "s.ini")
        self.sim = SimSerial(noise=False, warmup_ms=200)
        self.cfg = AppConfig(sim=self.sim, timeout_ms=100, reconnect_interval_s=0.1, stats_period_s=0.2)
        self.win = MainWindow(self.cfg, QSettings(self.settings_path, QSettings.IniFormat))
        self.win.show()

    def tearDown(self) -> None:
        self.win.close()
        self.assertFalse(self.win.worker.is_alive())
        self.tmp.cleanup()

    def wait_for(self, pred: Callable[[], bool], timeout: float = 4.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if pred():
                return
            time.sleep(0.005)
        self.fail("条件が満たされませんでした。ログ:\n" + self.win.log.toPlainText())

    def state_text(self) -> str:
        return self.win.overview._widgets["reg.STATE"].text()

    def connect_up(self) -> None:
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_CONNECTED and self.state_text() == "DISABLED")

    def test_connect_and_commands(self) -> None:
        self.connect_up()
        self.assertEqual(self.win.conn_bar.state_label.text(), "接続中")
        self.assertIn("BOARD_ID 3", self.win.conn_bar.info_label.text())
        self.assertTrue(self.win.overview.isEnabled())
        self.assertEqual(self.win.tabs.count(), 12)

        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: "COMMAND ENABLE: OK" in self.win.log.toPlainText())
        self.wait_for(lambda: self.state_text() == "ENABLED")
        self.win.estop_button.click()
        self.wait_for(lambda: "COMMAND ESTOP: OK" in self.win.log.toPlainText())

    def test_shortcuts(self) -> None:
        self.connect_up()
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.state_text() == "ENABLED")
        self.win.sim_dock.vm_spin.setFocus()   # 入力欄にフォーカスがあっても Esc は効く
        QTest.keyClick(self.win, Qt.Key_Escape)
        self.wait_for(lambda: "COMMAND ESTOP: OK" in self.win.log.toPlainText())
        n = self.win.log.toPlainText().count("COMMAND ESTOP")
        self.win.tabs.setFocus()
        QTest.keyClick(self.win, Qt.Key_Space)
        self.wait_for(lambda: self.win.log.toPlainText().count("COMMAND ESTOP") > n)

    def test_fault_event_and_lamps(self) -> None:
        self.connect_up()
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.state_text() == "ENABLED")
        self.win.sim_dock._inject("OPD", 0x01)
        self.wait_for(lambda: self.state_text() == "FAULT")
        self.wait_for(lambda: "異常通知" in self.win.log.toPlainText())
        fault_lamps = self.win.overview._widgets["reg.FAULT"]
        self.wait_for(lambda: any(s is True for s in fault_lamps._shown))

    def test_tab_polls(self) -> None:
        self.connect_up()
        polls = self.win.worker._polls
        self.assertIn("overview", polls)
        names = {s.group: n for n, s in enumerate(self.win._specs)}
        self.win.tabs.setCurrentIndex(0)
        self.assertNotIn("tab", polls)
        self.win.tabs.setCurrentIndex(names["motion"])
        keys, period = polls["tab"]
        self.assertIn("sym.position", keys)
        self.assertEqual(period, 0.05)
        self.wait_for(lambda: self.win._tables["motion"].item(0, 1).text() != "—")
        self.win.tabs.setCurrentIndex(names["monitor"])
        keys, period = polls["tab"]
        self.assertIn("sym.mon.vm_mv", keys)
        self.assertNotIn("sym.position", keys)
        self.assertEqual(period, 0.2)
        self.win.tabs.setCurrentIndex(self.win.tabs.count() - 1)   # グラフ（プレースホルダ）
        self.assertNotIn("tab", polls)
        self.assertIsNone(self.win.active_group)

    def test_unplug_and_replug(self) -> None:
        self.connect_up()
        self.sim.unplug()
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_WAITING)
        self.assertEqual(self.win.conn_bar.state_label.text(), "再接続待ち")
        self.assertFalse(self.win.overview.isEnabled())
        self.assertIn("切断", self.win.log.toPlainText())
        self.sim.replug()
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_CONNECTED)
        self.assertTrue(self.win.overview.isEnabled())

    def test_disconnect_button(self) -> None:
        self.connect_up()
        self.win.conn_bar.connect_button.click()          # 「切断」
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_DISCONNECTED)
        self.assertEqual(self.win.conn_bar.connect_button.text(), "接続")
        time.sleep(0.3)
        self.app.processEvents()
        self.assertEqual(self.win.conn_kind, vs.CONN_DISCONNECTED)   # 自動再接続しない
        self.win.conn_bar.connect_button.click()
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_CONNECTED)

    def test_stats_and_sim_dock(self) -> None:
        self.connect_up()
        self.wait_for(lambda: "要求" in self.win.stats_label.text())
        self.win.sim_dock.vm_spin.setValue(30000)
        self.win.sim_dock._apply_vm()
        self.wait_for(lambda: self.win.overview._widgets["reg.VM_MV"].text() == "30.00 V")
        self.win.sim_dock.ntc_check.setChecked(True)
        self.win.sim_dock._apply_temp()
        self.wait_for(lambda: self.win.overview._widgets["reg.BOARD_TEMP"].text() == "—")

    def test_log_limit_and_clear(self) -> None:
        self.assertEqual(self.win.log.maximumBlockCount(), 5000)
        self.win.log_info("テスト")
        self.assertIn("テスト", self.win.log.toPlainText())
        self.win.clear_button.click()
        self.assertEqual(self.win.log.toPlainText(), "")

    def test_settings_saved_on_close(self) -> None:
        self.connect_up()
        self.win.tabs.setCurrentIndex(3)
        self.win.close()
        s = QSettings(self.settings_path, QSettings.IniFormat)
        self.assertEqual(int(s.value("tab")), 3)
        self.assertIsNotNone(s.value("geometry"))

    # ---- G3: 操作タブ・設定タブ・ログ ----

    def tab_index(self, kind: str) -> int:
        return [s.kind for s in self.win._specs].index(kind)

    def log_text(self) -> str:
        return self.win.log.toPlainText()

    def test_ops_enable_and_move(self) -> None:
        self.connect_up()
        ops = self.win.ops_tab
        self.assertEqual(self.win.active_kind, vs.TAB_OPS)
        self.wait_for(lambda: ops.buttons[vs.OP_ENABLE].isEnabled())
        # DISABLED 中は移動系が無効で、理由がツールチップに出る
        for op in (vs.OP_MOVE_ABS, vs.OP_MOVE_REL, vs.OP_RUN_SPEED, vs.OP_DISABLE, vs.OP_HOME_NEG):
            self.assertFalse(ops.buttons[op].isEnabled(), op)
        self.assertIn("ENABLED", ops.buttons[vs.OP_MOVE_REL].toolTip())
        self.assertFalse(ops.quick_buttons[0].isEnabled())
        self.assertEqual(ops.state_label.text(), "DISABLED")

        ops.buttons[vs.OP_ENABLE].click()
        self.wait_for(lambda: "COMMAND ENABLE: OK" in self.log_text())
        self.wait_for(lambda: ops.buttons[vs.OP_MOVE_REL].isEnabled())
        self.assertFalse(ops.buttons[vs.OP_ENABLE].isEnabled())
        self.assertTrue(ops.buttons[vs.OP_DISABLE].isEnabled())
        self.assertFalse(ops.buttons[vs.OP_HOME_NEG].isEnabled())     # J10_MODE != HOME
        self.assertEqual(ops.state_label.text(), "ENABLED")
        pos0 = self.win.model.value("reg.POSITION")
        ops.quick_buttons[2].click()                                  # +200
        self.wait_for(lambda: self.win.model.value("reg.POSITION") != pos0)
        self.wait_for(lambda: ops.pos_label.text() == f"{self.win.model.value('reg.POSITION')} step"
                      and self.win.model.value("reg.POSITION") == pos0 + 200)
        self.assertIn("書き込み MOVE_REL = 200: OK", self.log_text())
        # 絶対移動と速度 0
        ops.target_spin.setValue(0)
        ops.buttons[vs.OP_MOVE_ABS].click()
        self.wait_for(lambda: self.win.model.value("reg.POSITION") == 0)
        ops.buttons[vs.OP_RUN_SPEED_ZERO].click()
        self.wait_for(lambda: "書き込み RUN_SPEED = 0: OK" in self.log_text())

    def test_ops_disabled_when_disconnected(self) -> None:
        self.connect_up()
        ops = self.win.ops_tab
        self.wait_for(lambda: ops.buttons[vs.OP_ENABLE].isEnabled())
        self.sim.unplug()
        self.wait_for(lambda: self.win.conn_kind == vs.CONN_WAITING)
        self.wait_for(lambda: not ops.buttons[vs.OP_ENABLE].isEnabled())
        self.assertTrue(all(not b.isEnabled() for b in ops.buttons.values()))
        self.assertIn("未接続", ops.buttons[vs.OP_ENABLE].toolTip())

    def test_ops_set_position_rejected_while_moving_is_disabled(self) -> None:
        self.connect_up()
        ops = self.win.ops_tab
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: ops.buttons[vs.OP_SET_POSITION].isEnabled())
        ops.speed_spin.setValue(1000)
        self.wait_for(lambda: ops.buttons[vs.OP_RUN_SPEED].isEnabled())
        ops.buttons[vs.OP_RUN_SPEED].click()
        self.wait_for(lambda: not ops.buttons[vs.OP_SET_POSITION].isEnabled())
        self.assertIn("動作中", ops.buttons[vs.OP_SET_POSITION].toolTip())
        ops.buttons[vs.OP_RUN_SPEED_ZERO].click()
        self.wait_for(lambda: ops.buttons[vs.OP_SET_POSITION].isEnabled(), timeout=6.0)

    def test_tab_polls_ops_and_settings(self) -> None:
        self.connect_up()
        polls = self.win.worker._polls
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        keys, period = polls["ops"]
        self.assertIn("reg.J10_MODE", keys)
        self.assertEqual(period, 0.1)
        self.assertNotIn("settings", polls)
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_SETTINGS))
        keys, period = polls["settings"]
        self.assertIn("reg.RUN_CURRENT", keys)
        self.assertIn("reg.CONFIG_STATUS", keys)
        self.assertEqual(period, 1.0)
        self.assertNotIn("ops", polls)
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_OPS) + 2)     # モーション
        self.assertNotIn("ops", polls)
        self.assertNotIn("settings", polls)
        self.assertIn("tab", polls)
        self.assertIn("overview", polls)

    def open_settings(self):
        self.connect_up()
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_SETTINGS))
        st = self.win.settings_tab
        self.wait_for(lambda: st.table.item(st._rows["reg.RUN_CURRENT"], 1).text() != "—")
        self.wait_for(lambda: st.lamps._shown[0] is True)       # LOADED
        return st

    def board_text(self, st, key: str) -> str:
        return st.table.item(st._rows[key], 1).text()

    def test_settings_edit_write_save(self) -> None:
        st = self.open_settings()
        key = "reg.RUN_CURRENT"
        row = st._rows[key]
        ed = st.editors[key]
        b = int(self.board_text(st, key))
        self.assertEqual(ed.value(), b)
        self.assertFalse(st.write_button.isEnabled())
        self.assertFalse(st.edit.changed())
        self.assertFalse(st.lamps._shown[1])                   # UNSAVED なし

        ed._spin.setValue(b + 100)
        self.assertTrue(st.edit.is_changed(key))
        self.assertIn("ffe08a", ed.styleSheet().lower())
        self.assertEqual(st.table.item(row, 0).background().color().name(), "#ffe08a")
        self.assertTrue(st.write_button.isEnabled())
        st.write_button.click()
        self.wait_for(lambda: st.table.item(row, 6).text() == "OK")
        self.wait_for(lambda: self.board_text(st, key) == str(b + 100))
        self.wait_for(lambda: st.lamps._shown[1] is True)      # UNSAVED が点灯
        self.assertIn("未保存", st.unsaved_label.text())
        self.assertFalse(st.edit.is_changed(key))
        self.assertEqual(st.table.item(row, 0).background().color().name(), "#000000")   # 強調が消える
        self.assertEqual(self.win.model.value(key), b + 100)

        st.save_button.click()
        self.wait_for(lambda: "COMMAND SAVE_SETTINGS: OK" in self.log_text())
        self.wait_for(lambda: st.lamps._shown[1] is False)
        self.assertEqual(st.unsaved_label.text(), "")

    def test_settings_range_and_polling_does_not_overwrite(self) -> None:
        st = self.open_settings()
        key = "reg.RUN_CURRENT"
        ed = st.editors[key]
        b = int(self.board_text(st, key))
        ed._spin.setValue(5000)                                # 範囲外は入力できない
        self.assertEqual(ed.value(), 3000)
        ed._spin.setValue(10)
        self.assertEqual(ed.value(), 250)
        self.assertEqual(vs.range_text(st.edit.items[0]), "250〜3000")
        ed._spin.setValue(b + 100)
        self.win.worker.send(wk.WriteReg(key, b + 200))        # 別経路で基板の値が変わる
        self.wait_for(lambda: self.board_text(st, key) == str(b + 200))
        self.assertEqual(ed.value(), b + 100)                  # 編集中の値は上書きされない
        self.assertTrue(st.edit.is_changed(key))
        st.reload_button.click()                               # 読み直しで編集値が基板の値に戻る
        self.wait_for(lambda: ed.value() == b + 200)
        self.assertFalse(st.edit.changed())

    def test_settings_widgets(self) -> None:
        st = self.open_settings()
        self.assertIsInstance(st.editors["reg.MICROSTEP"]._combo.itemData(0), int)
        self.assertEqual([st.editors["reg.MICROSTEP"]._combo.itemData(i) for i in range(6)], [1, 2, 4, 8, 16, 32])
        self.assertEqual(st.editors["reg.J10_MODE"]._combo.count(), 3)
        self.assertEqual(st.editors["reg.PA7_MODE"]._combo.count(), 3)
        flags = st.editors["reg.FLAGS"]
        self.assertEqual(len(flags._checks), 5)
        board = int(self.win.model.value("reg.FLAGS"))
        self.assertEqual(flags.value(), board)
        flags._checks[0].setChecked(not flags._checks[0].isChecked())
        self.assertEqual(flags.value(), board ^ 1)
        self.assertTrue(st.edit.is_changed("reg.FLAGS"))
        st.reload_button.click()
        self.wait_for(lambda: flags.value() == board)

    def test_settings_rejected_result_shown(self) -> None:
        st = self.open_settings()
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.win.model.value("reg.STATE") == 2)
        flags = st.editors["reg.FLAGS"]
        flags._checks[3].setChecked(not flags._checks[3].isChecked())      # ENABLED 中の AGC 変更は REJECTED
        st.write_button.click()
        row = st._rows["reg.FLAGS"]
        self.wait_for(lambda: st.table.item(row, 6).text() == "REJECTED")
        self.assertTrue(st.edit.is_changed("reg.FLAGS"))                   # 書けなかった行は強調のまま

    def test_settings_factory_confirm(self) -> None:
        st = self.open_settings()
        sent = []
        orig = st._send
        st._send = lambda req: (sent.append(req), orig(req))[1]
        asked = []
        st.confirm = lambda title, text: (asked.append(text), False)[1]
        st.factory_button.click()
        st.load_button.click()
        self.assertEqual(len(asked), 2)
        self.assertIn("失われます", asked[0])
        self.assertEqual(sent, [])                                         # キャンセルなら何も送らない
        key = "reg.RUN_CURRENT"
        b = int(self.board_text(st, key))
        st.editors[key]._spin.setValue(b + 100)
        st.confirm = lambda title, text: True
        st.factory_button.click()
        self.wait_for(lambda: "COMMAND FACTORY_SETTINGS: OK" in self.log_text())
        self.assertEqual([r.code for r in sent if isinstance(r, wk.Command)], [wk.Cmd.FACTORY_SETTINGS])
        self.wait_for(lambda: not st.edit.changed())                      # 実行後に読み直して編集値も戻る
        self.wait_for(lambda: self.board_text(st, key) == str(st.editors[key].value()))
        # LOAD_SETTINGS は確認 OK で送られる
        st.load_button.click()
        self.wait_for(lambda: "COMMAND LOAD_SETTINGS" in self.log_text())

    def test_settings_commands_disabled_while_moving(self) -> None:
        st = self.open_settings()
        self.assertTrue(st.save_button.isEnabled())
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.win.model.value("reg.STATE") == 2)
        self.win.worker.send(wk.WriteReg("RUN_SPEED", 1000))
        for b in (st.save_button, st.load_button, st.factory_button):
            self.wait_for(lambda b=b: not b.isEnabled())
            self.assertIn("動作中", b.toolTip())
        self.win.send_command(wk.Cmd.STOP)
        self.wait_for(lambda: st.save_button.isEnabled(), timeout=6.0)

    def test_int_spin_beyond_int32(self) -> None:
        from nemagui.ui.inputs import int_value, make_int_spin, set_int_value

        small = make_int_spin(0, 3000)
        self.assertIsInstance(small, QSpinBox)
        big = make_int_spin(0, 0xFFFFFFFF)
        self.assertIsInstance(big, QDoubleSpinBox)
        set_int_value(big, 0xFFFFFFFF)
        self.assertEqual(int_value(big), 0xFFFFFFFF)
        big.setValue(1e12)
        self.assertEqual(int_value(big), 0xFFFFFFFF)

    def test_log_lines_share_one_font(self) -> None:
        self.connect_up()
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: "COMMAND ENABLE: OK" in self.log_text())
        self.win.log_info("ASCII only line")
        self.win.log_warn("日本語を含む警告 <b>&amp;</b>")
        self.win._append_log(vs.LogEntry("エラー", vs.LEVEL_ERROR))
        self.assertIn("<b>&amp;</b>", self.log_text())                    # エスケープされず文字のまま出る
        fonts = set()
        colors = set()
        lines = 0
        block = self.win.log.document().begin()
        while block.isValid():
            lines += 1
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                for prop in (QTextFormat.FontFamily, QTextFormat.FontFamilies, QTextFormat.FontPointSize,
                             QTextFormat.FontPixelSize, QTextFormat.FontWeight):
                    self.assertFalse(fmt.hasProperty(prop), (block.text(), prop))
                fonts.add(fmt.font().toString())
                colors.add(fmt.foreground().color().name())
                it += 1
            block = block.next()
        self.assertGreaterEqual(lines, 4)
        self.assertEqual(len(fonts), 1, fonts)
        self.assertGreaterEqual(len(colors), 3)                           # 色は行ごとに付く
        self.assertEqual(self.win.log.document().defaultFont().toString(), self.win.log.font().toString())


    # ---- G4: 要点パネル・名前表ブラウザ・ウォッチ ----

    def open_group(self, group: str):
        self.connect_up()
        names = {sp.group: n for n, sp in enumerate(self.win._specs)}
        self.win.tabs.setCurrentIndex(names[group])
        return self.win._panels[group]

    def test_panels_exist_for_internal_tabs(self) -> None:
        self.assertEqual(sorted(self.win._panels), ["comm", "diag", "j10", "motion", "sensor", "state"])
        self.assertNotIn("monitor", self.win._panels)
        self.assertNotIn("other", self.win._panels)
        for g in self.win._panels:                      # 汎用表は詳細として残る
            self.assertIn(g, self.win._tables)

    def test_motion_panel(self) -> None:
        p = self.open_group("motion")
        self.assertIn("sym.hold_active", self.win.worker._polls["tab"][0])
        self.wait_for(lambda: p.mode.text() == "IDLE")
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.win.model.value("reg.STATE") == 2)
        self.win.worker.send(wk.WriteReg("RUN_SPEED", 1000))
        self.wait_for(lambda: p.mode.text() == "VEL" and p.running.is_on)
        self.wait_for(lambda: p.cur_speed.text() != "—" and float(p.cur_speed.text().split()[0]) > 100)
        self.assertEqual(p.dir.text(), "→")
        self.assertTrue(p.target_vel.text().startswith("1000"))
        self.win.worker.send(wk.WriteReg("RUN_SPEED", 0))
        self.wait_for(lambda: not p.running.is_on, timeout=6.0)
        pos = self.win.model.value("reg.POSITION")
        self.win.worker.send(wk.WriteReg("MOVE_REL", 600))
        self.wait_for(lambda: p.mode.text() == "POS")
        self.wait_for(lambda: p.remain.text() not in ("—", "0 step") and int(p.remain.text().split()[0]) > 0)
        self.wait_for(lambda: p.position.text() == f"{pos + 600} step" and not p.running.is_on, timeout=10.0)
        self.wait_for(lambda: p.remain.text() == "0 step")

    def test_state_panel_fault_lamps(self) -> None:
        p = self.open_group("state")
        self.assertTrue(set(p.keys) <= {i.key for i in self.win._items})
        self.wait_for(lambda: p.state.text() == "DISABLED")
        self.assertFalse(any(p.fault._shown))
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: p.state.text() == "ENABLED")
        self.win.sim_dock._inject("OPD", 0x01)
        self.wait_for(lambda: p.state.text() == "FAULT")
        self.wait_for(lambda: p.fault._shown[0] is True)             # OPD
        self.assertIn("d32f2f", p.state.styleSheet().lower())
        self.wait_for(lambda: p.cfg._shown[0] is True)               # LOADED
        self.assertNotEqual(p.cmd_result.text(), "—")

    def test_sensor_panel(self) -> None:
        p = self.open_group("sensor")
        self.wait_for(lambda: p.raw.text() != "—" and p.ok.text() != "—")
        self.wait_for(lambda: p.status._shown[0] is True)            # VALID
        self.assertEqual([lab.text() for lab in p.as_status._labels], ["MH", "ML", "MD"])
        self.wait_for(lambda: p.as_status._shown[2] is True)         # MD（磁石検出）
        self.assertEqual(p.streak.text(), "0")
        self.wait_for(lambda: "+" in p.ok.text(), timeout=5.0)       # 毎秒の増加量
        self.assertIn("°", p.angle.text())

    def test_j10_panel_home_progress(self) -> None:
        p = self.open_group("j10")
        self.sim.set_home_switches(1500, None)
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.win.model.value("reg.STATE") == 2)
        self.win.worker.send(wk.WriteReg("reg.J10_MODE", 2))
        self.win.worker.send(wk.WriteReg("reg.HOME_SPEED", 3000))
        self.wait_for(lambda: "J10_MODE = 2: OK" in self.log_text() and "HOME_SPEED = 3000 step/s: OK" in self.log_text())
        self.wait_for(lambda: p._stage_sig == (0, False))            # IDLE を強調
        seen = set()

        def watch() -> bool:
            seen.add(p._stage_sig)
            return p._stage_sig == (5, False)

        self.win.worker.send(wk.Command(wk.Cmd.HOME_POS))
        self.wait_for(watch, timeout=10.0)
        self.assertIn(1, {s[0] for s in seen})                       # SEEK を経由
        self.assertIn("HOME1", p.side.text())
        self.assertTrue(p.input_status._shown[3])                    # HOMED
        self.assertEqual(p.stage_labels[5].text(), "DONE")
        self.assertIn("1976d2", p.stage_labels[5].styleSheet().lower())     # 現在段階は青
        self.assertIn("c8e6c9", p.stage_labels[0].styleSheet().lower())     # 通過済み

    def test_j10_panel_failed(self) -> None:
        p = self.open_group("j10")
        self.sim.set_home_switches(None, None)
        self.sim.set_home_travel_limit(400)
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.win.model.value("reg.STATE") == 2)
        self.win.worker.send(wk.WriteReg("reg.J10_MODE", 2))
        self.win.worker.send(wk.WriteReg("reg.HOME_SPEED", 3000))
        self.win.worker.send(wk.Command(wk.Cmd.HOME_POS))
        self.wait_for(lambda: p._stage_sig == (None, True), timeout=10.0)
        self.assertIn("d32f2f", p.failed.styleSheet().lower())
        self.assertEqual(p.hm1.bar.maximum(), 2)

    def test_comm_panel_colors(self) -> None:
        from PySide6.QtGui import QColor

        p = self.open_group("comm")
        self.wait_for(lambda: p.usb_table.item(0, 1).text() not in ("—", ""))
        self.assertTrue(p.can_table.item(8, 1).text().startswith("0x"))          # err_last_code は 16 進
        cell = p.usb_table.item(list(sm.COMM_USB_KEYS).index("sym.usb_stats.crc_errors"), 1)
        t = time.time()
        raw = lambda v: v.to_bytes(4, "little")
        m = self.win.model
        m.update(t, {"sym.usb_stats.crc_errors": raw(0)})
        m.update(t + 1.1, {"sym.usb_stats.crc_errors": raw(0)})
        p.refresh(m)
        self.assertNotIn(cell.foreground().color().name(), (QColor(vs.ORANGE).name(), QColor(vs.RED).name()))
        m.update(t + 2.2, {"sym.usb_stats.crc_errors": raw(3)})                  # 0 でなくなり、増加中
        p.refresh(m)
        self.assertEqual(cell.text(), "3")
        self.assertEqual(cell.foreground().color().name(), QColor(vs.RED).name())
        m.update(t + 3.3, {"sym.usb_stats.crc_errors": raw(3)})                  # 増加が止まれば橙
        p.refresh(m)
        self.assertEqual(cell.foreground().color().name(), QColor(vs.ORANGE).name())
        ok = p.usb_table.item(list(sm.COMM_USB_KEYS).index("sym.usb_stats.rx_frames"), 1)
        self.assertNotIn(ok.foreground().color().name(), (QColor(vs.ORANGE).name(), QColor(vs.RED).name()))

    def test_diag_panel(self) -> None:
        p = self.open_group("diag")
        self.wait_for(lambda: p.uptime.text() != "—")
        self.assertRegex(p.uptime.text(), r"^0 日 \d\d:\d\d:\d\d\.\d{3}$")
        first = p.uptime.text()
        self.wait_for(lambda: p.uptime.text() != first)
        self.assertEqual(p.reset_main.text(), "電源投入（POR/BOR）")
        self.assertEqual(p.reset_flags.text(), "PINRSTF, BORRSTF")
        self.assertIn("704 / 12288 B", p.stack_text.text())
        self.assertEqual(p.stack_bar.format(), "5.7 %")
        self.assertIn(vs.GREEN, p.stack_bar.styleSheet())
        m = self.win.model
        for used, color in ((9500, vs.ORANGE), (11500, vs.RED)):      # 77.3 % / 93.6 %
            m.update(time.time(), {"sym.stack.used_max": used.to_bytes(4, "little")})
            p.refresh(m)
            self.assertIn(color, p.stack_bar.styleSheet())

    def test_sym_browser_filter_and_read(self) -> None:
        self.connect_up()
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_SYMS))
        b = self.win.sym_browser
        total = b.table.rowCount()
        self.assertGreater(total, 90)
        self.assertEqual(len(b.visible_names), total)
        self.assertEqual([b.table.horizontalHeaderItem(c).text() for c in range(6)],
                         ["名前", "アドレス", "TYPE", "NUM", "サイズ", "グループ"])
        b.filter_edit.setText("USB_STATS")
        names = b.visible_names
        self.assertTrue(names and all("usb_stats" in n for n in names))
        self.assertIn("usb_stats", names)                                  # 構造体全体も一覧に出る
        self.assertIn(f"{len(names)} / {total}", b.count_label.text())
        self.assertFalse(b.read_button.isEnabled())                        # 未選択
        names_all = [i.name for i in b._infos]
        b.table.selectRow(names_all.index("usb_stats.rx_frames"))
        self.assertTrue(b.read_button.isEnabled())
        b.read_button.click()
        self.wait_for(lambda: "usb_stats.rx_frames = " in b.result.toPlainText())
        b.table.selectRow(names_all.index("usb_stats"))                    # bytes 型は 16 進ダンプ
        b.read_button.click()
        self.wait_for(lambda: "usb_stats (32 B)" in b.result.toPlainText())
        self.assertIn("0010: ", b.result.toPlainText())
        self.assertNotIn("読み出し失敗", self.log_text())
        b.filter_edit.setText("")
        self.assertEqual(len(b.visible_names), total)
        row = names_all.index("state")                                     # アドレスは 16 進、TYPE は名前
        self.assertTrue(b.table.item(row, 1).text().startswith("0x2"))
        self.assertEqual(b.table.item(row, 2).text(), "u8")
        self.assertEqual(b.table.item(row, 5).text(), "状態遷移・異常")

    def test_watch_add_update_remove(self) -> None:
        self.connect_up()
        w = self.win.watch_dock
        polls = self.win.worker._polls
        self.assertNotIn("watch", polls)                                   # 空なら組を外す
        got = []
        w.changed.connect(got.append)
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_SYMS))
        b = self.win.sym_browser
        b.table.selectRow([i.name for i in b._infos].index("sensor.i2c_ok"))
        b.watch_button.click()
        self.assertEqual(w.keys(), ["sym.sensor.i2c_ok"])
        self.assertEqual(got[-1], ["sym.sensor.i2c_ok"])
        self.assertEqual(polls["watch"], (frozenset(["sym.sensor.i2c_ok"]), 0.2))
        self.assertFalse(w.add("sym.sensor.i2c_ok"))                       # 重複は無視
        self.win.add_to_watch("reg.POSITION")                              # レジスタ項目
        self.assertEqual(w.keys(), ["sym.sensor.i2c_ok", "reg.POSITION"])
        self.wait_for(lambda: w.table.item(0, 1).text() not in ("—", ""))
        self.wait_for(lambda: w.table.item(0, 3).text().startswith("+"), timeout=5.0)   # 毎秒
        self.assertEqual(w.table.item(1, 2).text(), "step")
        v0 = w.table.item(0, 1).text()
        self.wait_for(lambda: w.table.item(0, 1).text() != v0)             # 値が更新される
        w.period_spin.setValue(500)
        self.assertEqual(polls["watch"][1], 0.5)
        w.table.cellWidget(0, 4).click()                                   # 削除ボタン
        self.assertEqual(w.keys(), ["reg.POSITION"])
        w.remove("reg.POSITION")
        self.assertNotIn("watch", polls)

    def test_watch_from_group_table_signal(self) -> None:
        self.connect_up()
        t = self.win._tables["motion"]
        t.add_to_watch.emit(t.keys[0])
        self.assertEqual(self.win.watch_dock.keys(), [t.keys[0]])
        self.assertEqual(t.contextMenuPolicy(), Qt.CustomContextMenu)

    def test_watch_restored_on_restart(self) -> None:
        from nemagui.ui.main_window import AppConfig, MainWindow

        self.connect_up()
        self.win.add_to_watch("sym.position")
        self.win.add_to_watch("sym.cfg")                                   # 接続後にだけ存在する項目
        self.win.add_to_watch("reg.VM_MV")
        self.win.watch_dock.period_spin.setValue(300)
        self.win.close()
        s = QSettings(self.settings_path, QSettings.IniFormat)
        self.assertEqual(sm.decode_watch_keys(s.value("watch/keys")), ["sym.position", "sym.cfg", "reg.VM_MV"])
        s.setValue("watch/keys", sm.encode_watch_keys(["sym.position", "sym.cfg", "reg.VM_MV", "sym.no_such"]))
        s.sync()
        sim2 = SimSerial(noise=False, warmup_ms=200)
        win2 = MainWindow(AppConfig(sim=sim2, timeout_ms=100, reconnect_interval_s=0.1, stats_period_s=0.2),
                          QSettings(self.settings_path, QSettings.IniFormat))
        try:
            win2.show()
            self.assertEqual(win2.watch_dock.period_s, 0.3)
            self.assertIn("watch", win2.worker._polls)
            end = time.monotonic() + 4
            want = ["reg.VM_MV", "sym.cfg", "sym.position"]
            while time.monotonic() < end and sorted(win2.watch_dock.keys()) != want:
                self.app.processEvents()
                time.sleep(0.005)
            self.assertEqual(sorted(win2.watch_dock.keys()), want)
            self.assertNotIn("sym.no_such", win2.watch_dock.keys())        # 存在しない key は無視
            end = time.monotonic() + 4
            while time.monotonic() < end and win2.watch_dock.table.item(0, 1).text() in ("—", ""):
                self.app.processEvents()
                time.sleep(0.005)
            self.assertNotIn(win2.watch_dock.table.item(0, 1).text(), ("—", ""))
        finally:
            win2.close()
            self.assertFalse(win2.worker.is_alive())

    def test_settings_write_log_uses_register_name(self) -> None:
        st = self.open_settings()
        key = "reg.RUN_CURRENT"
        b = int(self.board_text(st, key))
        st.editors[key]._spin.setValue(b + 100)
        st.write_button.click()
        self.wait_for(lambda: f"書き込み RUN_CURRENT = {b + 100} mA: OK" in self.log_text())
        self.assertNotIn("reg.RUN_CURRENT", self.log_text())

    # ---- G5: グラフと CSV 記録 ----

    def open_graph(self):
        self.connect_up()
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_GRAPH))
        return self.win.graph_tab

    def set_save_path(self, path):
        asked = []

        def dialog(title, name, filt):
            asked.append((title, name, filt))
            return path
        self.win.graph_tab._save_dialog = dialog
        return asked

    def test_graph_motion_preset_and_poll(self) -> None:
        g = self.open_graph()
        polls = self.win.worker._polls
        self.assertNotIn("graph", polls)                                   # 系列が無ければ組は無い
        g.preset_combo.activated.emit(1)                                   # 「運動」
        self.assertEqual(g.selected_keys(), ["reg.POSITION", "reg.SPEED", "sym.cur_speed", "sym.plan_speed",
                                             "sym.target_pos"])
        keys, period = polls["graph"]
        self.assertEqual(keys, frozenset(g.selected_keys()))
        self.assertEqual(period, 0.05)
        self.assertEqual([s.unit for s in g.plot_specs], ["step", "step/s"])
        g.period_spin.setValue(100)
        self.assertEqual(polls["graph"][1], 0.1)
        g.period_spin.setValue(50)
        self.win.send_command(wk.Cmd.ENABLE)
        self.wait_for(lambda: self.state_text() == "ENABLED")
        self.wait_for(lambda: g.curve_points("reg.POSITION") > 5)
        n0 = g.curve_points("sym.cur_speed")
        self.win.worker.send(wk.WriteReg("MOVE_REL", 3200))
        self.wait_for(lambda: self.win.model.value("reg.POSITION") == 3200, timeout=8.0)
        self.wait_for(lambda: g.curve_points("sym.cur_speed") > n0 + 5)
        xs, ys = g._cache["sym.cur_speed"]
        self.assertGreater(max(ys), 100.0)                                 # 速度が立ち上がった
        self.assertLessEqual(max(xs), 0.0)
        # 一時停止中は描画が止まる。再開で動く
        g.pause_button.click()
        self.assertTrue(g.paused)
        cnt = g.render_count
        for _ in range(10):
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(g.render_count, cnt)
        self.assertIn("graph", polls)                                      # 取得は続く
        g.pause_button.click()
        self.wait_for(lambda: g.render_count > cnt)
        # クリアは表示だけ消し、履歴は消さない
        hist_n = len(self.win.model.history("reg.POSITION")[0])
        g.clear()
        self.assertLess(g.curve_points("reg.POSITION"), 3)
        self.assertGreaterEqual(len(self.win.model.history("reg.POSITION")[0]), hist_n)

    def test_graph_poll_leaves_with_tab(self) -> None:
        g = self.open_graph()
        polls = self.win.worker._polls
        g.apply_preset("電源・温度")
        self.assertIn("graph", polls)
        self.win.tabs.setCurrentIndex(0)
        self.assertNotIn("graph", polls)
        self.win.tabs.setCurrentIndex(self.tab_index(vs.TAB_GRAPH))
        self.assertIn("graph", polls)
        g.set_selected([])
        self.assertNotIn("graph", polls)
        g.apply_preset("電源・温度")
        # 記録中はタブを離れても続ける
        with tempfile.TemporaryDirectory() as d:
            self.set_save_path(os.path.join(d, "a.csv"))
            self.assertTrue(g.start_recording())
            self.win.tabs.setCurrentIndex(0)
            self.assertIn("graph", polls)
            g.stop_recording()
            self.assertNotIn("graph", polls)

    def test_graph_csv_recording(self) -> None:
        import csv

        g = self.open_graph()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rec.csv")
            asked = self.set_save_path(path)
            self.assertFalse(g.rec_button.isEnabled())                     # 系列が無い
            g.apply_preset("電源・温度")
            self.assertTrue(g.rec_button.isEnabled())
            g.rec_button.click()
            self.assertEqual(g.rec_button.text(), "記録停止")
            self.assertRegex(asked[0][1], r"^nemagui_\d{8}_\d{6}\.csv$")
            self.assertTrue(g.recording)
            self.assertTrue(self.win.model.csv_active)
            self.assertFalse(self.win.rec_label.isHidden())
            self.assertIn("記録中", self.win.rec_label.text())
            self.assertFalse(g.select_box.isEnabled())                     # 記録中は系列を変えられない
            self.assertFalse(g.set_selected(["reg.POSITION"]))
            self.assertEqual(g.selected_keys(), ["reg.VM_MV", "reg.BOARD_TEMP", "reg.MCU_TEMP"])
            self.wait_for(lambda: self.win.model.csv_rows >= 5)
            self.wait_for(lambda: "行" in g.rec_label.text() and "rec.csv" in g.rec_label.text())
            g.rec_button.click()
            self.assertFalse(g.recording)
            self.assertFalse(self.win.model.csv_active)
            self.assertTrue(self.win.rec_label.isHidden())
            self.assertEqual(g.rec_button.text(), "記録開始")
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0][:2], ["時刻", "経過[s]"])
            self.assertEqual(len(rows[0]), 5)
            self.assertGreaterEqual(len(rows) - 1, 5)
            self.assertEqual(len(rows) - 1, self.win.model.csv_rows)
            self.assertTrue(any(r[2] for r in rows[1:]))                   # VM_MV の値が入る

    def test_graph_csv_cancel_and_closed_on_disconnect(self) -> None:
        g = self.open_graph()
        with tempfile.TemporaryDirectory() as d:
            g.apply_preset("電源・温度")
            self.set_save_path(None)                                       # ダイアログを取り消し
            self.assertFalse(g.start_recording())
            self.assertFalse(g.recording)
            path = os.path.join(d, "x.csv")
            self.set_save_path(path)
            self.assertTrue(g.start_recording())
            self.wait_for(lambda: self.win.model.csv_rows >= 2)
            self.win.conn_bar.connect_button.click()                       # 切断
            self.wait_for(lambda: self.win.conn_kind == vs.CONN_DISCONNECTED)
            self.assertFalse(g.recording)
            self.assertFalse(self.win.model.csv_active)
            self.win.tabs.setCurrentIndex(0)                               # 表示も記録も無ければ組は外れる
            self.assertNotIn("graph", self.win.worker._polls)
            self.assertTrue(self.win.rec_label.isHidden())
            with open(path, encoding="utf-8-sig") as f:                    # 閉じているので読める
                self.assertGreaterEqual(len(f.read().splitlines()), 3)

    def test_graph_closed_on_window_close(self) -> None:
        g = self.open_graph()
        with tempfile.TemporaryDirectory() as d:
            g.apply_preset("電源・温度")
            self.set_save_path(os.path.join(d, "y.csv"))
            self.assertTrue(g.start_recording())
            self.wait_for(lambda: self.win.model.csv_rows >= 2)
            self.win.close()
            self.assertFalse(self.win.model.csv_active)

    def test_graph_state_plot_cursor_and_png(self) -> None:
        g = self.open_graph()
        g.apply_preset("原点復帰")
        self.assertEqual([s.unit for s in g.plot_specs], ["step", ""])
        self.assertEqual(len(g.plot_specs[1].keys), 4)
        self.wait_for(lambda: g.curve_points("sym.home.state") >= 2)
        self.assertEqual(g.curve_points("sym.home.state") % 2, 0)          # 階段状（点が 2 倍近く）
        g.set_cursor(-1.0)
        self.assertIn("t = -1.00 s", g.readout_text())
        self.assertIn("位置", g.readout_text())
        g.set_cursor(None)
        self.assertEqual(g.readout_text(), "")
        g.filter_edit.setText("reg.vm_mv")
        shown = [i for i in range(g.list.count()) if not g.list.item(i).isHidden()]
        self.assertEqual([g.list.item(i).data(Qt.UserRole) for i in shown], ["reg.VM_MV"])
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, "g.png")
            self.set_save_path(png)
            self.assertTrue(g.save_png())
            self.assertGreater(os.path.getsize(png), 1000)

    def test_graph_candidates_watch_first(self) -> None:
        g = self.open_graph()
        self.win.add_to_watch("reg.VM_MV")
        self.assertTrue(g.list.item(0).data(Qt.UserRole) == "reg.VM_MV")
        self.assertTrue(g.list.item(0).text().startswith("★"))
        g.list.item(0).setCheckState(Qt.Checked)
        self.assertEqual(g.selected_keys(), ["reg.VM_MV"])

    def test_watch_value_column_wide_enough(self) -> None:
        self.connect_up()
        w = self.win.watch_dock
        for k in ("sym.position", "reg.VM_MV", "reg.FAULT", "reg.STATE", "sym.uwTick"):
            self.win.add_to_watch(k)
        self.wait_for(lambda: all(w.table.item(r, 1).text() not in ("—", "") for r in range(w.table.rowCount())))
        fm = w.table.fontMetrics()
        for r in range(w.table.rowCount()):
            text = w.table.item(r, 1).text()
            self.assertGreaterEqual(w.table.columnWidth(1), fm.horizontalAdvance(text) + 8, text)
        self.assertGreaterEqual(w.table.columnWidth(1), fm.horizontalAdvance("0" * 10))


if __name__ == "__main__":
    unittest.main()
