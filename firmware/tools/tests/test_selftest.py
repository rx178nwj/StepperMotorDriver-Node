"""nemadbg.selftest（動作確認モード）の試験。擬似デバイス（sim）を偽の時計で動かし、偽オシロ・偽 CAN バスを注入する。

実機・オシロ・CAN アダプタ・pyvisa・python-can は使わない。
"""

import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Tuple
from unittest import mock

from nemadbg import canbus, cli, scope as scopemod, selftest as st
from nemadbg import sim as simmod
from nemadbg.link import Link, LinkError
from nemadbg.sim import SimSerial

BOARD = 3


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class StatsLink(Link):
    """comm_stats.*（sim では常に 0）を偽の値で返す Link。"""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.stats = {}

    def sym_upload(self, info):
        if info.name.startswith("comm_stats.") and info.name in self.stats:
            return struct.pack("<I", self.stats[info.name])
        return super().sym_upload(info)


class FakeScope:
    """sim の状態から物理量を作る偽オシロ。故障は属性で注入する。"""

    def __init__(self, sim: SimSerial) -> None:
        self.sim = sim
        self.vref_scale = 1.0
        self.freq_scale = 1.0
        self.drop_edges = 0
        self.dir_stuck = False
        self.fail_on: Optional[str] = None
        self.calls: List[str] = []
        self.closed = False
        self._pos0 = 0

    def _b(self):
        self.sim.sync()
        return self.sim._board

    def idn(self) -> str:
        return "FAKE,DS1054Z,0,0"

    def setup_live(self) -> None:
        self.calls.append("setup_live")

    def measure(self, ch: int, item: str) -> Optional[float]:
        self.calls.append(f"measure{ch}")
        if self.fail_on == "measure":
            raise scopemod.ScopeError("fake measure error")
        b = self._b()
        if ch == 1 and item == "VAVG":
            return b.cfg.run_current_ma / 1000.0 / 1.25 * self.vref_scale
        if ch == 2 and item == "FREQ":
            return abs(b.cur_speed) * self.freq_scale
        if ch == 3 and item == "VAVG":
            return 3.3 if (self.dir_stuck or b.dir > 0) else 0.0
        return None

    def arm_capture(self, ch: int, duration_s: float, wait_s: float = 3.0) -> float:
        self.calls.append("arm")
        self._pos0 = self._b().position
        return 0.2

    def read_capture(self, ch: int, wait_s: float = 10.0) -> scopemod.Waveform:
        self.calls.append("read")
        n = max(0, abs(self._b().position - self._pos0) - self.drop_edges)
        return scopemod.Waveform((b"\x00" * 9 + b"\xa5") * n, -1e-3, 1e-6, 0.02)

    def close(self) -> None:
        self.closed = True


class FakeCan:
    """sim の CAN_REQUEST 経由で応答する偽 CAN バス（アダプタ側から見た要求→応答）。統計は StatsLink に反映する。"""

    def __init__(self, link: StatsLink, sim: SimSerial) -> None:
        self.link = link
        self.sim = sim
        self.sent: List[Tuple[int, bytes]] = []
        self.silent = False
        self.override = {}            # data(bytes) -> 応答の差し替え
        self.closed = False

    def request(self, can_id: int, data: bytes, rsp_id: int, timeout: float = 0.05) -> Optional[bytes]:
        self.sent.append((can_id, bytes(data)))
        if self.silent:
            return None
        st_ = self.link.stats
        st_["comm_stats.rx_frames"] = st_.get("comm_stats.rx_frames", 0) + 1
        frames = self.link.can_request(can_id, data)
        rsp = [f for f in frames if f.can_id == rsp_id]
        if not rsp:
            return None
        st_["comm_stats.tx_frames"] = st_.get("comm_stats.tx_frames", 0) + 1
        return self.override.get(bytes(data), rsp[0].data)

    def close(self) -> None:
        self.closed = True


