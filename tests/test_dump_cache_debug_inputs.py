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

"""Every debug input a dump reads must change the whole-snapshot cache key, or
the dump must bypass the cache.

A cache key covers the binary's bytes and its headers. A debug artifact beside
the binary changes the snapshot without changing those bytes, so it has to be a
key input (content-hashed) or the request has to be uncacheable. The table below
is the oracle: each row states whether a default dump reads that artifact. The
sidecar paths are written here by hand, not taken from the resolver, so the
resolver is what the table tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.workflows.dump.cache.api import _cache_key_for
from tests._dump_cache_request import dump_request

_MACHO_MAGIC = b"\xcf\xfa\xed\xfe"


def _macho_dsym(lib: Path) -> Path:
    return (
        lib.parent / f"{lib.name}.dSYM" / "Contents" / "Resources" / "DWARF" / lib.name
    )


def _pe_pdb(lib: Path) -> Path:
    return lib.with_suffix(".pdb")


def _elf_debuglink(lib: Path) -> Path:
    return lib.parent / f"{lib.name}.debug"


# (format, binary name and bytes, sidecar path, whether a default dump reads it)
INPUTS = [
    pytest.param(
        "macho",
        "libk.dylib",
        _MACHO_MAGIC,
        _macho_dsym,
        True,
        id="macho-adjacent-dsym",
    ),
    pytest.param(
        "pe",
        "libk.dll",
        b"MZ" + b"\0" * 62,
        _pe_pdb,
        True,
        id="pe-adjacent-pdb",
    ),
    pytest.param(
        "elf",
        "libk.so",
        b"\x7fELF" + b"\0" * 60,
        _elf_debuglink,
        False,
        id="elf-gnu-debuglink-not-read-by-default",
    ),
]


def _key(lib: Path, fmt: str) -> str:
    return _cache_key_for(dump_request(path=lib, binary_fmt=fmt))


@pytest.mark.parametrize(("fmt", "name", "magic", "sidecar_of", "reads"), INPUTS)
def test_adding_replacing_and_removing_a_debug_input_follows_the_key(
    tmp_path: Path, fmt: str, name: str, magic: bytes, sidecar_of, reads: bool
) -> None:
    lib = tmp_path / name
    lib.write_bytes(magic + b"binary bytes")
    sidecar = sidecar_of(lib)
    before = _key(lib, fmt)
    assert before, "a plain dump of this shape must be cacheable"

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(b"debug one")
    added = _key(lib, fmt)
    assert (added != before) == reads

    sidecar.write_bytes(b"debug two, rebuilt")
    replaced = _key(lib, fmt)
    assert (replaced != added) == reads

    sidecar.unlink()
    assert _key(lib, fmt) == before


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"pdb_path": Path("explicit.pdb")}, id="explicit-pdb"),
        pytest.param({"debug_roots": [Path("/dbg")]}, id="debug-roots"),
        pytest.param({"enable_debuginfod": True}, id="debuginfod"),
        pytest.param({"debug_format": "dwarf"}, id="forced-debug-format"),
        pytest.param({"debug_presence_only": True}, id="presence-only"),
        pytest.param({"dwarf_only": True}, id="dwarf-only"),
    ],
)
def test_an_explicit_debug_input_bypasses_the_cache(
    tmp_path: Path, overrides: dict
) -> None:
    lib = tmp_path / "libk.so"
    lib.write_bytes(b"\x7fELF" + b"binary bytes")
    assert _cache_key_for(dump_request(path=lib, binary_fmt="elf", **overrides)) == ""
