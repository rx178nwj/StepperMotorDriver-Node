"""nemagui.model の試験。"""

import csv
import os
import struct
import tempfile
import unittest

from nemagui import items as im
from nemagui.model import Model


def u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


class ModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.items = im.build_items(None)
        self.m = Model(self.items, history_s=10.0, max_points=1000)

    def test_latest_and_text(self) -> None:
        self.assertEqual(self.m.text("reg.STATE"), "—")
        self.m.update(1.0, {"reg.STATE": b"\x02", "reg.BOARD_TEMP": struct.pack("<h", 253)})
        self.assertEqual(self.m.value("reg.STATE"), 2)
        self.assertEqual(self.m.text("reg.STATE"), "2 (ENABLED)")
        self.assertEqual(self.m.text("reg.BOARD_TEMP"), "25.3 °C")
        self.assertEqual(self.m.raw("reg.STATE"), b"\x02")
        self.assertEqual(self.m.sample("reg.STATE").time, 1.0)
        self.m.update(2.0, {"reg.BOARD_TEMP": b"\x00\x80"})
        self.assertIsNone(self.m.value("reg.BOARD_TEMP"))
        self.assertEqual(self.m.text("reg.BOARD_TEMP"), "無効")

    def test_unknown_and_short_ignored(self) -> None:
        self.m.update(1.0, {"reg.NOPE": b"\x01", "reg.POSITION": b"\x00"})
        self.assertIsNone(self.m.sample("reg.NOPE"))
        self.assertIsNone(self.m.sample("reg.POSITION"))

    def test_history_and_age_limit(self) -> None:
        for n in range(30):
            self.m.update(float(n), {"reg.POSITION": struct.pack("<i", n * 10)})
        ts, vs = self.m.history("reg.POSITION")
        self.assertEqual(ts[-1], 29.0)
        self.assertEqual(vs[-1], 290.0)
        self.assertGreaterEqual(ts[0], 29.0 - 10.0)      # 10 秒より古い点は捨てる
        self.assertEqual(len(ts), 11)
        self.assertEqual(self.m.history("reg.STATE"), ([], []))

    def test_history_max_points(self) -> None:
        m = Model(self.items, history_s=1e9, max_points=5)
        for n in range(20):
            m.update(float(n), {"reg.POSITION": struct.pack("<i", n)})
        ts, vs = m.history("reg.POSITION")
        self.assertEqual(vs, [15.0, 16.0, 17.0, 18.0, 19.0])

    def test_invalid_not_in_history(self) -> None:
        self.m.update(1.0, {"reg.BOARD_TEMP": struct.pack("<h", 200)})
        self.m.update(2.0, {"reg.BOARD_TEMP": b"\x00\x80"})
        ts, vs = self.m.history("reg.BOARD_TEMP")
        self.assertEqual(len(ts), 1)
        self.assertAlmostEqual(vs[0], 20.0)

    def test_rate(self) -> None:
        k = "sym.usb_stats.rx_frames"
        self.assertIsNone(self.m.rate(k))
        self.m.update(0.0, {k: u32(100)})
        self.assertIsNone(self.m.rate(k))
        self.m.update(0.5, {k: u32(150)})      # 窓（1 s）に満たない
        self.assertIsNone(self.m.rate(k))
        self.m.update(1.0, {k: u32(300)})
        self.assertAlmostEqual(self.m.rate(k), 200.0)
        self.m.update(3.0, {k: u32(500)})
        self.assertAlmostEqual(self.m.rate(k), 100.0)

    def test_rate_wraps_at_2_32(self) -> None:
        k = "sym.comm_stats.rx_frames"
        self.m.update(0.0, {k: u32(0xFFFFFFF0)})
        self.m.update(1.0, {k: u32(0x10)})
        self.assertAlmostEqual(self.m.rate(k), 32.0)

    def test_rate_only_for_counters(self) -> None:
        self.m.update(0.0, {"sym.uwTick": u32(0)})
        self.m.update(2.0, {"sym.uwTick": u32(2000)})
        self.assertIsNone(self.m.rate("sym.uwTick"))

    def test_reset(self) -> None:
        self.m.update(1.0, {"reg.STATE": b"\x02", "sym.usb_stats.rx_frames": u32(1)})
        self.m.reset()
        self.assertIsNone(self.m.sample("reg.STATE"))
        self.assertEqual(self.m.history("reg.STATE"), ([], []))
        self.m.update(5.0, {"sym.usb_stats.rx_frames": u32(1)})
        self.assertIsNone(self.m.rate("sym.usb_stats.rx_frames"))


class CsvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.m = Model(im.build_items(None))
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def read(self):
        self.m.stop_csv()
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))

    def test_header_and_rows(self) -> None:
        keys = ["reg.POSITION", "reg.BOARD_TEMP", "reg.STATE", "reg.VM_MV"]
        self.m.start_csv(self.path, keys)
        self.assertTrue(self.m.csv_active)
        self.m.update(1000.0, {"reg.POSITION": struct.pack("<i", -5), "reg.BOARD_TEMP": struct.pack("<h", 253)})
        self.m.update(1000.5, {"reg.STATE": b"\x02", "reg.BOARD_TEMP": b"\x00\x80"})
        rows = self.read()
        self.assertFalse(self.m.csv_active)
        self.assertEqual(rows[0], ["時刻", "経過[s]", "位置[step]", "基板温度[°C]", "状態", "モータ電圧[mV]"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][1:], ["0.000", "-5", "25.3", "", ""])         # 未取得は空欄
        self.assertEqual(rows[2][1:], ["0.500", "-5", "", "2", ""])            # 無効は空欄、他は直前の値を保つ

    def test_ignores_unknown_keys_and_restart(self) -> None:
        self.m.start_csv(self.path, ["reg.POSITION", "reg.NOPE"])
        self.m.update(1.0, {"reg.POSITION": struct.pack("<i", 1)})
        self.m.start_csv(self.path, ["reg.STATE"])          # 開き直すと上書き
        self.m.update(2.0, {"reg.STATE": b"\x01"})
        rows = self.read()
        self.assertEqual(rows[0][2:], ["状態"])
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
