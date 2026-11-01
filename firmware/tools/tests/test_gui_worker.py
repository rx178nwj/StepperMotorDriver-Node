"""nemagui.worker の試験。擬似デバイス（SimSerial）を実時間で動かす。タイムアウトと周期は短くしてある。"""

import queue
import time
import unittest
from typing import Any, Callable, List, Optional

from nemadbg import protocol as p
from nemadbg.link import Link
from nemadbg.sim import SimSerial
from nemagui import worker as wk

R_POS = "reg.POSITION"
R_STATE = "reg.STATE"


class WorkerCase(unittest.TestCase):
    def start(self, retries: int = 0, queued: Optional[list] = None, **kw: Any) -> None:
        self.sim = SimSerial(noise=False, warmup_ms=200)
        self.w = wk.Worker(lambda: Link(self.sim, timeout_s=0.05, retries=retries),
                           reconnect_interval_s=kw.pop("reconnect_interval_s", 0.05),
                           stats_period_s=kw.pop("stats_period_s", 0.1), **kw)
        self.buf: List[Any] = []
        for q in queued or []:
            self.w.send(q)
        self.w.start()

    def tearDown(self) -> None:
        w = getattr(self, "w", None)
        if w is not None:
            w.stop(join_timeout=3.0)
            self.assertFalse(w.is_alive())

    def wait(self, pred: Callable[[Any], bool], timeout: float = 3.0) -> Any:
        """条件に合う最初のメッセージを返す（合わなかったものは捨てる）。"""
        end = time.monotonic() + timeout
        while True:
            while self.buf:
                m = self.buf.pop(0)
                if pred(m):
                    return m
            rem = end - time.monotonic()
            if rem <= 0:
                self.fail("待っていたメッセージが来ませんでした")
            try:
                self.buf.append(self.w.out_q.get(timeout=min(rem, 0.1)))
            except queue.Empty:
                pass

    def wait_type(self, cls: type, timeout: float = 3.0) -> Any:
        return self.wait(lambda m: isinstance(m, cls), timeout)

    def write_result(self, req: Any, timeout: float = 3.0) -> wk.WriteResult:
        self.w.send(req)
        return self.wait(lambda m: isinstance(m, wk.WriteResult) and m.request is req, timeout)

    def latest(self, key: str, timeout: float = 3.0) -> bytes:
        return self.wait(lambda m: isinstance(m, wk.Values) and key in m.values, timeout).values[key]


