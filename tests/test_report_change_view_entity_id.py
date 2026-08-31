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

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
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
