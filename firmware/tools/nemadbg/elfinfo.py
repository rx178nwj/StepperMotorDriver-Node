"""ELF から build-id とシンボル表（名前→アドレス・サイズ）を取り出す。pyelftools は遅延 import。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


class ElfError(Exception):
    pass


@dataclass
class ElfInfo:
    build_id: bytes
    # 名前 -> [(アドレス, サイズ)]。同名（static 変数など）が複数あれば複数入る
    symbols: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)

    def resolve(self, name: str) -> Tuple[int, int]:
        entries = sorted(set(self.symbols.get(name, [])))
        if not entries:
            raise ElfError(f"ELF にシンボル '{name}' がありません")
        if len(entries) > 1:
            raise ElfError(f"シンボル '{name}' が複数あります: " + ", ".join(f"0x{a:08X}" for a, _ in entries))
        return entries[0]


def load(path: str) -> ElfInfo:
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    try:
        f = open(path, "rb")
    except OSError as e:
        raise ElfError(f"ELF を開けません: {e}") from e
    with f:
        elf = ELFFile(f)
        build_id = b""
        sec = elf.get_section_by_name(".note.gnu.build-id")
        if sec is not None:
            for note in sec.iter_notes():
                if note["n_type"] == "NT_GNU_BUILD_ID":
                    build_id = bytes.fromhex(note["n_desc"])
                    break
        if not build_id:
            raise ElfError("ELF に GNU build-id がありません")

        symbols: Dict[str, List[Tuple[int, int]]] = {}
        for s in elf.iter_sections():
            if not isinstance(s, SymbolTableSection):
                continue
            for sym in s.iter_symbols():
                if not sym.name or sym["st_shndx"] == "SHN_UNDEF":
                    continue
                if sym["st_info"]["type"] not in ("STT_OBJECT", "STT_FUNC", "STT_NOTYPE", "STT_COMMON", "STT_TLS"):
                    continue
                addr = sym["st_value"]
                if sym["st_info"]["type"] == "STT_FUNC":
                    addr &= ~1   # Thumb ビットを除く
                symbols.setdefault(sym.name, []).append((addr, sym["st_size"]))
        return ElfInfo(build_id, symbols)
