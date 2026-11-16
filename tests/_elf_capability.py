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
_CLANG_ELF_TARGET = "--target=x86_64-linux-gnu"

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
    return [path, _CLANG_ELF_TARGET] if family == "clang" else [path]


def _writes_elf(prefix: list[str], kind: str, lang: str) -> bool:
    """Whether *prefix* compiles a trivial *lang* source to an ELF *kind* artifact."""
    suffix = {"object": ".o", "shared": ".so", "executable": ""}[kind]
    source = "c" if lang == "c" else "cpp"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"probe.{source}"
        out = Path(tmp) / f"probe{suffix}"
        src.write_text(
            "int probe() { return 0; }\n"
            if kind != "executable"
            else "int main() { return 0; }\n",
            encoding="utf-8",
        )
        cmd = [*prefix, *_KIND_FLAGS[kind], str(src), "-o", str(out)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0 or not out.exists():
            return False
        return out.read_bytes()[:4] == _ELF_MAGIC


def elf_capability(family: str, kind: str) -> list[str] | None:
    """The compiler prefix that writes an ELF *kind* for *family*, or None.

    A None answer means the host has no ELF-capable producer for this family;
    the caller skips with that capability named in the reason.
    """
    prefix = _prefix(family)
    if prefix is None:
        return None
    return (
        prefix if _writes_elf(prefix, kind, "cpp" if family == "gcc" else "c") else None
    )


def skip_reason(family: str, kind: str) -> str:
    """The skip message for a missing ELF capability, naming it (not the OS)."""
    return (
        f"no {family} on this host can write an ELF {kind} (the ELF-DWARF capability)"
    )
