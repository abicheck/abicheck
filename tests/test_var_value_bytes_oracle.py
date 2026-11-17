"""Independent oracle for an exported const object's initializer bytes (G7).

Everything here goes through pyelftools and the compiler, never through the
abicheck detector or its helpers, so a detector test can be checked against
bytes the test read itself.

The oracle answers one question: for a built shared object, what are the
bytes at the exported symbol's address, read from the file's own section
contents.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

pytestmark = pytest.mark.integration


def _need_cc() -> str:
    for cc in ("gcc", "cc"):
        if shutil.which(cc):
            return cc
    pytest.skip("no C compiler on PATH")
    raise AssertionError("unreachable")


def build_shared(
    src: str, out: Path, *, opt: str = "-O2", extra: tuple[str, ...] = ()
) -> Path:
    """Compile *src* into a shared object at *out* and return it.

    *extra* are further driver or linker flags (e.g. a version script).
    """
    cc = _need_cc()
    c_file = out.with_suffix(".c")
    c_file.write_text(src, encoding="utf-8")
    subprocess.run(
        [cc, opt, "-shared", "-fPIC", *extra, "-o", str(out), str(c_file)],
        check=True,
        capture_output=True,
    )
    return out


def exported_object_bytes(path: Path, name: str) -> bytes | None:
    """The file bytes of exported object *name*, or None when it is not a
    defined dynamic object.

    Reads ``.dynsym`` for the symbol, maps its address to the section that
    contains it, and slices the section's file contents. A symbol with no
    file-backed section (``.bss``) has no initializer bytes to read.
    """
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        dynsym = elf.get_section_by_name(".dynsym")
        if dynsym is None:
            return None
        sym = None
        for s in dynsym.iter_symbols():
            if s.name == name and s["st_shndx"] != "SHN_UNDEF":
                sym = s
                break
        if sym is None or sym["st_info"]["type"] != "STT_OBJECT":
            return None
        addr = sym["st_value"]
        size = sym["st_size"]
        for sec in elf.iter_sections():
            if sec["sh_addr"] == 0 or sec["sh_type"] == "SHT_NOBITS":
                continue
            start, end = sec["sh_addr"], sec["sh_addr"] + sec["sh_size"]
            if start <= addr and addr + size <= end:
                offset = sec["sh_offset"] + (addr - start)
                fh.seek(offset)
                return fh.read(size)
    return None
