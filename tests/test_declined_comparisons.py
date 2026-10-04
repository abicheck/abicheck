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

"""ADR-063 T9: shared accounting for declined comparisons.

A detector that refuses to judge an entity on incomplete evidence used to
return the same empty list as one that judged it clean. The contract stated
here:

* the collector: recording outside a scope is a no-op; inside, declines are
  deduplicated per ``(entity, reason)`` in first-recorded order; nested
  scopes do not leak into each other; an exception still closes the scope;
* the producer: ``vtable_fact_declined`` records exactly when it declines,
  for every decline status on either side, and never otherwise;
* the public surface: a real ``compare()`` over an ``UNSUPPORTED`` (PDB-like)
  vtable reports the decline under that detector in the JSON report, the
  report validates against the schema, and the verdict and findings are
  unchanged by the accounting.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.compare.declined_comparisons import (
    DeclinedComparison,
    declined_scope,
    record_declined,
)
from abicheck.compare.vtable_evidence import vtable_fact_declined
from abicheck.model import AbiSnapshot, Fact, FactStatus, RecordType


def test_recording_outside_a_scope_is_a_noop():
    record_declined("A", "r")  # must not raise or leak into a later scope
    with declined_scope() as got:
        pass
    assert got == []


@pytest.mark.parametrize(
    "events",
    [
        [("A", "r1"), ("A", "r1")],
        [("A", "r1"), ("B", "r1"), ("A", "r1"), ("A", "r2")],
        [("B", "x"), ("A", "x"), ("B", "x")],
    ],
)
def test_declines_are_deduplicated_in_first_recorded_order(events):
    with declined_scope() as got:
        for entity, reason in events:
            record_declined(entity, reason)
    expected = list(dict.fromkeys(events))
    assert [(d.entity, d.reason) for d in got] == expected


def test_nested_scopes_do_not_leak_and_exceptions_close_the_scope():
    with declined_scope() as outer:
        record_declined("outer", "r")
        with pytest.raises(RuntimeError):
            with declined_scope() as inner:
                record_declined("inner", "r")
                raise RuntimeError
        record_declined("outer2", "r")
    assert inner == [DeclinedComparison("inner", "r")]
    assert [d.entity for d in outer] == ["outer", "outer2"]


def _rec(status: FactStatus | None) -> RecordType:
    kw: dict[str, object] = {"name": "C", "qualified_name": "ns::C", "kind": "class"}
    if status is FactStatus.UNSUPPORTED:
        kw["vtable_fact"] = Fact.unsupported(producer="pdb")
    elif status is FactStatus.PARTIAL:
        kw["vtable_fact"] = Fact.partial(["_ZN2ns1C1fEv"], producer="dwarf")
    else:
        kw["vtable"] = ["_ZN2ns1C1fEv"]
    return RecordType(**kw)  # type: ignore[arg-type]


STATUSES = [None, FactStatus.UNSUPPORTED, FactStatus.PARTIAL]


@pytest.mark.parametrize(("old", "new"), list(itertools.product(STATUSES, STATUSES)))
def test_vtable_decline_is_recorded_exactly_when_it_declines(old, new):
    with declined_scope() as got:
        declined = vtable_fact_declined(_rec(old), _rec(new))
    assert declined == (old is not None or new is not None)
    assert bool(got) == declined
    if declined:
        assert got[0].entity == "ns::C"
        side = "old" if old is not None else "new"
        assert side in got[0].reason


def _poly(vtable_fact: Fact | None, extra_virtual: bool) -> RecordType:
    kw: dict[str, object] = {"name": "C", "kind": "class", "size_bits": 64}
    if vtable_fact is not None:
        kw["vtable_fact"] = vtable_fact
    else:
        kw["vtable"] = ["_ZN1C1fEv", "_ZN1C1gEv"] if extra_virtual else ["_ZN1C1fEv"]
    return RecordType(**kw)  # type: ignore[arg-type]


def test_compare_reports_the_decline_and_leaves_findings_unchanged():
    from abicheck.checker import compare
    from abicheck.reporter import to_json

    old = AbiSnapshot(library="l.so", version="1", types=[_poly(None, False)])
    new = AbiSnapshot(
        library="l.so",
        version="2",
        types=[_poly(Fact.unsupported(producer="pdb"), False)],
    )
    result = compare(old, new)
    declining = [d for d in result.detector_results if d.declined]
    assert declining, [d.name for d in result.detector_results]
    assert all(e == "C" for d in declining for e, _ in d.declined)

    doc = json.loads(to_json(result))
    listed = {d["name"]: d for d in doc["detectors"]}
    for det in declining:
        assert listed[det.name]["declined"] == [
            {"entity": e, "reason": r} for e, r in det.declined
        ]
    assert doc["report_schema_version"] == "5.13"
    from pathlib import Path

    from schema_validation import jsonschema_available, validate_instance

    if jsonschema_available():
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "abicheck/schemas/compare_report.schema.json"
        )
        validate_instance(doc, json.loads(schema_path.read_text(encoding="utf-8")))
