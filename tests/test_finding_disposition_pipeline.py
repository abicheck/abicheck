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

"""The finding-disposition pipeline, through its interface.

`dispose_findings(changes, old, new, rules)` owns ADR-067 D1's conservation
invariant: every input finding ends the run kept, in one of the context's
buckets, or recorded in the disposition ledger -- whatever the rules, and
whether or not the caller supplied a ledger. `DispositionPipeline` enforces it
for any step list, from what each step *declares* its drops mean.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
from abicheck.policy.finding_disposition import (
    DispositionRules,
    PipelineContext,
    dispose_findings,
)
from abicheck.policy.finding_disposition.pipeline import DispositionPipeline
from abicheck.policy_file import PolicyFile
from abicheck.suppression import Suppression, SuppressionList


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    return (
        AbiSnapshot(library="libfoo.so.1", version="1.0"),
        AbiSnapshot(library="libfoo.so.1", version="2.0"),
    )


def _inputs() -> list[Change]:
    return [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="keep_me", description="x"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="waived", description="x"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="first"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="first"),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="added", description="x"),
        Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Root", description="x"),
        Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="uses_root",
            description="x",
            old_value="Root*",
            new_value="Root*",
        ),
    ]


_SUPPRESSIONS = {
    "none": None,
    "exact": SuppressionList([Suppression(symbol="waived", reason="w")]),
    "everything": SuppressionList(
        [Suppression(symbol_pattern=".*", reason="w", allow_public_break=True)]
    ),
}


@pytest.mark.parametrize(
    ("suppression", "scope", "with_ledger"),
    list(itertools.product(sorted(_SUPPRESSIONS), (False, True), (False, True))),
)
def test_every_input_is_accounted_for(
    suppression: str, scope: bool, with_ledger: bool
) -> None:
    inputs = _inputs()
    old, new = _snapshots()
    supplied = DispositionLedger() if with_ledger else None
    ctx = dispose_findings(
        list(inputs),
        old,
        new,
        DispositionRules(
            suppression=_SUPPRESSIONS[suppression], scope_to_public_surface=scope
        ),
        ledger=supplied,
    )
    ledger = ctx.disposition_ledger
    assert ledger is not None
    if supplied is not None:
        assert ledger is supplied
    placed = [
        *ctx.kept,
        *ctx.suppressed,
        *ctx.redundant,
        *ctx.opaque_filtered,
        *ctx.out_of_surface,
    ]
    for change in inputs:
        assert any(change is c for c in placed) or ledger.record_for(change), change
    for change in ctx.suppressed:
        record = ledger.record_for(change)
        assert record is not None
        assert record.disposition in {Disposition.SUPPRESSED, Disposition.DEDUPLICATED}


def test_rules_from_policy_file_carry_the_namespace_conventions() -> None:
    assert DispositionRules.from_policy_file(None) == DispositionRules()
    policy = PolicyFile(
        frozen_namespaces=["**::detail::r1"],
        internal_namespaces=["priv"],
        internal_namespaces_stated=True,
    )
    suppression = SuppressionList([Suppression(symbol="x", reason="r")])
    rules = DispositionRules.from_policy_file(
        policy,
        suppression=suppression,
        scope_to_public_surface=True,
        force_public_symbols={"f"},
        public_surface_allowlist={"g"},
    )
    assert rules.frozen_namespaces == ("**::detail::r1",)
    assert rules.internal_namespaces == ("priv",)
    assert rules.suppression is suppression
    assert rules.force_public_symbols == frozenset({"f"})
    assert rules.public_surface_allowlist == frozenset({"g"})

    old, new = _snapshots()
    ctx = PipelineContext.from_rules(old, new, rules, ledger=None)
    assert ctx.frozen_namespaces == ["**::detail::r1"]
    assert ctx.internal_namespaces == ("priv",)
    assert ctx.force_public_symbols == {"f"}
    assert ctx.public_surface_allowlist == {"g"}


@pytest.mark.parametrize(
    ("requested", "fell_back", "resolved"),
    [
        (False, False, True),
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_scope_resolved(requested: bool, fell_back: bool, resolved: bool) -> None:
    old, new = _snapshots()
    ctx = PipelineContext(
        old=old, new=new, scope_to_public_surface=requested, scope_fell_back=fell_back
    )
    assert ctx.scope_resolved is resolved


class _DropFirst:
    """A step that discards its first finding outright."""

    name = "drop_first"

    def __init__(self, disposition: object) -> None:
        if disposition != "default":
            self.dropped_finding_disposition = disposition

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        return changes[1:]


@pytest.mark.parametrize(
    ("declared", "recorded"),
    [
        ("default", Disposition.DEDUPLICATED),
        (Disposition.NON_GATING, Disposition.NON_GATING),
        (None, None),
    ],
)
def test_a_dropped_finding_is_recorded_as_its_step_declares(
    declared: object, recorded: Disposition | None
) -> None:
    old, new = _snapshots()
    first, second = _inputs()[:2]
    ctx = DispositionPipeline([_DropFirst(declared)]).run([first, second], old, new)
    record = ctx.disposition_ledger.record_for(first)
    assert (record.disposition if record else None) is recorded
    assert ctx.disposition_ledger.record_for(second) is None
    assert ctx.kept == [second]


class _AliasKept:
    name = "alias"
    establishes_kept_alias = True

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        ctx.kept = changes
        return changes


class _Rebind:
    name = "rebind"
    dropped_finding_disposition = None

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        return list(changes)


def test_a_step_that_breaks_the_kept_alias_fails_loudly() -> None:
    old, new = _snapshots()
    with pytest.raises(RuntimeError, match="aliasing contract"):
        DispositionPipeline([_AliasKept(), _Rebind()]).run(_inputs(), old, new)
    # Before the aliasing step, rebinding is allowed.
    ctx = DispositionPipeline([_Rebind(), _AliasKept()]).run(_inputs(), old, new)
    assert len(ctx.kept) == len(_inputs())
