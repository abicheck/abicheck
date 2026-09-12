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

"""``action/report_query.py``: what counts as a readable verdict.

Sibling of ``test_action_report_query.py``, which owns the reader's query
surface. This module owns one question: *which strings and shapes may be read as
a result at all*, and does that vocabulary still track the code that emits it.

Both directions matter, and the second is the one that bites. A vocabulary too
*wide* lets a document that answers nothing license a clean compatibility claim
(`{"verdict": "write interrupted"}`, a `libraries` array nothing reads, an
operational sentinel the exit-0 dispatcher cannot act on). A vocabulary too
*narrow* makes a working report read as unusable and fails healthy runs — the
same failure mode this PR already hit twice with the assurance schema
thresholds. So every set here is derived from the producing code and asserted
against it, never restated as literals.

Bug class: ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import pytest
from _action_report_reader import rq


class TestTheVerdictVocabularyTracksTheRealEmitters:
    """`KNOWN_VERDICTS` must stay a superset of what abicheck actually emits.

    Validating against a vocabulary replaces "any non-empty string" and closes
    the `{"verdict": "write interrupted"}` case, but it introduces the opposite
    failure mode: a verdict value added to the CLI and not added here would
    make a *working* report read as unusable and fail real runs. That is the
    same shape as the audit/release schema-threshold misses earlier in this PR
    -- a table that must track code, pinned by a test rather than by a comment
    asking the next author to remember.

    Derived from the producing code on both sides, so the check cannot be
    satisfied by copying a literal into two places.
    """

    def test_every_compatibility_tier_is_known(self) -> None:
        from abicheck.checker import Verdict

        missing = {v.value for v in Verdict} - rq.KNOWN_VERDICTS
        assert not missing, (
            f"emitted by Verdict but unreadable to the Action: {missing}"
        )

    def test_every_release_rollup_verdict_is_known(self) -> None:
        from abicheck.cli_compare_release_helpers import _RELEASE_VERDICT_ORDER

        missing = set(_RELEASE_VERDICT_ORDER) - rq.KNOWN_VERDICTS
        assert not missing, f"emitted by the release fan-out but unreadable: {missing}"

    def test_the_release_scope_states_are_known(self) -> None:
        # Derived from the emitter rather than restated as literals
        # (CodeRabbit review): `workflows/release_scope.py` writes these
        # per-library sentinels directly, not through the order table above, so
        # a key added there must land in `KNOWN_VERDICTS` or a working release
        # report reads as `no_result` and the Action fails a healthy run.
        from abicheck.workflows.release_scope import RELEASE_OPERATIONAL_VERDICTS

        missing = set(RELEASE_OPERATIONAL_VERDICTS) - rq.KNOWN_VERDICTS
        assert not missing, f"emitted by release_scope but unreadable: {missing}"

    def test_every_operational_sentinel_is_known(self) -> None:
        from abicheck.cli_compare_release_helpers import (
            _RELEASE_OPERATIONAL_SENTINELS,
        )

        missing = _RELEASE_OPERATIONAL_SENTINELS - rq.KNOWN_VERDICTS
        assert not missing, f"emitted as a release sentinel but unreadable: {missing}"


class TestTheOperationalSetTracksTheEngine:
    """`OPERATIONAL_VERDICTS` must cover every sentinel the engine treats so.

    These values are *known* verdicts — real emitter output — but they are not
    compatibility results, so the Action must not let one become a clean claim.
    The engine already separates them for the same reason
    (`_release_completed_compatibility_verdict` excludes them from
    `run_outcome.compatibility`), so this set is derived from both emitter sets
    rather than restated: a sentinel added there and missed here would silently
    publish COMPATIBLE again, which is the defect this exists to prevent.
    """

    def test_it_covers_the_release_sentinels(self) -> None:
        from abicheck.cli_compare_release_helpers import (
            _RELEASE_OPERATIONAL_SENTINELS,
        )

        missing = _RELEASE_OPERATIONAL_SENTINELS - rq.OPERATIONAL_VERDICTS
        assert not missing, f"engine treats as operational, Action does not: {missing}"

    def test_it_covers_the_release_scope_states(self) -> None:
        from abicheck.workflows.release_scope import RELEASE_OPERATIONAL_VERDICTS

        missing = set(RELEASE_OPERATIONAL_VERDICTS) - rq.OPERATIONAL_VERDICTS
        assert not missing, f"engine treats as operational, Action does not: {missing}"

    def test_every_operational_verdict_is_also_a_known_verdict(self) -> None:
        # Otherwise the reader would reject the document before the dispatcher
        # ever saw the sentinel, and the diagnostic would misdescribe it.
        assert rq.OPERATIONAL_VERDICTS <= rq.KNOWN_VERDICTS

    def test_no_real_compatibility_tier_is_operational(self) -> None:
        # The control: a tier landing in this set would make a genuine verdict
        # fail the step as an operational failure.
        from abicheck.checker import Verdict

        overlap = {v.value for v in Verdict} & rq.OPERATIONAL_VERDICTS
        assert not overlap, overlap

    def test_the_vocabulary_is_not_merely_everything(self) -> None:
        # Without this the two assertions above are satisfiable by accepting
        # any string, which is the defect the vocabulary replaced.
        for outsider in ("write interrupted", "compatible", "OK", ""):
            assert outsider not in rq.KNOWN_VERDICTS, outsider


#: A valid `verdict: null` beside the structural evidence, per shape -- what the
#: real emitters write (`report/not_comparable.py`, `report/no_baseline.py`).
REAL_NULL_VERDICT_SHAPES = (
    ("not_comparable", {"verdict": None, "reason": {"kind": "k", "message": "m"}}),
    (
        "audit",
        {
            "verdict": None,
            "no_baseline": True,
            "findings": [],
            "suppressed_findings": [],
        },
    ),
)


class TestStructuralFallbacksCannotBypassTheVocabulary:
    """`verdict: null` is required by the two structural rules, not just present.

    Both rules exist because their emitters write a literal `null` there and put
    the result somewhere else (a `reason` object; the two audit arrays). Keyed on
    mere *presence*, they re-admitted exactly what the vocabulary check rejects:
    `{"verdict": "write interrupted", "reason": {}}` passed on the strength of
    the `reason` object alone, and the unusable string then reached the
    COMPATIBLE fallthrough (Codex review, P2).

    Requiring `is None` is faithful to the emitters rather than stricter than
    them, which is why the real shapes are asserted alongside.
    """

    @pytest.mark.parametrize(
        "label,document",
        REAL_NULL_VERDICT_SHAPES,
        ids=[s[0] for s in REAL_NULL_VERDICT_SHAPES],
    )
    def test_the_real_null_verdict_shape_still_reads(
        self, label: str, document: dict
    ) -> None:
        assert rq._carries_a_result(document), label

    @pytest.mark.parametrize(
        "label,document",
        REAL_NULL_VERDICT_SHAPES,
        ids=[s[0] for s in REAL_NULL_VERDICT_SHAPES],
    )
    @pytest.mark.parametrize("bad", ("write interrupted", "OK", "", "0"))
    def test_a_non_null_unknown_verdict_is_not_rescued_by_the_structure(
        self, label: str, document: dict, bad: str
    ) -> None:
        assert not rq._carries_a_result({**document, "verdict": bad}), (label, bad)

    @pytest.mark.parametrize(
        "label,document",
        REAL_NULL_VERDICT_SHAPES,
        ids=[s[0] for s in REAL_NULL_VERDICT_SHAPES],
    )
    def test_a_real_verdict_beside_the_structure_still_reads(
        self, label: str, document: dict
    ) -> None:
        # Not a regression of the rule above: a *known* verdict is read by the
        # vocabulary rule that runs first, structure or no structure.
        assert rq._carries_a_result({**document, "verdict": "BREAKING"}), label


class TestALibraryArrayIsNotAVerdictSource:
    """`{"libraries": [...]}` alone must not be admitted.

    It was, and nothing reads it: neither `compat_verdict` here nor `run.sh`'s
    `_report_compat_verdict` looks at `libraries`, so admitting a library-only
    document licensed COMPATIBLE for a release whose members said `BREAKING`
    (Codex review, P2). A rollup was not the answer either --
    `cli_compare_release_helpers._format_release_json` emits a top-level
    `"verdict": worst_verdict`, already rolled up, on *every* release document,
    so a `libraries` array with no readable verdict beside it is not a shape any
    emitter produces.
    """

    @pytest.mark.parametrize(
        "document",
        (
            {"libraries": []},
            {"libraries": [{"verdict": "BREAKING"}]},
            {"libraries": [{"library": "libfoo"}]},
            {"release_schema_version": "1.3", "libraries": [{"verdict": "API_BREAK"}]},
        ),
    )
    def test_a_library_only_document_carries_no_result(self, document: dict) -> None:
        assert not rq._carries_a_result(document), document

    def test_the_real_release_envelope_still_reads(self) -> None:
        # The control, and the reason rejecting the rule is safe: a real release
        # document always carries the rolled-up verdict itself.
        assert rq._carries_a_result(
            {
                "release_schema_version": "1.3",
                "verdict": "BREAKING",
                "libraries": [{"library": "libfoo", "verdict": "BREAKING"}],
            }
        )

    def test_a_release_envelope_reporting_through_run_outcome_reads(self) -> None:
        assert rq._carries_a_result(
            {
                "release_schema_version": "1.3",
                "verdict": None,
                "libraries": [],
                "run_outcome": {"compatibility": "NO_CHANGE"},
            }
        )


#: Every value the `verdict` slot has been observed or proposed to hold.
_VERDICT_SLOTS = (
    None,
    "NO_CHANGE",
    "COMPATIBLE",
    "COMPATIBLE_WITH_RISK",
    "API_BREAK",
    "BREAKING",
    "ERROR",
    "not_comparable",
    "unsupported",
    "failed",
    "UNKNOWN",
    "write interrupted",
    "",
)

#: Every `run_outcome` shape, including the malformed ones.
_RUN_OUTCOMES = (
    None,
    {},
    {"operational": "none"},
    {"operational": "extraction_error"},
    {"operational": "not_comparable"},
    {"compatibility": "BREAKING"},
    {"compatibility": "BREAKING", "operational": "none"},
    {"compatibility": "ERROR"},
    {"compatibility": "write interrupted"},
    {"compatibility": None, "operational": "none"},
)

#: The structural extras that make a null verdict meaningful.
_STRUCTURES = (
    {},
    {"reason": {"kind": "k", "message": "m"}},
    {"no_baseline": True, "findings": [], "suppressed_findings": []},
    {"no_baseline": True, "findings": [{"kind": "x"}], "suppressed_findings": []},
    {"libraries": []},
    {"libraries": [{"library": "libfoo", "verdict": "BREAKING"}]},
)


def _documents():
    """The cross-product, as ``(label, document)`` pairs."""
    for verdict in _VERDICT_SLOTS:
        for outcome in _RUN_OUTCOMES:
            for structure in _STRUCTURES:
                doc: dict = {"report_schema_version": "4.4", **structure}
                if verdict is not None or "reason" in structure:
                    doc["verdict"] = verdict
                if outcome is not None:
                    doc["run_outcome"] = outcome
                yield (f"v={verdict!r} ro={outcome!r} st={sorted(structure)}", doc)


class TestAdmissionImpliesAnswerability:
    """The one invariant behind every escape in this area: if the reader admits a
    document as carrying a result, some query must be able to *say what*.

    This is the generalization the incremental fixes kept missing. Five separate
    review findings on this PR were all the same shape — a document
    `_carries_a_result` accepted, whose result no query then returned, leaving
    `_resolve_clean_exit_verdict` at its initial `COMPATIBLE`:

    * any non-empty string in `verdict`;
    * a `libraries` array nothing reads;
    * a structural fallback keyed on a present rather than null `verdict`;
    * an operational sentinel the exit-0 dispatcher did not handle;
    * the real not-comparable shape, whose `reason` no query consumed;
    * and `run_outcome.compatibility` holding an operational value.

    Each was fixed for its own shape. None of those fixes could fail for the
    *next* shape, because the property was never stated. It is stated here, over
    a generated cross-product rather than the reported inputs, so the next
    admission rule added without a matching consumer fails immediately.

    Bug class: ``report.unestablished_result_reads_as_success``.
    """

    def _answers(self, document: dict) -> dict[str, str]:
        return {
            query: (rq.answer(document, query) or "")
            for query in ("compat_verdict", "operational_verdict", "no_baseline_audit")
        }

    @pytest.mark.parametrize("label,document", list(_documents()), ids=lambda v: None)
    def test_an_admitted_document_can_be_acted_on(
        self, label: str, document: dict
    ) -> None:
        if not rq._carries_a_result(document):
            return  # rejected: the dispatcher never reaches a verdict from it
        answers = self._answers(document)
        assert any(answers.values()), (
            f"admitted but unanswerable, so the dispatcher would keep its "
            f"initial COMPATIBLE: {label} -> {answers}"
        )

    @pytest.mark.parametrize("label,document", list(_documents()), ids=lambda v: None)
    def test_a_clean_answer_requires_a_clean_tier(
        self, label: str, document: dict
    ) -> None:
        # The other half: whatever `compat_verdict` answers must be a real
        # compatibility tier. An operational sentinel arriving here is what made
        # the dispatcher treat "nothing was compared" as a tier it could ignore.
        answer = rq.answer(document, "compat_verdict") or ""
        if not answer:
            return
        assert answer in rq.KNOWN_VERDICTS, (label, answer)
        if answer in rq.OPERATIONAL_VERDICTS:
            # Permitted only when the operational query names it too, so the
            # dispatcher sees the operational fact rather than a bare tier.
            assert rq.answer(document, "operational_verdict"), (label, answer)

    def test_the_matrix_actually_exercises_both_outcomes(self) -> None:
        # Without this the two tests above are vacuous if every generated
        # document happened to be rejected (or every one admitted).
        admitted = [d for _, d in _documents() if rq._carries_a_result(d)]
        rejected = [d for _, d in _documents() if not rq._carries_a_result(d)]
        assert admitted, "no document was admitted"
        assert rejected, "no document was rejected"
