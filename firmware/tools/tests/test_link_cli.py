import contextlib
import io
import unittest
from unittest import mock

from nemadbg import cli, elfinfo
from nemadbg import protocol as p
from nemadbg.link import DeviceError, Link, LinkError, UnsafeRetryError

from fakedevice import BOARD_ID, BUILD_ID, RAM_BASE, FakeSerial


def make_link(dev: FakeSerial, timeout_s: float = 0.03) -> Link:
    return Link(dev, timeout_s=timeout_s)


def run_cli(dev: FakeSerial, *argv: str):
    """CLI を擬似デバイスで実行し (終了コード, stdout, stderr) を返す。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["--timeout", "30", *argv], link_factory=lambda a: Link(dev, timeout_s=a.timeout / 1000))
    return code, out.getvalue(), err.getvalue()


class LinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dev = FakeSerial()
        self.link = make_link(self.dev)

    def test_connect_and_ids(self) -> None:
        ci = self.link.connect()
        self.assertEqual((ci.fw_major, ci.fw_minor, ci.board_id, ci.max_len), (1, 2, BOARD_ID, 250))
        self.assertEqual(self.link.exchange_id(), BUILD_ID)
        self.assertEqual(self.link.get_version(), (1, 0))

    def test_ctr_increments(self) -> None:
        self.link.connect()
        self.link.get_version()
        self.assertEqual([r[1] for r in self.dev.requests], [1, 2])

    def test_not_connected_err_0x35(self) -> None:
        with self.assertRaises(DeviceError) as cm:
            self.link.exchange_id()
        self.assertEqual(cm.exception.err, 0x35)
        self.assertIn("アクセスロック", str(cm.exception))

    def test_no_resource_err_0x36(self) -> None:
        self.link.connect()
        with self.assertRaises(DeviceError) as cm:
            self.link.sym_find("nosuch")
        self.assertEqual(cm.exception.err, 0x36)

    def test_short_up_split(self) -> None:
        self.link.connect()
        data = self.link.short_up(RAM_BASE + 0x100, 300)
        self.assertEqual(data, bytes(i & 0xFF for i in range(300)))
        sizes = [r[2] for r in self.dev.requests if r[0] == p.CMD_SHORT_UP]
        self.assertEqual(sizes, [240, 60])

    def test_short_up_denied(self) -> None:
        self.link.connect()
        with self.assertRaises(DeviceError) as cm:
            self.link.short_up(0x40000000, 4)
        self.assertEqual(cm.exception.err, 0x33)

    def test_symbols(self) -> None:
        self.link.connect()
        self.assertEqual(self.link.sym_count(), 5)
        s = self.link.sym_info(1)
        self.assertEqual((s.name, s.addr, s.type, s.num), ("position", RAM_BASE + 4, 0x06, 1))
        with self.assertRaises(DeviceError):
            self.link.sym_info(99)
        big = self.link.sym_find("big")
        self.assertEqual(len(self.link.sym_upload(big)), 300)

    def test_can_request(self) -> None:
        self.link.connect()
        frames = self.link.can_request(0x100 + BOARD_ID, bytes([2, 0x00, 4]))
        self.assertEqual(frames, [p.CanFrame(0x180 + BOARD_ID, bytes([0x82, 0x00, 0xA3, 0, 0, BOARD_ID]))])
        self.assertEqual(self.link.can_request(0x100 + BOARD_ID + 1, bytes([2, 0, 1])), [])

    def test_retransmit_on_timeout(self) -> None:
        self.dev.drop_responses = 2
        self.link.connect()
        self.assertEqual(self.link.retransmits, 2)
        self.assertEqual(len(self.dev.requests), 3)
        self.assertEqual({r[1] for r in self.dev.requests}, {1})   # 再送は同じ CTR

    def test_gives_up_after_retries(self) -> None:
        self.dev.drop_responses = 100
        with self.assertRaises(LinkError):
            self.link.connect()
        self.assertEqual(len(self.dev.requests), 4)   # 初回 + 再送 3 回

    def _count_can_requests(self) -> int:
        return len([r for r in self.dev.requests if r[0] == p.CMD_CAN_REQUEST])

    def test_can_write_not_retransmitted(self) -> None:
        self.link.connect()
        self.dev.drop_responses = 100
        with self.assertRaises(UnsafeRetryError) as cm:
            self.link.can_request(0x100 + BOARD_ID, bytes([0x01, 0x20, 0x01]))   # OP=0x01（書き込み）
        self.assertIn("実行されたかは不明", str(cm.exception))
        self.assertIn("regs", str(cm.exception))
        self.assertEqual(self._count_can_requests(), 1)
        self.assertEqual(self.link.retransmits, 0)

    def test_can_broadcast_not_retransmitted(self) -> None:
        self.link.connect()
        self.dev.drop_responses = 100
        with self.assertRaises(LinkError):
            self.link.can_request(0x000, bytes([0x02, 0x00, 0x01]))   # 読み出しでも CAN_ID=0 は再送しない
        self.assertEqual(self._count_can_requests(), 1)

    def test_can_read_retransmitted(self) -> None:
        self.link.connect()
        self.dev.drop_responses = 100
        with self.assertRaises(LinkError):
            self.link.can_request(0x100 + BOARD_ID, bytes([0x02, 0x00, 0x04]))
        self.assertEqual(self._count_can_requests(), 4)   # 初回 + 再送 3 回
        self.dev.drop_responses = 1
        self.link.can_request(0x100 + BOARD_ID, bytes([0x02, 0x00, 0x04]))
        self.assertEqual(self.link.retransmits, 4)

    def test_stale_response_ignored(self) -> None:
        self.dev.stale_first = True
        self.assertEqual(self.link.connect().board_id, BOARD_ID)
        self.assertEqual(self.link.retransmits, 0)

    def test_events_queued(self) -> None:
        self.link.connect()
        self.dev.push_event(0x080 + BOARD_ID, bytes([3, 1]))
        self.link.get_version()
        self.assertEqual(len(self.link.events), 1)
        evs = self.link.wait_events(0.01)
        self.assertEqual(evs[0].evt, p.EVT_CAN_FRAME)
        self.assertEqual(p.parse_can_frames(evs[0].data, 1)[0].can_id, 0x083)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dev = FakeSerial()

    def test_info(self) -> None:
        code, out, _ = run_cli(self.dev, "info")
        self.assertEqual(code, 0)
        self.assertIn("FW: 1.2", out)
        self.assertIn(BUILD_ID.hex(), out)
        self.assertFalse(self.dev.connected)   # 終了時に DISCONNECT 済み
        self.assertTrue(self.dev.closed)

    def test_info_elf_match_and_mismatch(self) -> None:
        with mock.patch.object(elfinfo, "load", return_value=elfinfo.ElfInfo(BUILD_ID)):
            code, out, _ = run_cli(self.dev, "info", "--elf", "x.elf")
        self.assertEqual(code, 0)
        self.assertIn("一致", out)
        with mock.patch.object(elfinfo, "load", return_value=elfinfo.ElfInfo(bytes(20))):
            code, out, _ = run_cli(FakeSerial(), "info", "--elf", "x.elf")
        self.assertEqual(code, 2)
        self.assertIn("不一致", out)

    def test_syms(self) -> None:
        code, out, _ = run_cli(self.dev, "syms")
        self.assertEqual(code, 0)
        self.assertIn("position", out)
        self.assertIn("0x20000004", out)
        self.assertIn("5 件", out)

    def test_get(self) -> None:
        code, out, _ = run_cli(self.dev, "get", "state", "position", "speed_f", "arr16")
        self.assertEqual(code, 0)
        self.assertIn("state = 2", out)
        self.assertIn("position = -1234", out)
        self.assertIn("speed_f = 1.5", out)
        self.assertIn("arr16 = [1, 2, 65535]", out)

    def test_get_unknown_name(self) -> None:
        code, _, err = run_cli(self.dev, "get", "nosuch")
        self.assertEqual(code, 1)
        self.assertIn("0x36", err)
        self.assertIn("リソースなし", err)
        self.assertFalse(self.dev.connected)

    def test_read(self) -> None:
        code, out, _ = run_cli(self.dev, "read", "0x20000100", "20")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("20000100: 00 01 02"))

    def test_read_denied(self) -> None:
        code, _, err = run_cli(self.dev, "read", "0x40000000", "4")
        self.assertEqual(code, 1)
        self.assertIn("アクセス拒否", err)

    def test_read_var(self) -> None:
        elf = elfinfo.ElfInfo(BUILD_ID, {"position": [(RAM_BASE + 4, 4)], "arr": [(RAM_BASE + 0x10, 6)]})
        with mock.patch.object(elfinfo, "load", return_value=elf):
            code, out, _ = run_cli(self.dev, "read-var", "--elf", "x.elf", "position", "arr")
        self.assertEqual(code, 0)
        self.assertIn("2e fb ff ff", out)
        self.assertIn("signed -1234", out)
        self.assertIn("01 00 02 00 ff ff", out)

    def test_read_var_mismatch_does_not_read(self) -> None:
        elf = elfinfo.ElfInfo(bytes(20), {"position": [(RAM_BASE + 4, 4)]})
        with mock.patch.object(elfinfo, "load", return_value=elf):
            code, _, _ = run_cli(self.dev, "read-var", "--elf", "x.elf", "position")
        self.assertEqual(code, 2)
        self.assertNotIn(p.CMD_SHORT_UP, [r[0] for r in self.dev.requests])

    def test_read_var_requires_elf(self) -> None:
        code, _, err = run_cli(self.dev, "read-var", "position")
        self.assertEqual(code, 1)
        self.assertIn("--elf", err)

    def test_can_req(self) -> None:
        code, out, _ = run_cli(self.dev, "can-req", "0x103", "020301")
        self.assertEqual(code, 0)
        self.assertIn("id=0x183", out)
        self.assertIn("82 03 03", out)
        code, out, _ = run_cli(FakeSerial(), "can-req", "0x555", "")
        self.assertIn("応答なし", out)

    def test_regs(self) -> None:
        code, out, _ = run_cli(self.dev, "regs")
        self.assertEqual(code, 0)
        self.assertIn("DEVICE_ID", out)
        self.assertIn("STATE", out)
        self.assertIn("2 (ENABLED)", out)
        self.assertIn("[EXT_5V, TARGET_REACHED]", out)
        self.assertIn("POSITION", out)
        self.assertIn("-100", out)
        self.assertIn("24000", out)

    def test_regs_range(self) -> None:
        code, out, _ = run_cli(self.dev, "regs", "--start", "0x08", "--len", "4")
        self.assertEqual(code, 0)
        self.assertIn("POSITION", out)
        self.assertNotIn("SPEED", out)

    def test_watch_count(self) -> None:
        code, out, _ = run_cli(self.dev, "watch", "state", "position", "--interval", "0.01", "--count", "3")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("state=2 position=-1234", lines[0])

    def test_events(self) -> None:
        orig = FakeSerial.write

        def write_and_event(dev, data):
            n = orig(dev, data)
            if data[2:3] == bytes([p.CMD_CONNECT]):
                dev.push_event(0x083, bytes([3, 1]))
            return n

        with mock.patch.object(FakeSerial, "write", write_and_event):
            code, out, _ = run_cli(self.dev, "events", "--duration", "0.1")
        self.assertEqual(code, 0)
        self.assertIn("EVT CAN_FRAME id=0x083", out)

    def test_timeout_error_exit_1(self) -> None:
        self.dev.drop_responses = 100
        code, _, err = run_cli(self.dev, "info")
        self.assertEqual(code, 1)
        self.assertIn("応答がありません", err)

    def test_can_req_write_timeout_message(self) -> None:
        orig = FakeSerial.write

        def write_and_drop(dev, data):
            if data[2:3] == bytes([p.CMD_CAN_REQUEST]):
                dev.drop_responses = 100
            return orig(dev, data)

        with mock.patch.object(FakeSerial, "write", write_and_drop):
            code, _, err = run_cli(self.dev, "can-req", "0x103", "012001")
        self.assertEqual(code, 1)
        self.assertIn("実行されたかは不明", err)
        self.assertEqual(len([r for r in self.dev.requests if r[0] == p.CMD_CAN_REQUEST]), 1)


if __name__ == "__main__":
    unittest.main()
