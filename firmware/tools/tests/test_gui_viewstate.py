"""nemagui.viewstate（Qt 非依存の表示ロジック）の試験。"""

import unittest

from nemadbg.link import ConnectInfo
from nemagui import items as itemsmod
from nemagui import viewstate as vs
from nemagui import worker as wk

ITEMS = itemsmod.items_by_key(itemsmod.build_items())


class StateTest(unittest.TestCase):
    def test_state_colors(self) -> None:
        self.assertEqual(vs.state_color(0), vs.GRAY)
        self.assertEqual(vs.state_color(1), vs.YELLOW)
        self.assertEqual(vs.state_color(2), vs.GREEN)
        self.assertEqual(vs.state_color(3), vs.RED)
        self.assertEqual(vs.state_color(None), vs.UNKNOWN_GRAY)
        self.assertEqual(vs.state_color(9), vs.UNKNOWN_GRAY)
        self.assertEqual(vs.state_name(1), "DISABLED")
        self.assertEqual(vs.state_name(None), "—")
        self.assertIn("9", vs.state_name(9))

    def test_lamps(self) -> None:
        it = ITEMS["reg.FAULT"]
        st = dict(vs.lamp_states(it, 0b101))
        self.assertTrue(st["OPD"])
        self.assertFalse(st["ISD"])
        self.assertTrue(st["TSD"])
        self.assertFalse(any(on for _n, on in vs.lamp_states(it, None)))
        self.assertEqual(vs.lamp_color("FAULT", "OPD"), vs.RED)
        self.assertEqual(vs.lamp_color("STATUS", "MOVING"), vs.GREEN)
        self.assertEqual(vs.lamp_color("STATUS", "OVERTEMP_WARN"), vs.ORANGE)

    def test_conn_kind(self) -> None:
        info = ConnectInfo(1, 0, 0xA3, 0, 4, 3, 240)
        self.assertEqual(vs.conn_kind(wk.Connected(info, b"", {}, [])), vs.CONN_CONNECTED)
        self.assertEqual(vs.conn_kind(wk.Disconnected("x", True)), vs.CONN_WAITING)
        self.assertEqual(vs.conn_kind(wk.Disconnected("x", False)), vs.CONN_DISCONNECTED)
        self.assertEqual(vs.conn_kind(wk.Notice("n"), vs.CONN_CONNECTED), vs.CONN_CONNECTED)
        text = vs.device_info_text(wk.Connected(info, bytes.fromhex("0123456789abcdef"), {}, []))
        self.assertIn("FW 0.4", text)
        self.assertIn("BOARD_ID 3", text)
        self.assertIn("01234567", text)
        self.assertNotIn("89abcdef", text)


class ValueTextTest(unittest.TestCase):
    def test_vm_and_plain(self) -> None:
        self.assertEqual(vs.format_vm(24000), "24.00 V")
        self.assertEqual(vs.format_vm(None), "—")
        self.assertEqual(vs.plain_text(ITEMS["sym.position"], 12), "12")           # 単位 step が付かない
        self.assertEqual(vs.plain_text(ITEMS["reg.BOARD_TEMP"], 25.5), "25.5")
        self.assertTrue(vs.plain_text(ITEMS["reg.STATE"], 1).startswith("1 (DISABLED"))
        self.assertEqual(vs.plain_text(ITEMS["reg.BOARD_TEMP"], None), "無効")
        self.assertEqual(vs.counter_suffix(None), "")
        self.assertEqual(vs.counter_suffix(12.4), "  (+12/s)")


class TabTest(unittest.TestCase):
    def test_tab_specs(self) -> None:
        labels = [t.label for t in vs.tab_specs()]
        self.assertEqual(labels, ["操作", "設定", "モーション", "状態遷移", "監視", "センサ", "J10・原点", "通信統計",
                                  "診断", "その他", "名前表", "グラフ"])
        groups = [t.group for t in vs.tab_specs() if t.group]
        self.assertEqual(groups, ["motion", "state", "monitor", "sensor", "j10", "comm", "diag", "other"])
        for g in groups:
            self.assertIn(g, vs.POLL_PERIODS)
        self.assertEqual(vs.POLL_PERIODS["motion"], 0.05)
        self.assertEqual(vs.POLL_PERIODS["monitor"], 0.2)


