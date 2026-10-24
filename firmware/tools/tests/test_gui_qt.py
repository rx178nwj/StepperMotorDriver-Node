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
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from nemadbg.sim import SimSerial
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
        self.assertEqual(self.win.tabs.count(), 11)

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


if __name__ == "__main__":
    unittest.main()
