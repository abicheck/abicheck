# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A content hash of each exported function's code bytes.

The one producer of ``ElfSymbol.code_hash``. The fingerprint rename
detector (``diff_symbols_renames.py``) sees only snapshots, so the bytes are
hashed here, at dump time, while the binary is open.

Equal hashes mean byte-identical code. Unequal hashes do **not** prove the
code differs at the source level: a PC-relative call or data reference is
encoded as a displacement, so the same function moved within ``.text``
hashes differently. A consumer may treat equality as evidence and must
treat inequality as no evidence.

Each function is read on its own (one seek and one bounded read), so the
cost in memory is the largest function, never the whole code section.
"""

from __future__ import annotations

import hashlib
import logging
from typing import IO, Any

from elftools.common.exceptions import ELFError
from elftools.construct import ConstructError

log = logging.getLogger(__name__)

#: Below this many bytes a function is a stub or trampoline; identical bytes
#: say nothing about identity, and the rename matcher ignores such symbols.
MIN_HASHED_SIZE = 8

#: A single function larger than this is not hashed (a crafted ``st_size``
#: must not drive an unbounded read).
MAX_HASHED_SIZE = 16 * 1024 * 1024

#: 128-bit BLAKE2b: collision-safe for the few thousand symbols one library
#: pairs, at half the stored size of a SHA-256 hex digest.
_DIGEST_SIZE = 16

_EM_ARM = "EM_ARM"


def _load_segments(elf: Any) -> list[tuple[int, int, int]]:
    """``(p_vaddr, p_offset, p_filesz)`` of each ``PT_LOAD`` segment, or ``[]``."""
    try:
        return [
            (
                int(seg.header.p_vaddr),
                int(seg.header.p_offset),
                int(seg.header.p_filesz),
            )
            for seg in elf.iter_segments(type="PT_LOAD")
        ]
    except (IndexError, KeyError, ValueError, OSError, ELFError, ConstructError) as exc:
        log.debug("elf_code_hash: program headers unreadable: %s", exc)
        return []


def _containing(
    segments: list[tuple[int, int, int]], addr: int, size: int
) -> tuple[int, int, int] | None:
    """The one ``PT_LOAD`` file image that holds ``[addr, addr + size)``."""
    for vaddr, offset, filesz in segments:
        if vaddr <= addr and addr + size <= vaddr + filesz:
            return vaddr, offset, filesz
    return None


class CodeHasher:
    """Hashes function bytes out of one open ELF file.

    Section headers are looked up once per section index; function bytes
    are read straight from the file stream at the symbol's file offset.
    """

    def __init__(self, elf: Any, *, by_segment: bool = False) -> None:
        # ``None`` (a section carrying no ELF file, as a synthetic test
        # section does) yields a hasher that hashes nothing.
        self._elf = elf
        self._stream: IO[bytes] | None = getattr(elf, "stream", None)
        machine = getattr(getattr(elf, "header", None), "e_machine", None)
        # An ARM Thumb function's st_value has bit 0 set; its code starts one
        # byte lower.
        self._thumb = machine == _EM_ARM
        self._sections: dict[int, tuple[int, int, int] | None] = {}
        # ``by_segment``: bound each function by its ``PT_LOAD`` segment instead
        # of a section. The loader's view (``extract/elf_loader_view.py``) has
        # no section headers to name; the same bytes are mapped at the same
        # addresses, so the hash is the one the section path gives.
        self._segments: list[tuple[int, int, int]] | None = (
            _load_segments(elf) if by_segment else None
        )

    def _section(self, shndx: int) -> tuple[int, int, int] | None:
        if shndx not in self._sections:
            try:
                header = self._elf.get_section(shndx).header
            except (
                IndexError,
                KeyError,
                ValueError,
                OSError,
                ELFError,
                ConstructError,
            ) as exc:
                log.debug("elf_code_hash: section %s unreadable: %s", shndx, exc)
                header = None
            if header is None or header.sh_type == "SHT_NOBITS":
                self._sections[shndx] = None
            else:
                self._sections[shndx] = (
                    int(header.sh_addr),
                    int(header.sh_offset),
                    int(header.sh_size),
                )
        return self._sections[shndx]

    def hash(self, st_value: int, st_size: int, shndx: object) -> str:
        """Hex digest of the function's bytes, or ``""`` when unreadable."""
        if (
            not isinstance(shndx, int)
            or not MIN_HASHED_SIZE <= st_size <= MAX_HASHED_SIZE
        ):
            return ""
        if self._stream is None:
            return ""
        addr = st_value & ~1 if self._thumb else st_value
        if self._segments is not None:
            region = _containing(self._segments, addr, st_size)
        else:
            region = self._section(shndx)
        if region is None:
            return ""
        sec_addr, sec_offset, sec_size = region
        offset = addr - sec_addr
        if offset < 0 or offset + st_size > sec_size:
            return ""
        try:
            self._stream.seek(sec_offset + offset)
            data = self._stream.read(st_size)
        except OSError as exc:
            log.debug("elf_code_hash: read failed at %#x: %s", addr, exc)
            return ""
        if len(data) != st_size:
            return ""
        return hashlib.blake2b(data, digest_size=_DIGEST_SIZE).hexdigest()


__all__ = ["MAX_HASHED_SIZE", "MIN_HASHED_SIZE", "CodeHasher"]
