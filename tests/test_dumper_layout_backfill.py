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

    def test_no_dwarf_types_is_a_no_op(self) -> None:
        header = RecordType(name="Point", kind="struct")
        assert backfill_dwarf_layout([header], []) == [header]
