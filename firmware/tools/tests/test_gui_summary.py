"""nemagui.summary（要点パネル・名前表ブラウザ・ウォッチの Qt 非依存ロジック）と書き込みログ表記の試験。"""

import unittest

from nemadbg import registers as regs
from nemagui import items as itemsmod
from nemagui import summary as sm
from nemagui import viewstate as vs
from nemagui import worker as wk

ITEMS = itemsmod.build_items()
BY_KEY = itemsmod.items_by_key(ITEMS)


def csr(*names: str) -> int:
    bits = {n: b for b, n in itemsmod.RESET_CSR_BITS}
    return sum(1 << bits[n] for n in names)


class UptimeTest(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(sm.format_uptime(0), "0 日 00:00:00.000")
        self.assertEqual(sm.format_uptime(1), "0 日 00:00:00.001")
        self.assertEqual(sm.format_uptime(83_456), "0 日 00:01:23.456")
        self.assertEqual(sm.format_uptime(86_400_000 + 3_600_000 * 2 + 61_000 + 5), "1 日 02:01:01.005")
        self.assertEqual(sm.format_uptime(0xFFFFFFFF), "49 日 17:02:47.295")
        self.assertEqual(sm.format_uptime(None), "—")


class ResetCsrTest(unittest.TestCase):
    def test_power_on(self) -> None:
        info = sm.decode_reset_csr(0x0C000000)
        self.assertEqual(info.flags, ("PINRSTF", "BORRSTF"))
        self.assertEqual(info.primary, "電源投入（POR/BOR）")

    def test_priority(self) -> None:
        cases = [
            (("PINRSTF",), "NRST ピン"),
            (("PINRSTF", "SFTRSTF"), "ソフトウェア（ENTER_BOOTLOADER 等）"),       # ソフトウェアリセットは PINRSTF も立てる
            (("PINRSTF", "IWDGRSTF"), "IWDG（独立ウォッチドッグ）"),
            (("PINRSTF", "IWDGRSTF", "SFTRSTF"), "IWDG（独立ウォッチドッグ）"),
            (("PINRSTF", "WWDGRSTF"), "WWDG（ウィンドウウォッチドッグ）"),
            (("PINRSTF", "BORRSTF", "OBLRSTF"), "電源投入（POR/BOR）"),
            (("PINRSTF", "OBLRSTF"), "オプションバイトのロード"),
            (("LPWRRSTF",), "低消費電力モード移行"),
            (("FWRSTF",), "ファイアウォール"),
            (("BORRSTF", "SFTRSTF"), "ソフトウェア（ENTER_BOOTLOADER 等）"),
        ]
        for names, want in cases:
            self.assertEqual(sm.decode_reset_csr(csr(*names)).primary, want, names)

    def test_none_and_flags_order(self) -> None:
        self.assertIsNone(sm.decode_reset_csr(None))
        info = sm.decode_reset_csr(0)
        self.assertEqual(info.flags, ())
        self.assertIn("不明", info.primary)
        # 下位ビット（RCC_CSR の他の領域）は無視する
        self.assertEqual(sm.decode_reset_csr(csr("SFTRSTF") | 0x00F3).flags, ("SFTRSTF",))
        self.assertEqual(sm.decode_reset_csr(csr("IWDGRSTF", "PINRSTF")).flags, ("PINRSTF", "IWDGRSTF"))

    def test_every_cause_has_a_bit(self) -> None:
        names = {n for _b, n in itemsmod.RESET_CSR_BITS}
        self.assertEqual({n for n, _t in sm.RESET_CAUSES}, names)


class StackTest(unittest.TestCase):
    def test_percent_and_color(self) -> None:
        self.assertAlmostEqual(sm.stack_percent(704, 12288), 5.729, places=2)
        self.assertIsNone(sm.stack_percent(None, 100))
        self.assertIsNone(sm.stack_percent(10, 0))
        self.assertEqual(sm.stack_color(0.0), vs.GREEN)
        self.assertEqual(sm.stack_color(74.9), vs.GREEN)
        self.assertEqual(sm.stack_color(75.0), vs.ORANGE)
        self.assertEqual(sm.stack_color(89.9), vs.ORANGE)
        self.assertEqual(sm.stack_color(90.0), vs.RED)
        self.assertEqual(sm.stack_color(100.0), vs.RED)
        self.assertEqual(sm.stack_color(None), vs.GRAY)
        self.assertEqual(sm.stack_color(sm.stack_percent(900, 1000)), vs.RED)
        self.assertEqual(sm.stack_color(sm.stack_percent(750, 1000)), vs.ORANGE)


class HomeTest(unittest.TestCase):
    def test_stage(self) -> None:
        self.assertEqual(sm.HOME_STAGES, tuple(itemsmod.HOME_STATE_NAMES[i] for i in range(6)))
        for i in range(6):
            self.assertEqual(sm.home_stage(i), (i, False))
        self.assertEqual(sm.home_stage(6), (None, True))
        self.assertEqual(sm.home_stage(None), (None, False))
        self.assertEqual(sm.home_stage(99), (None, False))
        self.assertEqual(itemsmod.HOME_STATE_NAMES[6], "FAILED")

    def test_side_and_debounce(self) -> None:
        self.assertIn("HOME1", sm.home_side_text(1))
        self.assertIn("HOME2", sm.home_side_text(-1))
        self.assertEqual(sm.home_side_text(None), "—")
        self.assertEqual(sm.debounce_progress(0), (0, 2))
        self.assertEqual(sm.debounce_progress(1), (1, 2))
        self.assertEqual(sm.debounce_progress(200), (2, 2))      # 確定後も数え続けるが頭打ちにする
        self.assertEqual(sm.debounce_progress(None), (0, 2))

    def test_confirm_samples_matches_firmware(self) -> None:
        import re
        from pathlib import Path

        cfg = Path(__file__).resolve().parents[2] / "App" / "Inc" / "config.h"
        if not cfg.exists():
            self.skipTest("config.h がありません")
        m = re.search(r"#define\s+HOME_CONFIRM_SAMPLES\s+(\d+)", cfg.read_text(encoding="utf-8"))
        self.assertEqual(int(m.group(1)), sm.HOME_CONFIRM_SAMPLES)


class CounterTest(unittest.TestCase):
    def test_anomaly_color(self) -> None:
        k = "sym.usb_stats.crc_errors"
        self.assertIsNone(sm.anomaly_color(k, 0, None))
        self.assertIsNone(sm.anomaly_color(k, 0, 0.0))
        self.assertEqual(sm.anomaly_color(k, 3, None), vs.ORANGE)
        self.assertEqual(sm.anomaly_color(k, 3, 0.0), vs.ORANGE)
        self.assertEqual(sm.anomaly_color(k, 3, 2.0), vs.RED)
        self.assertEqual(sm.anomaly_color(k, None, 2.0), None)
        # 正常系カウンタと err_last_code は対象外
        self.assertIsNone(sm.anomaly_color("sym.usb_stats.rx_frames", 100, 50.0))
        self.assertIsNone(sm.anomaly_color("sym.comm_stats.tx_frames", 100, 50.0))
        self.assertIsNone(sm.anomaly_color(sm.LAST_CODE_KEY, 0x20, 1.0))

    def test_anomaly_set_is_exactly_the_specified_counters(self) -> None:
        want = {"rx_dropped", "tx_dropped", "err_events", "err_passive", "err_busoff", "err_rx_overrun"}
        self.assertEqual({k.split(".")[1] for k in sm.ANOMALY_COUNTERS if k.startswith("comm_stats.")}, want)
        self.assertEqual({k.split(".")[1] for k in sm.ANOMALY_COUNTERS if k.startswith("usb_stats.")},
                         {"crc_errors", "dropped", "rx_overflow", "rx_timeouts", "bad_len", "tx_dropped"})
        for k in sm.COMM_CAN_KEYS + sm.COMM_USB_KEYS:
            self.assertIn(k, BY_KEY)
            self.assertTrue(BY_KEY[k].group == "comm")

    def test_format(self) -> None:
        self.assertEqual(sm.format_counter_value(sm.LAST_CODE_KEY, 0x20), "0x00000020")
        self.assertEqual(sm.format_counter_value("sym.usb_stats.rx_frames", 20), "20")
        self.assertEqual(sm.format_counter_value("sym.usb_stats.rx_frames", None), "—")
        self.assertEqual(sm.format_rate(12.4), "+12/s")
        self.assertEqual(sm.format_rate(None), "—")


class MotionSensorTest(unittest.TestCase):
    def test_motion(self) -> None:
        self.assertEqual(sm.dir_arrow(1), "→")
        self.assertEqual(sm.dir_arrow(-1), "←")
        self.assertEqual(sm.dir_arrow(None), "—")
        self.assertEqual(sm.remaining_steps(100, 6500), 6400)
        self.assertEqual(sm.remaining_steps(100, -50), -150)
        self.assertIsNone(sm.remaining_steps(None, 5))

    def test_fail_streak(self) -> None:
        self.assertEqual(sm.fail_streak_text(0), ("0", vs.GREEN))
        self.assertEqual(sm.fail_streak_text(2), ("2", vs.ORANGE))
        self.assertEqual(sm.fail_streak_text(3)[1], vs.RED)
        self.assertIn("無効", sm.fail_streak_text(3)[0])
        self.assertEqual(sm.fail_streak_text(None)[0], "—")

    def test_as_status_bits(self) -> None:
        self.assertEqual(sm.AS_STATUS_BITS, ((3, "MH"), (4, "ML"), (5, "MD")))
        self.assertEqual(vs.lamp_color(sm.AS_STATUS_REG, "MH"), vs.ORANGE)
        self.assertEqual(vs.lamp_color(sm.AS_STATUS_REG, "MD"), vs.GREEN)
        self.assertEqual(vs.lamp_color("SENSOR_STATUS", "NO_RESPONSE"), vs.RED)


class SymBrowserLogicTest(unittest.TestCase):
    def test_filter(self) -> None:
        names = ["state", "stack.size", "usb_stats.rx_frames", "USB_X"]
        self.assertEqual(sm.filter_names(names, ""), names)
        self.assertEqual(sm.filter_names(names, "usb"), ["usb_stats.rx_frames", "USB_X"])
        self.assertEqual(sm.filter_names(names, " sta "), ["state", "stack.size", "usb_stats.rx_frames"])
        self.assertEqual(sm.filter_names(names, "zzz"), [])

    def test_hex_dump_and_read_text(self) -> None:
        d = sm.hex_dump(bytes(range(0x30, 0x30 + 20)))
        lines = d.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("0000: 30 31 32"))
        self.assertTrue(lines[0].endswith("0123456789:;<=>?"))
        self.assertTrue(lines[1].startswith("0010: 40 41 42 43"))
        pos = BY_KEY["sym.position"]
        self.assertEqual(sm.read_result_text(pos, (1234).to_bytes(4, "little")), "position = 1234 step   [d2 04 00 00]")
        self.assertIn("読めません", sm.read_result_text(pos, None))
        blob = itemsmod.Item(key="sym.cfg", label="cfg", group="other", source="sym", ctype="bytes", sym="cfg", size=40)
        t = sm.read_result_text(blob, bytes(40))
        self.assertTrue(t.startswith("cfg (40 B)\n0000: 00 00"))
        self.assertEqual(len(t.splitlines()), 1 + 3)

    def test_type_name(self) -> None:
        self.assertEqual(sm.sym_type_name(0x01), "u8")
        self.assertEqual(sm.sym_type_name(0x07), "f32")
        self.assertEqual(sm.sym_type_name(0x10), "bytes")


