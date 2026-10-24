"""コマンドライン本体。"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from datetime import datetime
from typing import Any, Callable, List, Optional

from . import dfu as dfu_mod
from . import elfinfo
from . import protocol as p
from . import registers as regs_mod
from .link import (DEFAULT_TIMEOUT_S, ConnectInfo, DeviceError, Link, LinkError, SymInfo,
                   UnsafeRetryError, find_ports, open_serial)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISMATCH = 2


class CliError(Exception):
    """利用者に見せるメッセージだけを出して終了コード 1 にする。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> "None":
        # 終了コード 2 は build-id 不一致に使うため、使い方の誤りは 1 にする
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


# ---- 整形 ----

def hexdump(data: bytes, base: int) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk).ljust(16 * 3 - 1)
        asc = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base + off:08X}: {hexs}  |{asc}|")
    return "\n".join(lines)


def format_sym_value(info: SymInfo, raw: bytes) -> str:
    name, size, fmt = p.SYM_TYPES.get(info.type, ("?", 1, ""))
    if not fmt:
        return raw.hex(" ")
    vals = [struct.unpack_from(fmt, raw, i * size)[0] for i in range(info.num)]
    texts = [f"{v:.7g}" if name == "f32" else str(v) for v in vals]
    return texts[0] if info.num == 1 else "[" + ", ".join(texts) + "]"


def type_name(t: int) -> str:
    return p.SYM_TYPES.get(t, (f"0x{t:02X}", 1, ""))[0]


def parse_int(s: str) -> int:
    try:
        return int(s, 0)
    except ValueError:
        raise CliError(f"数値として解釈できません: {s}")


# ---- 共通処理 ----

def resolve_port(args: argparse.Namespace) -> str:
    if args.port:
        return args.port
    ports = find_ports()
    if not ports:
        raise CliError("VID 0483 / PID 5740 のポートが見つかりません。--port で指定してください")
    if len(ports) > 1:
        raise CliError("該当ポートが複数あります。--port で指定してください: " + ", ".join(ports))
    return ports[0]


def default_link_factory(args: argparse.Namespace) -> Link:
    if getattr(args, "sim", False):
        from .sim import SimSerial   # 実機を使わないときだけ読み込む

        return Link(SimSerial(warmup_ms=200), timeout_s=args.timeout / 1000.0)
    port = resolve_port(args)
    try:
        ser = open_serial(port)
    except Exception as e:  # serial.SerialException など
        raise CliError(f"{port} を開けません: {e}")
    return Link(ser, timeout_s=args.timeout / 1000.0)


def load_elf(path: str) -> elfinfo.ElfInfo:
    try:
        return elfinfo.load(path)
    except elfinfo.ElfError as e:
        raise CliError(str(e))
    except ImportError:
        raise CliError("pyelftools が必要です: pip install -r requirements.txt")


def check_build_id(link: Link, elf: elfinfo.ElfInfo) -> bool:
    dev = link.exchange_id()
    ok = dev == elf.build_id
    print(f"build-id: 基板 {dev.hex()} / ELF {elf.build_id.hex()} -> " + ("一致" if ok else "不一致"))
    return ok


# ---- サブコマンド ----

