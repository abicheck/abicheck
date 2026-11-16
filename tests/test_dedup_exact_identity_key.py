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

"""`finding_dedup._dedup_exact` keys on the canonical finding identity, for
every `ChangeKind` (bug class: a hand-picked dedup field tuple that drops an
identity-bearing fact and merges two distinct same-kind findings).

The old key was ``(kind, description, symbol, old_value, new_value)``, plus
``entity_id``/``disambiguator`` for a fixed set of kinds. Two findings of one
kind that differed only in ``qualified_name`` (``ns1::Foo`` vs ``ns2::Foo``)
therefore collapsed into one, and the second real break vanished. AGENTS.md
"Record before disposing" forbids that.

The oracle is the canonical identity owner
(``compare.finding_identity.resolve_change_identity``), not
``_dedup_exact`` or its helpers: a group of same-kind copies must keep exactly
one finding per distinct ``(primary_id, disambiguator)``, and identical copies
must always collapse to one.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.finding_dedup import _dedup_exact
from abicheck.compare.finding_identity import resolve_change_identity
from abicheck.model.change import Change

#: Fields the old key ignored (or ignored outside a fixed kind set) whose
#: difference the canonical identity can observe. Each pair is (a, b).
_VARIANTS: dict[str, tuple[str, str]] = {
    "qualified_name": ("ns1::Foo", "ns2::Foo"),
    "source_location": ("a.h:1", "b.h:9"),
    "disambiguator": ("tu-a", "tu-b"),
}


def _finding(kind: ChangeKind, **extra: object) -> Change:
    return Change(
        kind=kind,
        symbol="Foo",
        description=f"{kind.value} on Foo",
        old_value="old",
        new_value="new",
        **extra,  # type: ignore[arg-type]
    )


def _oracle_survivors(group: list[Change]) -> int:
    """Independent expectation: one survivor per distinct canonical identity
    plus occurrence discriminator."""
    return len(
        {(resolve_change_identity(c).primary_id, c.disambiguator) for c in group}
    )


@pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
def test_identical_copies_collapse_to_one(kind: ChangeKind) -> None:
    group = [_finding(kind), _finding(kind)]
    assert len(_dedup_exact(group)) == 1


@pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
@pytest.mark.parametrize("field", list(_VARIANTS))
def test_findings_differing_in_an_identity_fact_match_the_identity_oracle(
    kind: ChangeKind, field: str
) -> None:
    a, b = _VARIANTS[field]
    group = [_finding(kind, **{field: a}), _finding(kind, **{field: b})]
    assert len(_dedup_exact(group)) == _oracle_survivors(group)


def test_same_kind_findings_differing_only_in_qualified_name_both_survive() -> None:
    """Concrete instance of the reported bug: the old key collapsed these."""
    group = [
        _finding(ChangeKind.FIELD_RENAMED, qualified_name="ns1::Foo"),
        _finding(ChangeKind.FIELD_RENAMED, qualified_name="ns2::Foo"),
    ]
    assert len(_dedup_exact(group)) == 2


def test_a_real_duplicate_with_the_same_qualified_name_still_collapses() -> None:
    group = [
        _finding(ChangeKind.FIELD_RENAMED, qualified_name="ns1::Foo"),
        _finding(ChangeKind.FIELD_RENAMED, qualified_name="ns1::Foo"),
    ]
    assert len(_dedup_exact(group)) == 1