class WatchTest(unittest.TestCase):
    def test_encode_decode(self) -> None:
        keys = ["sym.position", "reg.RUN_CURRENT", "sym.comm_stats.rx_frames"]
        self.assertEqual(sm.decode_watch_keys(sm.encode_watch_keys(keys)), keys)
        self.assertEqual(sm.decode_watch_keys(sm.encode_watch_keys(["a"])), ["a"])    # 1 要素でも文字列化けしない
        self.assertEqual(sm.decode_watch_keys(sm.encode_watch_keys([])), [])
        self.assertEqual(sm.decode_watch_keys(""), [])
        self.assertEqual(sm.decode_watch_keys(None), [])
        self.assertEqual(sm.decode_watch_keys("{broken"), [])
        self.assertEqual(sm.decode_watch_keys('{"a": 1}'), [])
        self.assertEqual(sm.decode_watch_keys('["a", 3, "a", "b"]'), ["a", "b"])

    def test_period(self) -> None:
        self.assertEqual(sm.clamp_watch_period(0.2), 0.2)
        self.assertEqual(sm.clamp_watch_period(0.0), sm.WATCH_PERIOD_MIN_S)
        self.assertEqual(sm.clamp_watch_period(99), sm.WATCH_PERIOD_MAX_S)
        self.assertEqual(sm.clamp_watch_period("x"), sm.WATCH_PERIOD_S)
        self.assertEqual(sm.clamp_watch_period("0.5"), 0.5)       # QSettings は文字列で返すことがある

    def test_list(self) -> None:
        w = sm.WatchList(ITEMS, ["sym.position", "nonexistent", "reg.STATE", "sym.position"])
        self.assertEqual(w.keys, ["sym.position", "reg.STATE"])
        self.assertEqual(w.pending, ["nonexistent"])
        self.assertTrue(w.add("sym.state"))
        self.assertFalse(w.add("sym.state"))
        self.assertFalse(w.add("nope"))
        self.assertEqual([i.key for i in w.items], ["sym.position", "reg.STATE", "sym.state"])
        self.assertTrue(w.remove("reg.STATE"))
        self.assertFalse(w.remove("reg.STATE"))
        self.assertEqual(sm.decode_watch_keys(w.to_text()), ["sym.position", "sym.state", "nonexistent"])

    def test_pending_resolved_on_connect(self) -> None:
        w = sm.WatchList(itemsmod.build_items(), ["sym.cfg", "sym.gone"])
        self.assertEqual(w.keys, [])
        changed = w.set_items(itemsmod.build_items([_info("cfg", 0x10, 40), _info("other_var", 0x05, 1)]))
        self.assertTrue(changed)
        self.assertEqual(w.keys, ["sym.cfg"])
        self.assertEqual(w.pending, [])                           # 存在しない key は捨てる
        self.assertEqual(sm.decode_watch_keys(w.to_text()), ["sym.cfg"])


