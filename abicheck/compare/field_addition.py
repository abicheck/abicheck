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

"""Which ``ChangeKind`` an added record field gets.

An appended field is ``TYPE_FIELD_ADDED_COMPATIBLE`` only for a struct known
to own no vtable and no virtual base; anything polymorphic is
``TYPE_FIELD_ADDED`` (breaking). The compatible kind also lets
``diff_filtering`` drop the struct's accompanying size change, so claiming it
on evidence that could not see polymorphism turns a real break into a pass.

What counts as "could not see" is the producer's own statement:

* ``UNSUPPORTED`` — the producer cannot capture vtables at all (PDB layout).
* ``FAILED`` — extraction was attempted and failed.
* ``PARTIAL`` with no entries — known-incomplete DWARF coverage.

``NOT_COLLECTED`` is deliberately *not* in that set: it is also what a
typed-API ``RecordType`` that omits ``vtable=`` resolves to, and what the
flat-C debug-layout producer emits, both meaning "no virtuals"
(``compare/vtable_evidence.py`` records the reverted attempt to gate on it).
A confirmed ``is_standard_layout``/``is_trivially_copyable`` trait still
proves non-polymorphism on its own, as in ``diff_layout``.
"""

from __future__ import annotations

from ..model.availability import FactStatus
from ..model.change_catalog.kinds import ChangeKind
from ..model.entities import RecordType
from ..model.fact import Fact, fact_confirmed_true, resolved_fact_value

_UNSEEN = frozenset({FactStatus.UNSUPPORTED, FactStatus.FAILED})


def _could_not_see(fact: Fact[list[str]] | None) -> bool:
    if fact is None:
        return False
    if fact.status in _UNSEEN:
        return True
    return fact.status is FactStatus.PARTIAL and not fact.value


def new_field_change_kind(t_new: RecordType) -> ChangeKind:
    """``TYPE_FIELD_ADDED_COMPATIBLE`` only for a struct proven non-polymorphic."""
    if t_new.kind != "struct":
        return ChangeKind.TYPE_FIELD_ADDED
    if resolved_fact_value(t_new.vtable_fact, []) or resolved_fact_value(
        t_new.virtual_bases_fact, []
    ):
        return ChangeKind.TYPE_FIELD_ADDED
    proven_by_trait = fact_confirmed_true(
        t_new.is_standard_layout_fact
    ) or fact_confirmed_true(t_new.is_trivially_copyable_fact)
    if not proven_by_trait and (
        _could_not_see(t_new.vtable_fact) or _could_not_see(t_new.virtual_bases_fact)
    ):
        return ChangeKind.TYPE_FIELD_ADDED
    return ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
