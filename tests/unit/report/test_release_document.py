"""The one release report builder (``report.release_document``).

Three invariants, each checked against an oracle that is not the builder's
own helper:

* every live-release format is a projection of one built view: rendering the
  same view repeatedly, in any format order, yields identical bytes, and the
  decision functions run once, at build time, never during a render;
* a stored (``BundleFacts``) comparison's JSON is the live release document
  itself, with its stored markers: every live key survives, and the shared
  semantic fields (verdict, per-library verdicts, bundle findings) agree with
  the inputs, not with either renderer.
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
from abicheck.report.release_assurance_section import release_assurance_terms
from abicheck.report.render_release_report import (
    render_release_text,
    render_stored_release_json,
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
def test_stored_document_is_the_live_document_with_its_markers(
    verdicts: list[Verdict], n_bundle: int
) -> None:
    result = _stored_result(verdicts, n_bundle)
    evaluation = _live_evaluation(result)
    live = json.loads(
        render_release_text("json", rd.build_release_report(evaluation), demangle=False)
    )
    stored = json.loads(render_stored_release_json(evaluation))
    # Oracle: the inputs themselves, not either document.
    expected_members = {d.library: d.verdict.value for d in result.per_library}
    assert {e["library"]: e["verdict"] for e in stored["libraries"]} == expected_members
    assert stored["verdict"] == live["verdict"]
    project = lambda findings: [(f["kind"], f["symbol"]) for f in findings]  # noqa: E731
    assert project(stored["bundle_findings"]) == [
        (f.kind.value, f.symbol) for f in result.bundle_findings
    ]
    assert project(stored["bundle_findings"]) == project(live["bundle_findings"])
    # Every live key survives; the stored document only adds its markers.
    assert set(live) <= set(stored)
    assert stored["effective_config_source"] == "reconstructed_from_stored_facts"
    assert stored["public_surface"]["state"] == "unavailable"
    assert "mode" not in stored


def test_stored_markdown_is_the_live_release_markdown() -> None:
    evaluation = _live_evaluation(_stored_result([Verdict.BREAKING], 1))
    text = render_release_text(
        "markdown", rd.build_release_report(evaluation), demangle=True
    )
    assert text.startswith("# ABI Release Comparison")


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


@pytest.mark.parametrize("n_bundle", [0, 1])
def test_stored_document_names_nothing_it_cannot_demangle(n_bundle: int) -> None:
    stored = json.loads(
        render_stored_release_json(
            _live_evaluation(_stored_result([Verdict.BREAKING], n_bundle))
        )
    )
    assert len(stored["bundle_findings"]) == n_bundle
    assert all("demangled_symbol" not in f for f in stored["bundle_findings"])
    assert "public_surface_reconciliation" not in stored


def test_stored_document_projects_the_stored_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck.report import release_public_surface_section as surface_section

    reconciliation = object()
    seen: list[object] = []

    class _Projection:
        def to_dict(self) -> dict[str, object]:
            return {"evaluated": True}

    def _project(recon: object, **_kw: object) -> _Projection:
        seen.append(recon)
        return _Projection()

    monkeypatch.setattr(surface_section, "compute_release_public_surface", _project)
    result = _stored_result([Verdict.BREAKING], 0)
    result.public_surface_reconciliation = reconciliation
    document = rd.stored_release_document(_live_evaluation(result), details={})
    # Oracle: the reconciliation the stored driver recorded, handed through as is.
    assert seen == [reconciliation]
    assert document.to_mapping()["public_surface_reconciliation"] == {"evaluated": True}