class ConnectTest(WorkerCase):
    def test_connected_message(self) -> None:
        self.start()
        c = self.wait_type(wk.Connected)
        self.assertEqual(c.info.device_id, 0xA3)
        self.assertEqual(c.info.board_id, 3)
        self.assertEqual(len(c.symtab), 116)
        self.assertEqual(len(c.build_id), 20)
        keys = {i.key for i in c.items}
        self.assertIn("sym.cfg", keys)
        self.assertIn("reg.POSITION", keys)

    def test_wrong_device_aborts_without_retry(self) -> None:
        import nemadbg.sim as simmod
        orig = simmod.DEVICE_ID
        simmod.DEVICE_ID = 0x55
        self.addCleanup(setattr, simmod, "DEVICE_ID", orig)
        self.start()
        d = self.wait_type(wk.Disconnected)
        self.assertFalse(d.will_retry)
        self.assertIn("DEVICE_ID", d.reason)
        time.sleep(0.2)
        self.assertFalse(any(isinstance(m, (wk.Connected, wk.Disconnected)) for m in list(self.w.out_q.queue)))

    def test_stop_sends_disconnect(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.stop(join_timeout=3.0)
        self.assertFalse(self.w.is_alive())
        self.assertEqual(self.sim.requests[-1][0], p.CMD_DISCONNECT)

    def test_disconnect_request_stops_reconnecting(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.send(wk.Disconnect())
        d = self.wait_type(wk.Disconnected)
        self.assertFalse(d.will_retry)
        time.sleep(0.2)
        self.assertFalse(any(isinstance(m, wk.Connected) for m in list(self.w.out_q.queue)))
        self.w.send(wk.Connect())
        self.wait_type(wk.Connected)


class PollingTest(WorkerCase):
    def test_values_periodic(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        keys = [R_STATE, R_POS, "sym.uwTick", "sym.position", "sym.state", "sym.ghost"]
        self.w.set_poll("overview", keys, 0.02)
        got = [self.wait_type(wk.Values) for _ in range(4)]
        self.assertEqual([i for i in got[0].values[R_STATE]], [1])      # DISABLED
        self.assertEqual(got[0].values["sym.state"], b"\x01")
        self.assertNotIn("sym.ghost", got[0].values)
        ticks = [int.from_bytes(g.values["sym.uwTick"], "little") for g in got]
        self.assertEqual(ticks, sorted(ticks))
        self.assertGreater(ticks[-1], ticks[0])
        self.assertGreater(got[-1].timestamp, got[0].timestamp)

    def test_two_polls_with_own_periods(self) -> None:
        self.start(stats_period_s=0.4)
        self.wait_type(wk.Connected)
        self.w.set_poll("fast", [R_POS], 0.02)
        self.w.set_poll("slow", ["reg.VM_MV"], 0.2)
        st = self.wait(lambda m: isinstance(m, wk.Stats) and "slow" in m.poll_periods_s and "fast" in m.poll_periods_s)
        self.assertLess(st.poll_periods_s["fast"], 0.1)
        self.assertGreater(st.poll_periods_s["slow"], 0.1)
        self.assertGreater(st.requests, 5)
        self.assertGreater(st.rtt_avg_ms, 0)
        self.assertGreaterEqual(st.rtt_max_ms, st.rtt_avg_ms)
        self.w.clear_poll("fast")
        self.w.clear_poll("slow")

    def test_overdue_poll_does_not_accumulate(self) -> None:
        # 周期 0 でも要求が溜まって暴走せず、止めたら止まる
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("x", [R_POS], 0.0)
        self.wait_type(wk.Values)
        self.w.clear_poll("x")
        time.sleep(0.1)
        self.buf.clear()
        while not self.w.out_q.empty():
            self.w.out_q.get_nowait()
        time.sleep(0.1)
        self.assertFalse([m for m in list(self.w.out_q.queue) if isinstance(m, wk.Values)])


class OperationTest(WorkerCase):
    def test_enable_and_move(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE, R_POS], 0.02)
        r = self.write_result(wk.Command(wk.Cmd.ENABLE))
        self.assertEqual((r.result, r.result_name, r.error, r.unknown), (0, "OK", "", False))
        self.assertTrue(r.ok)
        r = self.write_result(wk.WriteReg("MOVE_REL", 400))
        self.assertTrue(r.ok, r)
        end = time.monotonic() + 3
        pos = 0
        while time.monotonic() < end and pos != 400:
            pos = int.from_bytes(self.latest(R_POS), "little", signed=True)
        self.assertEqual(pos, 400)
        self.assertEqual(int.from_bytes(self.latest(R_STATE), "little"), 2)

    def test_no_vm_enable_rejected(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.sim.set_vm_mv(0)
        self.w.set_poll("o", [R_STATE], 0.02)
        end = time.monotonic() + 3
        while time.monotonic() < end and self.latest(R_STATE) != b"\x00":
            pass
        r = self.write_result(wk.Command("ENABLE"))
        self.assertEqual((r.result, r.result_name), (1, "REJECTED"))
        self.assertFalse(r.ok)

    def test_setting_write_by_key_and_range_error(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        r = self.write_result(wk.WriteReg("reg.RUN_CURRENT", 1500))
        self.assertTrue(r.ok)
        self.w.send(wk.ReadOnce(["reg.RUN_CURRENT", "sym.cfg.run_current_ma", "sym.ghost", "sym.cfg"]))
        rr = self.wait_type(wk.ReadResult)
        self.assertEqual(im.decode(im.build_items(None)[0], b"\x02") if False else rr.values["reg.RUN_CURRENT"],
                         (1500).to_bytes(2, "little"))
        self.assertEqual(rr.values["sym.cfg.run_current_ma"], (1500).to_bytes(2, "little"))
        self.assertEqual(len(rr.values["sym.cfg"]), 40)
        self.assertTrue(any("sym.ghost" in e for e in rr.errors))
        # 範囲外は送らずにエラー
        r = self.write_result(wk.WriteReg("reg.RUN_CURRENT", 9999))
        self.assertIsNone(r.result)
        self.assertIn("範囲外", r.error)
        r = self.write_result(wk.WriteReg("NO_SUCH", 1))
        self.assertTrue(r.error)
        r = self.write_result(wk.Command("BOGUS"))
        self.assertTrue(r.error)

    def test_broadcast_and_raw_can(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        b = wk.Broadcast(wk.Cmd.STOP)
        self.w.send(b)
        r = self.wait(lambda m: isinstance(m, wk.WriteResult) and m.request is b)
        self.assertIsNone(r.result)
        self.assertEqual(r.error, "")
        bad = wk.Broadcast(1)
        self.w.send(bad)
        r = self.wait(lambda m: isinstance(m, wk.WriteResult) and m.request is bad)
        self.assertTrue(r.error)
        raw = wk.RawCan(0x103, bytes([0x02, 0x04, 0x01]))
        self.w.send(raw)
        rc = self.wait_type(wk.RawCanResult)
        self.assertEqual(rc.frames, [(0x183, bytes([0x82, 0x04, 0x01]))])

    def test_estop_goes_first(self) -> None:
        a = wk.WriteReg("MOVE_REL", 1)
        b = wk.Command("STOP")
        c = wk.Command(wk.Cmd.ESTOP)
        d = wk.Broadcast(wk.Cmd.ESTOP)
        self.start(queued=[a, b, c, d])
        order = [self.wait_type(wk.WriteResult).request for _ in range(4)]
        self.assertEqual(order, [c, d, a, b])

    def test_writes_before_polls(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_POS, "sym.uwTick", "sym.position", "sym.state"], 0.0)
        self.wait_type(wk.Values)
        r = self.write_result(wk.Command("ENABLE"), timeout=1.0)    # ポーリング全力でも短時間で処理される
        self.assertTrue(r.ok)

    def test_bootloader(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.assertTrue(self.write_result(wk.Command("ENABLE")).ok)
        eb = wk.EnterBootloader()
        self.w.send(eb)
        r = self.wait_type(wk.BootloaderResult)
        self.assertFalse(r.accepted)
        self.assertIn("励磁中", r.error)
        self.assertTrue(self.write_result(wk.Command("DISABLE")).ok)
        self.w.send(wk.EnterBootloader())
        r = self.wait_type(wk.BootloaderResult)
        self.assertTrue(r.accepted)
        d = self.wait_type(wk.Disconnected)
        self.assertFalse(d.will_retry)
        self.assertTrue(self.sim.bootloader_entered)
        time.sleep(0.2)
        self.assertFalse(any(isinstance(m, wk.Connected) for m in list(self.w.out_q.queue)))

    def test_fault_event(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.assertTrue(self.write_result(wk.Command("ENABLE")).ok)
        self.sim.inject_fault(0x02)
        ev = self.wait_type(wk.Event)
        self.assertEqual((ev.state, ev.fault, ev.can_id), (3, 0x02, 0x083))
        self.assertIn("ISD", ev.text)
        self.assertIn("FAULT", ev.text)


class FailureTest(WorkerCase):
    def test_unplug_and_replug(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.wait_type(wk.Values)
        self.sim.unplug()
        d = self.wait_type(wk.Disconnected)
        self.assertTrue(d.will_retry)
        self.assertIn("ポート", d.reason)
        time.sleep(0.2)   # 抜けている間は再接続を試すだけで、通知は増えない
        self.assertFalse(any(isinstance(m, wk.Disconnected) for m in list(self.w.out_q.queue)))
        self.sim.replug()
        self.wait_type(wk.Connected)
        self.assertEqual(self.latest(R_STATE), b"\x01")

    def test_no_auto_reconnect(self) -> None:
        self.start(auto_reconnect=False)
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.sim.unplug()
        d = self.wait_type(wk.Disconnected)
        self.assertFalse(d.will_retry)
        self.sim.replug()
        time.sleep(0.3)
        self.assertFalse(any(isinstance(m, wk.Connected) for m in list(self.w.out_q.queue)))

    def test_factory_failure_then_success(self) -> None:
        self.sim = SimSerial(noise=False, warmup_ms=200)
        calls = []

        def factory() -> Link:
            calls.append(1)
            if len(calls) < 3:
                raise OSError("ポートがありません")
            return Link(self.sim, timeout_s=0.05, retries=0)

        self.w = wk.Worker(factory, reconnect_interval_s=0.05, stats_period_s=0.1)
        self.buf = []
        self.w.start()
        d = self.wait_type(wk.Disconnected)
        self.assertTrue(d.will_retry)
        self.assertIn("ポートがありません", d.reason)
        self.wait_type(wk.Connected)
        self.assertEqual(len(calls), 3)

    def test_read_timeout_counted_and_survives(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.wait_type(wk.Values)
        self.sim.drop_responses = 1
        st = self.wait(lambda m: isinstance(m, wk.Stats) and m.timeouts >= 1)
        self.assertTrue(st.connected)
        self.wait_type(wk.Values)       # 続けて読める
        self.assertFalse(any(isinstance(m, wk.Disconnected) for m in self.buf))

    def test_retransmit_counted(self) -> None:
        self.start(retries=2)
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.wait_type(wk.Values)
        self.sim.drop_responses = 1
        st = self.wait(lambda m: isinstance(m, wk.Stats) and m.retransmits >= 1)
        self.assertEqual(st.timeouts, 0)

    def test_write_timeout_is_unknown(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        time.sleep(0.05)
        self.sim.drop_responses = 1
        r = self.write_result(wk.Command("ENABLE"))
        self.assertTrue(r.unknown)
        self.assertIsNone(r.result)
        self.assertIn("不明", r.error)
        self.assertFalse(r.ok)
        # 実行はされていた（応答だけが失われた）。読み戻して確認できる
        self.w.send(wk.ReadOnce([R_STATE]))
        self.assertEqual(self.wait_type(wk.ReadResult).values[R_STATE], b"\x02")

    def test_consecutive_timeouts_disconnect_and_recover(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.wait_type(wk.Values)
        self.sim.drop_responses = -1
        d = self.wait_type(wk.Disconnected)
        self.assertTrue(d.will_retry)
        self.assertIn("3", d.reason)
        self.sim.drop_responses = 0
        self.wait_type(wk.Connected)
        self.wait_type(wk.Values)

    def test_locked_reconnects_with_connect(self) -> None:
        self.start()
        self.wait_type(wk.Connected)
        self.w.set_poll("o", [R_STATE], 0.02)
        self.wait_type(wk.Values)
        self.sim._session = False         # 基板側で接続が解除された（USB 再列挙・リセット相当）
        self.wait_type(wk.Connected)      # 切断通知なしで CONNECT からやり直す
        self.wait_type(wk.Values)


if __name__ == "__main__":
    unittest.main()
