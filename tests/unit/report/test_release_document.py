"""The one release report builder (``report.release_document``).

Three invariants, each checked against an oracle that is not the builder's
own helper:

* every live-release format is a projection of one built view: rendering the
  same view repeatedly, in any format order, yields identical bytes, and the
  decision functions run once, at build time, never during a render;
* the live and stored (``BundleFacts``) operands both go through
  ``build_release_report`` and agree on every semantic field both documents
  carry (verdict, bundle verdict, per-library verdicts, bundle findings);
* the two documents still differ in *shape* -- recorded in
  ``docs/contribute/known-gaps.md``; the pin below fails when they converge,
  so the entry gets closed rather than left stale.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.bundle_models import BundleDiffResult, BundleFinding
from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.report import release_document as rd
from abicheck.report.comparison_scope import comparison_scope_terms
from abicheck.report.release_assurance import release_assurance_terms
from abicheck.report.release_stored_scope import scope_terms_for
from abicheck.report.render_release_report import (
    render_release_text,
    render_stored_release_json,
    render_stored_release_markdown,
)
from abicheck.workflows.gate import (
    resolve_release_assurance_decision,
    resolve_scope_decision,
)

_LIVE_FORMATS = ("json", "markdown", "junit", "oneline", "html")


def _stored_result(verdicts: list[Verdict], n_bundle: int) -> BundleDiffResult:
    per_library = [
        DiffResult(
            old_version="old",
            new_version="new",
            library=f"lib{i}.so",
            changes=(
                [Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")]
                if v != Verdict.NO_CHANGE
                else []
            ),
            verdict=v,
        )
        for i, v in enumerate(verdicts)
    ]
    return BundleDiffResult(
        old_root=Path("/old"),
        new_root=Path("/new"),
        per_library=per_library,
        bundle_findings=[
            BundleFinding(
                kind=ChangeKind.BUNDLE_LIBRARY_REMOVED,
                symbol=f"gone{i}.so",
                description="removed",
            )
            for i in range(n_bundle)
        ],
    )


def _live_evaluation(result: BundleDiffResult) -> rd.ReleaseEvaluation:
    """The live operand for the *same* release: one flattened entry per
    member, the bundle analysis riding as ``bundle_result``."""
    worst = rd.stored_reported_verdict(result)
    return rd.ReleaseEvaluation(
        worst_verdict=worst,
        old_dir=Path("/old"),
        new_dir=Path("/new"),
        library_results=[
            {"library": d.library, "verdict": d.verdict.value}
            for d in result.per_library
        ],
        scope_terms=comparison_scope_terms(resolve_scope_decision(None, None)),
        assurance_terms=release_assurance_terms(
            resolve_release_assurance_decision((), require_complete=False)
        ),
        bundle_result=result,
        effective_config=("sha256:test", {"policy.base": "strict_abi"}),
    )


def _stored_evaluation(result: BundleDiffResult) -> rd.StoredReleaseEvaluation:
    return rd.StoredReleaseEvaluation(
        result=result,
        old_facts_path=Path("/old.bundle-facts.json"),
        new_dir=Path("/new"),
        scope_terms=scope_terms_for(result, {}),
    )


_CASES = [
    ([Verdict.NO_CHANGE], 0),
    ([Verdict.BREAKING, Verdict.NO_CHANGE], 0),
    ([Verdict.COMPATIBLE, Verdict.API_BREAK], 2),
    ([Verdict.NO_CHANGE, Verdict.NO_CHANGE], 1),
]


@pytest.mark.parametrize(("verdicts", "n_bundle"), _CASES)
def test_live_formats_are_order_independent_projections(
    verdicts: list[Verdict], n_bundle: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = rd.build_release_report(
        _live_evaluation(_stored_result(verdicts, n_bundle))
    )
    baseline = {
        f: render_release_text(f, report, demangle=False) for f in _LIVE_FORMATS
    }

    calls = {"exit": 0}

    def _spy(*_a: object, **_k: object) -> object:
        calls["exit"] += 1
        raise AssertionError("a renderer re-resolved the exit decision")

    monkeypatch.setattr(rd, "resolve_release_exit_decision_for_report", _spy)
    for order in itertools.islice(itertools.permutations(_LIVE_FORMATS), 0, None, 17):
        for fmt in order:
            assert render_release_text(fmt, report, demangle=False) == baseline[fmt]
    assert calls["exit"] == 0


@pytest.mark.parametrize(("verdicts", "n_bundle"), _CASES)
def test_live_and_stored_operands_agree_on_shared_semantics(
    verdicts: list[Verdict], n_bundle: int
) -> None:
    result = _stored_result(verdicts, n_bundle)
    live = json.loads(
        render_release_text(
            "json", rd.build_release_report(_live_evaluation(result)), demangle=False
        )
    )
    stored = json.loads(
        render_stored_release_json(rd.build_release_report(_stored_evaluation(result)))
    )
    # Oracle: the inputs themselves, not either document.
    expected_members = {d.library: d.verdict.value for d in result.per_library}
    assert {e["library"]: e["verdict"] for e in live["libraries"]} == expected_members
    assert {
        name: doc["verdict"] for name, doc in stored["libraries"].items()
    } == expected_members
    assert live["verdict"] == stored["verdict"]
    assert live["bundle_verdict"] == stored["bundle_verdict"]
    assert live["bundle_verdict"] == result.bundle_verdict.value
    project = lambda findings: [(f["kind"], f["symbol"]) for f in findings]  # noqa: E731
    assert project(live["bundle_findings"]) == project(stored["bundle_findings"])
    assert project(live["bundle_findings"]) == [
        (f.kind.value, f.symbol) for f in result.bundle_findings
    ]


def test_stored_and_live_documents_still_differ_in_shape() -> None:
    """Pins the recorded divergence (known-gaps: "Release report: live and
    stored operands render different documents"). When the two converge this
    fails -- close the known-gaps entry then."""
    result = _stored_result([Verdict.BREAKING], 1)
    live = json.loads(
        render_release_text(
            "json", rd.build_release_report(_live_evaluation(result)), demangle=False
        )
    )
    stored_report = rd.build_release_report(_stored_evaluation(result))
    stored = json.loads(render_stored_release_json(stored_report))
    assert "release_schema_version" in live
    assert "release_schema_version" not in stored
    assert stored["mode"] == "bundle_facts"
    assert isinstance(live["libraries"], list)
    assert isinstance(stored["libraries"], dict)
    assert render_stored_release_markdown(stored_report).startswith(
        "# Bundle-facts comparison"
    )


def test_json_document_requires_the_frontend_resolved_receipt() -> None:
    evaluation = _live_evaluation(_stored_result([Verdict.NO_CHANGE], 0))
    bare = rd.ReleaseEvaluation(**{**evaluation.__dict__, "effective_config": None})
    with pytest.raises(ValueError, match="effective_config"):
        rd.release_json_document(rd.build_release_report(bare))


def test_release_findings_for_render_never_leaks_private_keys() -> None:
    private = {
        "findings_view",
        "findings_view_truncated",
        "findings_view_truncated_kinds",
        "impact_table_view",
        "findings_total_count",
        "findings_total_count_view",
    }
    entry: dict[str, object] = {"library": "a", "verdict": "BREAKING"}
    entry.update({k: 1 for k in private})
    for show in (None, "breaking"):
        (projected,) = rd.release_findings_for_render([entry], show)
        assert private.isdisjoint(projected)
    assert private <= set(entry)  # the shared input is never mutated
