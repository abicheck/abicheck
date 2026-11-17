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

"""One dedup run computes each change's canonical identity at most once.

`_dedup_exact` (inside `deduplicate_ast_dwarf`) and the cross-detector pass
both key on `resolve_change_identity(c).primary_id` for the same change. The
per-run `IdentityMemo` shares that result between them. These tests count the
real `resolve_change_identity` calls, and check that the memoized result is
exactly the unmemoized one.
"""

from __future__ import annotations

from collections import Counter

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare import identity_memo
from abicheck.compare.finding_dedup import (
    IdentityMemo,
    deduplicate_ast_dwarf,
    deduplicate_cross_detector,
)
from abicheck.model.change import Change
from abicheck.model.identity import Namespace, entity_id_for_typedef
from abicheck.policy.finding_disposition.run_state import PipelineContext


def _changes() -> list[Change]:
    """Findings of kinds in both passes (cross-detector kinds), plus a
    duplicate, a qualified-name variant, and an AST/DWARF twin, so the memo is
    exercised on the paths that matter."""
    eid = entity_id_for_typedef((Namespace("a"),), "Foo")
    return [
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="gone",
            old_value="x",
        ),
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="gone",
            old_value="x",
        ),
        Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol="foo",
            description="gone",
            old_value="x",
        ),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="new"),
        Change(kind=ChangeKind.VAR_REMOVED, symbol="v", description="gone"),
        Change(
            kind=ChangeKind.FIELD_RENAMED,
            symbol="Foo",
            description="r",
            old_value="a",
            new_value="b",
            qualified_name="ns1::Foo",
        ),
        Change(
            kind=ChangeKind.FIELD_RENAMED,
            symbol="Foo",
            description="r",
            old_value="a",
            new_value="b",
            qualified_name="ns2::Foo",
        ),
        Change(
            kind=ChangeKind.FIELD_RENAMED,
            symbol="Foo",
            description="r",
            old_value="a",
            new_value="b",
            entity_id=eid,
        ),
        Change(
            kind=ChangeKind.FIELD_RENAMED,
            symbol="Foo",
            description="r",
            old_value="a",
            new_value="b",
        ),
    ]


def _run(changes: list[Change], memo: IdentityMemo | None) -> list[Change]:
    stage = deduplicate_ast_dwarf(changes, identity_memo=memo)
    return deduplicate_cross_detector(stage, identity_memo=memo)


def test_each_change_resolves_its_identity_at_most_once_per_run(monkeypatch) -> None:
    calls: Counter[int] = Counter()
    real = identity_memo.resolve_change_identity

    def counting(change: Change, **kwargs):
        calls[id(change)] += 1
        return real(change, **kwargs)

    monkeypatch.setattr(identity_memo, "resolve_change_identity", counting)
    changes = _changes()
    _run(changes, IdentityMemo())
    assert calls, "the dedup run never asked for an identity"
    assert max(calls.values()) == 1


def test_memoized_run_matches_unmemoized_run_exactly() -> None:
    changes = _changes()
    memoized = _run(changes, IdentityMemo())
    unmemoized = _run(changes, None)
    assert [id(c) for c in memoized] == [id(c) for c in unmemoized]


def test_each_pipeline_run_gets_its_own_memo() -> None:
    """A memo never outlives its run: two contexts share no entries."""
    first = PipelineContext(old=None, new=None)  # type: ignore[arg-type]
    second = PipelineContext(old=None, new=None)  # type: ignore[arg-type]
    assert first.identity_memo is not second.identity_memo


@pytest.mark.parametrize("kind", [ChangeKind.FUNC_REMOVED, ChangeKind.VAR_REMOVED])
def test_memo_returns_the_same_primary_id_as_resolution(kind: ChangeKind) -> None:
    from abicheck.compare.finding_identity import resolve_change_identity

    change = Change(kind=kind, symbol="foo", description="gone")
    assert (
        IdentityMemo().primary_id(change) == resolve_change_identity(change).primary_id
    )


def test_a_rewritten_identity_input_is_seen_by_the_next_lookup() -> None:
    """Stale-proof by construction: a rewrite of ``qualified_name`` between two
    lookups (the enum-name bridge does exactly this) must not return the key
    computed before the rewrite."""
    from abicheck.compare.finding_identity import resolve_change_identity

    memo = IdentityMemo()
    change = Change(
        kind=ChangeKind.FIELD_RENAMED,
        symbol="Foo",
        description="r",
        old_value="a",
        new_value="b",
        qualified_name="ns1::Foo",
    )
    before = memo.primary_id(change)
    change.qualified_name = "ns2::Foo"
    after = memo.primary_id(change)
    assert after == resolve_change_identity(change).primary_id
    assert after != before


def test_an_in_place_list_mutation_is_seen_by_the_next_lookup() -> None:
    """A cached finding whose list field is appended to in place must not keep
    its old identity: distinct findings must not be deduplicated against a stale
    key (CodeRabbit, PR #2107)."""
    from abicheck.compare.finding_identity import resolve_change_identity

    memo = IdentityMemo()
    change = Change(
        kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
        symbol="lib.so",
        description="The old snapshot's mismatched records are X.",
        affected_symbols=["A"],
    )
    before = memo.primary_id(change)
    assert change.affected_symbols is not None
    change.affected_symbols.append("B")
    after = memo.primary_id(change)
    assert after == resolve_change_identity(change).primary_id
    assert after != before


def test_a_cached_identity_is_read_without_a_memo_call(monkeypatch) -> None:
    """The cross-detector pass takes a cached key straight from the memo's
    entries: a hit makes no ``IdentityMemo.primary_id`` call, and the first
    lookup of a change makes exactly one."""
    from abicheck.compare.finding_dedup import dedup_identity_key
    from abicheck.compare.finding_identity import resolve_change_identity

    calls: list[int] = []
    real = IdentityMemo.primary_id

    def counting(self, change: Change) -> str:
        calls.append(id(change))
        return real(self, change)

    monkeypatch.setattr(IdentityMemo, "primary_id", counting)
    memo = IdentityMemo()
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="gone")
    expected = resolve_change_identity(change).primary_id

    assert dedup_identity_key(change, memo) == expected
    assert calls == [id(change)]
    calls.clear()
    assert dedup_identity_key(change, memo) == expected
    assert calls == []


def test_a_list_valued_input_gives_the_fresh_key_on_the_cross_pass() -> None:
    """A list-valued field is frozen into the cross pass's validity snapshot
    without crashing, and the key it yields is the fresh canonical one."""
    from abicheck.compare.finding_dedup import dedup_identity_key
    from abicheck.compare.finding_identity import resolve_change_identity

    memo = IdentityMemo()
    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="foo",
        description="gone",
        old_value=["a", "b"],
    )
    assert (
        dedup_identity_key(change, memo) == resolve_change_identity(change).primary_id
    )


def test_a_set_valued_input_gives_the_fresh_key_on_the_cross_pass() -> None:
    """The same for a set-valued field."""
    from abicheck.compare.finding_dedup import dedup_identity_key
    from abicheck.compare.finding_identity import resolve_change_identity

    memo = IdentityMemo()
    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="foo",
        description="gone",
        old_value={"a", "b"},  # type: ignore[arg-type]
    )
    assert (
        dedup_identity_key(change, memo) == resolve_change_identity(change).primary_id
    )