class LogTest(unittest.TestCase):
    def test_fault_event(self) -> None:
        ev = wk.Event(0.0, 1, b"", can_id=0x83, state=3, fault=0x05, text="異常通知")
        e = vs.describe_event(ev)
        self.assertEqual(e.level, vs.LEVEL_ERROR)
        self.assertIn("FAULT", e.text)
        self.assertIn("OPD", e.text)
        self.assertIn("TSD", e.text)
        self.assertNotIn("ISD", e.text)

    def test_other_event(self) -> None:
        e = vs.describe_event(wk.Event(0.0, 0x20, b"\x01\x02"))
        self.assertEqual(e.level, vs.LEVEL_WARN)
        self.assertIn("0x20", e.text)

    def test_write_result(self) -> None:
        req = wk.Command(wk.Cmd.ESTOP)
        ok = vs.describe_write_result(wk.WriteResult(req, 0, "OK"))
        self.assertEqual((ok.text, ok.level), ("COMMAND ESTOP: OK", vs.LEVEL_INFO))
        rej = vs.describe_write_result(wk.WriteResult(wk.Command(1), 1, "REJECTED"))
        self.assertEqual(rej.level, vs.LEVEL_WARN)
        self.assertIn("REJECTED", rej.text)
        unk = vs.describe_write_result(wk.WriteResult(req, None, "不明", "応答なし", True))
        self.assertEqual(unk.level, vs.LEVEL_ERROR)
        self.assertIn("実行されたか不明", unk.text)
        err = vs.describe_write_result(wk.WriteResult(req, None, "", "未接続です"))
        self.assertEqual(err.level, vs.LEVEL_ERROR)
        self.assertIn("未接続です", err.text)
        bc = vs.describe_write_result(wk.WriteResult(wk.Broadcast(4), None, "送信済み（応答なし）"))
        self.assertEqual(bc.level, vs.LEVEL_INFO)
        self.assertIn("ESTOP", bc.text)
        w = vs.describe_write_result(wk.WriteResult(wk.WriteReg("MOVE_REL", 100), 0, "OK"))
        self.assertIn("MOVE_REL = 100", w.text)

    def test_log_entries(self) -> None:
        self.assertEqual(vs.log_entries(wk.Values(0.0, {})), [])
        self.assertEqual(vs.log_entries(wk.Stats(0.0, True, 0, 0, 0, 0, 0.0, 0.0, {})), [])
        d = vs.log_entries(wk.Disconnected("ポートが使えません", True))[0]
        self.assertEqual(d.level, vs.LEVEL_WARN)
        self.assertIn("再接続", d.text)
        self.assertIn("ポートが使えません", d.text)
        n = vs.log_entries(wk.Notice("読み出し失敗"))[0]
        self.assertEqual(n.level, vs.LEVEL_WARN)
        self.assertEqual(len(vs.stamp(0.0)), 12)


