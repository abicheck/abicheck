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

"""Unit tests for abicheck.dumper_layout_backfill."""
from __future__ import annotations

import pytest

from abicheck.dumper_layout_backfill import (
    backfill_dwarf_layout,
    dwarf_layout_types_or_empty,
)
from abicheck.model import RecordType, TypeField


def _dwarf_meta(has_dwarf: bool):
    class _M:
        pass
    m = _M()
    m.has_dwarf = has_dwarf
    return m


class TestDwarfLayoutTypesOrEmpty:
    """dwarf_layout_types_or_empty must gate on the *actual* parser backend,
    not a static --ast-frontend guess (Codex/CodeRabbit review: the "auto"
    frontend can fall back from castxml to clang internally)."""

    def test_empty_when_not_clang_backend(self) -> None:
        result = dwarf_layout_types_or_empty(
            None, None, _dwarf_meta(True), None, False,
            symbols_only=False, debug_presence_only=False,
            version="1.0", language_profile=None, session=None,
        )
        assert result == []

    def test_empty_when_no_dwarf(self) -> None:
        result = dwarf_layout_types_or_empty(
            None, None, _dwarf_meta(False), None, True,
            symbols_only=False, debug_presence_only=False,
            version="1.0", language_profile=None, session=None,
        )
        assert result == []

    def test_empty_when_symbols_only(self) -> None:
        result = dwarf_layout_types_or_empty(
            None, None, _dwarf_meta(True), None, True,
            symbols_only=True, debug_presence_only=False,
            version="1.0", language_profile=None, session=None,
        )
        assert result == []

    def test_extracts_dwarf_types_when_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The one branch that actually calls build_snapshot_from_dwarf."""
        import abicheck.dwarf_snapshot as dwarf_snapshot

        expected = [RecordType(name="Point", kind="struct", size_bits=64)]

        class _FakeSnap:
            types = expected

        calls = []

        def _fake_build(so_path, elf_meta, dwarf_meta, dwarf_adv, *, version, language_profile, session):
            calls.append((so_path, version, language_profile))
            return _FakeSnap()

        monkeypatch.setattr(dwarf_snapshot, "build_snapshot_from_dwarf", _fake_build)

        result = dwarf_layout_types_or_empty(
            "libfoo.so", None, _dwarf_meta(True), None, True,
            symbols_only=False, debug_presence_only=False,
            version="1.0", language_profile="c++", session=None,
        )
        assert result == expected
        assert calls == [("libfoo.so", "1.0", "c++")]


class TestBackfillDwarfLayout:
    def test_backfills_size_and_field_offsets(self) -> None:
        header = RecordType(
            name="Point", kind="struct",
            fields=[TypeField(name="x", type="int"), TypeField(name="y", type="int")],
        )
        dwarf = RecordType(
            name="Point", kind="struct", size_bits=64, alignment_bits=32,
            fields=[
                TypeField(name="x", type="int", offset_bits=0),
                TypeField(name="y", type="int", offset_bits=32),
            ],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert len(out) == 1
        assert out[0].size_bits == 64
        assert out[0].alignment_bits == 32
        assert [f.offset_bits for f in out[0].fields] == [0, 32]

    def test_leaves_field_untouched_when_already_offset_or_unmatched(self) -> None:
        """A field that already has an offset (e.g. from a prior backfill), or
        one with no same-named DWARF counterpart, is left as-is rather than
        overwritten or dropped."""
        header = RecordType(
            name="Point", kind="struct",
            fields=[
                TypeField(name="x", type="int", offset_bits=0),  # already known
                TypeField(name="ghost", type="int"),  # no DWARF counterpart
            ],
        )
        dwarf = RecordType(
            name="Point", kind="struct", size_bits=64,
            fields=[TypeField(name="x", type="int", offset_bits=999)],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].fields[0].offset_bits == 0  # untouched, not overwritten with 999
        assert out[0].fields[1].offset_bits is None  # left alone, not dropped

    def test_leaves_castxml_derived_type_untouched(self) -> None:
        """A type that already has size_bits (castxml) must not be touched,
        even if a same-named DWARF type carries different layout."""
        header = RecordType(name="Point", kind="struct", size_bits=64)
        dwarf = RecordType(name="Point", kind="struct", size_bits=128)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 64

    def test_leaves_opaque_type_untouched(self) -> None:
        header = RecordType(name="Handle", kind="struct", is_opaque=True)
        dwarf = RecordType(name="Handle", kind="struct", size_bits=64)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits is None
        assert out[0].is_opaque

    def test_leaves_template_pattern_untouched(self) -> None:
        """A class template's pattern body (e.g. clang's CXXRecordDecl inside a
        ClassTemplateDecl) shares the bare name "Foo" with any unrelated real
        type or instantiation named "Foo" in DWARF — matching it by name would
        silently attach the wrong layout, since the pattern itself has no
        fixed layout for any one instantiation (Codex review)."""
        header = RecordType(
            name="Buffer", kind="class", is_template_pattern=True,
            fields=[TypeField(name="data_", type="T *")],
        )
        dwarf = RecordType(name="Buffer", kind="class", size_bits=128)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits is None
        assert out[0].is_template_pattern
        assert [f.name for f in out[0].fields] == ["data_"]

    def test_matches_namespaced_dwarf_name_by_unambiguous_suffix(self) -> None:
        """The clang header backend emits a bare name ("Foo") while DWARF
        qualifies it ("api::Foo"); an unambiguous suffix match must still
        recover the layout (Codex review)."""
        header = RecordType(name="Foo", kind="struct", fields=[TypeField(name="v", type="int")])
        dwarf = RecordType(
            name="api::Foo", kind="struct", size_bits=32,
            fields=[TypeField(name="v", type="int", offset_bits=0)],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 32
        assert out[0].fields[0].offset_bits == 0

    def test_ambiguous_suffix_is_never_guessed(self) -> None:
        """Two different DWARF types sharing a bare suffix (different
        namespaces) must never be silently attached to the wrong header
        type — this is the dangerous half of the namespace-matching gap
        (Codex review): a wrong match would be silent data corruption, not
        just a missed backfill."""
        header = RecordType(name="Foo", kind="struct")
        dwarf_a = RecordType(name="ns_a::Foo", kind="struct", size_bits=32)
        dwarf_b = RecordType(name="ns_b::Foo", kind="struct", size_bits=999)
        out = backfill_dwarf_layout([header], [dwarf_a, dwarf_b])
        assert out[0].size_bits is None

    def test_global_and_namespaced_same_bare_name_is_never_guessed(self) -> None:
        """A global ``Foo`` matches the header's bare "Foo" by exact name just
        as validly as a namespaced ``api::Foo`` matches it by suffix — an
        exact-match-first lookup would silently pick the global one and never
        even reach the ambiguity check (Codex review: the dangerous mixed
        global/namespaced case). Both must be left unmatched."""
        header = RecordType(name="Foo", kind="struct")
        dwarf_global = RecordType(name="Foo", kind="struct", size_bits=64)
        dwarf_namespaced = RecordType(name="api::Foo", kind="struct", size_bits=999)
        out = backfill_dwarf_layout([header], [dwarf_global, dwarf_namespaced])
        assert out[0].size_bits is None

    def test_no_dwarf_types_is_a_no_op(self) -> None:
        header = RecordType(name="Point", kind="struct")
        assert backfill_dwarf_layout([header], []) == [header]