def _info(name: str, type_code: int, num: int):   # type: ignore[no-untyped-def]
    from nemadbg.link import SymInfo

    return SymInfo(0, 0x20000000, type_code, num, name)


class WriteLabelTest(unittest.TestCase):
    def label(self, target, value) -> str:
        return vs.describe_write_result(wk.WriteResult(wk.WriteReg(target, value), 0, "OK")).text

    def test_register_name_and_unit(self) -> None:
        self.assertEqual(self.label("reg.RUN_CURRENT", 1300), "書き込み RUN_CURRENT = 1300 mA: OK")
        self.assertEqual(self.label("reg.MAX_SPEED", 5000), "書き込み MAX_SPEED = 5000 step/s: OK")
        self.assertEqual(self.label("reg.MICROSTEP", 16), "書き込み MICROSTEP = 16: OK")
        self.assertEqual(self.label("reg.J10_MODE", 2), "書き込み J10_MODE = 2: OK")

    def test_ops_style_unchanged(self) -> None:
        self.assertEqual(self.label("MOVE_REL", 6400), "書き込み MOVE_REL = 6400: OK")
        self.assertEqual(self.label("RUN_SPEED", 0), "書き込み RUN_SPEED = 0: OK")
        addr = next(r.addr for r in regs.REGISTERS if r.name == "RUN_CURRENT")
        self.assertEqual(self.label(addr, 900), "書き込み RUN_CURRENT = 900 mA: OK")


