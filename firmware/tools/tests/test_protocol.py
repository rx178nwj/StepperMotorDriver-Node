import unittest

from nemadbg import protocol as p


class Crc16Test(unittest.TestCase):
    def test_check_value(self) -> None:
        self.assertEqual(p.crc16(b"123456789"), 0x29B1)

    def test_empty(self) -> None:
        self.assertEqual(p.crc16(b""), 0xFFFF)


class FrameTest(unittest.TestCase):
    def test_encode_layout(self) -> None:
        body = bytes([0x01, 0x05])
        f = p.encode_frame(body)
        crc = p.crc16(bytes([2]) + body)
        self.assertEqual(f, bytes([0xA5, 2, 0x01, 0x05, crc & 0xFF, crc >> 8]))

    def test_encode_len_range(self) -> None:
        with self.assertRaises(ValueError):
            p.encode_frame(b"\x01")
        with self.assertRaises(ValueError):
            p.encode_frame(bytes(251))
        p.encode_frame(bytes(2))
        p.encode_frame(bytes(250))

    def test_roundtrip(self) -> None:
        for n in (2, 3, 100, 250):
            body = bytes((i * 7) & 0xFF for i in range(n))
            self.assertEqual(p.FrameParser().feed(p.encode_frame(body)), [body])

    def test_byte_by_byte(self) -> None:
        body = b"\xff\x00\x01abc"
        parser = p.FrameParser()
        out = []
        for b in p.encode_frame(body):
            out += parser.feed(bytes([b]))
        self.assertEqual(out, [body])

    def test_two_frames_in_one_chunk(self) -> None:
        a, b = b"\x01\x01", b"\x1b\x02xy"
        self.assertEqual(p.FrameParser().feed(p.encode_frame(a) + p.encode_frame(b)), [a, b])

    def test_garbage_before_frame(self) -> None:
        body = b"\x01\x01"
        self.assertEqual(p.FrameParser().feed(b"\x00\x12\x34" + p.encode_frame(body)), [body])

    def test_bad_len_resync(self) -> None:
        body = b"\x01\x09"
        parser = p.FrameParser()
        # LEN=0, LEN=1, LEN=251 は範囲外。SOF の直後に続いて本物のフレーム
        data = bytes([0xA5, 0x00, 0xA5, 0x01, 0xA5, 0xFB]) + p.encode_frame(body)
        self.assertEqual(parser.feed(data), [body])
        self.assertEqual(parser.bad_len, 3)

    def test_reset_drops_partial(self) -> None:
        # 先頭の余分な SOF が LEN=0xA5 と解釈され保留される。reset で捨てれば次のフレームを拾える
        body = b""
        parser = p.FrameParser()
        self.assertEqual(parser.feed(bytes([0xA5]) + p.encode_frame(body)), [])
        parser.reset()
        self.assertEqual(parser.feed(p.encode_frame(body)), [body])

    def test_crc_mismatch_discarded(self) -> None:
        bad = bytearray(p.encode_frame(b"\x01\x01"))
        bad[-1] ^= 0xFF
        good = b"\x1b\x02"
        parser = p.FrameParser()
        self.assertEqual(parser.feed(bytes(bad) + p.encode_frame(good)), [good])
        self.assertEqual(parser.crc_errors, 1)

    def test_truncated_then_new_frame(self) -> None:
        full = p.encode_frame(b"\x01\x01\x02\x03\x04\x05")
        good = p.encode_frame(b"\x1b\x02")
        parser = p.FrameParser()
        # 途中で切れたフレームの後に次のフレームが来ても、次のフレームを拾う
        out = parser.feed(full[:5] + good + good)
        self.assertIn(b"\x1b\x02", out)
        self.assertEqual(parser.feed(b""), [])

    def test_incomplete_waits(self) -> None:
        f = p.encode_frame(b"\x01\x01")
        parser = p.FrameParser()
        self.assertEqual(parser.feed(f[:-1]), [])
        self.assertEqual(parser.feed(f[-1:]), [b"\x01\x01"])


class BodyTest(unittest.TestCase):
    def test_request(self) -> None:
        f = p.encode_request(p.CMD_SHORT_UP, 0x102, b"\x04\x00\x00\x00\x20")
        (body,) = p.FrameParser().feed(f)
        self.assertEqual(body, bytes([0x0F, 0x02, 4, 0, 0, 0, 0x20]))

    def test_decode(self) -> None:
        r = p.decode_body(bytes([0xFF, 0x32, 7, 1, 2]))
        self.assertEqual(r, p.Response(0x32, 7, b"\x01\x02"))
        e = p.decode_body(bytes([0xFE, 1, 0x83, 0, 2, 5, 6]))
        self.assertEqual(e, p.Event(1, b"\x83\x00\x02\x05\x06"))
        with self.assertRaises(ValueError):
            p.decode_body(b"\x01\x02")

    def test_can_frames(self) -> None:
        data = p.encode_can(p.CanFrame(0x183, b"\x82\x04\x02")) + p.encode_can(p.CanFrame(0x080, b""))
        self.assertEqual(p.parse_can_frames(data, 2),
                         [p.CanFrame(0x183, b"\x82\x04\x02"), p.CanFrame(0x080, b"")])
        with self.assertRaises(ValueError):
            p.parse_can_frames(data[:4], 1)


if __name__ == "__main__":
    unittest.main()
