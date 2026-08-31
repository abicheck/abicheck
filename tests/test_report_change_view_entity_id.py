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

"""``_ReportChangeView`` must keep pace with every attribute
``finding_identity.resolve_change_identity`` reads on a ``Change``.

Split out of ``tests/test_aggregate_findings.py`` (a debt-tracked, no-growth
test module -- see ``architecture/debt.yaml``) rather than added there, so
this regression didn't need to fight that file's frozen line budget.

ADR-063 Phase 2 gave ``Change`` an ``entity_id`` field and later taught
``resolve_change_identity`` to read it unconditionally
(``change.entity_id``, gated only on the pre-existing ``is_batch`` check).
``abicheck.workflows.aggregate.reconcile._ReportChangeView`` -- the
read-back adapter ``resolve_report_change_identity`` builds from a report's
JSON and passes to that same function -- was never given a matching field,
so any report round trip of a ``Change`` that carried a live ``entity_id``
raised ``AttributeError``. This is a real regression class, not one input:
whenever ``resolve_change_identity`` starts reading a new ``Change``
attribute, ``_ReportChangeView`` must be updated in lockstep or every
report-based aggregation call breaks the same way, unconditionally, for any
finding at all -- which is exactly what happened here (105 tests failed on
``main`` from this one gap).
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.finding_identity import resolve_change_identity
from abicheck.model.identity import EntityId, EntityKind
from abicheck.reporter import _change_to_dict
from abicheck.workflows.aggregate.reconcile import resolve_report_change_identity

_KWARGS = {
    "kind": ChangeKind.FUNC_REMOVED,
    "symbol": "_ZN3lib3addEii",
    "description": "Function removed",
}


def test_a_live_changes_entity_id_survives_the_report_round_trip() -> None:
    """A real `Change` carrying an `entity_id` must round-trip through a
    report without raising, and `entity_id` (never serialized by
    `_change_to_dict`) must not change the round-tripped identity relative
    to an otherwise-identical finding that never had one."""
    eid = EntityId(scope=(), kind=EntityKind.FUNCTION, leaf_name="add")
    with_id = resolve_report_change_identity(
        _change_to_dict(Change(entity_id=eid, **_KWARGS))
    )
    without_id = resolve_report_change_identity(_change_to_dict(Change(**_KWARGS)))
    assert with_id.primary_id == without_id.primary_id
    assert with_id.tier == without_id.tier


#: Real `Change` instances spanning the axes a single hand-picked
#: `FUNC_REMOVED` case does not reach: a type-level kind (scalar old/new), a
#: kind whose `new_value` is a list (`diff_python.py`'s own real shape, per
#: `_ReportChangeView`'s own docstring), and an always-batch-shaped kind
#: (`_is_batch_shaped_change`) that clears `entity_id`/`qualified_name`
#: *before* `resolve_change_identity` would ever read them off `change` —
#: this is the generalized parity check Codex asked for: not one input, but
#: the field-parity invariant checked across every axis
#: `_ReportChangeView`'s own fields are meant to cover (kind category, batch
#: vs. non-batch, scalar vs. structured value).
_PARITY_CASES = [
    pytest.param(
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_ZN3lib3addEii",
            description="Function removed",
        ),
        id="symbol-level",
    ),
    pytest.param(
        Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Foo",
            description="size 8 -> 16",
            old_value="8",
            new_value="16",
        ),
        id="type-level-scalar-value",
    ),
    pytest.param(
        Change(
            kind=ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
            symbol="foo",
            description="uses a private CPython C-API symbol",
            new_value=["a", "b"],
        ),
        id="list-valued-new_value",
    ),
    pytest.param(
        Change(
            kind=ChangeKind.VISIBILITY_LEAK,
            symbol="SomeType",
            description="leaked",
        ),
        id="batch-shaped",
    ),
    pytest.param(
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_ZN3lib3addEii",
            description="Function removed",
            entity_id=EntityId(scope=(), kind=EntityKind.FUNCTION, leaf_name="add"),
        ),
        id="entity_id-set",
    ),
]


@pytest.mark.parametrize("change", _PARITY_CASES)
def test_report_round_trip_matches_the_live_identity(change: Change) -> None:
    """The mechanical form of the field-parity invariant: for a `Change`
    spanning every axis above, round-tripping through a report (`_change_to_dict`
    then `resolve_report_change_identity`) must neither raise nor diverge from
    calling `resolve_change_identity` on the live object directly. A field
    `_ReportChangeView` is missing would either raise (this bug) or silently
    diverge (a field present on `Change` but read as `None` on the view) —
    this test's oracle is `resolve_change_identity` itself, run on the
    live `Change`, not a hand-computed expected string, so it fails the same
    way for a *future* field `resolve_change_identity` starts reading that
    `_ReportChangeView` doesn't yet carry."""
    live = resolve_change_identity(change)
    report = resolve_report_change_identity(_change_to_dict(change))
    assert report.primary_id == live.primary_id
    assert report.tier == live.tier
