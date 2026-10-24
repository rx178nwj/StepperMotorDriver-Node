"""nemagui.items の試験。"""

import struct
import unittest

from nemadbg import registers as regs
from nemadbg.link import SymInfo
from nemadbg.sim import SYMBOLS
from nemagui import items as im


def sim_infos():
    return [SymInfo(i, e.addr, e.type, e.num, e.name) for i, e in enumerate(SYMBOLS)]


class StaticDefinitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.items = im.build_items(sim_infos())
        self.by_key = im.items_by_key(self.items)

    def test_keys_unique(self) -> None:
        self.assertEqual(len(self.items), len(self.by_key))

    def test_sym_names_exist_in_firmware_table(self) -> None:
        names = {e.name for e in SYMBOLS}
        for it in self.items:
            if it.source == "sym":
                self.assertIn(it.sym, names, it.key)

    def test_sym_ctype_matches_firmware_type(self) -> None:
        by_name = {e.name: e for e in SYMBOLS}
        for it in self.items:
            if it.source == "sym":
                e = by_name[it.sym]
                ct = im._TYPE_TO_CTYPE[e.type]
                self.assertEqual(it.ctype, ct if e.num == 1 else "bytes", it.key)

    def test_reg_items_match_registers(self) -> None:
        table = {r.name: r for r in regs.REGISTERS}
        for it in self.items:
            if it.source != "reg":
                continue
            self.assertEqual(it.key, "reg." + it.reg.name)
            self.assertIs(it.reg, table[it.reg.name])
            self.assertEqual(it.nbytes, it.reg.size)
            self.assertEqual(it.ctype[0] == "i", it.reg.signed)
            self.assertEqual(it.bits, it.reg.bits)
            self.assertEqual(it.enum, it.reg.enum)
            self.assertNotEqual(it.reg.access, "W")

    def test_settings_are_all_rw_registers(self) -> None:
        rw = {r.name for r in regs.REGISTERS if r.access == "RW"}
        got = {i.reg.name for i in self.items if i.group == "settings"}
        self.assertEqual(got, rw)
        for i in self.items:
            if i.group == "settings":
                self.assertTrue(i.writable)
                self.assertIsNotNone(i.min)
                self.assertIsNotNone(i.max)

    def test_overview_registers(self) -> None:
        names = [i.reg.name for i in self.items if i.group == "overview"]
        self.assertEqual(names, ["STATE", "FAULT", "STATUS", "CMD_RESULT", "POSITION", "SPEED", "VM_MV", "BOARD_TEMP",
                                 "MCU_TEMP", "AD_IN_MV", "CONFIG_STATUS", "SENSOR_STATUS", "SENSOR_RAW", "SHAFT_ANGLE",
                                 "INPUT_STATUS", "ENC_COUNT"])

    def test_groups_order_and_labels(self) -> None:
        order = [g for n, g in enumerate(im.GROUPS)]
        seen = []
        for i in self.items:
            if not seen or seen[-1] != i.group:
                seen.append(i.group)
        self.assertEqual(seen, [g for g in order if g in seen])
        self.assertEqual(set(im.GROUPS), set(im.GROUP_LABELS))

    def test_atomic(self) -> None:
        for n in ("position", "cur_speed", "target_pos", "target_vel", "plan_speed", "commit_pos", "uwTick",
                  "enc_count", "home.start_pos"):
            self.assertTrue(self.by_key["sym." + n].atomic, n)
        self.assertFalse(self.by_key["sym.state"].atomic)
        self.assertFalse(self.by_key["reg.POSITION"].atomic)

    def test_counters(self) -> None:
        for it in self.items:
            if it.sym and (it.sym.startswith("comm_stats.") or it.sym.startswith("usb_stats.")):
                self.assertEqual(it.counter, it.sym != "comm_stats.err_last_code", it.key)
        self.assertFalse(self.by_key["sym.uwTick"].counter)

    def test_other_gets_undefined_names(self) -> None:
        other = {i.sym: i for i in self.items if i.group == "other"}
        self.assertIn("cfg", other)
        self.assertEqual((other["cfg"].ctype, other["cfg"].nbytes), ("bytes", 40))
        self.assertEqual(other["comm_stats"].nbytes, 36)
        self.assertEqual(other["usb_stats"].nbytes, 32)
        self.assertEqual(other["cfg.run_current_ma"].ctype, "u16")
        self.assertNotIn("position", other)
        # 静的に定義した名前は other に重複しない
        self.assertEqual(len([i for i in self.items if i.sym == "position"]), 1)

    def test_unknown_name_goes_to_other(self) -> None:
        infos = [SymInfo(0, 0x20000000, 0x07, 1, "newvar"), SymInfo(1, 0x20000010, 0x03, 4, "arr"),
                 SymInfo(2, 0x20000020, 0x04, 1, "position")]
        items = im.build_items(infos)
        other = {i.sym: i for i in items if i.group == "other"}
        self.assertEqual(set(other), {"newvar", "arr"})
        self.assertEqual(other["newvar"].ctype, "f32")
        self.assertEqual((other["arr"].ctype, other["arr"].nbytes), ("bytes", 8))

    def test_without_symtab_static_only(self) -> None:
        items = im.build_items(None)
        self.assertFalse([i for i in items if i.group == "other"])


class DecodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.by_key = im.items_by_key(im.build_items(None))

    def test_temperature_scale_and_invalid(self) -> None:
        it = self.by_key["reg.BOARD_TEMP"]
        self.assertAlmostEqual(im.decode(it, struct.pack("<h", 253)), 25.3)
        self.assertAlmostEqual(im.decode(it, struct.pack("<h", -50)), -5.0)
        self.assertIsNone(im.decode(it, b"\x00\x80"))
        self.assertEqual(im.format_value(it, 25.3), "25.3 °C")
        self.assertEqual(im.format_value(it, None), "無効")
        # MCU 温度に無効値は無い
        self.assertAlmostEqual(im.decode(self.by_key["reg.MCU_TEMP"], b"\x00\x80"), -3276.8)

    def test_invalid_ffff(self) -> None:
        for k in ("reg.AD_IN_MV", "reg.SENSOR_RAW", "reg.SHAFT_ANGLE", "sym.mon.ad_in_mv", "sym.shaft_angle"):
            it = self.by_key[k]
            self.assertIsNone(im.decode(it, b"\xff\xff\x00\x00"), k)
        self.assertEqual(im.decode(self.by_key["reg.AD_IN_MV"], struct.pack("<H", 1234)), 1234)
        self.assertAlmostEqual(im.decode(self.by_key["reg.SHAFT_ANGLE"], struct.pack("<H", 9000)), 90.0)
        self.assertEqual(im.format_value(self.by_key["reg.SHAFT_ANGLE"], 90.0), "90.00 °")

    def test_signed_and_unsigned(self) -> None:
        self.assertEqual(im.decode(self.by_key["reg.POSITION"], struct.pack("<i", -1000)), -1000)
        self.assertEqual(im.decode(self.by_key["reg.VM_MV"], struct.pack("<H", 24000)), 24000)
        self.assertEqual(im.format_value(self.by_key["reg.POSITION"], -1000), "-1000 step")

    def test_f32(self) -> None:
        it = self.by_key["sym.cur_speed"]
        self.assertAlmostEqual(im.decode(it, struct.pack("<f", 1234.5)), 1234.5)
        self.assertEqual(im.format_value(it, 1234.5), "1234.5 step/s")

    def test_bits_and_enum_names(self) -> None:
        st = self.by_key["reg.STATUS"]
        self.assertEqual(im.format_value(st, 0x05), "0x05 [MOVING, EXT_5V]")
        self.assertEqual(im.format_value(st, 0), "0x00 [-]")
        self.assertEqual(im.format_value(self.by_key["reg.STATE"], 2), "2 (ENABLED)")
        self.assertEqual(im.format_value(self.by_key["reg.STATE"], 9), "9 (不明)")
        self.assertEqual(im.format_value(self.by_key["sym.home.state"], 6), "6 (FAILED)")
        self.assertEqual(im.format_value(self.by_key["sym.mode"], 1), "1 (POS)")
        self.assertEqual(im.format_value(self.by_key["reg.PA7_MODE"], 2), "2 (Z)")
        rc = self.by_key["sym.reset_csr"]
        self.assertEqual(im.format_value(rc, 0x0C000000), "0x0C000000 [PINRSTF, BORRSTF]")
        self.assertEqual(im.format_value(rc, 1 << 31), "0x80000000 [LPWRRSTF]")
        self.assertEqual([n for _, n in rc.bits][0], "FWRSTF")

    def test_bytes(self) -> None:
        it = im.build_items([SymInfo(0, 0, 0x10, 4, "blob")])[-1]
        self.assertEqual(im.decode(it, b"\x01\x02\x03\x04"), b"\x01\x02\x03\x04")
        self.assertEqual(im.format_value(it, b"\x01\xab"), "01 ab")

    def test_short_raw(self) -> None:
        with self.assertRaises(ValueError):
            im.decode(self.by_key["reg.POSITION"], b"\x00\x00")


class EncodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.by_key = im.items_by_key(im.build_items(None))

    def test_encode_values(self) -> None:
        self.assertEqual(im.encode(self.by_key["reg.RUN_CURRENT"], 1500), struct.pack("<H", 1500))
        self.assertEqual(im.encode(self.by_key["reg.MAX_SPEED"], 3200), struct.pack("<I", 3200))
        self.assertEqual(im.encode(self.by_key["reg.FLAGS"], 0x1F), b"\x1f")
        self.assertEqual(im.encode(self.by_key["reg.RUN_CURRENT"], 1500.0), struct.pack("<H", 1500))

    def test_out_of_range(self) -> None:
        for key, v in (("reg.RUN_CURRENT", 249), ("reg.RUN_CURRENT", 3001), ("reg.FLAGS", 0x20),
                       ("reg.MAX_SPEED", 0), ("reg.MAX_SPEED", 100001), ("reg.PA7_MODE", 3), ("reg.GEAR_RATIO", 1001)):
            with self.assertRaises(ValueError, msg=f"{key}={v}"):
                im.encode(self.by_key[key], v)

    def test_microstep_choices(self) -> None:
        self.assertEqual(im.encode(self.by_key["reg.MICROSTEP"], 32), b"\x20")
        with self.assertRaises(ValueError):
            im.encode(self.by_key["reg.MICROSTEP"], 3)

    def test_not_integer_and_not_writable(self) -> None:
        with self.assertRaises(ValueError):
            im.encode(self.by_key["reg.RUN_CURRENT"], 1000.5)
        with self.assertRaises(ValueError):
            im.encode(self.by_key["reg.POSITION"], 0)
        with self.assertRaises(ValueError):
            im.encode(self.by_key["sym.position"], 0)

    def test_encode_reg(self) -> None:
        r = {x.name: x for x in regs.REGISTERS}
        self.assertEqual(im.encode_reg(r["MOVE_REL"], -5), struct.pack("<i", -5))
        self.assertEqual(im.encode_reg(r["COMMAND"], 1), b"\x01")
        with self.assertRaises(ValueError):
            im.encode_reg(r["COMMAND"], 256)
        with self.assertRaises(ValueError):
            im.encode_reg(r["POSITION"], 1)

    def test_scaled_encode_round_trip(self) -> None:
        # scale 付きの項目を書けるようにしたときの逆変換（試験用に書き込み可の項目を作る）
        import dataclasses
        it = dataclasses.replace(self.by_key["reg.RUN_CURRENT"], scale=0.1, min=0, max=100, ctype="i16", size=2,
                                 writable=True)
        self.assertEqual(im.encode(it, 25.3), struct.pack("<h", 253))
        self.assertAlmostEqual(im.decode(it, im.encode(it, 25.3)), 25.3)


if __name__ == "__main__":
    unittest.main()
