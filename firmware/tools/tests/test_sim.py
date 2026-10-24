"""nemadbg.sim（USB 擬似デバイス）の試験。偽の時計で決定的に動かす（実時間は待たない）。"""

import contextlib
import io
import re
import struct
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

from nemadbg import cli, dfu
from nemadbg import protocol as p
from nemadbg import sim as simmod
from nemadbg.link import DeviceError, Link, LinkError, UnsafeRetryError
from nemadbg.sim import SimSerial, SimUnplugged

BOARD = 3
REQ = 0x100 + BOARD

# レジスタ
R_STATE, R_FAULT, R_STATUS, R_CMD_RESULT = 0x04, 0x05, 0x06, 0x07
R_POSITION, R_SPEED, R_VM, R_BOARD_TEMP = 0x08, 0x0C, 0x10, 0x12
R_CONFIG_STATUS, R_SENSOR_STATUS, R_SENSOR_RAW, R_SHAFT_ANGLE, R_INPUT_STATUS = 0x18, 0x19, 0x1A, 0x1C, 0x1E
R_MICROSTEP, R_FLAGS, R_COMM_TIMEOUT, R_MAX_SPEED = 0x24, 0x25, 0x26, 0x28
R_MOVE_ABS, R_MOVE_REL, R_RUN_SPEED, R_SET_POSITION, R_COMMAND = 0x40, 0x44, 0x48, 0x4C, 0x50
R_J10_MODE = 0x59

OK, REJECTED, INVALID, FAILED = 0, 1, 2, 3
C_ENABLE, C_DISABLE, C_STOP, C_ESTOP, C_CLEAR_FAULT = 1, 2, 3, 4, 5
C_SAVE, C_LOAD, C_FACTORY, C_SYNC, C_HOME_NEG, C_HOME_POS = 7, 8, 9, 10, 11, 12


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class SimCase(unittest.TestCase):
    def make(self, **kw) -> None:
        self.clk = Clock()
        kw.setdefault("noise", False)
        self.sim = SimSerial(clock=self.clk, board_id=BOARD, **kw)
        self.link = Link(self.sim, timeout_s=0.03)

    def setUp(self) -> None:
        self.make()

    # ---- 補助 ----
    def advance(self, seconds: float) -> None:
        """偽の時計を進めて、基板の時間を追いつかせる（10 s 上限を避けて分割）。"""
        while seconds > 0:
            dt = min(seconds, 5.0)
            self.clk.t += dt
            self.sim.sync()
            seconds -= dt

    def rd(self, reg: int, n: int) -> bytes:
        f = self.link.can_request(REQ, bytes([2, reg, n]))
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].can_id, 0x180 + BOARD)
        self.assertEqual(f[0].data[:2], bytes([0x82, reg]))
        return f[0].data[2:]

    def u(self, reg: int, size: int = 1, signed: bool = False) -> int:
        return int.from_bytes(self.rd(reg, size), "little", signed=signed)

    def wr(self, reg: int, data: bytes) -> int:
        f = self.link.can_request(REQ, bytes([1, reg]) + data)
        self.assertEqual(f[0].data[:2], bytes([0x81, reg]))
        return f[0].data[2]

    def wr32(self, reg: int, v: int) -> int:
        return self.wr(reg, struct.pack("<i", v))

    def cmd(self, c: int) -> int:
        return self.wr(R_COMMAND, bytes([c]))

    def boot(self, enable: bool = True) -> None:
        """CONNECT、電源投入後 60 ms 待って DISABLED、必要なら ENABLE して 10 ms 待つ。"""
        self.link.connect()
        self.advance(0.06)
        self.assertEqual(self.u(R_STATE), 1)
        if enable:
            self.assertEqual(self.cmd(C_ENABLE), OK)
            self.advance(0.01)
            self.assertEqual(self.u(R_STATE), 2)

    def wait_for(self, cond, limit_s: float, step_s: float = 0.01) -> bool:
        t = 0.0
        while t < limit_s:
            if cond():
                return True
            self.advance(step_s)
            t += step_s
        return cond()

    def device_error(self, fn, err: int) -> None:
        with self.assertRaises(DeviceError) as cm:
            fn()
        self.assertEqual(cm.exception.err, err)


class ProtocolTest(SimCase):
    def test_connect_response(self) -> None:
        ci = self.link.connect()
        self.assertEqual((ci.proto_major, ci.proto_minor, ci.device_id), (1, 0, 0xA3))
        self.assertEqual((ci.fw_major, ci.fw_minor, ci.board_id, ci.max_len), (0, 4, BOARD, 250))
        self.assertEqual(self.sim.requests[0][:2], bytes([p.CMD_CONNECT, 1]))

    def test_connect_frame_matches_spec_example(self) -> None:
        """usb_interface.md 5.1 章の CONNECT 要求に対する応答（CTR=0、基板 ID 3）が例と一致する。"""
        self.sim.write(bytes.fromhex("A5 02 01 00 CD 91"))
        self.assertEqual(self.sim.read(64), bytes.fromhex("A5 0A FF 00 00 01 00 A3 00 04 03 FA 27 00"))

    def test_locked_before_connect(self) -> None:
        self.device_error(lambda: self.link.can_request(REQ, bytes([2, 4, 1])), 0x35)
        self.device_error(self.link.exchange_id, 0x35)
        self.device_error(lambda: self.link.request(0x7E), 0x35)    # 未定義 CMD も未接続なら 0x35
        self.assertEqual(self.link.get_version(), (1, 0))             # GET_VERSION は未接続でも可

    def test_unknown_cmd_and_syntax_errors(self) -> None:
        self.link.connect()
        self.device_error(lambda: self.link.request(0x7E), 0x30)
        self.device_error(lambda: self.link.request(p.CMD_SET_MTA, b"\x00"), 0x31)
        self.device_error(lambda: self.link.request(p.CMD_UPLOAD, b""), 0x31)
        self.device_error(lambda: self.link.request(p.CMD_CAN_REQUEST, b"\x03\x01\x03\x00"), 0x31)   # 長さ不一致
        self.device_error(lambda: self.link.request(p.CMD_CAN_REQUEST, struct.pack("<HB", 0x800, 0)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_CAN_REQUEST, struct.pack("<HB", 0x103, 9) + bytes(9)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_SHORT_UP, struct.pack("<BI", 0, 0x20000000)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_SHORT_UP, struct.pack("<BI", 241, 0x20000000)), 0x32)
        self.device_error(lambda: self.link.short_up(0x00000000, 4), 0x33)
        self.device_error(lambda: self.link.sym_info(104), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_SYM_FIND, b"\x00"), 0x32)
        self.device_error(lambda: self.link.sym_find("no_such_symbol"), 0x36)

    def test_disconnect_then_locked(self) -> None:
        self.link.connect()
        self.link.disconnect()
        self.device_error(self.link.disconnect, 0x35)

    def test_crc_error_is_dropped_silently(self) -> None:
        self.link.connect()
        before = self.u_sym("usb_stats.crc_errors")
        frame = bytearray(p.encode_request(p.CMD_GET_VERSION, 9))
        frame[-1] ^= 0x01
        self.sim.write(bytes(frame))
        self.assertEqual(self.sim.in_waiting, 0)
        self.assertEqual(self.u_sym("usb_stats.crc_errors"), before + 1)

    def u_sym(self, name: str) -> int:
        info = self.link.sym_find(name)
        return int.from_bytes(self.link.sym_upload(info), "little")

    def test_split_and_joined_frames(self) -> None:
        a = p.encode_request(p.CMD_CONNECT, 1)
        b = p.encode_request(p.CMD_GET_VERSION, 2)
        for ch in a:
            self.sim.write(bytes([ch]))
        self.sim.write(b)
        parser = p.FrameParser()
        bodies = parser.feed(self.sim.read(256))
        self.assertEqual([x[2] for x in bodies], [1, 2])

    def test_exchange_id_and_version(self) -> None:
        self.link.connect()
        self.assertEqual(len(self.link.exchange_id()), 20)
        self.assertEqual(self.link.get_version(), (1, 0))

    def test_upload_advances_mta(self) -> None:
        self.link.connect()
        self.advance(0.1)
        addr = self.link.sym_find("cfg").addr
        self.link.request(p.CMD_SET_MTA, struct.pack("<I", addr))
        a = self.link.request(p.CMD_UPLOAD, bytes([4]))
        b = self.link.request(p.CMD_UPLOAD, bytes([4]))
        self.assertEqual(a + b, self.link.short_up(addr, 8))


