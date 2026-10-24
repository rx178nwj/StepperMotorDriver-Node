"""nemagui.settings_edit（設定タブの編集状態。Qt 非依存）の試験。"""

import unittest

from nemagui import items as itemsmod
from nemagui.settings_edit import SettingsEdit

ITEMS = [i for i in itemsmod.build_items() if i.group == "settings"]
RC, HC, MS = "reg.RUN_CURRENT", "reg.HOLD_CURRENT", "reg.MICROSTEP"


class SettingsEditTest(unittest.TestCase):
    def make(self) -> SettingsEdit:
        e = SettingsEdit(ITEMS)
        e.update_board({RC: 1000, HC: 500, MS: 16})
        return e

    def test_only_writable_items(self) -> None:
        e = SettingsEdit(itemsmod.build_items())
        self.assertEqual([i.key for i in e.items], [i.key for i in ITEMS])
        self.assertTrue(all(i.writable for i in e.items))

    def test_value_follows_board_until_edited(self) -> None:
        e = self.make()
        self.assertEqual(e.value(RC), 1000)
        e.update_board({RC: 1200})
        self.assertEqual(e.value(RC), 1200)
        e.edit(RC, 1500)
        e.update_board({RC: 1300})                  # ポーリングで編集中の値は上書きしない
        self.assertEqual(e.value(RC), 1500)
        self.assertEqual(e.board(RC), 1300)
        self.assertTrue(e.is_changed(RC))

    def test_edit_back_to_board_value_is_not_a_change(self) -> None:
        e = self.make()
        e.edit(RC, 1500)
        e.edit(RC, 1000)
        self.assertFalse(e.is_changed(RC))
        self.assertFalse(e.is_editing(RC))
        self.assertEqual(e.changed(), [])

    def test_changed_in_item_order(self) -> None:
        e = self.make()
        e.edit(MS, 8)
        e.edit(HC, 400)
        e.edit(RC, 900)
        self.assertEqual([(i.key, v) for i, v in e.changed()], [(RC, 900), (HC, 400), (MS, 8)])

    def test_unknown_board_value_is_not_a_change(self) -> None:
        e = SettingsEdit(ITEMS)
        e.edit(RC, 900)
        self.assertEqual(e.changed(), [])

    def test_write_flow(self) -> None:
        e = self.make()
        e.edit(RC, 900)
        e.edit(HC, 400)
        todo = e.begin_write()
        self.assertEqual([(i.key, v) for i, v in todo], [(RC, 900), (HC, 400)])
        self.assertTrue(e.busy)
        self.assertEqual(e.begin_write(), [])       # 書き込み中は二重に送らない
        self.assertFalse(e.record_result(RC, "OK"))
        self.assertFalse(e.record_result(MS, "OK"))  # 対象外の結果は無視
        self.assertTrue(e.record_result(HC, "REJECTED"))
        self.assertFalse(e.busy)
        self.assertEqual(e.results, {RC: "OK", HC: "REJECTED"})
        # 読み直しで OK の行は編集が外れ、REJECTED の行は残る
        e.update_board({RC: 900, HC: 500})
        self.assertEqual([(i.key, v) for i, v in e.changed()], [(HC, 400)])

    def test_abort_and_clear(self) -> None:
        e = self.make()
        e.edit(RC, 900)
        e.begin_write()
        e.abort("切断されました")
        self.assertFalse(e.busy)
        self.assertEqual(e.results[RC], "切断されました")
        e.clear_edits()
        self.assertEqual(e.results, {})
        self.assertEqual(e.value(RC), 1000)

    def test_set_items_resets_on_change(self) -> None:
        e = self.make()
        e.edit(RC, 900)
        e.set_items(ITEMS)
        self.assertTrue(e.is_editing(RC))
        e.set_items(ITEMS[:3])
        self.assertFalse(e.is_editing(RC))
        self.assertIsNone(e.board(RC))


if __name__ == "__main__":
    unittest.main()
