"""nemagui.graphmodel（Qt 非依存のグラフ判断ロジック）の試験。"""

import unittest
from datetime import datetime

from nemagui import graphmodel as gm
from nemagui import items as itemsmod

ITEMS = itemsmod.items_by_key(itemsmod.build_items())


class PresetTest(unittest.TestCase):
    def test_resolve(self) -> None:
        keys = gm.resolve_preset("運動", ITEMS)
        self.assertEqual(keys, ["reg.POSITION", "reg.SPEED", "sym.cur_speed", "sym.plan_speed", "sym.target_pos"])
        self.assertEqual(gm.resolve_preset("運動", ["reg.SPEED", "x"]), ["reg.SPEED"])   # 無い key は黙って除く
        self.assertEqual(gm.resolve_preset("なし", ITEMS), [])
        self.assertEqual(len(gm.preset_names()), 5)

    def test_all_preset_keys_exist_and_numeric(self) -> None:
        for name, keys in gm.PRESETS:
            for k in keys:
                self.assertIn(k, ITEMS, (name, k))
                self.assertTrue(ITEMS[k].numeric, k)


class LayoutTest(unittest.TestCase):
    def test_by_unit_and_state(self) -> None:
        specs = gm.plot_layout(ITEMS, ["sym.home.state", "reg.POSITION", "reg.SPEED", "sym.cur_speed", "sym.target_pos",
                                       "sym.input_status", "reg.VM_MV"])
        self.assertEqual([s.unit for s in specs], ["step", "step/s", "mV", ""])
        self.assertEqual(specs[0].keys, ("reg.POSITION", "sym.target_pos"))
        self.assertEqual(specs[1].keys, ("reg.SPEED", "sym.cur_speed"))
        self.assertTrue(specs[3].is_state)
        self.assertEqual(specs[3].keys, ("sym.home.state", "sym.input_status"))
        self.assertEqual(specs[3].title, gm.STATE_LABEL)

    def test_excludes_unknown_bytes_dupes(self) -> None:
        blob = itemsmod.Item("sym.blob", "ブロブ", "other", "sym", "bytes", sym="blob", size=4)
        items = dict(ITEMS, **{blob.key: blob})
        self.assertNotIn(blob, gm.numeric_items(items.values()))
        specs = gm.plot_layout(items, ["nothing", "sym.blob", "reg.POSITION", "reg.POSITION"])
        self.assertEqual([s.keys for s in specs], [("reg.POSITION",)])
        self.assertEqual(gm.plot_layout(ITEMS, []), [])


class CandidateTest(unittest.TestCase):
    def test_order_and_filter(self) -> None:
        order = gm.candidate_order(ITEMS.values(), ["sym.cur_speed", "reg.POSITION", "nothing"])
        self.assertEqual([i.key for i in order[:2]], ["sym.cur_speed", "reg.POSITION"])
        self.assertTrue(all(i.numeric for i in order))
        rest = [i.key for i in order[2:]]
        self.assertEqual(rest, sorted(rest))
        self.assertNotIn("sym.cur_speed", rest)
        it = ITEMS["reg.VM_MV"]
        self.assertTrue(gm.matches_filter(it, ""))
        self.assertTrue(gm.matches_filter(it, "vm_mv"))
        self.assertTrue(gm.matches_filter(it, "reg mv"))
        self.assertFalse(gm.matches_filter(it, "zzz"))


class TimeTest(unittest.TestCase):
    def test_window_slice(self) -> None:
        ts = [float(t) for t in range(100, 111)]      # 100..110
        vs = [t * 2 for t in ts]
        xs, ys = gm.window_slice(ts, vs, now=110.0, window_s=3.0)
        self.assertEqual(xs, [-4.0, -3.0, -2.0, -1.0, 0.0])    # 窓の左端の直前の 1 点（106）を含む
        self.assertEqual(ys[-1], 220.0)
        xs, ys = gm.window_slice(ts, vs, now=110.0, window_s=100.0)
        self.assertEqual(len(xs), 11)
        self.assertEqual(xs[0], -10.0)
        # now より新しい点は含めない
        xs, _ = gm.window_slice(ts, vs, now=105.5, window_s=2.0)
        self.assertEqual(xs[-1], -0.5)
        self.assertEqual(gm.window_slice([], [], 1.0, 1.0), ([], []))

    def test_window_slice_start(self) -> None:
        ts = [1.0, 2.0, 3.0, 4.0]
        xs, ys = gm.window_slice(ts, ts, now=4.0, window_s=10.0, start=2.5)   # クリア以前は出さない
        self.assertEqual(xs, [-1.0, 0.0])
        self.assertEqual(ys, [3.0, 4.0])

    def test_step_xy(self) -> None:
        x, y = gm.step_xy([-3.0, -2.0, -1.0], [1.0, 2.0, 2.0], 0.0)
        self.assertEqual(x, [-3.0, -2.0, -2.0, -1.0, -1.0, 0.0])
        self.assertEqual(y, [1.0, 1.0, 2.0, 2.0, 2.0, 2.0])
        self.assertEqual(gm.step_xy([], [], 0.0), ([], []))

    def test_value_at(self) -> None:
        xs, ys = [-3.0, -2.0, -1.0], [10.0, 20.0, 30.0]
        self.assertIsNone(gm.value_at(xs, ys, -4.0))
        self.assertEqual(gm.value_at(xs, ys, -2.0), 20.0)
        self.assertEqual(gm.value_at(xs, ys, -1.5), 20.0)
        self.assertEqual(gm.value_at(xs, ys, 5.0), 30.0)


class MiscTest(unittest.TestCase):
    def test_names_and_period(self) -> None:
        d = datetime(2026, 10, 24, 9, 5, 7)
        self.assertEqual(gm.default_csv_name(d), "nemagui_20261024_090507.csv")
        self.assertEqual(gm.default_png_name(d), "nemagui_20261024_090507.png")
        self.assertEqual(gm.format_elapsed(3725.9), "01:02:05")
        self.assertEqual(gm.format_elapsed(-1), "00:00:00")
        self.assertEqual(gm.clamp_period(0.001), gm.GRAPH_PERIOD_MIN_S)
        self.assertEqual(gm.clamp_period(10), gm.GRAPH_PERIOD_MAX_S)
        self.assertEqual(gm.clamp_period(0.1), 0.1)
        self.assertEqual(gm.series_color(0), gm.series_color(len(gm.PALETTE)))


if __name__ == "__main__":
    unittest.main()