class CanTest(SimCase):
    def test_read_write_and_invalid(self) -> None:
        self.link.connect()
        self.assertEqual(self.rd(0x00, 4), bytes([0xA3, 0, 4, BOARD]))
        self.assertEqual(self.rd(0x5E, 6)[2:], b"\xff\xff\xff\xff"[:4])    # マップ外は 0xFF
        self.assertEqual(self.rd(0x40, 4), bytes(4))                        # 書き込み専用は 0
        f = self.link.can_request(REQ, bytes([0x05, 0x04, 0x00]))           # 未定義の OP
        self.assertEqual(f[0].data, bytes([0xFF, 0x04, 0x02]))
        f = self.link.can_request(REQ, bytes([0x02, 0x04]))                 # DLC 不一致
        self.assertEqual(f[0].data, bytes([0xFF, 0x04, 0x02]))
        f = self.link.can_request(REQ, bytes([0x02, 0x04, 0x07]))           # LEN 範囲外
        self.assertEqual(f[0].data, bytes([0xFF, 0x04, 0x02]))
        f = self.link.can_request(REQ, b"")                                  # DLC 0
        self.assertEqual(f[0].data, bytes([0xFF, 0x00, 0x02]))

    def test_other_station_ignored(self) -> None:
        self.link.connect()
        self.assertEqual(self.link.can_request(0x100 + BOARD + 1, bytes([2, 4, 1])), [])

    def test_cmd_result_register_follows_last_write(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.wr(R_MICROSTEP, b"\x03"), INVALID)
        self.assertEqual(self.u(R_CMD_RESULT), INVALID)
        self.assertEqual(self.wr(R_MICROSTEP, b"\x08"), OK)
        self.assertEqual(self.u(R_CMD_RESULT), OK)

    def test_multi_register_and_partial_writes(self) -> None:
        self.boot(enable=False)
        # MICROSTEP と FLAGS をまとめて書く
        self.assertEqual(self.wr(R_MICROSTEP, bytes([4, 0x03])), OK)
        self.assertEqual(self.rd(R_MICROSTEP, 2), bytes([4, 0x03]))
        # 最後の非 OK が結果になる: MICROSTEP=3(INVALID)、FLAGS=0x01(OK)
        self.assertEqual(self.wr(R_MICROSTEP, bytes([3, 0x01])), INVALID)
        # 32bit レジスタを途中までしか覆わない書き込みは INVALID（反映しない）
        before = self.u(R_MAX_SPEED, 4)
        self.assertEqual(self.wr(R_MAX_SPEED, struct.pack("<I", 5000)[:3]), INVALID)
        self.assertEqual(self.u(R_MAX_SPEED, 4), before)
        # COMM_TIMEOUT(2) + 隙間 + MAX_SPEED の途中
        self.assertEqual(self.wr(R_COMM_TIMEOUT, struct.pack("<H", 50) + struct.pack("<H", 1)), INVALID)
        # 未定義アドレスは INVALID
        self.assertEqual(self.wr(0x1F, b"\x00"), INVALID)
        self.assertEqual(self.wr(0x08, b"\x00\x00\x00\x00"), INVALID)

    def test_broadcast(self) -> None:
        self.boot()
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(0.5)
        self.assertEqual(self.u(R_SPEED, 4, True), 1600)
        self.assertEqual(self.link.can_request(0x000, bytes([4])), [])        # ESTOP
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertEqual(self.u(R_STATE), 2)
        self.assertEqual(self.link.can_request(0x000, bytes([2])), [])        # DISABLE
        self.assertEqual(self.u(R_STATE), 1)
        self.assertEqual(self.link.can_request(0x000, bytes([1])), [])        # ENABLE は無視
        self.assertEqual(self.u(R_STATE), 1)
        self.assertEqual(self.link.can_request(0x000, bytes([3, 3])), [])     # DLC 2 は無視