def cmd_info(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    ver = link.get_version()
    print(f"プロトコル: {ver[0]}.{ver[1]}")
    print(f"DEVICE_ID: 0x{ci.device_id:02X}")
    print(f"FW: {ci.fw_major}.{ci.fw_minor}")
    print(f"BOARD_ID: {ci.board_id}")
    print(f"MAX_LEN: {ci.max_len}")
    if args.elf:
        return EXIT_OK if check_build_id(link, load_elf(args.elf)) else EXIT_MISMATCH
    print(f"build-id: {link.exchange_id().hex()}")
    return EXIT_OK


def cmd_syms(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    count = link.sym_count()
    print(f"{'INDEX':>5}  {'NAME':<32} {'ADDRESS':<10} {'TYPE':<6} NUM")
    for i in range(count):
        s = link.sym_info(i)
        print(f"{s.index:>5}  {s.name:<32} 0x{s.addr:08X} {type_name(s.type):<6} {s.num}")
    print(f"{count} 件")
    return EXIT_OK


def cmd_get(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    for name in args.names:
        info = link.sym_find(name)
        print(f"{name} = {format_sym_value(info, link.sym_upload(info))}")
    return EXIT_OK


def cmd_read(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    addr, size = parse_int(args.addr), parse_int(args.size)
    if size < 1 or addr < 0 or addr + size > 1 << 32:
        raise CliError("ADDR / SIZE が範囲外です")
    print(hexdump(link.short_up(addr, size), addr))
    return EXIT_OK


def cmd_read_var(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    elf = load_elf(args.elf)
    if not check_build_id(link, elf):
        print("build-id が一致しないため読み出しません", file=sys.stderr)
        return EXIT_MISMATCH
    for sym in args.symbols:
        try:
            addr, size = elf.resolve(sym)
        except elfinfo.ElfError as e:
            raise CliError(str(e))
        if size == 0:
            raise CliError(f"シンボル '{sym}' のサイズが 0 です")
        raw = link.short_up(addr, size)
        text = f"{sym} @0x{addr:08X} ({size} B): {raw.hex(' ')}"
        if size in (1, 2, 4):
            u = int.from_bytes(raw, "little")
            s = int.from_bytes(raw, "little", signed=True)
            text += f" = {u} (0x{u:0{size * 2}X})" + (f" / signed {s}" if s != u else "")
        print(text)
    return EXIT_OK


def cmd_can_req(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    can_id = parse_int(args.can_id)
    try:
        data = bytes.fromhex((args.data or "").replace(" ", "").replace("0x", ""))
    except ValueError:
        raise CliError("DATA_HEX は 16 進数で指定してください")
    frames = link.can_request(can_id, data)
    if not frames:
        print("応答なし")
    for f in frames:
        print(f"CAN id=0x{f.can_id:03X} dlc={len(f.data)} data={f.data.hex(' ')}")
    return EXIT_OK


def read_regs(link: Link, board_id: int, start: int, length: int) -> bytes:
    """レジスタ空間を 6 B ずつ CAN_REQUEST で読む。"""
    out = bytearray()
    addr = start
    end = start + length
    while addr < end:
        n = min(regs_mod.READ_MAX, end - addr)
        frames = link.can_request(regs_mod.BOARD_REQ_BASE + board_id,
                                  bytes([regs_mod.OP_READ, addr, n]))
        rsp = [f for f in frames if f.can_id == regs_mod.BOARD_RSP_BASE + board_id]
        if not rsp:
            raise CliError(f"レジスタ 0x{addr:02X} の読み出しに応答がありません")
        d = rsp[0].data
        if len(d) != 2 + n or d[0] != regs_mod.OP_READ_RSP or d[1] != addr:
            raise CliError(f"レジスタ 0x{addr:02X} の応答が不正です: {d.hex(' ')}")
        out += d[2:]
        addr += n
    return bytes(out)


def cmd_regs(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    start = parse_int(args.start)
    length = parse_int(args.len) if args.len is not None else regs_mod.REG_END - start
    if not 0 <= start <= 0xFF or length < 1 or start + length > 0x100:
        raise CliError("--start / --len が範囲外です")
    image = read_regs(link, ci.board_id, start, length)
    for r in regs_mod.REGISTERS:
        if r.addr < start or r.addr + r.size > start + length:
            continue
        raw = image[r.addr - start : r.addr - start + r.size]
        val = "(書き込み専用)" if r.access == "W" else regs_mod.format_value(r, raw)
        print(f"0x{r.addr:02X} {r.name:<14} {r.access:<2} {val}")
    return EXIT_OK


def cmd_watch(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    infos = [link.sym_find(n) for n in args.names]
    n = 0
    try:
        while args.count is None or n < args.count:
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cols = [f"{i.name}={format_sym_value(i, link.sym_upload(i))}" for i in infos]
            print(stamp + " " + " ".join(cols), flush=True)
            n += 1
            if args.count is None or n < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("中断しました")
    return EXIT_OK


def format_event(e: p.Event) -> str:
    if e.evt == p.EVT_CAN_FRAME:
        try:
            f = p.parse_can_frames(e.data, 1)[0]
            return f"EVT CAN_FRAME id=0x{f.can_id:03X} dlc={len(f.data)} data={f.data.hex(' ')}"
        except ValueError:
            pass
    return f"EVT 0x{e.evt:02X} data={e.data.hex(' ')}"


def cmd_events(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    end = None if args.duration is None else time.monotonic() + args.duration
    try:
        while end is None or time.monotonic() < end:
            wait = 0.2 if end is None else max(0.0, min(0.2, end - time.monotonic()))
            for e in link.wait_events(wait):
                print(datetime.now().strftime("%H:%M:%S.%f")[:-3] + " " + format_event(e), flush=True)
    except KeyboardInterrupt:
        print("中断しました")
    return EXIT_OK


DFU_ERR_ENABLED = 0x33   # ENTER_BOOTLOADER が ENABLED 中に返す ERR（FR-BL-02）
DFU_WRITE_EXAMPLE = "STM32_Programmer_CLI -c port=usb1 -w {elf} -v -g 0x08000000"


def confirm_dfu(args: argparse.Namespace) -> bool:
    """dfu の確認プロンプト。--yes なら聞かない。"""
    if args.yes:
        return True
    try:
        ans = args.prompt("ROM ブートローダ（USB DFU）へ移行します。ファームウェアは書き換え用の状態になり、"
                          "通常の動作は止まります。実行しますか？ [y/N] ")
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes")


def cmd_dfu(args: argparse.Namespace, link: Link, ci: ConnectInfo) -> int:
    # ENTER_BOOTLOADER は再送しない。応答が失われても移行済みのおそれがあるため、この時点から DISCONNECT は送らない。
    # 基板が拒否した（DeviceError）ときだけ、通常どおり DISCONNECT を送る
    args.no_disconnect = True
    try:
        link.enter_bootloader()
    except DeviceError as e:
        args.no_disconnect = False
        if e.err == DFU_ERR_ENABLED:
            raise CliError("励磁中（ENABLED）のため移行できません。DISABLE してから再実行してください")
        raise
    print("ENTER_BOOTLOADER を受け付けました。基板がリセットされ、COM ポートは消えます")
    try:
        link.close()   # 以後は COM ポートを使わない（検出中に再列挙されるため先に閉じる）
    except Exception:
        pass

    rc = EXIT_OK
    tools: dfu_mod.DfuTools = args.dfu_tools
    cli = tools.which()
    if cli is None:
        print(f"{dfu_mod.CLI_NAME} が PATH にないため、DFU デバイスの検出を省きます"
              "（デバイスマネージャーなどで 0483:DF11 を確認してください）")
    else:
        print(f"{dfu_mod.CLI_NAME} で DFU デバイス（0483:DF11）を最大 {tools.timeout_s:g} 秒探します ...")
        if tools.wait_for_dfu(cli):
            print("DFU デバイス（0483:DF11）を検出しました")
        else:
            print(f"{tools.timeout_s:g} 秒以内に DFU デバイスを検出できませんでした", file=sys.stderr)
            rc = EXIT_ERROR
    elf = args.elf or "<ELF>"
    print("書き込みの手順例（nemadbg は書き込まない）:")
    print("  " + DFU_WRITE_EXAMPLE.format(elf=elf))
    return rc


# ---- 引数 ----

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--port", help="COM ポート（省略時は VID 0483 / PID 5740 を自動検出）")
    common.add_argument("--elf", help="ELF ファイル")
    common.add_argument("--sim", action="store_true",
                        help="実機の代わりに擬似デバイス（シミュレータ）を使う（ポート検出をしない。状態は実行ごとに初期化）")
    common.add_argument("--timeout", type=int, help=f"応答待ち [ms]（既定 {int(DEFAULT_TIMEOUT_S * 1000)}）")

    ap = _Parser(prog="nemadbg", description="NEMA23 ステッピングモータドライバの USB CDC デバッグツール",
                 parents=[common])
    sub = ap.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def add(name: str, func: Callable[..., int], help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, parents=[common])
        sp.set_defaults(func=func)
        return sp

    add("info", cmd_info, "バージョンと build-id を表示")
    add("syms", cmd_syms, "名前表を列挙")
    add("get", cmd_get, "名前指定で変数を読む").add_argument("names", nargs="+", metavar="NAME")
    sp = add("read", cmd_read, "アドレス指定で読み出して hex ダンプ")
    sp.add_argument("addr", metavar="ADDR")
    sp.add_argument("size", metavar="SIZE")
    add("read-var", cmd_read_var, "ELF のシンボルを解決して読む（--elf 必須）").add_argument(
        "symbols", nargs="+", metavar="SYMBOL")
    sp = add("can-req", cmd_can_req, "CAN_REQUEST を実行")
    sp.add_argument("can_id", metavar="CAN_ID")
    sp.add_argument("data", nargs="?", metavar="DATA_HEX")
    sp = add("regs", cmd_regs, "CAN レジスタを読んで表示")
    sp.add_argument("--start", default="0x00")
    sp.add_argument("--len", default=None)
    sp = add("watch", cmd_watch, "名前指定で周期読み出し")
    sp.add_argument("names", nargs="+", metavar="NAME")
    sp.add_argument("--interval", type=float, default=1.0, help="周期 [s]")
    sp.add_argument("--count", type=int, default=None, help="回数（省略時は Ctrl-C まで）")
    sp = add("dfu", cmd_dfu, "ROM ブートローダ（USB DFU）へ移行（書き込みは行わない）")
    sp.add_argument("--yes", "-y", action="store_true", default=False, help="確認プロンプトを省略")
    sp = add("events", cmd_events, "イベントを表示し続ける")
    sp.add_argument("--duration", type=float, default=None, help="秒数（省略時は Ctrl-C まで）")
    return ap


def main(argv: Optional[List[str]] = None, link_factory: Optional[Callable[[Any], Link]] = None,
         prompt: Callable[[str], str] = input, dfu_tools: Optional[dfu_mod.DfuTools] = None) -> int:
    args = build_parser().parse_args(argv)
    args.port = getattr(args, "port", None)
    args.elf = getattr(args, "elf", None)
    args.timeout = getattr(args, "timeout", int(DEFAULT_TIMEOUT_S * 1000))
    args.yes = getattr(args, "yes", False)
    args.sim = getattr(args, "sim", False)
    args.prompt = prompt
    args.dfu_tools = dfu_tools or dfu_mod.DfuTools()
    args.no_disconnect = False   # dfu は移行後に COM ポートが消えるため DISCONNECT を送らない
    if args.command == "read-var" and not args.elf:
        print("エラー: read-var には --elf が必要です", file=sys.stderr)
        return EXIT_ERROR
    if args.timeout <= 0:
        print("エラー: --timeout は正の値にしてください", file=sys.stderr)
        return EXIT_ERROR

    link: Optional[Link] = None
    connected = False
    try:
        if args.command == "dfu" and not confirm_dfu(args):
            print("中止しました")
            return EXIT_ERROR
        link = (link_factory or default_link_factory)(args)
        ci = link.connect()
        connected = True
        return args.func(args, link, ci)
    except CliError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    except DeviceError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    except UnsafeRetryError as e:
        print(f"通信エラー（実行結果は不明）: {e}", file=sys.stderr)
        return EXIT_ERROR
    except LinkError as e:
        print(f"通信エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        return EXIT_OK
    finally:
        if link is not None:
            if connected and not args.no_disconnect:
                try:
                    link.disconnect()
                except (DeviceError, LinkError, KeyboardInterrupt):
                    pass
            try:
                link.close()
            except Exception:
                if not args.no_disconnect:
                    raise
