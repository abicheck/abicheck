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

"""The dump cache key sees the adjacent dSYM of a Mach-O library.

A header-free Mach-O dump reads DWARF from ``<lib>.dSYM`` beside the binary. The
binary's bytes do not change when that bundle appears or changes, so the key
must hash the bundle itself. Otherwise a dump made before a bundle existed is
served for a dump made after it (macOS CI showed exactly that: a header-free
snapshot with no DWARF from a binary that had a real dSYM beside it).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.workflows.dump.cache.api import _cache_key_for, _debug_sidecar
from tests._dump_cache_request import dump_request


def _bundle_path(lib: Path) -> Path:
    return (
        lib.parent / f"{lib.name}.dSYM" / "Contents" / "Resources" / "DWARF" / lib.name
    )


def _macho_request(lib: Path):
    return dump_request(path=lib, binary_fmt="macho")


def test_adding_a_dsym_beside_a_macho_changes_the_key(tmp_path: Path) -> None:
    lib = tmp_path / "libk.dylib"
    lib.write_bytes(b"\xcf\xfa\xed\xfe" + b"binary bytes")
    before = _cache_key_for(_macho_request(lib))
    assert before, "a plain Mach-O dump must be cacheable"
    bundle = _bundle_path(lib)
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"dwarf one")
    after = _cache_key_for(_macho_request(lib))
    assert after != before


def test_replacing_the_dsym_changes_the_key(tmp_path: Path) -> None:
    lib = tmp_path / "libk.dylib"
    lib.write_bytes(b"\xcf\xfa\xed\xfe" + b"binary bytes")
    bundle = _bundle_path(lib)
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"dwarf one")
    first = _cache_key_for(_macho_request(lib))
    bundle.write_bytes(b"dwarf two, rebuilt")
    assert _cache_key_for(_macho_request(lib)) != first


def test_the_dsym_is_the_macho_sidecar_only(tmp_path: Path) -> None:
    lib = tmp_path / "libk.dylib"
    lib.write_bytes(b"\xcf\xfa\xed\xfe" + b"binary bytes")
    assert _debug_sidecar(_macho_request(lib)) == []
    bundle = _bundle_path(lib)
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"dwarf one")
    assert _debug_sidecar(_macho_request(lib)) == [bundle]
    elf = tmp_path / "libk.so"
    elf.write_bytes(b"\x7fELF" + b"elf bytes")
    assert _debug_sidecar(dump_request(path=elf, binary_fmt="elf")) == []
