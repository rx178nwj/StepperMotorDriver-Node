import contextlib
import struct
import io
import unittest
from unittest import mock

from nemadbg import cli, dfu, elfinfo
from nemadbg import protocol as p
from nemadbg.link import DeviceError, Link, LinkError, UnsafeRetryError

from fakedevice import BOARD_ID, BUILD_ID, RAM_BASE, FakeSerial


def make_link(dev: FakeSerial, timeout_s: float = 0.03) -> Link:
    return Link(dev, timeout_s=timeout_s)


def run_cli(dev: FakeSerial, *argv: str, **kwargs):
    """CLI を擬似デバイスで実行し (終了コード, stdout, stderr) を返す。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["--timeout", "30", *argv], link_factory=lambda a: Link(dev, timeout_s=a.timeout / 1000),
                        **kwargs)
    return code, out.getvalue(), err.getvalue()


class FakeDfuTools(dfu.DfuTools):
    """STM32_Programmer_CLI の偽物。時間は進めた分だけ clock が増える（実際には待たない）。"""

    def __init__(self, cli_path="C:/prog/STM32_Programmer_CLI.exe", found_after=None) -> None:
        self.t = 0.0
        self.calls = 0
        self.cli_path = cli_path
        self.found_after = found_after      # この回数目の実行から DFU デバイスが見える（None なら見えない）
        super().__init__(which=lambda: self.cli_path, run=self._run, sleep=self._sleep, clock=lambda: self.t)

    def _run(self, cli: str) -> str:
        self.calls += 1
        self.t += 0.3                       # CLI 1 回の実行時間
        if self.found_after is not None and self.calls >= self.found_after:
            return ("=====  DFU Interface   =====\n\nTotal number of available STM32 device in DFU mode: 1\n\n"
                    "  Device Index           : USB1\n")
        return "=====  DFU Interface   =====\n\nNo STM32 device in DFU mode connected\n"

    def _sleep(self, s: float) -> None:
        self.t += s


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

    def test_enter_bootloader_param_and_no_retry(self) -> None:
        self.dev.regs[0x04] = 1
        self.link.connect()
        self.link.enter_bootloader()
        reqs = [r for r in self.dev.requests if r[0] == p.CMD_ENTER_BOOTLOADER]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0][2:], b"BOOT")                       # KEY u32 リトルエンディアン 0x544F4F42
        self.assertEqual(reqs[0][2:], struct.pack("<I", 0x544F4F42))
        self.assertTrue(self.dev.bootloader_entered)

    def test_enter_bootloader_lost_response_not_retransmitted(self) -> None:
        self.dev.regs[0x04] = 1
        self.link.connect()
        self.dev.drop_responses = 100
        with self.assertRaises(UnsafeRetryError) as cm:
            self.link.enter_bootloader()
        self.assertIn("実行されたかは不明", str(cm.exception))
        self.assertEqual(len([r for r in self.dev.requests if r[0] == p.CMD_ENTER_BOOTLOADER]), 1)
        self.assertEqual(self.link.retransmits, 0)

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

    def test_regs_new_registers(self) -> None:
        code, out, _ = run_cli(self.dev, "regs")
        self.assertEqual(code, 0)
        line = {ln.split()[1]: ln for ln in out.splitlines() if ln.startswith("0x")}
        self.assertTrue(line["INPUT_STATUS"].startswith("0x1E"))
        self.assertIn("[HOME1, HOMED]", line["INPUT_STATUS"])
        self.assertIn("[LOCAL_JOG, AGC, HOME_ACTIVE_HIGH]", line["FLAGS"])
        self.assertTrue(line["J10_MODE"].startswith("0x59"))
        self.assertIn("2 (HOME)", line["J10_MODE"])
        self.assertTrue(line["HOME_SPEED"].startswith("0x5A"))
        self.assertIn("800", line["HOME_SPEED"])
        self.assertTrue(line["ENC_COUNT"].startswith("0x5C"))
        self.assertIn("-7", line["ENC_COUNT"])
        reads = [r for r in self.dev.requests if r[0] == p.CMD_CAN_REQUEST]
        self.assertEqual(len(reads), 16)                             # 0x00〜0x5F を 6 B ずつ（0x5A〜0x5F が最後）
        self.assertEqual(reads[-1][-2:], bytes([0x5A, 6]))

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

    def _dfu(self, *args: str, answer="y", tools=None, dev=None):
        dev = dev or self.dev
        prompts = []

        def prompt(text: str) -> str:
            prompts.append(text)
            if isinstance(answer, Exception):
                raise answer
            return answer

        tools = tools or FakeDfuTools(found_after=2)
        code, out, err = run_cli(dev, "dfu", *args, prompt=prompt, dfu_tools=tools)
        return code, out, err, prompts, tools

    def test_dfu_prompt_yes(self) -> None:
        self.dev.regs[0x04] = 1
        code, out, err, prompts, tools = self._dfu()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(prompts), 1)
        self.assertTrue(self.dev.bootloader_entered)
        self.assertIn("検出しました", out)
        self.assertIn("STM32_Programmer_CLI -c port=usb1 -w <ELF> -v -g 0x08000000", out)
        self.assertEqual(tools.calls, 2)

    def test_dfu_prompt_declined(self) -> None:
        for ans in ("n", "", "no", EOFError()):
            dev = FakeSerial()
            dev.regs[0x04] = 1
            code, out, _, prompts, _ = self._dfu(answer=ans, dev=dev)
            self.assertEqual(code, 1)
            self.assertEqual(len(prompts), 1)
            self.assertIn("中止", out)
            self.assertEqual(dev.requests, [])                       # 接続もしない
            self.assertFalse(dev.bootloader_entered)

    def test_dfu_yes_option_skips_prompt(self) -> None:
        self.dev.regs[0x04] = 1
        code, _, _, prompts, _ = self._dfu("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(prompts, [])
        self.assertTrue(self.dev.bootloader_entered)

    def test_dfu_does_not_send_disconnect(self) -> None:
        self.dev.regs[0x04] = 1
        self._dfu("--yes")
        self.assertEqual([r[0] for r in self.dev.requests], [p.CMD_CONNECT, p.CMD_ENTER_BOOTLOADER])
        self.assertTrue(self.dev.closed)

    def test_dfu_enabled_err_0x33_message(self) -> None:
        self.dev.regs[0x04] = 2
        code, out, err, _, tools = self._dfu("--yes")
        self.assertEqual(code, 1)
        self.assertIn("励磁中（ENABLED）", err)
        self.assertIn("DISABLE", err)
        self.assertFalse(self.dev.bootloader_entered)
        self.assertEqual(tools.calls, 0)                             # 検出もしない
        self.assertEqual(self.dev.requests[-1][0], p.CMD_DISCONNECT)  # 拒否されたときは通常どおり切断する

    def test_dfu_lost_response_not_retried_no_disconnect(self) -> None:
        self.dev.regs[0x04] = 1
        orig = FakeSerial.write

        def write_and_drop(dev, data):
            if data[2:3] == bytes([p.CMD_ENTER_BOOTLOADER]):
                dev.drop_responses = 100
            return orig(dev, data)

        with mock.patch.object(FakeSerial, "write", write_and_drop):
            code, _, err, _, tools = self._dfu("--yes")
        self.assertEqual(code, 1)
        self.assertIn("実行されたかは不明", err)
        self.assertEqual([r[0] for r in self.dev.requests], [p.CMD_CONNECT, p.CMD_ENTER_BOOTLOADER])
        self.assertEqual(tools.calls, 0)

    def test_dfu_without_programmer_cli(self) -> None:
        self.dev.regs[0x04] = 1
        tools = FakeDfuTools(cli_path=None)
        code, out, _, _, _ = self._dfu("--yes", "--elf", "app.elf", tools=tools)
        self.assertEqual(code, 0)
        self.assertEqual(tools.calls, 0)
        self.assertIn("PATH にない", out)
        self.assertIn("検出を省き", out)
        self.assertIn("STM32_Programmer_CLI -c port=usb1 -w app.elf -v -g 0x08000000", out)

    def test_dfu_not_detected_in_5_seconds(self) -> None:
        self.dev.regs[0x04] = 1
        tools = FakeDfuTools(found_after=None)
        code, out, err, _, _ = self._dfu("--yes", tools=tools)
        self.assertEqual(code, 1)
        self.assertIn("検出できませんでした", err)
        self.assertGreater(tools.calls, 3)
        self.assertLessEqual(tools.t, 5.0 + 0.3 + 0.5)               # 最大 5 秒（最後の 1 回分の実行時間を含む）
        self.assertIn("STM32_Programmer_CLI -c port=usb1", out)      # 手順は表示する

    def test_dfu_listing_formats(self) -> None:
        self.assertFalse(dfu.is_dfu_listed("=====  DFU Interface   =====\n\nNo STM32 device in DFU mode connected\n"))
        self.assertFalse(dfu.is_dfu_listed(""))
        self.assertTrue(dfu.is_dfu_listed("Total number of available STM32 device in DFU mode: 1\n"))
        self.assertFalse(dfu.is_dfu_listed("Total number of available STM32 device in DFU mode: 0\n"))
        self.assertTrue(dfu.is_dfu_listed("  Device Index           : USB1\n"))

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
