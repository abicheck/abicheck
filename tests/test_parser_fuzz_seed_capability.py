"""The ELF/DWARF parser-fuzz seeds are built only by a gcc that emits ELF.

``gcc`` on PATH is not the capability: on macOS it is clang emitting Mach-O,
on Windows MinGW emitting PE. The fuzz oracles then fed a non-ELF seed to the
ELF and DWARF readers and failed ("elf seed holds no facts", "ELFError: Magic
number does not match") on main's macOS and Windows legs. A host whose gcc
emits another format skips with that format named (a capability absent, the
three-state rule in tests/CLAUDE.md); a gcc that emits ELF builds the seed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parser_fuzz_oracle as fuzz  # noqa: E402
from _parser_fuzz_oracle import object_format  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake gcc is a POSIX shell script"
)

# The leading bytes a non-ELF gcc writes, by the format it targets.
_FOREIGN = {
    "Mach-O": b"\xcf\xfa\xed\xfe" + b"\0" * 60,
    "PE/COFF": b"MZ" + b"\0" * 62,
}


def _fake_gcc(bindir: Path, payload: bytes) -> None:
    """A ``gcc`` that writes *payload* to its ``-o`` target, as a non-ELF
    toolchain would write its own object format."""
    bindir.mkdir(parents=True, exist_ok=True)
    data = bindir / "payload.bin"
    data.write_bytes(payload)
    script = bindir / "gcc"
    script.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = -o ]; then shift; cp "' + str(data) + '" "$1"; fi\n'
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


@pytest.fixture
def fresh_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fuzz, "_SEEDS", {})
    monkeypatch.setattr(fuzz, "_NO_ELF_SEED", [])


@pytest.mark.parametrize("fmt", sorted(_FOREIGN))
@pytest.mark.parametrize("debug", [False, True])
def test_a_gcc_emitting_another_format_skips_naming_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_seeds: None,
    fmt: str,
    debug: bool,
) -> None:
    _fake_gcc(tmp_path / "bin", _FOREIGN[fmt])
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    with pytest.raises(pytest.skip.Exception, match=f"emits {fmt}, not ELF"):
        fuzz._so_seed(debug=debug)
    # Once known, every later seed request skips the same way without rebuilding.
    with pytest.raises(pytest.skip.Exception, match=f"emits {fmt}, not ELF"):
        fuzz._so_seed(debug=not debug)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="needs an ELF-targeting gcc"
)
def test_an_elf_gcc_builds_the_seed(fresh_seeds: None) -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc is required to build the ELF/DWARF parser seeds")
    seed = fuzz._so_seed(debug=True)
    assert seed[:4] == b"\x7fELF"
    assert fuzz._NO_ELF_SEED == []


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (b"\x7fELF\x02\x01", "ELF"),
        (b"MZ\x90\x00", "PE/COFF"),
        (b"\xcf\xfa\xed\xfe", "Mach-O"),
        (b"\xce\xfa\xed\xfe", "Mach-O"),
        (b"\xca\xfe\xba\xbe", "Mach-O"),
        (b"#!/bin/sh", "unknown"),
        (b"", "unknown"),
    ],
)
def test_object_format_reads_the_magic(data: bytes, fmt: str) -> None:
    assert object_format(data) == fmt