class Rig:
    """偽の時計で動く sim と Link。"""

    def __init__(self, vm_mv: int = 12000, **sim_kw) -> None:
        self.clk = Clock()
        sim_kw.setdefault("noise", False)
        self.sim = SimSerial(clock=self.clk, board_id=BOARD, **sim_kw)
        self.link = StatsLink(self.sim, timeout_s=0.03)
        self.ci = self.link.connect()
        self.sim.set_vm_mv(vm_mv)
        self.sleep(0.5)
        self.scope = FakeScope(self.sim)
        self.can = FakeCan(self.link, self.sim)
        self.tmp = tempfile.TemporaryDirectory()

    def close(self) -> None:
        self.tmp.cleanup()

    def sleep(self, s: float) -> None:
        self.clk.t += s
        self.sim.sync()

    def env(self, scope: bool = True, can: bool = True) -> st.SelftestEnv:
        def scope_factory(res):
            if not scope:
                raise scopemod.ScopeUnavailable("pyvisa が未インストールです")
            return self.scope

        def can_factory(iface, ch, bitrate):
            if not can:
                raise canbus.CanUnavailable("python-can が未インストールです")
            return self.can

        return st.SelftestEnv(sleep=self.sleep, clock=self.clk, scope_factory=scope_factory, can_factory=can_factory)

    def run(self, prompt=input, scope: bool = True, can: bool = True, **opt) -> Tuple[int, st.SelfTest]:
        opt.setdefault("yes", True)
        opt.setdefault("out_dir", self.tmp.name)
        t = st.SelfTest(self.link, self.ci, st.SelftestOptions(**opt), self.env(scope, can), prompt, out=lambda s: None)
        return t.run(), t

    @property
    def board(self):
        self.sim.sync()
        return self.sim._board


def results(t: st.SelfTest) -> dict:
    return {i.id: i.result for i in t.items}


class RecordCommands:
    """Dut.command の呼び出しを記録する。"""

    def __init__(self) -> None:
        self.codes: List[int] = []
        self._orig = st.Dut.command

    def __enter__(self):
        rec, orig = self.codes, self._orig

        def command(dut, code):
            rec.append(code)
            return orig(dut, code)

        self._p = mock.patch.object(st.Dut, "command", command)
        self._p.start()
        return self

    def __exit__(self, *a) -> None:
        self._p.stop()


class UnitTest(unittest.TestCase):
    def test_fold_total(self) -> None:
        self.assertAlmostEqual(st.fold_total_deg([35000, 100, 1000]), 20.0)       # 350 -> 1 -> 10 deg
        self.assertAlmostEqual(st.fold_total_deg([100, 35000]), -11.0)
        self.assertAlmostEqual(st.fold_total_deg([0, 9000, 18000, 27000, 0]), 360.0)
        self.assertAlmostEqual(st.fold_total_deg([0xFFFF, 100, 0xFFFF, 300]), 2.0)

    def test_rising_edges(self) -> None:
        w = scopemod.Waveform((b"\x00" * 5 + b"\xa5" * 3) * 10, 0.0, 1e-6, 0.02)
        e = scopemod.rising_edges(w)
        self.assertEqual(len(e), 10)
        self.assertEqual(e[0], 5)
        # 低レベルに戻らない揺れ（0.8 V 超 2 V 未満）は数えない
        w2 = scopemod.Waveform(bytes([0, 0, 120, 70, 120, 70, 120]), 0.0, 1e-6, 0.02)
        self.assertEqual(len(scopemod.rising_edges(w2)), 1)

    def test_pick_timebase(self) -> None:
        self.assertEqual(scopemod.pick_timebase(1.2), 0.1)
        self.assertEqual(scopemod.pick_timebase(1.3), 0.2)
        self.assertAlmostEqual(scopemod.pick_timebase(0.012), 1e-3)

    def test_parse_block(self) -> None:
        data = bytes([1, 2, 3])
        self.assertEqual(scopemod._parse_block(b"#9000000003" + data + bytes([10])), data)

    def test_open_scope_without_pyvisa(self) -> None:
        with mock.patch.dict("sys.modules", {"pyvisa": None}):
            with self.assertRaises(scopemod.ScopeUnavailable):
                scopemod.open_scope()

    def test_open_bus_without_python_can(self) -> None:
        with mock.patch.dict("sys.modules", {"can": None}):
            with self.assertRaises(canbus.CanUnavailable):
                canbus.open_bus()


class GroupABTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.addCleanup(self.rig.close)

    def test_ab_pass(self) -> None:
        rc, t = self.rig.run(only="AB")
        r = results(t)
        self.assertEqual(rc, 0, [i for i in t.items if i.result == st.FAIL])
        for k in ("A.device_id", "A.vm", "A.vdda", "A.mcu_temp", "A.board_temp", "A.stack", "A.usb_errors",
                  "B.i2c_ok", "B.i2c_fail", "B.fail_streak", "B.magnet"):
            self.assertEqual(r[k], st.PASS, k)
        for k in ("A.fw", "A.loop_rate", "A.loop_max", "A.reset_csr", "B.agc"):
            self.assertEqual(r[k], st.INFO, k)
        self.assertFalse(any(k.startswith(("C.", "D.", "E.")) for k in r))

    def test_vm_out_of_range_fails_and_exit_code_1(self) -> None:
        self.rig.sim.set_vm_mv(24000)
        self.rig.sleep(0.3)
        rc, t = self.rig.run(only="A")
        self.assertEqual(rc, 1)
        self.assertEqual(results(t)["A.vm"], st.FAIL)

    def test_sensor_missing_fails(self) -> None:
        self.rig.sim.set_sensor_present(False)
        self.rig.sleep(0.2)
        rc, t = self.rig.run(only="B")
        r = results(t)
        self.assertEqual(rc, 1)
        self.assertEqual(r["B.i2c_ok"], st.FAIL)
        self.assertEqual(r["B.fail_streak"], st.FAIL)
        self.assertEqual(r["B.magnet"], st.FAIL)

    def test_usb_error_growth_fails(self) -> None:
        orig = st.SelfTest.group_b

        def noisy(self_):
            orig(self_)
            self.rig.sim._stats["crc_errors"] += 2     # 試験中に USB エラーが増えた

        with mock.patch.object(st.SelfTest, "group_b", noisy):
            rc, t = self.rig.run(only="AB")
        self.assertEqual(results(t)["A.usb_errors"], st.FAIL)
        self.assertEqual(rc, 1)


class MotionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.addCleanup(self.rig.close)

    def assert_restored(self) -> None:
        b = self.rig.board
        self.assertEqual(b.cfg.run_current_ma, 1000)
        self.assertEqual(b.cfg.max_speed, 3200)
        self.assertNotEqual(b.state, simmod.STATE_ENABLED)
        self.assertFalse(b.running)

    def test_c_pass_without_scope(self) -> None:
        with RecordCommands() as rc_:
            rc, t = self.rig.run(only="C", scope=False)
        r = results(t)
        self.assertEqual(rc, 0, [i for i in t.items if i.result == st.FAIL])
        for k in ("C.setup", "C.enable", "C.fwd_pos", "C.fwd_angle", "C.rev_pos", "C.rev_angle", "C.run_speed",
                  "C.stop", "C.restore"):
            self.assertEqual(r[k], st.PASS, k)
        self.assertEqual(r["C.fwd_sign"], st.INFO)
        self.assertNotIn(7, rc_.codes)                     # SAVE_SETTINGS は送らない
        self.assertEqual(rc_.codes[-3:], [st.CMD_STOP, st.CMD_STOP, st.CMD_DISABLE])   # 連続回転の STOP のあと shutdown
        self.assert_restored()
        d = [i for i in t.items if i.id == "C.fwd_pos"][0]
        self.assertEqual(d.value, 3200)

    def test_flash_not_modified(self) -> None:
        before = self.rig.board.saved.pack()
        self.rig.run(only="C", scope=False)
        self.assertEqual(self.rig.board.saved.pack(), before)

    def test_microstep_scales_steps(self) -> None:
        self.rig.sim._board.cfg.microstep = 8
        rc, t = self.rig.run(only="C", scope=False)
        self.assertEqual(rc, 0, [i for i in t.items if i.result == st.FAIL])
        self.assertEqual([i for i in t.items if i.id == "C.fwd_pos"][0].value, 1600)

    def test_angle_fail_when_sensor_does_not_follow(self) -> None:
        """軸が回らない（機械的に空転・センサ不良）と角度の判定が FAIL になる。"""
        self.rig.sim._board.cfg.gear_ratio = 1
        orig = simmod._Board._sensor_raw_now
        with mock.patch.object(simmod._Board, "_sensor_raw_now", lambda self_: 100):
            rc, t = self.rig.run(only="C", scope=False)
        r = results(t)
        self.assertEqual(rc, 1)
        self.assertEqual(r["C.fwd_pos"], st.PASS)
        self.assertEqual(r["C.fwd_angle"], st.FAIL)

    def test_full_rev_angle_checks_cumulative_rotation(self) -> None:
        self.rig.sim._board.cfg.gear_ratio = 1
        rc, t = self.rig.run(only="C", scope=False)
        r = results(t)
        self.assertEqual(rc, 0, [i for i in t.items if i.result == st.FAIL])
        a = [i for i in t.items if i.id == "C.fwd_angle"][0]
        self.assertAlmostEqual(a.value, 360.0, delta=5.0)

    def test_enable_rejected_no_vm(self) -> None:
        self.rig.sim.set_vm_mv(0)
        self.rig.sleep(0.5)
        rc, t = self.rig.run(only="C", scope=False)
        r = results(t)
        self.assertEqual(rc, 1)
        self.assertEqual(r["C.enable"], st.FAIL)
        self.assertNotIn("C.fwd_pos", r)
        self.assert_restored()

    def test_exception_during_run_speed_stops_and_restores(self) -> None:
        orig = st.Dut.get

        def get(dut, name):
            if name == "cur_speed":
                raise LinkError("USB が切れた")
            return orig(dut, name)

        with RecordCommands() as rc_, mock.patch.object(st.Dut, "get", get):
            rc, t = self.rig.run(only="C", scope=False)
        self.assertEqual(rc, 1)
        self.assertEqual(results(t)["C.error"], st.FAIL)
        self.assertEqual(rc_.codes[-2:], [st.CMD_STOP, st.CMD_DISABLE])
        self.assert_restored()

    def test_keyboard_interrupt_stops_and_restores(self) -> None:
        orig = st.Dut.get

        def get(dut, name):
            if name == "cur_speed":
                raise KeyboardInterrupt
            return orig(dut, name)

        with RecordCommands() as rc_, mock.patch.object(st.Dut, "get", get):
            rc, t = self.rig.run(only="CD", scope=True)
        self.assertEqual(rc, 1)
        self.assertEqual(results(t)["X.abort"], st.FAIL)
        self.assertEqual(rc_.codes[-2:], [st.CMD_STOP, st.CMD_DISABLE])
        self.assert_restored()
        self.assertTrue(self.rig.scope.closed)

    def test_prompt_declined_does_not_move(self) -> None:
        asked: List[str] = []
        start = self.rig.board.position
        with RecordCommands() as rc_:
            rc, t = self.rig.run(prompt=lambda m: asked.append(m) or "n", yes=False, only="CD")
        r = results(t)
        self.assertEqual(len(asked), 1)
        self.assertEqual((r["C.all"], r["D.all"]), (st.SKIP, st.SKIP))
        self.assertEqual(rc_.codes, [])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rig.board.position, start)

    def test_prompt_eof_skips_and_yes_skips_prompt(self) -> None:
        def eof(m):
            raise EOFError

        rc, t = self.rig.run(prompt=eof, yes=False, only="C", scope=False)
        self.assertEqual(results(t)["C.all"], st.SKIP)

        def never(m):
            raise AssertionError("--yes ではプロンプトを出さない")

        rc, t = self.rig.run(prompt=never, yes=True, only="C", scope=False)
        self.assertEqual(rc, 0)

    def test_prompt_accepted(self) -> None:
        rc, t = self.rig.run(prompt=lambda m: "y", yes=False, only="C", scope=False)
        self.assertEqual(rc, 0)
        self.assertEqual(results(t)["C.fwd_pos"], st.PASS)

    def test_no_motion_skips_c_and_d(self) -> None:
        with RecordCommands() as rc_:
            rc, t = self.rig.run(no_motion=True, only="CD")
        r = results(t)
        self.assertEqual((r["C.all"], r["D.all"]), (st.SKIP, st.SKIP))
        self.assertEqual(rc_.codes, [])
        self.assertEqual(rc, 0)

    # ---- D ----

    def test_d_pass(self) -> None:
        rc, t = self.rig.run(only="CD", current_ma=1500)
        r = results(t)
        self.assertEqual(rc, 0, [(i.id, i.value, i.note) for i in t.items if i.result == st.FAIL])
        for k in ("D.vref", "D.clk_freq", "D.clk_edges", "D.dir"):
            self.assertEqual(r[k], st.PASS, k)
        edges = [i for i in t.items if i.id == "D.clk_edges"][0]
        self.assertEqual(edges.value, 3200)
        vref = [i for i in t.items if i.id == "D.vref"][0]
        self.assertAlmostEqual(vref.value, 1500, delta=1)
        out = Path(self.rig.tmp.name)
        self.assertTrue((out / "scope_clk_edges.csv").exists())
        self.assertTrue((out / "scope_clk_wave.csv").exists())
        self.assertEqual(len((out / "scope_clk_edges.csv").read_text().splitlines()), 3201)
        self.assertTrue(self.rig.scope.closed)
        self.assert_restored()

    def test_d_only_hides_c_items(self) -> None:
        rc, t = self.rig.run(only="D")
        r = results(t)
        self.assertFalse([k for k in r if k.startswith("C.")])
        self.assertEqual(r["D.clk_edges"], st.PASS)

    def test_d_failures(self) -> None:
        cases = [("vref_scale", 0.5, "D.vref"), ("freq_scale", 1.05, "D.clk_freq"), ("drop_edges", 3, "D.clk_edges"),
                 ("dir_stuck", True, "D.dir")]
        for attr, val, key in cases:
            with self.subTest(attr):
                rig = Rig()
                self.addCleanup(rig.close)
                setattr(rig.scope, attr, val)
                rc, t = rig.run(only="CD")
                r = results(t)
                self.assertEqual(r[key], st.FAIL)
                self.assertEqual(rc, 1)
                others = [k for k in ("D.vref", "D.clk_freq", "D.clk_edges", "D.dir") if k != key]
                self.assertTrue(all(r[k] == st.PASS for k in others), r)

    def test_d_vref_tolerance_boundary(self) -> None:
        rig = Rig()
        self.addCleanup(rig.close)
        rig.scope.vref_scale = 1.09          # +9 % は合格
        _, t = rig.run(only="CD")
        self.assertEqual(results(t)["D.vref"], st.PASS)
        rig2 = Rig()
        self.addCleanup(rig2.close)
        rig2.scope.vref_scale = 1.12         # +12 % は不合格
        _, t = rig2.run(only="CD")
        self.assertEqual(results(t)["D.vref"], st.FAIL)

    def test_d_skip_when_scope_unavailable(self) -> None:
        rc, t = self.rig.run(only="CD", scope=False)
        r = results(t)
        self.assertEqual(r["D.all"], st.SKIP)
        self.assertEqual(r["C.fwd_pos"], st.PASS)
        self.assertEqual(rc, 0)
        note = [i for i in t.items if i.id == "D.all"][0].note
        self.assertIn("pyvisa", note)

    def test_d_skip_with_no_scope(self) -> None:
        rc, t = self.rig.run(only="CD", no_scope=True)
        self.assertEqual(results(t)["D.all"], st.SKIP)
        self.assertEqual(self.rig.scope.calls, [])

    def test_d_scope_comm_error_is_skip_not_pass(self) -> None:
        self.rig.scope.fail_on = "measure"
        rc, t = self.rig.run(only="CD")
        r = results(t)
        self.assertEqual(r["D.vref"], st.SKIP)
        self.assertEqual(r["D.clk_freq"], st.SKIP)
        self.assertEqual(r["D.clk_edges"], st.PASS)          # 単発取り込みは通信できていた
        self.assertEqual(r["C.run_speed"], st.PASS)          # オシロの失敗でモーター試験は続く
        self.assertEqual(rc, 0)

    def test_d_skipped_in_sim_mode(self) -> None:
        rc, t = self.rig.run(only="CD", sim=True)
        self.assertEqual(results(t)["D.all"], st.SKIP)
        self.assertEqual(self.rig.scope.calls, [])


class CanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.addCleanup(self.rig.close)

    def test_e_pass(self) -> None:
        rc, t = self.rig.run(only="E")
        r = results(t)
        self.assertEqual(rc, 0, [(i.id, i.value) for i in t.items if i.result == st.FAIL])
        for k in ("E.device_id", "E.board_id", "E.state", "E.position", "E.invalid", "E.err_busoff", "E.err_passive",
                  "E.err_rx_overrun", "E.rx_frames", "E.tx_frames"):
            self.assertEqual(r[k], st.PASS, k)
        ids = {c for c, _ in self.rig.can.sent}
        self.assertEqual(ids, {0x100 + BOARD})                 # BOARD_ID（CONNECT 応答）の要求 ID に送る
        self.assertTrue(self.rig.can.closed)
        self.assertEqual(self.rig.can.sent[0][1], bytes([2, 0, 1]))

    def test_can_node_override(self) -> None:
        rig = Rig()
        self.addCleanup(rig.close)
        rc, t = rig.run(only="E", can_node=5)
        self.assertEqual({c for c, _ in rig.can.sent}, {0x105})
        self.assertEqual(results(t)["E.device_id"], st.FAIL)   # 実機は ID=3 なので 0x105 には応答しない

    def test_e_fail_on_errors_and_wrong_reply(self) -> None:
        self.rig.link.stats["comm_stats.err_busoff"] = 1
        self.rig.can.override[bytes([2, 0, 1])] = bytes([0x82, 0x00, 0xA4])
        rc, t = self.rig.run(only="E")
        r = results(t)
        self.assertEqual(rc, 1)
        self.assertEqual(r["E.err_busoff"], st.FAIL)
        self.assertEqual(r["E.device_id"], st.FAIL)
        self.assertEqual(r["E.board_id"], st.PASS)

    def test_e_error_counter_growth_fails(self) -> None:
        orig = self.rig.can.request

        def request(can_id, data, rsp_id, timeout=0.05):
            self.rig.link.stats["comm_stats.err_passive"] = 1     # 試験中にエラーパッシブへ遷移
            return orig(can_id, data, rsp_id, timeout)

        self.rig.can.request = request
        rc, t = self.rig.run(only="E")
        self.assertEqual(results(t)["E.err_passive"], st.FAIL)

    def test_e_no_response_fails_and_stats_do_not_increase(self) -> None:
        self.rig.can.silent = True
        rc, t = self.rig.run(only="E")
        r = results(t)
        self.assertEqual(rc, 1)
        self.assertEqual(r["E.device_id"], st.FAIL)
        self.assertEqual(r["E.rx_frames"], st.FAIL)
        self.assertEqual(r["E.tx_frames"], st.FAIL)

    def test_e_skip_cases(self) -> None:
        rc, t = self.rig.run(only="E", can=False)
        self.assertEqual(results(t)["E.all"], st.SKIP)
        self.assertIn("python-can", [i for i in t.items if i.id == "E.all"][0].note)
        rc, t = self.rig.run(only="E", no_can=True)
        self.assertEqual(results(t)["E.all"], st.SKIP)
        rc, t = self.rig.run(only="E", sim=True)
        self.assertEqual(results(t)["E.all"], st.SKIP)
        self.assertEqual(rc, 0)


