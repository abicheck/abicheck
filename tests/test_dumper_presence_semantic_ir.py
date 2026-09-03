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
"""``_dump_elf``'s presence-only-probe/``semantic_ir`` interaction, split
out of ``tests/test_dumper_coverage.py`` to keep that file at its
``architecture/debt.yaml`` no-growth baseline (Codex review, PR #1026,
fresh evidence).

``symbols_only``/``debug_presence_only`` resolve debug metadata through
``dwarf_presence.cheap_debug_presence_metadata`` -- which confirms only
that BTF/CTF debug info EXISTS (``has_dwarf=True``), never parses its
structs/enums at all. ``_dump_elf`` must not let ``_build_symbol_only_
snapshot``'s own BTF/CTF branch treat that presence-only signal as real
debug metadata to build a ``SemanticIR`` from -- doing so would stamp a
real (if empty) ``SemanticIR`` claiming this probe confirmed zero
occurrences, when it never looked at all (the same "not evaluated" vs.
"confirmed empty" distinction this codebase's ``Fact``/``FactStatus``
discipline exists to preserve elsewhere).
"""

from __future__ import annotations

import pytest

from abicheck.dumper import dump
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType


def _patch_elf(monkeypatch: pytest.MonkeyPatch) -> None:
    elf_meta = ElfMetadata(symbols=[ElfSymbol(name="foo", sym_type=SymbolType.FUNC)])
    monkeypatch.setattr(
        "abicheck.dumper._pyelftools_exported_symbols", lambda _p: ({"foo"}, {"foo"})
    )
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda _p: elf_meta)
    # Presence-only: has_dwarf=True (the BTF/CTF section is present), but no
    # structs/enums were ever parsed -- exactly what
    # dwarf_presence._section_presence_metadata(True) returns.
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_debug_presence_metadata",
        lambda *_a, **_kw: (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata()),
    )


def test_symbols_only_btf_presence_probe_never_populates_semantic_ir(
    tmp_path, monkeypatch
):
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    _patch_elf(monkeypatch)

    snap = dump(
        so_path=so_path,
        headers=[],
        version="1.0",
        symbols_only=True,
        debug_format="btf",
    )

    assert snap.semantic_ir is None


def test_debug_presence_only_ctf_probe_never_populates_semantic_ir(
    tmp_path, monkeypatch
):
    """Same gap, the other trigger named by the review: ``debug_presence_
    only`` (rather than ``symbols_only``) combined with a headerless dump
    also resolves through the cheap presence probe."""
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    _patch_elf(monkeypatch)

    snap = dump(
        so_path=so_path,
        headers=[],
        version="1.0",
        debug_presence_only=True,
        debug_format="ctf",
    )

    assert snap.semantic_ir is None
