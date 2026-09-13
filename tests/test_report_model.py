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

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report_model import ReportModel


def _fn(name: str, ret: str = "void") -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
    )


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so.1", version="1.0", functions=funcs)


def test_buckets_partition_changes_by_effective_verdict() -> None:
    old = _snap([_fn("kept"), _fn("dropped")])
    new = _snap([_fn("kept"), _fn("added")])
    result = compare(old, new, scope_to_public_surface=False)

    model = ReportModel.from_result(result)
    buckets = [model.breaking, model.source_breaks, model.risk, model.compatible]
    flat = [c for b in buckets for c in b]
    # Every classified change comes from the (filtered) change set, exactly once.
    assert sorted(id(c) for c in flat) == sorted(id(c) for c in model.changes)
    # And each lands in the bucket matching its effective verdict.
    ev = result._effective_verdict_for_change
    assert all(ev(c) == Verdict.BREAKING for c in model.breaking)
    assert all(ev(c) == Verdict.API_BREAK for c in model.source_breaks)
    assert all(ev(c) == Verdict.COMPATIBLE_WITH_RISK for c in model.risk)
    assert all(ev(c) == Verdict.COMPATIBLE for c in model.compatible)


def test_from_result_defaults_to_all_changes() -> None:
    old = _snap([_fn("a"), _fn("b")])
    new = _snap([_fn("a")])
    result = compare(old, new, scope_to_public_surface=False)
    model = ReportModel.from_result(result)
    assert len(model.changes) == len(list(result.changes))
    assert model.summary.total_changes >= 1


def test_from_result_respects_prefiltered_changes() -> None:
    old = _snap([_fn("a"), _fn("b")])
    new = _snap([_fn("a")])
    result = compare(old, new, scope_to_public_surface=False)
    # Caller pre-filters; the model classifies exactly what it is given.
    model = ReportModel.from_result(result, changes=[])
    assert model.changes == []
    assert model.breaking == [] and model.compatible == []


def test_reporter_classifier_delegates_to_model() -> None:
    # _classify_changes_by_kind must produce the same partition as the model.
    from abicheck.reporter import _classify_changes_by_kind

    old = _snap([_fn("a"), _fn("b")])
    new = _snap([_fn("a")])
    result = compare(old, new, scope_to_public_surface=False)
    changes = list(result.changes)
    assert _classify_changes_by_kind(changes, result) == ReportModel.classify(
        changes, result
    )
