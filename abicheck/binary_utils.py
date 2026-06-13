# Copyright 2026 Nikolay Petrov
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

"""Shared binary format detection utilities.

Provides a single-file-open format detector for ELF, PE, and Mach-O
binaries, replacing duplicated detection logic in cli.py, appcompat.py,
and mcp_server.py.
"""
from __future__ import annotations

import re
from pathlib import Path

# Mach-O magic bytes — covers all variants:
# 32-bit BE/LE, 64-bit BE/LE, fat archive 32/64
_MACHO_MAGICS: frozenset[bytes] = frozenset({
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # 32-bit
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # 64-bit
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # fat archive 32
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",  # fat archive 64
})

# Unix `ar` archive magics — shared by static libraries (`.a`) and MSVC/COFF
# import/static libraries (`.lib`). `!<arch>\n` is the standard archive;
# `!<thin>\n` is a GNU thin archive (`ar rcT`, members referenced by path).
# Both are member archives, not the kind of single linkable image abicheck
# analyses (see detect_archive / G8). Both magics are 8 bytes.
_ARCHIVE_MAGICS: frozenset[bytes] = frozenset({b"!<arch>\n", b"!<thin>\n"})
_ARCHIVE_MAGIC_LEN: int = 8
_LD_SCRIPT_RE = re.compile(r"\b(?:INPUT|GROUP|OUTPUT_FORMAT)\s*\(")
_LD_KEYWORDS = frozenset({"AS_NEEDED", "INPUT", "GROUP", "OUTPUT_FORMAT"})


def classify_magic(magic: bytes) -> str | None:
    """Classify binary format from the first 4 (or more) magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or ``None``.
    """
    if len(magic) >= 4 and magic[:4] == b"\x7fELF":
        return "elf"
    if len(magic) >= 2 and magic[:2] == b"MZ":
        return "pe"
    if len(magic) >= 4 and magic[:4] in _MACHO_MAGICS:
        return "macho"
    return None


def detect_binary_format(path: str | Path) -> str | None:
    """Detect binary format from file magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or ``None`` for unknown/unreadable.
    Uses a single file open and reads only 4 bytes.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return None
    return classify_magic(magic)


def resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld INPUT()/GROUP() script to a referenced library.

    Returns ``(target, is_linker_script)``. ``target`` is ``None`` when the file
    is not a linker script, or when no referenced library can be found.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return None, False
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    if not _LD_SCRIPT_RE.search(text):
        return None, False
    for group in re.findall(r"(?:INPUT|GROUP)\s*\(([^)]*)\)", text):
        for tok in group.replace(",", " ").split():
            if tok in _LD_KEYWORDS or tok.startswith(("-l", "-L", "(")):
                continue
            if ".so" not in tok and not tok.endswith(".a"):
                continue
            candidate = Path(tok)
            for cand in (candidate, path.parent / tok, path.parent / candidate.name):
                if cand.is_file():
                    return cand, True
    return None, True


def normalize_binary_input(path: Path) -> tuple[Path, str | None]:
    """Detect binary format, following resolvable GNU ld linker scripts."""
    fmt = detect_binary_format(path)
    if fmt is not None:
        return path, fmt
    target, is_ld = resolve_linker_script(path)
    if is_ld and target is not None:
        return target, detect_binary_format(target)
    return path, fmt


def detect_archive(path: str | Path) -> bool:
    """Return True if ``path`` is a Unix `ar` archive (`.a` / `.lib`).

    Static libraries and COFF import libraries are member archives sharing the
    ``!<arch>\\n`` magic; GNU *thin* archives (``ar rcT``) use ``!<thin>\\n``.
    abicheck analyses single linkable images (shared libraries / objects), so
    the service layer uses this to fail deliberately with guidance rather than
    late with a misleading "unknown format" error.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(_ARCHIVE_MAGIC_LEN)
    except OSError:
        return False
    return magic in _ARCHIVE_MAGICS
