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

"""The ``aggregate`` fan-in document renders as itself, never as a clean
``compare``.

**Bug class:** ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest_report.py``). ``build_model`` dispatched on
payload keys (``libraries``, ``application``/``relevant_changes``,
``scan_schema_version``, ``audit_report_schema_version``) and fell through to
the ``compare`` adapter for everything else. An aggregate document has none
of those keys *and* no ``changes`` array, so it fell through, was read for a
key it does not carry, and rendered "✅ No ABI changes" — for a fan-in that
may have been failing on every target in it.

**General invariant**, stated over the whole shape rather than the one
document that exposed it: *a rendered aggregate comment claims a clean
result only when the document says every target was analyzed, every axis
closed, the gate passed, and every member report was actually read.* Any
other combination must produce a visible limitation and must post under
``--on=changes``. :class:`TestNoFalseCleanOverTheStateCrossProduct` states
that as an exhaustive enumeration over the small domain of per-target states
crossed with the document's own axes, against an oracle derived from the
*specification* of each document rather than from the model under test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from abicheck.pr_comment import build_model, render_comment, should_post
from abicheck.pr_comment_render import GITHUB_COMMENT_LIMIT
from abicheck.pr_comment_sections import _truncate_to_budget
from abicheck.report.change_summary import (
    ChangeSummary,
    EntityRow,
    fold_change_summaries,
    summarize_changes,
)
from tests._aggregate_documents import (
    CLEAN_HEADLINES,
    headline_of as _headline,
    member_report as _member_report,
)

# ---------------------------------------------------------------------------
# Document builders — a spec, and the document it describes
# ---------------------------------------------------------------------------

#: Every per-target state an aggregate document can record, as this adapter
#: must distinguish them. Deliberately an explicit vocabulary rather than a
#: sample: the enumeration below crosses it exhaustively, so a state added
#: here is automatically covered everywhere the cross-product runs.
TARGET_STATES = (
    "clean",  # analyzed, NO_CHANGE, member report readable
    "additions",  # analyzed, COMPATIBLE, compatible findings only
    "break",  # analyzed, BREAKING, a real ABI break
    "unavailable",  # never reported
    "not_comparable",  # analyzed in bookkeeping, never reached a comparison
    "operational_error",  # its own run failed operationally
    "missing_report",  # analyzed, but its member report is not on disk
)

#: States in which the document itself establishes nothing about the target.
_NO_RESULT_STATES = frozenset(
    {"unavailable", "not_comparable", "operational_error", "missing_report"}
)


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    state: str
    required: bool = True


@dataclass(frozen=True)
class DocSpec:
    """What a document is *supposed* to say — the oracle's only input.

    The expectations below are derived from this, never from the
    :class:`~abicheck.pr_comment_base.CommentModel` under test, so an
    implementation that answered every question from one shared helper could
    not make the assertions agree with themselves.
    """

    targets: tuple[TargetSpec, ...]
    contract_coverage_incomplete: tuple[str, ...] = ()
    contract_coverage_exit: int = 0
    analysis_assurance_incomplete: tuple[str, ...] = ()
    analysis_assurance_exit: int = 0
    scope_incomplete: tuple[str, ...] = ()
    scope_exit: int = 0
    missing_required_blocking: bool = True

    # --- oracle -----------------------------------------------------------
    @property
    def expects_limitation(self) -> bool:
        """Whether a reviewer must be shown something is not fully checked."""
        return bool(
            [t for t in self.targets if t.state in _NO_RESULT_STATES]
            or self.contract_coverage_incomplete
            or self.analysis_assurance_incomplete
            or self.scope_incomplete
        )

    @property
    def expects_breaking(self) -> bool:
        return any(t.state == "break" for t in self.targets)

    @property
    def expects_compatible_findings(self) -> bool:
        return any(t.state == "additions" for t in self.targets)

    @property
    def is_fully_clean(self) -> bool:
        """Every target analyzed and clean, every axis closed."""
        return (
            bool(self.targets)
            and all(t.state == "clean" for t in self.targets)
            and not self.expects_limitation
        )


_STATE_VERDICT = {
    "clean": "NO_CHANGE",
    "additions": "COMPATIBLE",
    "break": "BREAKING",
    # `aggregate` forces a synthetic BREAKING for a not-comparable report so
    # the leg cannot pass a gate unnoticed; the discriminator is the gate
    # category, not the verdict (see `TargetReport`'s own docstring).
    "not_comparable": "BREAKING",
    "operational_error": "BREAKING",
    "missing_report": "COMPATIBLE",
}


def _target_block(spec: TargetSpec) -> dict[str, object]:
    tid = spec.target_id
    common: dict[str, object] = {
        "target_id": tid,
        "required": spec.required,
        "contract_coverage_exit": 0,
        "analysis_assurance_exit": 0,
        "scope_completeness_exit": 0,
    }
    if spec.state == "unavailable":
        return {
            **common,
            "state": "unavailable",
            "compatibility_verdict": None,
            "gate": None,
            "reason": "no report was produced for this expected target",
        }
    if spec.state in ("not_comparable", "operational_error"):
        return {
            **common,
            "state": "analyzed",
            "compatibility_verdict": "BREAKING",
            "gate": {
                "exit_code": 1,
                "blocking": True,
                "blocking_categories": [spec.state],
                "from_report": True,
            },
            "report_path": f"{tid}.json",
            "reason": "the comparison never ran",
        }
    if spec.state == "missing_report":
        return {
            **common,
            "state": "analyzed",
            "compatibility_verdict": "COMPATIBLE",
            "gate": {
                "exit_code": 0,
                "blocking": False,
                "blocking_categories": [],
                "from_report": True,
            },
            "report_path": f"{tid}-never-written.json",
        }
    breaking = spec.state == "break"
    return {
        **common,
        "state": "analyzed",
        "compatibility_verdict": _STATE_VERDICT[spec.state],
        "gate": {
            "exit_code": 4 if breaking else 0,
            "blocking": breaking,
            "blocking_categories": ["abi_breaking"] if breaking else [],
            "from_report": True,
        },
        "report_path": f"{tid}.json",
        "library": "libunderthetest.so",
    }


def write_document(tmp_path: Path, spec: DocSpec) -> Path:
    """Materialise *spec* as a real aggregate document plus member reports."""
    for target in spec.targets:
        if target.state in ("clean", "additions", "break"):
            (tmp_path / f"{target.target_id}.json").write_text(
                json.dumps(_member_report(target.state)), encoding="utf-8"
            )
        elif target.state in ("not_comparable", "operational_error"):
            # A real file exists; the adapter must still refuse to itemize
            # it, because the document says no comparison happened.
            (tmp_path / f"{target.target_id}.json").write_text(
                json.dumps({"verdict": None, "reason": {"message": "nope"}}),
                encoding="utf-8",
            )
    missing_required = [
        t.target_id for t in spec.targets if t.required and t.state == "unavailable"
    ]
    exit_code = max(
        [4 if spec.expects_breaking else 0]
        + [
            1
            for t in spec.targets
            if t.state in ("not_comparable", "operational_error")
        ]
        + [1 if (missing_required and spec.missing_required_blocking) else 0]
        + [spec.contract_coverage_exit, spec.analysis_assurance_exit, spec.scope_exit]
    )
    document: dict[str, object] = {
        "aggregate_schema_version": "1.4",
        "status": "pass" if exit_code == 0 else "fail",
        "compatibility": {
            "verdict": "BREAKING" if spec.expects_breaking else "COMPATIBLE",
            "analyzed_targets": sum(
                1 for t in spec.targets if t.state not in ("unavailable",)
            ),
        },
        "coverage": {
            "status": "partial" if missing_required else "complete",
            "required_targets": sum(1 for t in spec.targets if t.required),
            "analyzed_required_targets": sum(
                1 for t in spec.targets if t.required and t.state != "unavailable"
            ),
            "missing_required_targets": missing_required,
            "blocking": bool(missing_required) and spec.missing_required_blocking,
        },
        "gate": {
            "passed": exit_code == 0,
            "exit_code": exit_code,
            "blocking_targets": sorted(
                t.target_id
                for t in spec.targets
                if t.state in ("break", "not_comparable", "operational_error")
            ),
            "coverage_blocking": bool(missing_required)
            and spec.missing_required_blocking,
        },
        "contract_coverage": {
            "exit_contribution": spec.contract_coverage_exit,
            "incomplete_targets": list(spec.contract_coverage_incomplete),
        },
        "analysis_assurance": {
            "exit_contribution": spec.analysis_assurance_exit,
            "incomplete_targets": list(spec.analysis_assurance_incomplete),
        },
        "scope_completeness": {
            "exit_contribution": spec.scope_exit,
            "incomplete_targets": list(spec.scope_incomplete),
        },
        "disposition_audit_missing_targets": [],
        "effective_policy": {
            "missing_required": "fail" if spec.missing_required_blocking else "warn",
            "unexpected_target": "include",
            "source": "manifest",
        },
        "targets": [_target_block(t) for t in spec.targets],
        "unexpected_targets": [],
        "profile_matrix": [],
        "finding_matrix": [],
    }
    path = tmp_path / "aggregate.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def model_for(tmp_path: Path, spec: DocSpec):
    path = write_document(tmp_path, spec)
    return build_model(
        json.loads(path.read_text(encoding="utf-8")), report_dir=path.parent
    )


# ---------------------------------------------------------------------------
# The class-level invariant
# ---------------------------------------------------------------------------


class TestNoFalseCleanOverTheStateCrossProduct:
    """Exhaustive enumeration, not a reproducer.

    Every ordered pair of target states is rendered, plus every axis
    shortfall on an otherwise-clean document. The assertions are written
    against :class:`DocSpec`'s own oracle properties, which are derived from
    the specification the document was generated from.
    """

    @pytest.mark.parametrize("first", TARGET_STATES)
    @pytest.mark.parametrize("second", TARGET_STATES)
    def test_pairwise_states(self, tmp_path: Path, first: str, second: str) -> None:
        spec = DocSpec(
            targets=(
                TargetSpec("alpha-target", first),
                TargetSpec("beta-target", second),
            )
        )
        model = model_for(tmp_path, spec)
        body = render_comment(model, sha="0123456789ab", detail="full")
        headline = _headline(body)

        # A complete oracle, not merely "it isn't clean": each branch names
        # the one headline the specification allows, so an implementation
        # that answered every document with the same wording fails here
        # rather than passing three of four branches by accident.
        if spec.expects_limitation:
            assert not any(phrase in headline for phrase in CLEAN_HEADLINES), (
                f"{first}/{second} rendered a clean headline: {headline}"
            )
        elif spec.expects_breaking:
            assert "ABI BREAKING" in headline, headline
        elif spec.expects_compatible_findings:
            assert "No compatibility impact detected" in headline, headline
        else:
            assert "No ABI changes" in headline, headline
        if spec.expects_limitation:
            assert model.has_incomplete, f"{first}/{second} showed no limitation"
            assert should_post(model, "changes"), (
                f"{first}/{second} would not post under --on=changes"
            )
            # Every target that established nothing must be named, so a
            # reviewer can tell *which* leg is unchecked.
            for target in spec.targets:
                if target.state in _NO_RESULT_STATES:
                    assert target.target_id in body
        if spec.expects_breaking:
            assert model.counts[0] >= 1
        # A not-comparable/operational-error leg is never reported as a
        # break, despite the synthetic BREAKING verdict the document carries
        # for it.
        synthetic_only = all(
            t.state in ("not_comparable", "operational_error", "clean")
            for t in spec.targets
        )
        if synthetic_only:
            assert model.counts[0] == 0, (
                "a leg that never reached a comparison was reported as a break"
            )

    @pytest.mark.parametrize(
        "axis_field,exit_field",
        [
            ("contract_coverage_incomplete", "contract_coverage_exit"),
            ("analysis_assurance_incomplete", "analysis_assurance_exit"),
            ("scope_incomplete", "scope_exit"),
        ],
    )
    @pytest.mark.parametrize("contribution", [0, 1])
    def test_each_axis_shortfall_is_visible_gated_or_not(
        self, tmp_path: Path, axis_field: str, exit_field: str, contribution: int
    ) -> None:
        """An axis accepted under a ``warn`` policy contributes 0 and must
        still be reported. Gating visibility on the contribution is how an
        accepted gap becomes an invisible one (ADR-049 Section 6.2)."""
        base = DocSpec(targets=(TargetSpec("alpha-target", "clean"),))
        spec = replace(
            base,
            **{axis_field: ("alpha-target",), exit_field: contribution},
        )
        model = model_for(tmp_path, spec)
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert model.has_incomplete
        assert should_post(model, "changes")
        assert not any(phrase in _headline(body) for phrase in CLEAN_HEADLINES)
        # Blocking-ness follows the document's own contribution, never the
        # mere existence of the shortfall.
        assert model.incomplete_blocking is (contribution == 1)

    def test_all_clean_document_posts_nothing_under_on_changes(
        self, tmp_path: Path
    ) -> None:
        spec = DocSpec(
            targets=(
                TargetSpec("alpha-target", "clean"),
                TargetSpec("beta-target", "clean"),
                TargetSpec("gamma-target", "clean"),
            )
        )
        model = model_for(tmp_path, spec)
        assert spec.is_fully_clean
        assert not should_post(model, "changes")
        assert should_post(model, "always")
        body = render_comment(model, sha="0123456789ab")
        assert any(phrase in _headline(body) for phrase in CLEAN_HEADLINES)

    def test_additions_only_is_not_reported_as_breaking(self, tmp_path: Path) -> None:
        spec = DocSpec(
            targets=(
                TargetSpec("alpha-target", "additions"),
                TargetSpec("beta-target", "additions"),
            )
        )
        model = model_for(tmp_path, spec)
        assert model.counts == (0, 0, 2)
        assert should_post(model, "changes")
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "Public API additions" in body
        assert "ABI BREAKING" not in body

    def test_a_document_with_no_analyzed_target_never_reads_as_clean(
        self, tmp_path: Path
    ) -> None:
        """ADR-065 D7's rule, one level up: a fan-in in which nothing was
        compared may not render "no ABI changes"."""
        spec = DocSpec(
            targets=(
                TargetSpec("alpha-target", "unavailable"),
                TargetSpec("beta-target", "unavailable"),
            )
        )
        model = model_for(tmp_path, spec)
        assert model.no_comparison_completed
        body = render_comment(model, sha="0123456789ab")
        assert "No comparison completed" in _headline(body)

    def test_resolved_from_previous_break_now_posts_nothing(
        self, tmp_path: Path
    ) -> None:
        """A PR that fixed its break: the same document shape, now clean,
        must produce no comment under ``--on=changes`` so a sticky publisher
        can clear the stale one instead of updating it with a false result."""
        broken = model_for(
            tmp_path / "before",
            DocSpec(targets=(TargetSpec("alpha-target", "break"),)),
        )
        fixed = model_for(
            tmp_path / "after",
            DocSpec(targets=(TargetSpec("alpha-target", "clean"),)),
        )
        assert should_post(broken, "changes")
        assert not should_post(fixed, "changes")

    @pytest.fixture(autouse=True)
    def _subdirs(self, tmp_path: Path) -> None:
        (tmp_path / "before").mkdir(exist_ok=True)
        (tmp_path / "after").mkdir(exist_ok=True)


class TestPerTargetAttribution:
    """A folded finding stays attributable to the target that reported it."""

    def test_same_symbol_from_several_targets_stays_several_rows(
        self, tmp_path: Path
    ) -> None:
        spec = DocSpec(
            targets=tuple(TargetSpec(f"target-{i}", "break") for i in range(3))
        )
        model = model_for(tmp_path, spec)
        # Every member report names the same symbol on purpose: this is the
        # cross-platform matrix case aggregate exists for.
        assert [f.symbol for f in model.breaking] == ["shared_entry"] * 3
        assert sorted(f.component for f in model.breaking) == [
            "target-0",
            "target-1",
            "target-2",
        ]
        body = render_comment(model, sha="0123456789ab", detail="standard")
        for i in range(3):
            assert f"target-{i}" in body

    def test_component_is_part_of_the_grouping_key(self, tmp_path: Path) -> None:
        """Standard detail rolls findings up by enclosing API. Without the
        component in that key, three targets' reports of one symbol collapse
        into a single row whose member list repeats the name and names no
        target at all."""
        spec = DocSpec(
            targets=tuple(TargetSpec(f"target-{i}", "break") for i in range(3))
        )
        model = model_for(tmp_path, spec)
        body = render_comment(model, sha="0123456789ab", detail="standard")
        # Each target contributes its own row rather than one merged row.
        assert body.count("`shared_entry`") >= 3


# ---------------------------------------------------------------------------
# Member-report loading — the untrusted-input boundary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Size bounding over a folded model
# ---------------------------------------------------------------------------


def _wide_document(tmp_path: Path, n_targets: int, per_target: int, symbol: str):
    """A document whose members carry *per_target* findings each."""
    targets = []
    for i in range(n_targets):
        tid = f"target-{i}"
        (tmp_path / f"{tid}.json").write_text(
            json.dumps(
                {
                    "library": "libwide.so",
                    "old_version": "baseline",
                    "new_version": "candidate",
                    "verdict": "BREAKING",
                    "changes": [
                        {
                            "kind": "func_removed",
                            "symbol": f"{symbol}_{i}_{j}",
                            "severity": "breaking",
                            "description": f"{symbol} removed ({i}, {j})",
                            "source_location": f"include/{symbol}.h:{j}",
                        }
                        for j in range(per_target)
                    ],
                }
            ),
            encoding="utf-8",
        )
        targets.append(
            {
                "target_id": tid,
                "required": True,
                "state": "analyzed",
                "compatibility_verdict": "BREAKING",
                "gate": {
                    "exit_code": 4,
                    "blocking": True,
                    "blocking_categories": ["abi_breaking"],
                    "from_report": True,
                },
                "contract_coverage_exit": 0,
                "analysis_assurance_exit": 0,
                "scope_completeness_exit": 0,
                "report_path": f"{tid}.json",
            }
        )
    document = {
        "aggregate_schema_version": "1.4",
        "status": "fail",
        "compatibility": {"verdict": "BREAKING", "analyzed_targets": n_targets},
        "coverage": {
            "status": "complete",
            "required_targets": n_targets,
            "analyzed_required_targets": n_targets,
            "missing_required_targets": [],
            "blocking": False,
        },
        "gate": {
            "passed": False,
            "exit_code": 4,
            "blocking_targets": [t["target_id"] for t in targets],
            "coverage_blocking": False,
        },
        "contract_coverage": {"exit_contribution": 0, "incomplete_targets": []},
        "analysis_assurance": {"exit_contribution": 0, "incomplete_targets": []},
        "scope_completeness": {"exit_contribution": 0, "incomplete_targets": []},
        "disposition_audit_missing_targets": [],
        "effective_policy": {
            "missing_required": "fail",
            "unexpected_target": "include",
            "source": "default",
        },
        "targets": targets,
        "unexpected_targets": [],
        "profile_matrix": [],
        "finding_matrix": [],
    }
    path = tmp_path / "aggregate.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return build_model(document, report_dir=path.parent)


class TestFoldedModelStaysWithinTheCommentBudget:
    @pytest.mark.parametrize("detail", ["summary", "standard", "full"])
    def test_large_folded_finding_set_fits(self, tmp_path: Path, detail: str) -> None:
        model = _wide_document(tmp_path, n_targets=8, per_target=400, symbol="entry")
        assert model.counts[0] == 3200
        body = render_comment(model, sha="0123456789ab", detail=detail)
        assert len(body) <= GITHUB_COMMENT_LIMIT
        # Shortening never costs the reader the identity, the counts, or the
        # fact that something was left out.
        assert "abicheck —" in body
        assert "3200 breaking" in body or "**3200 breaking**" in body

    def test_multibyte_text_near_the_limit_is_not_corrupted(
        self, tmp_path: Path
    ) -> None:
        """A folded model full of non-ASCII symbols still renders cleanly.

        The character/byte distinction matters here: this body is well under
        the character limit while being several KB larger in UTF-8 bytes, so
        a consumer bounding it in bytes (the publisher Action does) is
        bounding a different number than the renderer is.
        """
        model = _wide_document(
            tmp_path, n_targets=6, per_target=900, symbol="關數_ünïcödé_λ"
        )
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert len(body) <= GITHUB_COMMENT_LIMIT
        assert "\ufffd" not in body
        # Round-trips as UTF-8 with no lone surrogate or partial sequence.
        assert body.encode("utf-8").decode("utf-8") == body
        assert "關數" in body
        # The premise the publisher's own byte bound rests on: characters
        # and bytes genuinely diverge for this content.
        assert len(body.encode("utf-8")) > len(body)

    @pytest.mark.parametrize("budget", [400, 1000, 4000, 12000])
    def test_hard_truncation_never_splits_a_codepoint(
        self, tmp_path: Path, budget: int
    ) -> None:
        """The last-resort cut, exercised directly at budgets the ordinary
        row-budget plan never reaches.

        Driving it through :func:`render_comment` alone would not reach this
        path -- the shortening plan fits a summary-level body long before
        the hard cut applies -- so the primitive is exercised as one, with
        multibyte content, across several budgets.
        """
        model = _wide_document(
            tmp_path, n_targets=4, per_target=300, symbol="關數_ünïcödé_λ"
        )
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert len(body) > budget, "premise: the body must exceed the budget"
        cut = _truncate_to_budget(body, "https://example.invalid/r/1", None, budget)
        assert len(cut) <= budget
        assert "\ufffd" not in cut
        assert cut.encode("utf-8").decode("utf-8") == cut
        assert "truncated to fit" in cut

    def test_shortening_is_disclosed_never_presented_as_an_empty_set(
        self, tmp_path: Path
    ) -> None:
        """A body that had to shrink says so, and keeps the exact total."""
        model = _wide_document(tmp_path, n_targets=10, per_target=2000, symbol="entry")
        body = render_comment(
            model,
            sha="0123456789ab",
            detail="full",
            report_url="https://example.invalid/run/1",
        )
        assert len(body) <= GITHUB_COMMENT_LIMIT
        assert "20000 breaking" in body
        assert "truncated" in body or "Shortened" in body or "not shown" in body


# ---------------------------------------------------------------------------
# `fold_change_summaries` — a reusable merge primitive, tested as one
# ---------------------------------------------------------------------------


def _summary(**counts: int) -> ChangeSummary:
    rows = tuple(
        EntityRow(entity=e, label=e.title(), removed=r, modified=m, added=a)
        for e, (r, m, a) in counts.items()  # type: ignore[misc]
    )
    return ChangeSummary(rows=rows, counted=sum(r.total for r in rows))


class TestFoldChangeSummariesProperties:
    """The merge primitive's contract, stated as invariants.

    Per AGENTS.md's "Primitive-level property tests": a reusable merge gets
    its own contract tests, decoupled from the one caller's domain, because
    a test written to confirm the fix just made only encodes the bug its
    author already thought of.
    """

    @staticmethod
    def _rand_summaries(seed: int, n: int) -> list[ChangeSummary]:
        import random

        rng = random.Random(seed)
        entities = ["function", "variable", "type", "enum", "binary"]
        out = []
        for _ in range(n):
            picked = rng.sample(entities, rng.randint(1, len(entities)))
            out.append(
                _summary(
                    **{
                        e: (rng.randint(0, 5), rng.randint(0, 5), rng.randint(0, 5))
                        for e in picked
                    }
                )
            )
        return out

    @pytest.mark.parametrize("seed", range(25))
    def test_counts_are_the_plain_sum_of_the_inputs(self, seed: int) -> None:
        parts = self._rand_summaries(seed, 4)
        folded = fold_change_summaries(parts)
        assert folded.counted == sum(p.counted for p in parts)
        for op in ("removed", "modified", "added"):
            assert sum(getattr(r, op) for r in folded.rows) == sum(
                getattr(r, op) for p in parts for r in p.rows
            )

    @pytest.mark.parametrize("seed", range(25))
    def test_the_result_never_depends_on_input_order(self, seed: int) -> None:
        import random

        parts = self._rand_summaries(seed, 5)
        shuffled = list(parts)
        random.Random(seed + 1000).shuffle(shuffled)
        assert fold_change_summaries(parts) == fold_change_summaries(shuffled)

    @pytest.mark.parametrize("position", range(4))
    def test_one_inexact_input_makes_the_whole_fold_inexact(
        self, position: int
    ) -> None:
        parts = self._rand_summaries(7, 4)
        parts[position] = ChangeSummary(
            rows=parts[position].rows,
            exact=False,
            inexact_reason="sampled upstream",
            counted=parts[position].counted,
        )
        folded = fold_change_summaries(parts)
        assert folded.exact is False
        assert folded.inexact_reason

    def test_all_exact_inputs_fold_to_an_exact_result(self) -> None:
        folded = fold_change_summaries(self._rand_summaries(3, 4))
        assert folded.exact is True
        assert folded.inexact_reason == ""

    def test_empty_input_folds_to_an_empty_exact_summary(self) -> None:
        folded = fold_change_summaries([])
        assert folded.is_empty
        assert folded.exact
        assert folded.counted == 0

    def test_folding_one_summary_reproduces_it(self) -> None:
        only = summarize_changes(
            [
                {"kind": "func_removed", "symbol": "a"},
                {"kind": "func_added", "symbol": "b"},
            ]
        )
        assert fold_change_summaries([only]).rows == only.rows

    def test_oracle_is_not_vacuous(self) -> None:
        """Guard on the generator itself: a fixture that produced empty
        summaries would make every invariant above pass while asserting
        nothing."""
        parts = self._rand_summaries(11, 6)
        assert sum(p.counted for p in parts) > 0
        assert any(p.rows for p in parts)


# ---------------------------------------------------------------------------
# The document's own bookkeeping, folded rather than recomputed
# ---------------------------------------------------------------------------


def _minimal_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "aggregate_schema_version": "1.4",
        "status": "pass",
        "compatibility": {"verdict": "COMPATIBLE", "analyzed_targets": 0},
        "coverage": {
            "status": "complete",
            "required_targets": 0,
            "analyzed_required_targets": 0,
            "missing_required_targets": [],
            "blocking": False,
        },
        "gate": {
            "passed": True,
            "exit_code": 0,
            "blocking_targets": [],
            "coverage_blocking": False,
        },
        "contract_coverage": {"exit_contribution": 0, "incomplete_targets": []},
        "analysis_assurance": {"exit_contribution": 0, "incomplete_targets": []},
        "scope_completeness": {"exit_contribution": 0, "incomplete_targets": []},
        "disposition_audit_missing_targets": [],
        "effective_policy": {
            "missing_required": "fail",
            "unexpected_target": "include",
            "source": "default",
        },
        "targets": [],
        "unexpected_targets": [],
        "profile_matrix": [],
        "finding_matrix": [],
    }
    document.update(overrides)
    return document


def _analyzed_target(tid: str, report_path: str, verdict: str = "BREAKING"):
    return {
        "target_id": tid,
        "required": True,
        "state": "analyzed",
        "compatibility_verdict": verdict,
        "gate": {
            "exit_code": 4 if verdict == "BREAKING" else 0,
            "blocking": verdict == "BREAKING",
            "blocking_categories": ["abi_breaking"] if verdict == "BREAKING" else [],
            "from_report": True,
        },
        "contract_coverage_exit": 0,
        "analysis_assurance_exit": 0,
        "scope_completeness_exit": 0,
        "report_path": report_path,
    }


class TestDocumentFactsAreReadNotRecomputed:
    def test_the_documents_own_disposition_audit_is_carried_verbatim(
        self, tmp_path: Path
    ) -> None:
        """Re-folding it over the member reports would double-count every
        target whose audit the document already absorbed."""
        audit = {"raw_total": 12, "effective_total": 3, "by_disposition": {}}
        document = _minimal_document(disposition_audit=audit)
        model = build_model(document, report_dir=tmp_path)
        assert model.disposition_audit == audit

    def test_an_unexpected_target_is_reported_rather_than_dropped(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "extra.json").write_text(
            json.dumps(_member_report("break")), encoding="utf-8"
        )
        target = _analyzed_target("new-leg", "extra.json")
        target["unexpected"] = True
        document = _minimal_document(unexpected_targets=[target])
        model = build_model(document, report_dir=tmp_path)
        assert model.counts[0] == 1
        assert any(
            "not in the declared expected set" in f.detail for f in model.incomplete
        )

    def test_an_unexpected_target_under_a_fail_policy_blocks(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "extra.json").write_text(
            json.dumps(_member_report("clean")), encoding="utf-8"
        )
        target = _analyzed_target("new-leg", "extra.json", verdict="NO_CHANGE")
        target["unexpected"] = True
        document = _minimal_document(
            unexpected_targets=[target],
            effective_policy={
                "missing_required": "fail",
                "unexpected_target": "fail",
                "source": "manifest",
            },
        )
        model = build_model(document, report_dir=tmp_path)
        assert model.incomplete_blocking is True

    def test_an_unavailable_target_carrying_a_forced_gate_says_so(
        self, tmp_path: Path
    ) -> None:
        """A run that aborted (a budget overflow, an evidence-contract
        refusal) publishes a real gate decision with no comparison behind
        it. "Unavailable" alone would under-report a leg actively failing
        the run."""
        document = _minimal_document(
            targets=[
                {
                    "target_id": "aborted-leg",
                    "required": True,
                    "state": "unavailable",
                    "compatibility_verdict": None,
                    "gate": {
                        "exit_code": 1,
                        "blocking": True,
                        "blocking_categories": [],
                        "from_report": True,
                    },
                    "contract_coverage_exit": 0,
                    "analysis_assurance_exit": 0,
                    "scope_completeness_exit": 0,
                    "reason": "the scan aborted on its evidence contract",
                }
            ]
        )
        model = build_model(document, report_dir=tmp_path)
        assert model.incomplete_blocking is True
        assert any("blocking gate" in f.detail for f in model.incomplete)

    def test_targets_with_no_disposition_audit_are_named(self, tmp_path: Path) -> None:
        document = _minimal_document(
            disposition_audit_missing_targets=["legacy-leg", "other-leg"]
        )
        model = build_model(document, report_dir=tmp_path)
        details = " ".join(f.detail for f in model.incomplete)
        assert "legacy-leg" in details and "other-leg" in details

    def test_a_blocking_gate_nothing_else_explains_still_reports(
        self, tmp_path: Path
    ) -> None:
        """The adapter's one unconditional guarantee: a document whose gate
        failed never renders as a clean comment, even when no axis this
        build knows about accounts for it."""
        document = _minimal_document(
            status="fail",
            gate={
                "passed": False,
                "exit_code": 9,
                "blocking_targets": ["mystery-leg"],
                "coverage_blocking": False,
            },
        )
        model = build_model(document, report_dir=tmp_path)
        assert model.has_incomplete
        assert model.incomplete_blocking
        assert should_post(model, "changes")
        assert "mystery-leg" in " ".join(f.detail for f in model.incomplete)

    def test_a_members_own_evidence_limitation_is_surfaced_per_target(
        self, tmp_path: Path
    ) -> None:
        member = _member_report("clean")
        member["coverage_warnings"] = [
            "No header/AST data; type-level changes may be missed",
            "A second recorded limit",
        ]
        (tmp_path / "leg.json").write_text(json.dumps(member), encoding="utf-8")
        document = _minimal_document(
            targets=[_analyzed_target("leg", "leg.json", verdict="NO_CHANGE")]
        )
        model = build_model(document, report_dir=tmp_path)
        details = " ".join(f.detail for f in model.incomplete)
        assert "No header/AST data" in details
        assert "+1 more" in details

    def test_the_rollup_is_marked_inexact_when_a_target_could_not_be_itemized(
        self, tmp_path: Path
    ) -> None:
        """One readable member and one that is not: the entity rollup counts
        only what it could read, so it must not present itself as a total."""
        (tmp_path / "readable.json").write_text(
            json.dumps(_member_report("break")), encoding="utf-8"
        )
        document = _minimal_document(
            targets=[
                _analyzed_target("readable", "readable.json"),
                _analyzed_target("gone", "not-written.json"),
            ]
        )
        model = build_model(document, report_dir=tmp_path)
        assert model.change_summary is not None
        assert model.change_summary.exact is False
        assert "could not be itemized" in model.change_summary.inexact_reason
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "Not exact totals" in body

    @pytest.mark.parametrize(
        "verdict,expected",
        [
            ("BREAKING", (1, 0, 0)),
            ("API_BREAK", (0, 1, 0)),
            ("COMPATIBLE_WITH_RISK", (0, 1, 0)),
            ("COMPATIBLE", (0, 0, 1)),
            ("NO_CHANGE", (0, 0, 0)),
            ("SOMETHING_NEW", (0, 1, 0)),
            ("", (0, 1, 0)),
        ],
    )
    def test_an_unitemizable_target_contributes_a_lower_bound_never_a_zero(
        self, tmp_path: Path, verdict: str, expected: tuple[int, int, int]
    ) -> None:
        """A row of zeros beside a non-clean verdict would let the headline
        read "no ABI changes" while the table under it shows a break. Stated
        over the whole verdict vocabulary, including one this build does not
        know, rather than the one that prompted it."""
        document = _minimal_document(
            targets=[_analyzed_target("gone", "not-written.json", verdict=verdict)]
        )
        document["targets"][0]["compatibility_verdict"] = verdict or None
        model = build_model(document, report_dir=tmp_path)
        assert model.counts == expected
        assert model.has_incomplete
