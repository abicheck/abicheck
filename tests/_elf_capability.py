"""Which host compilers can write ELF, the format the DWARF oracles read.

readelf, llvm-dwarfdump and pyelftools read ELF. On a host whose compiler writes
PE/COFF or Mach-O, an oracle that builds with that compiler reads the wrong
format, so the ELF-DWARF oracles build for an explicit ELF target instead:
``clang --target=x86_64-linux-gnu`` on any host, or a native ELF toolchain.

``elf_capability`` probes what the host can actually emit. A test asks for an
ELF producer and skips with the missing capability named when there is none;
a compiler that exists but cannot write ELF is not a failure of the code under
test.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

_ELF_MAGIC = b"\x7fELF"
_CLANG_ELF_TARGET = ["--target=x86_64-linux-gnu", "-fuse-ld=lld"]

#: What each kind of ELF artifact the oracles need, as compiler flags.
_KIND_FLAGS: dict[str, list[str]] = {
    "object": ["-c"],
    "shared": ["-shared", "-fPIC"],
    "executable": [],
}


def _prefix(family: str) -> list[str] | None:
    """The compiler command (with its ELF target) for *family*, or None if absent."""
    compiler = {"gcc": "g++", "clang": "clang++"}[family]
    path = shutil.which(compiler)
    if path is None:
        return None
    return [path, *_CLANG_ELF_TARGET] if family == "clang" else [path]


#: The standard headers an oracle compiles against. Opt-in (``std_headers=True``):
#: a probe for a source with no headers must not fail on a target that lacks a
#: C++ standard library, and an oracle that uses the headers must check for them.
_PROBE_HEADERS = "#include <cstdio>\n#include <type_traits>\n"


def _writes_elf(prefix: list[str], kind: str, *, std_headers: bool = False) -> bool:
    """Whether *prefix* compiles a C++ probe to ELF *kind*, optionally with the std headers."""
    suffix = {"object": ".o", "shared": ".so", "executable": ""}[kind]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.cpp"
        out = Path(tmp) / f"probe{suffix}"
        body = (
            "int main() { return 0; }\n"
            if kind == "executable"
            else "int probe() { return 0; }\n"
        )
        header = _PROBE_HEADERS if std_headers else ""
        src.write_text(header + body, encoding="utf-8")
        cmd = [*prefix, *_KIND_FLAGS[kind], str(src), "-o", str(out)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0 or not out.exists():
            return False
        return out.read_bytes()[:4] == _ELF_MAGIC


def elf_capability(
    family: str, kind: str, *, std_headers: bool = False
) -> list[str] | None:
    """The compiler prefix that writes an ELF *kind* for *family*, or None.

    A None answer means the host has no ELF-capable producer for this family;
    the caller skips with that capability named in the reason.
    """
    prefix = _prefix(family)
    if prefix is None:
        return None
    return prefix if _writes_elf(prefix, kind, std_headers=std_headers) else None


def skip_reason(family: str, kind: str, *, std_headers: bool = False) -> str:
    """The skip message for a missing ELF capability, naming it (not the OS)."""
    what = " with the C++ standard headers" if std_headers else ""
    return (
        f"no {family} on this host can write an ELF {kind}{what} "
        "(the ELF-DWARF capability)"
    )


def subprogram_conventions(obj: Path) -> list[tuple[str | None, int | None]]:
    """(name, DW_AT_calling_convention code or None) of each subprogram in *obj*.

    Read with pyelftools, so the same oracle runs on every host (no readelf).
    """
    from elftools.elf.elffile import ELFFile

    found: list[tuple[str | None, int | None]] = []
    with obj.open("rb") as fh:
        dwarf = ELFFile(fh).get_dwarf_info()
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram":
                    continue
                name = die.attributes.get("DW_AT_name")
                cc = die.attributes.get("DW_AT_calling_convention")
                found.append(
                    (
                        name.value.decode() if name is not None else None,
                        int(cc.value) if cc is not None else None,
                    )
                )
    return found