class StateTest(SimCase):
    def test_no_vm_to_disabled_after_50ms(self) -> None:
        self.link.connect()
        self.advance(0.049)
        self.assertEqual(self.u(R_STATE), 0)
        self.advance(0.001)
        self.assertEqual(self.u(R_STATE), 1)

    def test_enable_disable(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.cmd(C_ENABLE), OK)
        self.assertEqual(self.u(R_STATE), 2)
        self.assertEqual(self.cmd(C_ENABLE), REJECTED)
        self.assertEqual(self.cmd(C_DISABLE), OK)
        self.assertEqual(self.u(R_STATE), 1)
        self.assertEqual(self.cmd(C_DISABLE), REJECTED)
        self.assertEqual(self.cmd(99), INVALID)

    def test_enable_rejected_in_no_vm(self) -> None:
        self.link.connect()
        self.assertEqual(self.u(R_STATE), 0)
        self.assertEqual(self.cmd(C_ENABLE), REJECTED)
        self.assertEqual(self.u(R_CMD_RESULT), REJECTED)

    def test_vm_loss_in_disabled_returns_to_no_vm(self) -> None:
        self.boot(enable=False)
        self.sim.set_vm_mv(3000)
        self.advance(0.05)
        self.assertEqual(self.u(R_STATE), 0)
        self.assertEqual(self.u(R_FAULT), 0)
        self.sim.set_vm_mv(24000)
        self.advance(0.04)
        self.assertEqual(self.u(R_STATE), 0)
        self.advance(0.02)
        self.assertEqual(self.u(R_STATE), 1)

    def test_vm_register_and_monitor_values(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.u(R_VM, 2), 24000)
        self.assertEqual(self.u(R_BOARD_TEMP, 2, True), 250)
        self.assertEqual(self.u(0x16, 2), 0xFFFF)               # PA7_MODE=DIR のとき AD_IN は無効
        self.assertEqual(self.wr(0x58, b"\x00"), OK)
        self.sim.set_ad_in_mv(1234)
        self.advance(0.02)
        self.assertEqual(self.u(0x16, 2), 1234)
        self.sim.set_board_temp_dc(None)
        self.advance(0.02)
        self.assertEqual(self.u(R_BOARD_TEMP, 2, True), -32768)

    def test_noise_is_reproducible_with_seed(self) -> None:
        def temps(seed: int) -> List[int]:
            self.make(noise=True, seed=seed)
            self.link.connect()
            out = []
            for _ in range(20):
                self.advance(0.05)
                out.append(self.u(R_BOARD_TEMP, 2, True))
            return out

        a, b = temps(5), temps(5)
        self.assertEqual(a, b)
        self.assertTrue(all(240 <= t <= 260 for t in a))
        self.assertGreater(len(set(a)), 1)

    def test_vm_undervoltage_fault_event_and_clear(self) -> None:
        self.boot()
        self.assertEqual(self.link.wait_events(0), [])
        self.sim.set_vm_mv(5000)
        self.advance(0.05)
        self.assertEqual(self.u(R_STATE), 3)
        self.assertEqual(self.u(R_FAULT), 0x08)
        events = self.link.wait_events(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].evt, p.EVT_CAN_FRAME)
        f = p.parse_can_frames(events[0].data, 1)[0]
        self.assertEqual((f.can_id, f.data), (0x080 + BOARD, bytes([3, 0x08])))
        # VM が戻っても FAULT のまま。CLEAR_FAULT で DISABLED
        self.sim.set_vm_mv(24000)
        self.advance(0.1)
        self.assertEqual(self.u(R_STATE), 3)
        self.assertEqual(self.cmd(C_ENABLE), REJECTED)
        self.assertEqual(self.cmd(C_CLEAR_FAULT), OK)
        self.assertEqual((self.u(R_STATE), self.u(R_FAULT)), (1, 0))
        self.assertEqual(self.cmd(C_CLEAR_FAULT), REJECTED)

    def test_overvoltage_and_clear_without_vm(self) -> None:
        self.boot()
        self.sim.set_vm_mv(30000)
        self.advance(0.05)
        self.assertEqual((self.u(R_STATE), self.u(R_FAULT)), (3, 0x10))
        self.sim.set_vm_mv(0)
        self.advance(0.05)
        self.assertEqual(self.cmd(C_CLEAR_FAULT), OK)
        self.assertEqual(self.u(R_STATE), 0)        # VM が無ければ NO_VM

    def test_overtemp_fault_and_clear_rejected_when_hot(self) -> None:
        self.boot()
        self.sim.set_board_temp_c(72.0)
        self.advance(0.03)
        self.assertEqual(self.u(R_STATE), 2)
        self.assertTrue(self.u(R_STATUS) & 0x20)    # 温度警告
        self.sim.set_board_temp_c(85.0)
        self.advance(0.03)
        self.assertEqual((self.u(R_STATE), self.u(R_FAULT)), (3, 0x20))
        self.sim.set_board_temp_c(75.0)
        self.advance(0.03)
        self.assertEqual(self.cmd(C_CLEAR_FAULT), REJECTED)
        self.sim.set_board_temp_c(60.0)
        self.advance(0.03)
        self.assertEqual(self.cmd(C_CLEAR_FAULT), OK)
        self.assertEqual(self.u(R_STATE), 1)

    def test_injected_driver_fault_stops_motion(self) -> None:
        self.boot()
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(0.2)
        self.sim.inject_fault(0x02)     # ISD
        self.advance(0.01)
        self.assertEqual((self.u(R_STATE), self.u(R_FAULT)), (3, 0x02))
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        events = self.link.wait_events(0)
        self.assertEqual(p.parse_can_frames(events[0].data, 1)[0].data, bytes([3, 0x02]))

    def test_no_event_when_not_connected(self) -> None:
        self.boot()
        self.link.disconnect()
        self.sim.set_vm_mv(5000)
        self.advance(0.05)
        self.assertEqual(self.sim.in_waiting, 0)


