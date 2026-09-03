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

"""ADR-063 Phase 5B: ``PARTIAL`` ``bases_fact`` evidence must stay usable at
``diff_cxx_rules._owner_descends_from``/``vtable_slot_is_override_reuse``.

Split out of ``test_vtable_severity.py`` (that file sits at its own frozen
architecture-debt line-count baseline; a `debt-no-growth` check forbids
adding to it -- move responsibility to a sibling module instead, per this
repo's own AGENTS.md "Files that are large" guidance).

Codex review on the PR that introduced ``_transitive_bases``'s
completeness-tracking return value (ADR-063 Phase 5B): the helper's first
draft returned an empty list for any non-``PRESENT`` ``bases_fact``,
including ``PARTIAL`` -- collapsing "confirmed complete" and "known but
possibly incomplete" onto the same empty value. ``_owner_descends_from``
(feeding ``vtable_slot_is_override_reuse``, part of the separate
vtable/vptr_offset_bits evidence-gap cluster) reads only the walk's *set* of
names and discards its completeness flag entirely, so that collapse
silently dropped a real, known ``Derived -> Base`` relationship a
``PARTIAL`` fact still carries -- which could make
``vtable_slot_is_override_reuse`` return ``False`` for a genuine override
and fabricate a ``TYPE_VTABLE_CHANGED``. Fixed by having the underlying
helper preserve the value for both ``PRESENT`` and ``PARTIAL`` (matching
the pre-existing ``_fact_str_list``'s own read) and only gating the
*completeness* flag on ``PRESENT``.
"""

from __future__ import annotations

from abicheck.diff_cxx_rules import _owner_descends_from, vtable_slot_is_override_reuse
from abicheck.model import Fact, Function, Param, RecordType


class TestOwnerDescendsFromPartialEvidence:
    def test_partial_bases_evidence_is_still_trusted_for_a_known_entry(self) -> None:
        """`_owner_descends_from` reads `_transitive_bases`'s *set* of names
        only and discards its completeness flag entirely -- its own
        evidence-gap handling is scoped to the separate vtable/
        vptr_offset_bits slice. A `PARTIAL` `bases_fact` still carries real,
        known entries (the uncovered part of the scope is merely *unknown*,
        not the covered part being wrong), so a `PARTIAL`-evidenced
        `Derived -> Base` relationship must resolve here exactly as a
        `PRESENT` one would -- losing it would make
        `vtable_slot_is_override_reuse` (this function's own caller) return
        `False` for a real override and fabricate a `TYPE_VTABLE_CHANGED`.
        """
        types = {
            "ns::Derived": RecordType(
                name="ns::Derived",
                kind="class",
                bases_fact=Fact.partial(["ns::Base"]),
            ),
        }
        assert _owner_descends_from("ns::Derived", "ns::Base", types)


class TestVtableOverrideSlotReusePartialEvidence:
    def test_partial_bases_evidence_still_recognises_override_reuse(self) -> None:
        """The exact scenario the review flagged -- a `PARTIAL` (not
        `NOT_COLLECTED`) `bases_fact` that still carries its one known
        entry (`["Base"]`) must resolve through `vtable_slot_is_override_
        reuse` exactly as a `PRESENT` one would, at the real
        `vtable_slot_is_override_reuse` call site rather than only at
        `_owner_descends_from` directly.
        """
        old_funcs = {
            "_ZN4Base5paintEi": Function(
                name="Base::paint",
                mangled="_ZN4Base5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_funcs = {
            "_ZN7Derived5paintEi": Function(
                name="Derived::paint",
                mangled="_ZN7Derived5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_types = {
            "Derived": RecordType(
                name="Derived",
                kind="class",
                bases_fact=Fact.partial(["Base"]),
            )
        }
        assert vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi",
            "_ZN7Derived5paintEi",
            old_funcs,
            new_funcs,
            {},
            new_types,
        )
