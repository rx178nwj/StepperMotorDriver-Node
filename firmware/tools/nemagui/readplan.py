"""読み出し計画。項目の集合から、実際に発行する読み出し要求の列を作る純粋関数（I/O なし）。

- レジスタ: 6 B（READ_MAX）以内に収まるものを 1 回の CAN 読み出しにまとめる。マップ外は 0xFF が返るので隙間を跨いでよい。
- 名前表（atomic でない）: アドレス順に並べ、隙間が小さければ 1 回の SHORT_UPLOAD（最大 240 B）にまとめる。
- 名前表（atomic）: 1 項目 1 回の SYM_UPLOAD（8 B 以下は基板が割り込み禁止でコピーする）。
- 名前表に無い名前の項目は読まず、kind="missing" の ReadOp として残す（UI が「—」を出せるように）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from nemadbg import protocol as p
from nemadbg import registers as regs
from nemadbg.link import SymInfo

from .items import Item

DEFAULT_GAP = 32       # 名前表の読み出しをまとめる隙間の上限 [B]

KIND_REG = "reg"
KIND_SHORT = "short"
KIND_SYM = "sym"
KIND_MISSING = "missing"

Slice = Tuple[str, int, int]   # (項目 key, 読み出したデータ内のオフセット, バイト数)


@dataclass(frozen=True)
class ReadOp:
    """1 回の読み出し要求。

    kind  : "reg"（CAN 読み出し。addr はレジスタ番号、size は 1〜6）/ "short"（SHORT_UPLOAD。addr はメモリアドレス）/
            "sym"（SYM_UPLOAD。sym に名前表の情報）/ "missing"（未登録。読まない）
    slices: この要求で得たバイト列から各項目を切り出す位置
    """

    kind: str
    addr: int
    size: int
    slices: Tuple[Slice, ...]
    sym: Optional[SymInfo] = None

    @property
    def keys(self) -> List[str]:
        return [s[0] for s in self.slices]


def _plan_regs(items: List[Item]) -> List[ReadOp]:
    items = sorted(items, key=lambda i: i.reg.addr)    # type: ignore[union-attr]
    ops: List[ReadOp] = []
    start = 0
    end = 0
    cur: List[Item] = []

    def flush() -> None:
        if cur:
            ops.append(ReadOp(KIND_REG, start, end - start,
                              tuple((i.key, i.reg.addr - start, i.reg.size) for i in cur)))   # type: ignore[union-attr]

    for it in items:
        r = it.reg
        assert r is not None
        if cur and r.addr + r.size - start <= regs.READ_MAX:
            cur.append(it)
            end = max(end, r.addr + r.size)
            continue
        flush()
        cur = [it]
        start, end = r.addr, r.addr + r.size
    flush()
    return ops


def _plan_short(pairs: List[Tuple[Item, SymInfo]], gap: int) -> List[ReadOp]:
    pairs = sorted(pairs, key=lambda x: x[1].addr)
    ops: List[ReadOp] = []
    cur: List[Tuple[Item, SymInfo]] = []
    start = end = 0

    def flush() -> None:
        if cur:
            ops.append(ReadOp(KIND_SHORT, start, end - start,
                              tuple((it.key, inf.addr - start, inf.size) for it, inf in cur)))

    for it, inf in pairs:
        a, e = inf.addr, inf.addr + inf.size
        if cur and a - end <= gap and max(end, e) - start <= p.UPLOAD_MAX:
            cur.append((it, inf))
            end = max(end, e)
            continue
        flush()
        cur = [(it, inf)]
        start, end = a, e
    flush()
    return ops


def plan_reads(items: Iterable[Item], symtab: Mapping[str, SymInfo], gap: int = DEFAULT_GAP) -> List[ReadOp]:
    """項目から読み出し計画を作る。同じ key の重複は 1 つにまとめる。

    返す順序: レジスタ、SHORT_UPLOAD、SYM_UPLOAD（atomic）、未登録。
    """
    seen: Dict[str, Item] = {}
    for it in items:
        seen.setdefault(it.key, it)
    reg_items: List[Item] = []
    short: List[Tuple[Item, SymInfo]] = []
    atomic: List[ReadOp] = []
    missing: List[Item] = []
    for it in seen.values():
        if it.source == "reg":
            reg_items.append(it)
            continue
        info = symtab.get(it.sym or "")
        if info is None:
            missing.append(it)
        elif it.atomic:
            atomic.append(ReadOp(KIND_SYM, info.addr, info.size, ((it.key, 0, info.size),), sym=info))
        else:
            short.append((it, info))
    ops = _plan_regs(reg_items) + _plan_short(short, gap) + atomic
    if missing:
        ops.append(ReadOp(KIND_MISSING, 0, 0, tuple((i.key, 0, 0) for i in missing)))
    return ops


def extract(op: ReadOp, data: bytes) -> Dict[str, bytes]:
    """1 つの読み出し結果から、項目ごとの生バイト列を切り出す。足りない項目は含めない。"""
    out: Dict[str, bytes] = {}
    for key, off, size in op.slices:
        chunk = bytes(data[off : off + size])
        if size and len(chunk) == size:
            out[key] = chunk
    return out


def apply(results: Iterable[Tuple[ReadOp, Optional[bytes]]]) -> Dict[str, bytes]:
    """(ReadOp, 読み出したバイト列) の列から {key: 生バイト列} を作る。データが None（失敗）の要求は飛ばす。"""
    out: Dict[str, bytes] = {}
    for op, data in results:
        if data is not None and op.kind != KIND_MISSING:
            out.update(extract(op, data))
    return out


def missing_keys(ops: Iterable[ReadOp]) -> List[str]:
    """計画のうち名前表に無くて読めない項目の key。"""
    return [k for op in ops if op.kind == KIND_MISSING for k in op.keys]
