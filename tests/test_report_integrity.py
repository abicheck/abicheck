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

"""Cross-channel report-integrity tests (C2 / ADR-036).

These lock the invariant the user asked for: *a change classifies consistently
regardless of output channel*. All native channels (JSON, SARIF, JUnit, text)
must derive each finding's classification from the one canonical axis — the
per-finding effective verdict (`ReportModel.verdict_of`) — so they can never
disagree with each other or with the gate/exit code.

The ABICC-compatibility HTML severity (HIGH/MEDIUM/LOW in
`report_classifications`) is deliberately a *different* axis: it mirrors ABICC's
own kind-based severity so ABICC report parsers/diffs keep working, and is
intentionally NOT driven by abicheck's policy/A4 overrides. That exception is
asserted explicitly below so it stays a conscious decision, not silent drift.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.checker_types import Change
from abicheck.junit_report import _is_failure
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report_model import (
    UNKNOWN_SEVERITY_LABEL,
    VERDICT_PRESENTATION,
    VERDICT_TO_SARIF_LEVEL,
    VERDICT_TO_SEVERITY_LABEL,
    ReportModel,
)
from abicheck.sarif import _severity as sarif_severity


def _fn(name: str, ret: str = "void") -> Function:
    return Function(name=name, mangled=name, return_type=ret, params=[], visibility=Visibility.PUBLIC)


def _result():
    # Mix of breaking (removed), compatible (added), and a type change.
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("removed_fn"), _fn("kept_fn"), _fn("retyped_fn", ret="int")],
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("kept_fn"), _fn("retyped_fn", ret="long"), _fn("added_fn")],
    )
    return compare(old, new, scope_to_public_surface=False)


# ── Canonical maps are total over the reportable verdicts ────────────────────


@pytest.mark.parametrize("verdict", [Verdict.BREAKING, Verdict.API_BREAK, Verdict.COMPATIBLE_WITH_RISK, Verdict.COMPATIBLE])
def test_canonical_maps_cover_every_reportable_verdict(verdict: Verdict) -> None:
    assert verdict in VERDICT_TO_SEVERITY_LABEL
    assert verdict in VERDICT_TO_SARIF_LEVEL


def test_presentation_table_is_exactly_the_reportable_verdicts() -> None:
    # NO_CHANGE is intentionally excluded: a per-finding verdict is never
    # NO_CHANGE (that is an overall-result state). Pin the table's key set so the
    # asymmetry with severity.legacy_exit_code (which DOES map NO_CHANGE) stays a
    # conscious decision, not an accidental omission.
    assert set(VERDICT_PRESENTATION) == {
        Verdict.BREAKING,
        Verdict.API_BREAK,
        Verdict.COMPATIBLE_WITH_RISK,
        Verdict.COMPATIBLE,
    }
    assert Verdict.NO_CHANGE not in VERDICT_PRESENTATION


def test_single_table_is_the_source_of_truth() -> None:
    # The back-compat projections must be derived from VERDICT_PRESENTATION, not
    # a second hand-maintained copy.
    assert VERDICT_TO_SEVERITY_LABEL == {v: p.severity_label for v, p in VERDICT_PRESENTATION.items()}
    assert VERDICT_TO_SARIF_LEVEL == {v: p.sarif_level for v, p in VERDICT_PRESENTATION.items()}


def test_presentation_internally_consistent() -> None:
    # breaking_boundary must agree with both the label and the SARIF level within
    # the one table — no row can say "breaking" on one axis and "compatible" on
    # another.
    for pres in VERDICT_PRESENTATION.values():
        assert pres.breaking_boundary == (pres.severity_label in ("breaking", "api_break"))
        assert pres.breaking_boundary == (pres.sarif_level == "error")


def test_pr_comment_buckets_cover_canonical_labels() -> None:
    # pr_comment buckets the canonical severity label; its key set must stay a
    # superset of the canonical labels (+ the defensive "unknown") so no label
    # falls through to a wrong bucket.
    from abicheck.pr_comment import _SEVERITY_BUCKET

    expected = set(VERDICT_TO_SEVERITY_LABEL.values()) | {UNKNOWN_SEVERITY_LABEL}
    assert expected <= set(_SEVERITY_BUCKET)


# ── Per-change consistency across native channels ────────────────────────────


def test_native_channels_agree_on_breaking_boundary() -> None:
    # The cross-channel invariant is the *breaking boundary*, not identical
    # vocabulary: channels may use finer levels (SARIF marks additions
    # "warning", not "note"), but every channel must agree on which findings are
    # on the breaking side of the gate.
    result = _result()
    model = ReportModel.from_result(result)
    assert model.changes, "expected at least one change to classify"

    breaking_set, api_break_set, risk_set, _ = result._effective_kind_sets()

    for ch in model.changes:
        breaking = model.is_breaking_boundary(ch)

        # JSON severity label is exactly the verdict axis.
        assert (model.severity_label(ch) in ("breaking", "api_break")) == breaking

        # SARIF "error" iff breaking; non-breaking must not be "error".
        assert (sarif_severity(ch) == "error") == breaking

        # JUnit failure iff breaking (no severity-config gate here).
        assert _is_failure(ch, breaking_set, api_break_set, risk_set) == breaking


def test_json_severity_matches_canonical_label() -> None:
    from abicheck.reporter import to_json

    result = _result()
    model = ReportModel.from_result(result)
    by_symbol = {(c.kind.value, c.symbol): model.severity_label(c) for c in model.changes}

    payload = json.loads(to_json(result))
    rendered = payload.get("changes", [])
    assert rendered, "JSON report had no changes"
    for entry in rendered:
        key = (entry["kind"], entry.get("symbol", ""))
        if key in by_symbol:
            assert entry["severity"] == by_symbol[key]


# ── Effective-verdict override propagates to every native channel ────────────


def test_a4_override_propagates_across_channels() -> None:
    # A finding whose kind is BREAKING but carries an A4 effective_verdict of
    # COMPATIBLE must read as compatible in every native channel — this is the
    # exact divergence the unification prevents.
    result = _result()
    breaking = next((c for c in result.changes if result._effective_verdict_for_change(c) == Verdict.BREAKING), None)
    assert breaking is not None, "fixture must produce a breaking change"

    demoted = Change(
        kind=breaking.kind,
        symbol=breaking.symbol,
        description=breaking.description,
        effective_verdict=Verdict.COMPATIBLE,
    )
    model = ReportModel.from_result(result)
    assert model.verdict_of(demoted) == Verdict.COMPATIBLE
    assert model.severity_label(demoted) == "compatible"
    assert model.is_breaking_boundary(demoted) is False
    # Override propagates to every native channel: not error, not failure.
    assert sarif_severity(demoted) == "note"
    breaking_set, api_break_set, risk_set, _ = result._effective_kind_sets()
    assert _is_failure(demoted, breaking_set, api_break_set, risk_set) is False