class MotionTest(SimCase):
    def setUp(self) -> None:
        super().setUp()
        self.boot()

    def test_move_rel_reaches_target(self) -> None:
        self.assertEqual(self.wr32(R_MOVE_REL, 3200), OK)
        peak = 0
        moving_seen = False
        for _ in range(400):
            self.advance(0.01)
            sp = self.u(R_SPEED, 4, True)
            peak = max(peak, sp)
            moving_seen |= bool(self.u(R_STATUS) & 0x01)
            self.assertLessEqual(sp, 3200)
            if self.u(R_STATUS) & 0x80:
                break
        status = self.u(R_STATUS)
        self.assertTrue(status & 0x80, "目標到達 (bit7)")
        self.assertFalse(status & 0x01, "MOVING が落ちる")
        self.assertTrue(moving_seen)
        self.assertEqual(self.u(R_POSITION, 4, True), 3200)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertGreater(peak, 1000)

    def test_move_abs_negative_and_speed_limited_by_setting(self) -> None:
        self.assertEqual(self.wr(R_MAX_SPEED, struct.pack("<I", 1000)), OK)
        self.assertEqual(self.wr32(R_MOVE_ABS, -2000), OK)
        peak = 0
        for _ in range(500):
            self.advance(0.01)
            peak = max(peak, -self.u(R_SPEED, 4, True))
            if self.u(R_STATUS) & 0x80:
                break
        self.assertEqual(self.u(R_POSITION, 4, True), -2000)
        self.assertLessEqual(peak, 1000)
        self.assertGreater(peak, 900)

    def test_target_reached_is_cleared_by_new_move(self) -> None:
        self.wr32(R_MOVE_REL, 100)
        self.advance(0.5)
        self.assertTrue(self.u(R_STATUS) & 0x80)
        self.wr32(R_MOVE_REL, 100)
        self.advance(0.02)
        self.assertFalse(self.u(R_STATUS) & 0x80)

    def test_run_speed_stop_decelerates(self) -> None:
        self.assertEqual(self.wr32(R_RUN_SPEED, 1600), OK)
        self.advance(0.5)
        self.assertEqual(self.u(R_SPEED, 4, True), 1600)
        self.assertTrue(self.u(R_STATUS) & 0x01)
        self.assertEqual(self.cmd(C_STOP), OK)
        self.advance(0.02)
        mid = self.u(R_SPEED, 4, True)
        self.assertTrue(0 < mid < 1600, f"減速中の速度 {mid}")
        self.advance(0.5)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertFalse(self.u(R_STATUS) & 0x01)
        self.assertEqual(self.u(R_STATE), 2)

    def test_run_speed_negative_and_zero(self) -> None:
        self.wr32(R_RUN_SPEED, -800)
        self.advance(0.5)
        self.assertEqual(self.u(R_SPEED, 4, True), -800)
        self.assertLess(self.u(R_POSITION, 4, True), 0)
        self.wr32(R_RUN_SPEED, 0)
        self.advance(0.5)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)

    def test_reverse_while_running(self) -> None:
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(0.3)
        self.wr32(R_RUN_SPEED, -1600)
        self.advance(1.0)
        self.assertEqual(self.u(R_SPEED, 4, True), -1600)

    def test_estop_stops_immediately_and_keeps_excitation(self) -> None:
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(0.5)
        pos = self.u(R_POSITION, 4, True)
        self.assertEqual(self.cmd(C_ESTOP), OK)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertEqual(self.u(R_STATE), 2)
        self.assertEqual(self.u(R_POSITION, 4, True), pos)
        self.advance(0.1)
        self.assertEqual(self.u(R_POSITION, 4, True), pos)

    def test_disable_stops_immediately(self) -> None:
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(0.5)
        self.assertEqual(self.cmd(C_DISABLE), OK)
        self.assertEqual((self.u(R_STATE), self.u(R_SPEED, 4, True)), (1, 0))

    def test_motion_rejected_when_not_enabled(self) -> None:
        self.cmd(C_DISABLE)
        self.assertEqual(self.wr32(R_MOVE_REL, 100), REJECTED)
        self.assertEqual(self.wr32(R_MOVE_ABS, 100), REJECTED)
        self.assertEqual(self.wr32(R_RUN_SPEED, 100), REJECTED)
        self.assertEqual(self.wr32(R_RUN_SPEED, 0), REJECTED)

    def test_set_position(self) -> None:
        self.assertEqual(self.wr32(R_SET_POSITION, 12345), OK)
        self.assertEqual(self.u(R_POSITION, 4, True), 12345)
        self.wr32(R_MOVE_REL, 100)
        self.advance(0.5)
        self.assertEqual(self.u(R_POSITION, 4, True), 12445)
        self.wr32(R_RUN_SPEED, 800)
        self.advance(0.1)
        self.assertEqual(self.wr32(R_SET_POSITION, 0), REJECTED)     # 移動中は不可

    def test_hold_current_after_hold_delay(self) -> None:
        self.assertFalse(self.u(R_STATUS) & 0x40)
        self.advance(0.45)
        self.assertFalse(self.u(R_STATUS) & 0x40)
        self.advance(0.1)
        self.assertTrue(self.u(R_STATUS) & 0x40)
        self.wr32(R_MOVE_REL, 3000)
        self.advance(0.05)
        self.assertFalse(self.u(R_STATUS) & 0x40)

    def test_comm_timeout_stops_motion(self) -> None:
        self.assertEqual(self.wr(R_COMM_TIMEOUT, struct.pack("<H", 100)), OK)
        self.wr32(R_RUN_SPEED, 1600)
        # 50 ms ごとに要求を送り続けるあいだは走り続ける
        for _ in range(10):
            self.advance(0.05)
            self.assertEqual(self.u(R_STATE), 2)
        self.assertEqual(self.u(R_SPEED, 4, True), 1600)
        # 要求が途絶えたら減速停止
        self.advance(0.6)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertFalse(self.u(R_STATUS) & 0x01)
        self.assertEqual(self.u(R_STATE), 2)

    def test_comm_timeout_disabled_by_zero(self) -> None:
        self.wr32(R_RUN_SPEED, 1600)
        self.advance(5.0)
        self.assertEqual(self.u(R_SPEED, 4, True), 1600)

    def test_broadcast_does_not_refresh_comm_timeout(self) -> None:
        self.wr(R_COMM_TIMEOUT, struct.pack("<H", 100))
        self.wr32(R_RUN_SPEED, 1600)
        for _ in range(10):
            self.advance(0.05)
            self.link.can_request(0x000, bytes([1]))     # 無視されるブロードキャスト
        self.assertEqual(self.u(R_SPEED, 4, True), 0)


class SettingsTest(SimCase):
    def test_out_of_range_values_are_invalid(self) -> None:
        self.boot(enable=False)
        cases: List[Tuple[int, bytes]] = [
            (0x20, struct.pack("<H", 249)), (0x20, struct.pack("<H", 3001)),
            (0x22, struct.pack("<H", 0)), (0x24, b"\x00"), (0x24, b"\x40"), (0x25, b"\x20"),
            (0x28, struct.pack("<I", 0)), (0x28, struct.pack("<I", 100001)),
            (0x2C, struct.pack("<I", 0)), (0x2C, struct.pack("<I", 10000001)),
            (0x30, struct.pack("<I", 0)), (0x34, struct.pack("<I", 0)), (0x38, struct.pack("<I", 100001)),
            (0x3E, struct.pack("<H", 0)), (0x54, struct.pack("<H", 0)), (0x54, struct.pack("<H", 1001)),
            (0x56, struct.pack("<H", 4096)), (0x58, b"\x03"), (0x59, b"\x03"), (0x5A, struct.pack("<H", 0)),
        ]
        before = self.rd(0x20, 6) + self.rd(0x26, 6) + self.rd(0x2C, 6)
        for reg, data in cases:
            self.assertEqual(self.wr(reg, data), INVALID, f"reg 0x{reg:02X} {data.hex()}")
        self.assertEqual(before, self.rd(0x20, 6) + self.rd(0x26, 6) + self.rd(0x2C, 6))
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x01)      # 拒否したので UNSAVED にならない

    def test_in_range_values_are_accepted(self) -> None:
        self.boot(enable=False)
        for reg, data in [(0x20, struct.pack("<H", 250)), (0x20, struct.pack("<H", 3000)), (0x24, b"\x20"),
                          (0x25, b"\x1f"), (0x28, struct.pack("<I", 100000)), (0x2C, struct.pack("<I", 10000000)),
                          (0x3C, struct.pack("<H", 0)), (0x3E, struct.pack("<H", 65535)),
                          (0x54, struct.pack("<H", 1000)), (0x56, struct.pack("<H", 4095)), (0x58, b"\x02"),
                          (0x5A, struct.pack("<H", 65535))]:
            self.assertEqual(self.wr(reg, data), OK, f"reg 0x{reg:02X}")
            self.assertEqual(self.rd(reg, len(data)), data)

    def test_microstep_change_rejected_while_moving(self) -> None:
        self.boot()
        self.wr32(R_RUN_SPEED, 800)
        self.advance(0.05)
        self.assertEqual(self.wr(R_MICROSTEP, b"\x08"), REJECTED)
        self.assertEqual(self.u(R_MICROSTEP), 16)
        self.assertEqual(self.wr(R_J10_MODE, b"\x01"), REJECTED)
        self.assertEqual(self.wr(R_MICROSTEP, b"\x10"), OK)          # 同じ値なら変化なし
        self.cmd(C_STOP)
        self.advance(0.5)
        self.assertEqual(self.wr(R_MICROSTEP, b"\x08"), OK)
        self.assertEqual(self.u(R_MICROSTEP), 8)

    def test_agc_flag_change_rejected_while_enabled(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.wr(R_FLAGS, b"\x0a"), OK)           # DISABLED 中は変えられる
        self.assertEqual(self.sim.sync() or self.u_sym_agc(), 1)
        self.cmd(C_ENABLE)
        self.assertEqual(self.wr(R_FLAGS, b"\x02"), REJECTED)    # AGC を外す → FLAGS 全体が拒否
        self.assertEqual(self.u(R_FLAGS), 0x0A)
        self.assertEqual(self.wr(R_FLAGS, b"\x0b"), OK)          # AGC 以外のビットは変えられる
        self.assertEqual(self.u(R_FLAGS), 0x0B)

    def u_sym_agc(self) -> int:
        self.advance(0.02)
        info = self.link.sym_find("agc_out")
        return self.link.sym_upload(info)[0]

    def test_save_load_factory_and_config_status(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x01)                         # LOADED
        self.assertEqual(self.wr(R_MAX_SPEED, struct.pack("<I", 5000)), OK)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x03)                         # LOADED | UNSAVED
        self.assertEqual(self.cmd(C_SAVE), OK)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x01)
        self.assertEqual(self.wr(R_MAX_SPEED, struct.pack("<I", 4000)), OK)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x03)
        self.assertEqual(self.cmd(C_LOAD), OK)
        self.assertEqual(self.u(R_MAX_SPEED, 4), 5000)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x01)
        self.assertEqual(self.cmd(C_FACTORY), OK)
        self.assertEqual(self.u(R_MAX_SPEED, 4), 3200)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x03)                         # 保存するまで UNSAVED
        self.assertEqual(self.cmd(C_LOAD), OK)                                  # Flash は保存済みのまま
        self.assertEqual(self.u(R_MAX_SPEED, 4), 5000)

    def test_save_rejected_while_moving(self) -> None:
        self.boot()
        self.wr32(R_RUN_SPEED, 800)
        self.advance(0.05)
        self.assertEqual(self.cmd(C_SAVE), REJECTED)
        self.assertEqual(self.cmd(C_LOAD), REJECTED)
        self.assertEqual(self.cmd(C_FACTORY), REJECTED)

    def test_blank_flash_and_save_failure(self) -> None:
        self.make(blank_flash=True)
        self.boot(enable=False)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x00)
        self.assertEqual(self.cmd(C_LOAD), FAILED)
        self.sim.set_save_fails(True)
        self.assertEqual(self.cmd(C_SAVE), FAILED)
        self.assertEqual(self.u(R_CONFIG_STATUS) & 0x04, 0x04)
        self.sim.set_save_fails(False)
        self.assertEqual(self.cmd(C_SAVE), OK)
        self.assertEqual(self.u(R_CONFIG_STATUS) & 0x06, 0)

    def test_saved_settings_survive_reboot(self) -> None:
        self.boot(enable=False)
        self.wr(R_MAX_SPEED, struct.pack("<I", 4321))
        self.wr(0x20, struct.pack("<H", 1234))
        self.cmd(C_SAVE)
        self.wr(0x20, struct.pack("<H", 2000))
        self.sim.reboot()
        self.link.connect()
        self.advance(0.1)
        self.assertEqual(self.u(R_MAX_SPEED, 4), 4321)
        self.assertEqual(self.u(0x20, 2), 1234)
        self.assertEqual(self.u(R_CONFIG_STATUS), 0x01)


