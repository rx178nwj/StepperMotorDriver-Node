"""nemagui.readplan の試験。"""

import struct
import unittest

from nemadbg import registers as regs
from nemadbg.link import SymInfo
from nemadbg.sim import SYMBOLS
from nemagui import items as im
from nemagui import readplan as rp

ITEMS = im.items_by_key(im.build_items([SymInfo(i, e.addr, e.type, e.num, e.name) for i, e in enumerate(SYMBOLS)]))
SYMTAB = {e.name: SymInfo(i, e.addr, e.type, e.num, e.name) for i, e in enumerate(SYMBOLS)}


def pick(*keys):
    return [ITEMS[k] for k in keys]


def sym_item(name, addr, num=1, atomic=False, typ=0x05):
    info = SymInfo(0, addr, typ, num, name)
    it = im.Item(key="sym." + name, label=name, group="other", source="sym", ctype="u32" if typ == 5 else "bytes",
                 sym=name, size=num if typ == 0x10 else 0, atomic=atomic)
    return it, info


class RegPlanTest(unittest.TestCase):
    def test_merge_within_six_bytes(self) -> None:
        ops = rp.plan_reads(pick("reg.STATE", "reg.FAULT", "reg.STATUS", "reg.CMD_RESULT"), {})
        self.assertEqual([(o.kind, o.addr, o.size) for o in ops], [("reg", 4, 4)])
        self.assertEqual(ops[0].slices, (("reg.STATE", 0, 1), ("reg.FAULT", 1, 1), ("reg.STATUS", 2, 1),
                                         ("reg.CMD_RESULT", 3, 1)))

    def test_never_exceeds_read_max(self) -> None:
        keys = [k for k, i in ITEMS.items() if i.source == "reg"]
        ops = rp.plan_reads([ITEMS[k] for k in keys], {})
        self.assertTrue(all(o.kind == "reg" and 1 <= o.size <= regs.READ_MAX for o in ops))
        covered = sorted(k for o in ops for k in o.keys)
        self.assertEqual(covered, sorted(keys))

    def test_gap_is_spanned(self) -> None:
        # VM_MV(0x10, 2 B) と AD_IN_MV(0x16, 2 B) は 0x10〜0x17 で 8 B なのでまとまらない。BOARD_TEMP(0x12) までは 4 B
        ops = rp.plan_reads(pick("reg.VM_MV", "reg.MCU_TEMP"), {})
        self.assertEqual([(o.addr, o.size) for o in ops], [(0x10, 6)])
        self.assertEqual(ops[0].slices[1], ("reg.MCU_TEMP", 4, 2))
        ops = rp.plan_reads(pick("reg.VM_MV", "reg.AD_IN_MV"), {})
        self.assertEqual(len(ops), 2)

    def test_split_at_boundary(self) -> None:
        ops = rp.plan_reads(pick("reg.POSITION", "reg.SPEED"), {})
        self.assertEqual([(o.addr, o.size) for o in ops], [(8, 4), (12, 4)])

    def test_extract(self) -> None:
        op = rp.plan_reads(pick("reg.STATE", "reg.FAULT"), {})[0]
        self.assertEqual(rp.extract(op, b"\x02\x05"), {"reg.STATE": b"\x02", "reg.FAULT": b"\x05"})
        self.assertEqual(rp.extract(op, b"\x02"), {"reg.STATE": b"\x02"})   # 短いデータは足りない項目を除く

    def test_duplicates_collapsed(self) -> None:
        ops = rp.plan_reads(pick("reg.STATE", "reg.STATE"), {})
        self.assertEqual(sum(len(o.slices) for o in ops), 1)