class AngleTest(unittest.TestCase):
    TOTAL = (1, 200, 16)   # 3200 step/rev

    def angle(self, pos):
        return sm.motor_angle_deg(pos, *self.TOTAL)

    def test_normalize(self) -> None:
        self.assertEqual(self.angle(0), 0.0)
        self.assertEqual(self.angle(800), 90.0)
        self.assertEqual(self.angle(1600), 180.0)
        self.assertEqual(self.angle(3199), 3199 / 3200 * 360.0)
        self.assertEqual(self.angle(3200), 0.0)            # ちょうど 1 回転で 0
        self.assertEqual(self.angle(3200 * 5 + 800), 90.0)  # 累積値

    def test_negative(self) -> None:
        self.assertEqual(self.angle(-800), 270.0)
        self.assertEqual(self.angle(-3200), 0.0)
        self.assertEqual(self.angle(-1), 3199 / 3200 * 360.0)

    def test_gear_ratio(self) -> None:
        self.assertEqual(sm.motor_angle_deg(3200, 2, 200, 16), 180.0)

    def test_unknown(self) -> None:
        self.assertIsNone(sm.motor_angle_deg(None, 1, 200, 16))
        self.assertIsNone(sm.motor_angle_deg(0, None, 200, 16))
        self.assertIsNone(sm.motor_angle_deg(0, 1, None, 16))
        self.assertIsNone(sm.motor_angle_deg(0, 1, 200, None))
        self.assertIsNone(sm.motor_angle_deg(0, 0, 200, 16))
        self.assertIsNone(sm.motor_angle_deg(0, 1, 200, 0))

    def test_diff(self) -> None:
        self.assertEqual(sm.angle_diff_deg(10.0, 350.0), 20.0)
        self.assertEqual(sm.angle_diff_deg(350.0, 10.0), -20.0)
        self.assertEqual(sm.angle_diff_deg(180.0, 0.0), -180.0)
        self.assertIsNone(sm.angle_diff_deg(None, 1.0))
        self.assertIsNone(sm.angle_diff_deg(1.0, None))


if __name__ == "__main__":
    unittest.main()