class HomeTest(SimCase):
    def setUp(self) -> None:
        super().setUp()
        self.boot(enable=False)
        self.assertEqual(self.wr(R_J10_MODE, b"\x02"), OK)
        self.sim.set_home_switches(2000, -2000)
        self.cmd(C_ENABLE)
        self.advance(0.01)

    def home(self, c: int) -> None:
        self.assertEqual(self.cmd(c), OK)
        self.advance(0.01)
        self.assertTrue(self.u(R_INPUT_STATUS) & 0x04, "原点復帰中 (bit2)")
        self.assertTrue(self.u(R_STATUS) & 0x01, "原点復帰中は MOVING")

    def finish(self) -> int:
        self.wait_for(lambda: not (self.u(R_INPUT_STATUS) & 0x04), 30.0, 0.1)
        return self.u(R_INPUT_STATUS)

    def test_home_pos_completes_with_position_zero(self) -> None:
        self.home(C_HOME_POS)
        st = self.finish()
        self.assertTrue(st & 0x08, f"原点確定 (bit3) st=0x{st:02X}")
        self.assertFalse(st & 0x10)
        self.assertFalse(st & 0x04)
        self.assertEqual(self.u(R_POSITION, 4, True), 0)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        self.assertFalse(self.u(R_STATUS) & 0x01)
        self.assertEqual(self.u(R_STATE), 2)
        self.assertFalse(st & 0x01)       # スイッチから離れて停止している

    def test_home_neg_completes(self) -> None:
        self.home(C_HOME_NEG)
        st = self.finish()
        self.assertTrue(st & 0x08)
        self.assertEqual(self.u(R_POSITION, 4, True), 0)

    def test_home_fails_when_switch_not_found(self) -> None:
        self.sim.set_home_switches(None, None)
        self.sim.set_home_travel_limit(1000)
        self.home(C_HOME_POS)
        st = self.finish()
        self.assertTrue(st & 0x10, f"原点失敗 (bit4) st=0x{st:02X}")
        self.assertFalse(st & 0x08)
        self.assertFalse(self.u(R_STATUS) & 0x01)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)

    def test_home_fails_when_both_switches_active(self) -> None:
        self.sim.set_home_switches(-100, 100)        # 位置 0 で両方作動（配線の誤り・固着）
        self.advance(0.01)
        self.assertEqual(self.cmd(C_HOME_POS), OK)
        self.advance(0.05)
        st = self.u(R_INPUT_STATUS)
        self.assertEqual(st & 0x1F, 0x13)            # HOME1 | HOME2 | 失敗
        self.assertEqual(self.u(R_SPEED, 4, True), 0)

    def test_home_aborted_by_stop(self) -> None:
        self.home(C_HOME_POS)
        self.advance(0.5)
        self.assertEqual(self.cmd(C_STOP), OK)
        self.advance(0.5)
        st = self.u(R_INPUT_STATUS)
        self.assertTrue(st & 0x10)
        self.assertFalse(st & 0x04)
        self.assertEqual(self.u(R_SPEED, 4, True), 0)

    def test_home_rejected_conditions(self) -> None:
        self.assertEqual(self.wr32(R_RUN_SPEED, 800), OK)
        self.advance(0.05)
        self.assertEqual(self.cmd(C_HOME_POS), REJECTED)        # 移動中
        self.cmd(C_ESTOP)
        self.cmd(C_DISABLE)
        self.assertEqual(self.cmd(C_HOME_POS), REJECTED)        # ENABLED でない
        self.cmd(C_ENABLE)
        self.assertEqual(self.wr(R_J10_MODE, b"\x00"), OK)
        self.assertEqual(self.cmd(C_HOME_NEG), REJECTED)        # J10_MODE が 2 でない

    def test_motion_commands_rejected_during_homing(self) -> None:
        self.home(C_HOME_POS)
        self.assertEqual(self.wr32(R_MOVE_REL, 10), REJECTED)
        self.assertEqual(self.wr32(R_MOVE_ABS, 10), REJECTED)
        self.assertEqual(self.wr32(R_RUN_SPEED, 10), REJECTED)
        self.assertEqual(self.wr32(R_SET_POSITION, 0), REJECTED)
        self.assertEqual(self.wr(R_J10_MODE, b"\x00"), REJECTED)
        self.assertEqual(self.wr32(R_RUN_SPEED, 0), OK)         # 0 は停止指令として受け付ける
        self.advance(0.2)
        self.assertTrue(self.u(R_INPUT_STATUS) & 0x10)

    def test_limit_stop(self) -> None:
        self.sim.set_home_switches(500, -500)
        self.assertEqual(self.wr32(R_RUN_SPEED, 1600), OK)
        self.advance(1.0)
        st = self.u(R_INPUT_STATUS)
        self.assertTrue(st & 0x20, f"リミット停止 (bit5) st=0x{st:02X}")
        self.assertTrue(st & 0x01)
        self.assertEqual(self.u(R_STATE), 2)          # FAULT にはならない
        self.assertEqual(self.u(R_SPEED, 4, True), 0)
        pos = self.u(R_POSITION, 4, True)
        self.assertGreaterEqual(pos, 500)
        self.assertLess(pos, 520)
        self.assertEqual(self.wr32(R_MOVE_REL, 100), REJECTED)     # 作動中の側へは動けない
        self.assertEqual(self.wr32(R_RUN_SPEED, 100), REJECTED)
        self.assertEqual(self.wr32(R_MOVE_ABS, pos + 5), REJECTED)
        self.assertEqual(self.wr32(R_MOVE_REL, -100), OK)          # 逆方向へは動ける
        self.assertFalse(self.u(R_INPUT_STATUS) & 0x20)            # 受け付けた移動指令で bit5 が消える
        self.advance(1.0)
        self.assertEqual(self.u(R_POSITION, 4, True), pos - 100)
        self.assertFalse(self.u(R_INPUT_STATUS) & 0x01)

    def test_no_limit_in_other_j10_modes(self) -> None:
        self.assertEqual(self.wr(R_J10_MODE, b"\x00"), OK)
        self.sim.set_home_switches(500, -500)
        self.wr32(R_MOVE_ABS, 1000)
        self.advance(2.0)
        self.assertEqual(self.u(R_POSITION, 4, True), 1000)
        self.assertEqual(self.u(R_INPUT_STATUS), 0)

    def test_active_high_flag_does_not_change_result(self) -> None:
        self.cmd(C_DISABLE)
        self.assertEqual(self.wr(R_FLAGS, b"\x12"), OK)
        self.cmd(C_ENABLE)
        self.advance(0.01)
        self.home(C_HOME_POS)
        self.assertTrue(self.finish() & 0x08)