class SymPlanTest(unittest.TestCase):
    def test_merge_small_gap(self) -> None:
        a, ia = sym_item("a", 0x20000000)
        b, ib = sym_item("b", 0x20000000 + 4 + 32)       # 隙間 32 B は含める
        ops = rp.plan_reads([b, a], {"a": ia, "b": ib})
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual((op.kind, op.addr, op.size), ("short", 0x20000000, 40))
        self.assertEqual(op.slices, (("sym.a", 0, 4), ("sym.b", 36, 4)))

    def test_split_when_gap_exceeds_threshold(self) -> None:
        a, ia = sym_item("a", 0x20000000)
        b, ib = sym_item("b", 0x20000000 + 4 + 33)
        ops = rp.plan_reads([a, b], {"a": ia, "b": ib})
        self.assertEqual(len(ops), 2)
        ops = rp.plan_reads([a, b], {"a": ia, "b": ib}, gap=33)
        self.assertEqual(len(ops), 1)

    def test_span_limit_240(self) -> None:
        its, tab = [], {}
        for n in range(5):
            it, inf = sym_item(f"v{n}", 0x20000000 + n * 100, num=100, typ=0x10)   # 100 B ずつ、隙間 0
            its.append(it)
            tab[it.sym] = inf
        ops = rp.plan_reads(its, tab)
        self.assertEqual([o.size for o in ops], [200, 200, 100])
        self.assertTrue(all(o.size <= 240 for o in ops))
        self.assertEqual(sorted(k for o in ops for k in o.keys), sorted(i.key for i in its))

    def test_atomic_is_single_sym_upload(self) -> None:
        a, ia = sym_item("a", 0x20000000, atomic=True)
        b, ib = sym_item("b", 0x20000004)
        c, ic = sym_item("c", 0x20000008, atomic=True)
        ops = rp.plan_reads([a, b, c], {"a": ia, "b": ib, "c": ic})
        kinds = [(o.kind, o.keys) for o in ops]
        self.assertEqual(kinds, [("short", ["sym.b"]), ("sym", ["sym.a"]), ("sym", ["sym.c"])])
        sym_op = ops[1]
        self.assertIs(sym_op.sym, ia)
        self.assertEqual(sym_op.size, 4)

    def test_real_items_atomic_not_merged(self) -> None:
        ops = rp.plan_reads(pick("sym.position", "sym.target_pos", "sym.state", "sym.fault"), SYMTAB)
        self.assertEqual(sorted(o.keys[0] for o in ops if o.kind == "sym"), ["sym.position", "sym.target_pos"])
        short = [o for o in ops if o.kind == "short"]
        self.assertEqual(len(short), 1)
        self.assertEqual(sorted(short[0].keys), ["sym.fault", "sym.state"])

    def test_missing_name_kept_in_plan(self) -> None:
        a, ia = sym_item("a", 0x20000000)
        ghost, _ = sym_item("ghost", 0)
        ops = rp.plan_reads([a, ghost], {"a": ia})
        self.assertEqual(rp.missing_keys(ops), ["sym.ghost"])
        self.assertEqual([o.kind for o in ops], ["short", "missing"])

    def test_apply(self) -> None:
        a, ia = sym_item("a", 0x20000000)
        b, ib = sym_item("b", 0x20000008, atomic=True)
        ghost, _ = sym_item("ghost", 0)
        ops = rp.plan_reads([a, b, ghost], {"a": ia, "b": ib})
        short, sym, miss = ops
        out = rp.apply([(short, struct.pack("<I", 7)), (sym, struct.pack("<I", 9)), (miss, b"")])
        self.assertEqual(out, {"sym.a": struct.pack("<I", 7), "sym.b": struct.pack("<I", 9)})
        self.assertEqual(rp.apply([(short, None)]), {})

    def test_overlapping_aliases(self) -> None:
        a, ia = sym_item("a", 0x20000000, num=8, typ=0x10)
        b, ib = sym_item("b", 0x20000004)
        ops = rp.plan_reads([a, b], {"a": ia, "b": ib})
        self.assertEqual([(o.addr, o.size) for o in ops], [(0x20000000, 8)])
        self.assertEqual(ops[0].slices[1], ("sym.b", 4, 4))


if __name__ == "__main__":
    unittest.main()