class CliTest(unittest.TestCase):
    def run_cli(self, *argv: str, rig: Optional[Rig] = None, **kw):
        rig = rig or Rig()
        self.addCleanup(rig.close)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["--timeout", "30", *argv], link_factory=lambda a: rig.link, selftest_env=rig.env(), **kw)
        return rc, out.getvalue(), err.getvalue(), rig

    def test_sim_abc_end_to_end_and_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rc, out, err, rig = self.run_cli("--sim", "selftest", "--yes", "--out", d)
            self.assertEqual(rc, 0, out + err)
            txt = (Path(d) / "report.txt").read_text(encoding="utf-8")
            self.assertEqual(txt.rstrip("\n").splitlines(), out.rstrip("\n").splitlines()[:-1])   # 最後はファイル名の案内
            self.assertIn("総合 PASS", txt)
            js = json.loads((Path(d) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(set(js), {"tool", "started", "finished", "device", "conditions", "items", "summary", "files"})
            self.assertEqual(js["device"], {"device_id": "0xA3", "fw": "0.4", "board_id": 3, "sim": True})
            self.assertEqual(js["conditions"]["current_ma"], 1500)
            self.assertEqual(js["summary"]["overall"], "PASS")
            self.assertEqual(js["summary"]["FAIL"], 0)
            for it in js["items"]:
                self.assertEqual(set(it), {"id", "group", "name", "result", "value", "unit", "limit", "note"})
                self.assertIn(it["result"], ("PASS", "FAIL", "SKIP", "INFO"))
                self.assertEqual(it["id"].split(".")[0], it["group"])
            groups = [it["group"] for it in js["items"]]
            self.assertEqual(groups, sorted(groups))
            by = {it["id"]: it["result"] for it in js["items"]}
            self.assertEqual((by["D.all"], by["E.all"]), ("SKIP", "SKIP"))
            self.assertEqual(by["C.fwd_pos"], "PASS")
            self.assertEqual(rig.board.cfg.run_current_ma, 1000)

    def test_exit_code_1_on_fail(self) -> None:
        rig = Rig(vm_mv=24000)
        with tempfile.TemporaryDirectory() as d:
            rc, out, err, _ = self.run_cli("selftest", "--only", "A", "--out", d, rig=rig)    # --sim なし: VM は 24 V のまま
            self.assertEqual(rc, 1)
            self.assertIn("FAIL", out)
            js = json.loads((Path(d) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(js["summary"]["overall"], "FAIL")

    def test_prompt_injection(self) -> None:
        asked: List[str] = []
        with tempfile.TemporaryDirectory() as d:
            rc, out, err, rig = self.run_cli("selftest", "--only", "C", "--no-scope", "--out", d,
                                             prompt=lambda m: asked.append(m) or "no")
            self.assertEqual(len(asked), 1)
            self.assertIn("C.all", out)
            self.assertEqual(rc, 0)
            self.assertEqual(rig.board.state, simmod.STATE_DISABLED)

    def test_default_out_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d, contextlib.chdir(d):
            rc, out, err, rig = self.run_cli("selftest", "--only", "A")
            dirs = list((Path(d) / "selftest_reports").iterdir())
            self.assertEqual(len(dirs), 1)
            self.assertRegex(dirs[0].name, r"^\d{8}-\d{6}$")
            self.assertTrue((dirs[0] / "report.json").exists())

    def test_bad_arguments(self) -> None:
        for argv in (["--current", "5000"], ["--current", "100"], ["--speed", "0"], ["--only", "AX"],
                     ["--can-node", "9"]):
            with self.subTest(argv):
                rc, out, err, _ = self.run_cli("selftest", *argv)
                self.assertEqual(rc, 1)
                self.assertIn("エラー", err)

    def test_options_reach_selftest(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rc, out, err, rig = self.run_cli("selftest", "--only", "A", "--current", "1200", "--speed", "0.5",
                                             "--can-interface", "slcan", "--can-channel", "COM7", "--can-bitrate",
                                             "250000", "--scope-visa", "X", "--out", d)
            c = json.loads((Path(d) / "report.json").read_text(encoding="utf-8"))["conditions"]
            self.assertEqual((c["current_ma"], c["speed_rev_s"], c["can_interface"], c["can_channel"],
                              c["can_bitrate"], c["scope_visa"]), (1200, 0.5, "slcan", "COM7", 250000, "X"))


if __name__ == "__main__":
    unittest.main()