class SensorTest(SimCase):
    def test_sensor_follows_position_and_sync(self) -> None:
        self.boot(enable=False)
        self.advance(0.05)
        self.assertEqual(self.u(R_SENSOR_STATUS), 0x11)      # 有効 | SYNCED（起動時の自動同期）
        self.assertEqual(self.u(R_SENSOR_RAW, 2), 0)
        self.assertEqual(self.u(R_SHAFT_ANGLE, 2), 0)
        # 出力軸が 1/4 回転（N = 20 * 200 * 16 = 64000 step）
        self.sim.set_shaft_offset(16000)
        self.advance(0.02)
        self.assertEqual(self.u(R_SENSOR_RAW, 2), 1024)
        self.assertEqual(self.u(R_SHAFT_ANGLE, 2), 9000)
        self.assertEqual(self.cmd(C_SYNC), OK)
        self.assertEqual(self.u(R_POSITION, 4, True), 16000)
        # SENSOR_OFFSET は原点の RAW ANGLE: RAW はその分ずれ、軸角度は変わらない
        self.assertEqual(self.wr(0x56, struct.pack("<H", 1024)), OK)
        self.advance(0.02)
        self.assertEqual(self.u(R_SENSOR_RAW, 2), 2048)
        self.assertEqual(self.u(R_SHAFT_ANGLE, 2), 9000)

    def test_sensor_absent(self) -> None:
        self.make()
        self.sim.set_sensor_present(False)
        self.boot(enable=False)
        self.advance(0.05)
        self.assertEqual(self.u(R_SENSOR_STATUS), 0x08)
        self.assertEqual(self.u(R_SENSOR_RAW, 2), 0xFFFF)
        self.assertEqual(self.u(R_SHAFT_ANGLE, 2), 0xFFFF)
        self.assertEqual(self.cmd(C_SYNC), FAILED)

    def test_sync_rejected_while_moving(self) -> None:
        self.boot()
        self.wr32(R_RUN_SPEED, 800)
        self.advance(0.05)
        self.assertEqual(self.cmd(C_SYNC), REJECTED)

    def test_encoder_follows_position(self) -> None:
        self.boot(enable=False)
        self.assertEqual(self.u(0x5C, 4, True), 0)            # J10_MODE=0 では 0
        self.assertEqual(self.wr(R_J10_MODE, b"\x01"), OK)
        self.cmd(C_ENABLE)
        self.advance(0.01)
        self.wr32(R_MOVE_REL, -500)
        self.advance(1.0)
        self.assertEqual(self.u(0x5C, 4, True), -500)