class OpsTest(unittest.TestCase):
    """操作の受付条件（GUI 側の目安。最終判断は基板）。"""

    @staticmethod
    def av(state=2, moving=False, homing=False, j10=2, connected=True):
        status = 1 if moving else 0
        inp = vs.INPUT_HOMING if homing else 0
        return vs.op_availability(vs.OpsContext(connected, state, status, inp, j10))

    @staticmethod
    def enabled(av):
        return {k for k, v in av.items() if v.enabled}

    def test_disconnected_all_disabled(self) -> None:
        av = self.av(connected=False)
        self.assertEqual(self.enabled(av), set())
        self.assertTrue(all("未接続" in v.reason for v in av.values()))
        self.assertEqual(set(av), set(vs.ALL_OPS))

    def test_unknown_values_disabled_with_reason(self) -> None:
        av = vs.op_availability(vs.OpsContext(True))
        self.assertEqual(self.enabled(av), set())
        self.assertTrue(all(v.reason for v in av.values()))

    def test_state_commands(self) -> None:
        cmds = {"ENABLE", "DISABLE", "CLEAR_FAULT"}
        self.assertEqual(self.enabled(self.av(state=1)) & cmds, {"ENABLE"})
        self.assertEqual(self.enabled(self.av(state=2)) & cmds, {"DISABLE"})
        self.assertEqual(self.enabled(self.av(state=3)) & cmds, {"CLEAR_FAULT"})
        self.assertEqual(self.enabled(self.av(state=0)) & cmds, set())
        self.assertIn("DISABLED", self.av(state=2)["ENABLE"].reason)

    def test_motion_needs_enabled_and_not_homing(self) -> None:
        motion = {"MOVE_ABS", "MOVE_REL", "RUN_SPEED"}
        self.assertEqual(self.enabled(self.av(state=2, moving=True)) & motion, motion)   # 動作中でも指令は可
        self.assertEqual(self.enabled(self.av(state=1)) & motion, set())
        self.assertIn("ENABLED", self.av(state=1)["MOVE_ABS"].reason)
        av = self.av(state=2, homing=True)
        self.assertEqual(self.enabled(av) & motion, set())
        self.assertIn("原点復帰中", av["MOVE_REL"].reason)
        self.assertTrue(av["RUN_SPEED_ZERO"].enabled)       # 速度 0 は原点復帰中でも可
        self.assertFalse(self.av(state=1)["RUN_SPEED_ZERO"].enabled)

    def test_position_ops_need_stopped(self) -> None:
        ops = {"SET_POSITION", "SYNC_POSITION", "RESET_ELEC_ANGLE"}
        self.assertEqual(self.enabled(self.av(state=2)) & ops, ops)
        self.assertEqual(self.enabled(self.av(state=2, moving=True)) & ops, set())
        self.assertIn("動作中", self.av(state=2, moving=True)["SET_POSITION"].reason)
        self.assertEqual(self.enabled(self.av(state=2, homing=True)) & ops, set())
        # SET/SYNC は状態を問わない。RESET_ELEC_ANGLE は ENABLED が必要
        self.assertEqual(self.enabled(self.av(state=1)) & ops, {"SET_POSITION", "SYNC_POSITION"})

    def test_home_needs_enabled_stopped_j10(self) -> None:
        home = {"HOME_NEG", "HOME_POS"}
        self.assertEqual(self.enabled(self.av()) & home, home)
        self.assertEqual(self.enabled(self.av(state=1)) & home, set())
        self.assertEqual(self.enabled(self.av(moving=True)) & home, set())
        av = self.av(j10=1)
        self.assertEqual(self.enabled(av) & home, set())
        self.assertIn("J10_MODE", av["HOME_NEG"].reason)
        self.assertEqual(self.enabled(self.av(j10=None)) & home, set())

    def test_settings_cmd(self) -> None:
        self.assertTrue(vs.settings_cmd_availability(True, 0).enabled)
        self.assertTrue(vs.settings_cmd_availability(True, 0x80).enabled)
        self.assertFalse(vs.settings_cmd_availability(True, 1).enabled)
        self.assertIn("動作中", vs.settings_cmd_availability(True, 1).reason)
        self.assertFalse(vs.settings_cmd_availability(False, 0).enabled)
        self.assertFalse(vs.settings_cmd_availability(True, None).enabled)

    def test_ops_context_from_model(self) -> None:
        class M:
            def value(self, key):
                return {"reg.STATE": 2, "reg.STATUS": 1}.get(key)

        ctx = vs.ops_context(M(), True)
        self.assertEqual((ctx.state, ctx.moving, ctx.homing, ctx.j10_mode), (2, True, None, None))


class SettingsViewTest(unittest.TestCase):
    def test_range_text(self) -> None:
        self.assertEqual(vs.range_text(ITEMS["reg.RUN_CURRENT"]), "250〜3000")
        self.assertEqual(vs.range_text(ITEMS["reg.MICROSTEP"]), "1 / 2 / 4 / 8 / 16 / 32")
        self.assertIn("2=HOME", vs.range_text(ITEMS["reg.J10_MODE"]))
        self.assertEqual(vs.range_text(ITEMS["reg.FLAGS"]), "0x00〜0x1F")

    def test_write_result_text(self) -> None:
        w = wk.WriteReg("reg.RUN_CURRENT", 1)
        self.assertEqual(vs.write_result_text(wk.WriteResult(w, 0, "OK")), "OK")
        self.assertEqual(vs.write_result_text(wk.WriteResult(w, 1, "REJECTED")), "REJECTED")
        self.assertEqual(vs.write_result_text(wk.WriteResult(w, 2, "INVALID")), "INVALID")
        self.assertIn("不明", vs.write_result_text(wk.WriteResult(w, None, "不明", "無応答", True)))
        self.assertIn("失敗", vs.write_result_text(wk.WriteResult(w, None, "", "範囲外")))

    def test_tab_kinds(self) -> None:
        kinds = [t.kind for t in vs.tab_specs()]
        self.assertEqual(kinds[:2], [vs.TAB_OPS, vs.TAB_SETTINGS])
        self.assertEqual(kinds[-2:], [vs.TAB_SYMS, vs.TAB_GRAPH])


class StatsTest(unittest.TestCase):
    def test_format(self) -> None:
        s = wk.Stats(0.0, True, 120, 2, 3, 1, 1.25, 4.5, {"overview": 0.052})
        t = vs.format_stats(s)
        for part in ("要求 120", "タイムアウト 2", "再送 3", "エラー 1", "平均 1.2", "最大 4.5", "52 ms"):
            self.assertIn(part, t)
        self.assertIn("—", vs.format_stats(wk.Stats(0.0, False, 0, 0, 0, 0, 0.0, 0.0, {})))


if __name__ == "__main__":
    unittest.main()