class SymbolTest(SimCase):
    SRC = Path(__file__).resolve().parents[2] / "App" / "Src"
    INC = Path(__file__).resolve().parents[2] / "App" / "Inc"

    def c_entries(self) -> Dict[str, Tuple[int, str]]:
        types = {}
        for m in re.finditer(r"#define\s+SYM_(U8|I8|U16|I16|U32|I32|F32|BYTES)\s+(0x[0-9A-Fa-f]+)u", (self.INC / "symtab.h").read_text(encoding="utf-8")):
            types["SYM_" + m.group(1)] = int(m.group(2), 16)
        self.assertEqual(len(types), 8)
        plain = re.compile(r"^SYM_ENTRY\(\s*(?P<n>[^,]+?)\s*,\s*(?P<t>SYM_\w+)\s*,\s*(?P<num>.+?)\)\s*;", re.M)
        alias = re.compile(r'^SYM_ENTRY_AS\(\s*"(?P<n>[^"]+)"\s*,\s*[^,]+,\s*(?P<t>SYM_\w+)\s*,\s*(?P<num>.+?)\)\s*;', re.M)
        out: Dict[str, Tuple[int, str]] = {}
        for path in sorted(self.SRC.glob("*.c")):
            text = path.read_text(encoding="utf-8")
            for rx in (plain, alias):
                for m in rx.finditer(text):
                    self.assertNotIn(m.group("n"), out, f"名前が重複: {m.group('n')}")
                    out[m.group("n")] = (types[m.group("t")], m.group("num").strip())
        return out

    def test_symbol_table_matches_firmware_sources(self) -> None:
        if not self.SRC.is_dir():
            self.skipTest("firmware/App/Src が見つかりません")
        c = self.c_entries()
        s = {e.name: e for e in simmod.SYMBOLS}
        self.assertEqual(len(simmod.SYMBOLS), len(s), "sim の名前表に重複があります")
        self.assertEqual(sorted(set(c) - set(s)), [], "ファームにあって sim に無い名前（sim/sim.py に追加してください）")
        self.assertEqual(sorted(set(s) - set(c)), [], "sim にあってファームに無い名前")
        for name, (typ, num) in c.items():
            self.assertEqual(s[name].type, typ, f"{name} の TYPE")
            if typ != simmod.T_BYTES:
                self.assertEqual(s[name].num, int(num), f"{name} の NUM")
        self.assertEqual(len(c), 104)

    def test_symbols_over_usb(self) -> None:
        self.link.connect()
        self.assertEqual(self.link.sym_count(), 104)
        for i in (0, 20, 103):
            info = self.link.sym_info(i)
            e = simmod.SYMBOLS[i]
            self.assertEqual((info.name, info.addr, info.type, info.num), (e.name, e.addr, e.type, e.num))
            found = self.link.sym_find(info.name)
            self.assertEqual((found.index, found.addr, found.type, found.num), (i, e.addr, e.type, e.num))
        self.assertEqual(self.link.sym_find("cfg").num, 40)
        self.assertEqual(self.link.sym_find("comm_stats").num, 36)
        self.assertEqual(self.link.sym_find("usb_stats").num, 32)
        self.device_error(lambda: self.link.sym_find("Position"), 0x36)    # 大文字小文字を区別

    def test_struct_fields_are_close_and_whole_covers_them(self) -> None:
        s = {e.name: e for e in simmod.SYMBOLS}
        for whole in ("cfg", "comm_stats", "usb_stats"):
            w = s[whole]
            fields = [e for n, e in s.items() if n.startswith(whole + ".")]
            self.assertTrue(fields)
            for f in fields:
                self.assertTrue(w.addr <= f.addr and f.addr + f.size <= w.addr + w.size, f.name)
        # 状態・監視などはまとめ読みできる近さに置く
        self.assertLess(abs(s["mon.vm_mv"].addr - s["mon.board_temp_dc"].addr), 16)
        self.assertLess(abs(s["position"].addr - s["prm.invert_dir"].addr), 64)
        self.assertLess(abs(s["home.state"].addr - s["hm2.count"].addr), 64)

    def test_cfg_whole_matches_registers(self) -> None:
        self.boot(enable=False)
        self.wr(R_MAX_SPEED, struct.pack("<I", 4242))
        s = {e.name: e for e in simmod.SYMBOLS}
        whole = self.link.short_up(s["cfg"].addr, 40)
        self.assertEqual(struct.unpack_from("<I", whole, 8)[0], 4242)
        self.assertEqual(whole, self.link.sym_upload(self.link.sym_find("cfg")))
        self.assertEqual(whole, simmod.Settings(max_speed=4242).pack())
        # 個別フィールドは同じ領域を指す
        f = self.link.sym_find("cfg.max_speed")
        self.assertEqual(f.addr, s["cfg"].addr + 8)
        self.assertEqual(struct.unpack("<I", self.link.sym_upload(f))[0], 4242)

    def test_sym_upload_ranges(self) -> None:
        self.link.connect()
        raw = b"cfg"
        ok = self.link.request(p.CMD_SYM_UPLOAD, bytes([3]) + raw + struct.pack("<HB", 8, 4))
        self.assertEqual(len(ok), 4)
        self.assertEqual(len(self.link.request(p.CMD_SYM_UPLOAD, bytes([3]) + raw + struct.pack("<HB", 36, 0))), 4)
        self.device_error(lambda: self.link.request(p.CMD_SYM_UPLOAD, bytes([3]) + raw + struct.pack("<HB", 40, 1)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_SYM_UPLOAD, bytes([3]) + raw + struct.pack("<HB", 38, 4)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_SYM_UPLOAD, bytes([3]) + raw + b"\x00"), 0x31)

    def test_sym_upload_position_matches_register(self) -> None:
        self.boot()
        self.wr32(R_MOVE_REL, -1500)
        self.advance(0.12)        # 移動途中
        reg = self.u(R_POSITION, 4, True)
        info = self.link.sym_find("position")
        self.assertEqual((info.type, info.num), (simmod.T_I32, 1))
        self.assertEqual(struct.unpack("<i", self.link.sym_upload(info))[0], reg)
        self.assertLess(reg, 0)
        cur = struct.unpack("<f", self.link.sym_upload(self.link.sym_find("cur_speed")))[0]
        self.assertEqual(int(cur), self.u(R_SPEED, 4, True))
        self.assertEqual(self.link.sym_upload(self.link.sym_find("state"))[0], 2)
        self.assertEqual(self.link.sym_upload(self.link.sym_find("mode"))[0], 1)

    def test_state_symbols_follow_registers(self) -> None:
        self.boot()
        for name, reg, size in (("status", R_STATUS, 1), ("cmd_result", R_CMD_RESULT, 1), ("fault", R_FAULT, 1),
                                ("cfg_status", R_CONFIG_STATUS, 1), ("input_status", R_INPUT_STATUS, 1)):
            self.assertEqual(int.from_bytes(self.link.sym_upload(self.link.sym_find(name)), "little"),
                             self.u(reg, size), name)
        self.assertEqual(struct.unpack("<I", self.link.sym_upload(self.link.sym_find("mon.vm_mv")))[0], 24000)
        self.assertEqual(struct.unpack("<h", self.link.sym_upload(self.link.sym_find("mon.board_temp_dc")))[0], 250)
        self.assertEqual(self.link.sym_upload(self.link.sym_find("vm_ok"))[0], 1)

    def test_uwtick_increases_with_time(self) -> None:
        self.link.connect()
        info = self.link.sym_find("uwTick")
        a = struct.unpack("<I", self.link.sym_upload(info))[0]
        self.advance(0.25)
        b = struct.unpack("<I", self.link.sym_upload(info))[0]
        self.assertEqual(b - a, 250)

    def test_reset_csr_and_diag_defaults(self) -> None:
        self.link.connect()
        csr = struct.unpack("<I", self.link.sym_upload(self.link.sym_find("reset_csr")))[0]
        self.assertEqual(csr, simmod.RESET_CSR_POWER_ON)
        self.assertTrue(csr & (1 << 27))
        self.assertGreater(struct.unpack("<I", self.link.sym_upload(self.link.sym_find("stack.size")))[0], 0)

    def test_usb_stats_count_frames(self) -> None:
        self.link.connect()
        for _ in range(5):
            self.link.get_version()
        rx = self.u_sym("usb_stats.rx_frames")
        # 読み出し自身の要求（最後の SYM_UPLOAD）までの数と一致する
        self.assertEqual(rx, len(self.sim.requests))
        tx = self.u_sym("usb_stats.tx_frames")
        self.assertEqual(tx, len(self.sim.requests) - 1)     # 直前の応答まで
        self.assertEqual(self.u_sym("usb_stats.crc_errors"), 0)

    def u_sym(self, name: str) -> int:
        return int.from_bytes(self.link.sym_upload(self.link.sym_find(name)), "little")

    def test_memory_read_regions(self) -> None:
        self.link.connect()
        self.assertEqual(self.link.short_up(0x08000000, 4), b"\xff" * 4)
        self.assertEqual(len(self.link.short_up(0x20000000, 300)), 300)       # 240 B ずつ分割して読める
        self.device_error(lambda: self.link.short_up(0x40000000, 4), 0x33)
        self.device_error(lambda: self.link.short_up(0x2000FFFE, 4), 0x33)    # 領域をまたぐ

    def test_symbol_values_are_refreshed_each_read(self) -> None:
        self.link.connect()
        self.advance(0.1)
        info = self.link.sym_find("mon.vm_mv")
        self.assertEqual(struct.unpack("<I", self.link.sym_upload(info))[0], 24000)
        self.sim.set_vm_mv(12000)
        self.advance(0.05)
        self.assertEqual(struct.unpack("<I", self.link.sym_upload(info))[0], 12000)


class LinkLevelTest(SimCase):
    def test_unplug_and_replug(self) -> None:
        self.boot()
        self.wr32(R_SET_POSITION, 777)
        self.sim.unplug()
        with self.assertRaises(OSError):
            self.link.get_version()
        with self.assertRaises(SimUnplugged):
            self.sim.read(1)
        with self.assertRaises(OSError):
            self.sim.in_waiting
        self.advance(1.0)       # 抜かれている間も基板の時間は進む
        self.sim.replug()
        self.assertEqual(self.link.get_version(), (1, 0))            # GET_VERSION は接続なしでも可
        self.device_error(lambda: self.link.can_request(REQ, bytes([2, 4, 1])), 0x35)
        self.link.connect()
        self.assertEqual(self.u(R_POSITION, 4, True), 777)           # 基板の状態は保たれる
        self.assertEqual(self.u(R_STATE), 2)

    def test_unplug_discards_pending_events(self) -> None:
        self.boot()
        self.sim.set_vm_mv(5000)
        self.advance(0.05)
        self.sim.unplug()
        self.sim.replug()
        self.assertEqual(self.sim.in_waiting, 0)

    def test_close_does_not_raise_when_unplugged(self) -> None:
        self.sim.unplug()
        self.link.close()
        self.assertTrue(self.sim.closed)

    def test_read_retried_after_dropped_response(self) -> None:
        self.link.connect()
        self.sim.drop_responses = 1
        self.assertEqual(self.link.get_version(), (1, 0))
        self.assertEqual(self.link.retransmits, 1)
        self.sim.drop_responses = 2
        self.assertEqual(self.u(R_STATE), 0)
        self.assertEqual(self.link.retransmits, 3)

    def test_read_fails_after_retries(self) -> None:
        self.link.connect()
        n = len(self.sim.requests)
        self.sim.drop_responses = -1
        with self.assertRaises(LinkError) as cm:
            self.link.get_version()
        self.assertNotIsInstance(cm.exception, UnsafeRetryError)
        self.assertEqual(len(self.sim.requests) - n, 4)          # 初回 + 再送 3 回

    def test_write_not_retried_but_executed(self) -> None:
        self.boot(enable=False)
        n = len(self.sim.requests)
        self.sim.drop_responses = 1
        with self.assertRaises(UnsafeRetryError):
            self.wr(R_MICROSTEP, b"\x08")
        self.assertEqual(len(self.sim.requests) - n, 1)           # 再送していない
        self.assertEqual(self.u(R_MICROSTEP), 8)                  # しかし実行されている

    def test_broadcast_not_retried(self) -> None:
        self.boot()
        n = len(self.sim.requests)
        self.sim.drop_responses = 1
        with self.assertRaises(UnsafeRetryError):
            self.link.can_request(0x000, bytes([2]))
        self.assertEqual(len(self.sim.requests) - n, 1)
        self.assertEqual(self.u(R_STATE), 1)

    def test_catch_up_is_limited_to_10_seconds(self) -> None:
        self.link.connect()
        info = self.link.sym_find("uwTick")
        a = struct.unpack("<I", self.link.sym_upload(info))[0]
        self.clk.t += 3600.0
        b = struct.unpack("<I", self.link.sym_upload(info))[0]
        self.assertEqual(b - a, 10000)
        self.clk.t += 0.5
        c = struct.unpack("<I", self.link.sym_upload(info))[0]
        self.assertEqual(c - b, 500)

    def test_clock_going_backwards_is_harmless(self) -> None:
        self.link.connect()
        self.clk.t -= 5.0
        self.link.get_version()
        self.advance(0.05)
        self.assertEqual(self.sim.uptime_ms, 50)

    def test_enter_bootloader(self) -> None:
        self.boot()
        self.device_error(self.link.enter_bootloader, 0x33)                         # ENABLED 中
        self.device_error(lambda: self.link.request(p.CMD_ENTER_BOOTLOADER, struct.pack("<I", 1)), 0x32)
        self.device_error(lambda: self.link.request(p.CMD_ENTER_BOOTLOADER, b"\x00"), 0x31)
        self.cmd(C_DISABLE)
        self.assertFalse(self.sim.bootloader_entered)
        self.link.enter_bootloader()
        self.assertTrue(self.sim.bootloader_entered)
        with self.assertRaises(OSError):
            self.link.get_version()                                                   # ポートが消えた
        self.sim.replug()                                                            # 電源再投入相当
        self.link.connect()
        self.assertEqual(self.u(R_STATE), 0)

    def test_enter_bootloader_locked_before_connect(self) -> None:
        self.device_error(self.link.enter_bootloader, 0x35)


class CliSimTest(unittest.TestCase):
    def run_cli(self, *argv: str, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), **kw)
        return code, out.getvalue(), err.getvalue()

    def test_info(self) -> None:
        code, out, err = self.run_cli("--sim", "info")
        self.assertEqual(code, 0, err)
        self.assertIn("DEVICE_ID: 0xA3", out)
        self.assertIn("BOARD_ID: 3", out)

    def test_option_after_subcommand(self) -> None:
        code, out, err = self.run_cli("info", "--sim")
        self.assertEqual(code, 0, err)

    def test_regs(self) -> None:
        code, out, err = self.run_cli("--sim", "regs")
        self.assertEqual(code, 0, err)
        self.assertIn("STATE", out)
        self.assertIn("1 (DISABLED)", out)
        self.assertIn("MAX_SPEED", out)

    def test_get_position(self) -> None:
        code, out, err = self.run_cli("--sim", "get", "position")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "position = 0")

    def test_syms_and_can_req_enable(self) -> None:
        code, out, err = self.run_cli("--sim", "syms")
        self.assertEqual(code, 0, err)
        self.assertIn("104 件", out)
        code, out, err = self.run_cli("--sim", "can-req", "0x103", "015001")
        self.assertEqual(code, 0, err)
        self.assertIn("data=81 50 00", out)

    def test_read_and_events(self) -> None:
        code, out, err = self.run_cli("--sim", "read", "0x20000040", "4")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("--sim", "events", "--duration", "0.05")
        self.assertEqual(code, 0, err)

    def test_dfu_accepts_enter_bootloader(self) -> None:
        tools = dfu.DfuTools(which=lambda: None)
        code, out, err = self.run_cli("--sim", "dfu", "--yes", dfu_tools=tools)
        self.assertEqual(code, 0, err)
        self.assertIn("ENTER_BOOTLOADER を受け付けました", out)

    def test_sim_ignores_port_detection(self) -> None:
        code, out, err = self.run_cli("--sim", "--port", "COM99", "info")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
