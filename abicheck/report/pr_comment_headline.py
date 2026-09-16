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

"""The sticky PR comment's headline -- the one line a reviewer actually reads.

Split out of ``pr_comment_render.py``, which had grown past the architecture
gate's production file ceiling. The seam is a real responsibility boundary
rather than a line-count convenience: :func:`headline` decides *what this
comment claims*, in strict priority order, from the model alone; everything
left in the renderer decides *how the body is laid out and how much of it
fits*. They change for different reasons -- the first when the compatibility
or gate vocabulary changes, the second when a size budget or a table format
does -- and the headline is the part that must never overstate, so it earns
its own module and its own reading.

The priority order is the whole design and is stated in :func:`headline`'s
own body: a genuine break outranks an incompletely-checked analysis, which
outranks an advisory review finding, which outranks a coverage note, which
outranks "nothing to report". Every branch either names a fact the model
carries or falls through; none infers a clean result from an empty bucket.
"""

from __future__ import annotations

from ..pr_comment_base import CommentModel

_VERDICT_EMOJI = {
    "BREAKING": "❌",
    "API_BREAK": "⚠️",
    "COMPATIBLE_WITH_RISK": "⚠️",
    "COMPATIBLE": "✅",
    "NO_CHANGE": "✅",
    "ERROR": "🛑",
    "unsupported": "🚫",
    # `aggregate`'s two non-comparison states (see
    # `report/pr_comment_aggregate.NON_COMPARISON_GATE_CATEGORIES`): a leg
    # that never reached a comparison at all. Deliberately not ❌ -- nothing
    # about its compatibility was observed, so an ABI-break glyph would
    # assert the one thing that run could not establish.
    "NOT_COMPARABLE": "🛑",
    "OPERATIONAL_ERROR": "🛑",
}


#: Reviewer-facing headline for a Breaking bucket whose members are *only*
#: policy-gated COMPATIBLE findings (a severity-config category promoted to
#: ``error``) — not a genuine ABI/API incompatibility. Keyed by the exact
#: frozenset of categories responsible; a bucket that also holds a genuine
#: ``abi_breaking``/``potential_breaking`` finding never reaches this table
#: (see ``_header``), since that finding *is* accurately a break.
_POLICY_ONLY_HEADER: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"addition"}): ("⛔", "Public API expansion requires approval"),
    frozenset({"quality_issues"}): ("⛔", "Quality policy violation"),
    frozenset({"addition", "quality_issues"}): (
        "⛔",
        "Policy violation blocks this PR",
    ),
}


def _pre_comparison_headline(model: CommentModel) -> tuple[str, str] | None:
    """The headline for a model whose buckets cannot speak for the run.

    Four cases, in priority order, that all share one property: the
    breaking/review/safe counts below are empty or unrepresentative *by
    construction*, so reading them would report "we compared and found
    nothing" for a run that compared nothing. ``None`` means the ordinary
    bucket-driven ladder applies.
    """
    if model.no_baseline_audit:
        # No two-sided comparison ran, so none of the "ABI BREAKING"/"Source
        # API changed; binary ABI unchanged" wording below applies (Codex
        # review, PR #1210, rounds 3-5). Blocking-ness comes from the
        # report's own overall `exit_code` (`no_baseline_audit_blocking`,
        # every orthogonal axis max-folded into it -- not bucket membership,
        # and checked even when every bucket is empty: `evidence_contract`
        # (exit 7) can block with no itemizable finding at all).
        if model.no_baseline_audit_blocking:
            if model.no_baseline_audit_gate_fired:
                return "🛑", "Audit gate: candidate-side finding blocks this step"
            # A different orthogonal axis blocked (e.g. --contract's
            # coverage ledger, or evidence_contract); "🛑 Analysis
            # incomplete" below names which one when there's a finding for it.
            return "🛑", "Audit: this run blocks the step"
        # A policy override can reclassify an audit finding as
        # "compatible" (bucketed into `model.safe`, not `breaking`/
        # `review`), and a fully-suppressed audit has only
        # `suppressed_count` -- both left this check believing the run
        # found nothing at all (Codex review, PR #1210, round 11, fresh
        # evidence): the Action still publishes AUDIT_RISK/exit-code 0 for
        # either shape, and the body renders the finding(s), so the
        # headline must not claim "no baseline to compare" (a green,
        # nothing-happened headline) alongside a body that shows one.
        if (
            not model.breaking
            and not model.review
            and not model.has_incomplete
            and not model.safe
            and not model.suppressed_count
        ):
            return "✅", "Audit — no baseline to compare"
        return "⚠️", "Audit — candidate-side finding(s), not gated"
    if (
        model.mode == "scan"
        and model.scan_audit_only
        and not model.breaking
        and not model.review
        and not model.has_incomplete
    ):
        # An audit-only `scan` (no `--against` baseline at all) ran no
        # comparison, so every compatibility bucket is necessarily empty —
        # the generic "✅ No ABI changes" wording below would misreport that
        # as "we compared and found nothing" rather than "there was nothing
        # to compare".
        return "✅", "Scan audit — no baseline to compare"
    if model.removed_libraries:
        return "❌", "LIBRARY REMOVED"
    if model.no_comparison_completed:
        # ADR-065 D7: nothing was compared, so no bucket below can speak
        # for the scope -- never "No ABI changes".
        return "🛑", "No comparison completed"
    return None


def _breaking_headline(model: CommentModel) -> tuple[str, str]:
    """Wording for a non-empty Breaking bucket.

    A finding is only accurately reported as "ABI BREAKING" when the bucket
    holds a genuine abi_breaking/potential_breaking finding — never infer
    that wording from bucket membership alone. Severity-config promotion
    (ADR-042: compatibility and gate decisions are separate axes) can
    populate Breaking with a COMPATIBLE addition/quality finding that policy
    chose to block; that is a policy violation, not an ABI/API break.
    """
    cats = model.breaking_categories
    if "abi_breaking" in cats:
        return "❌", "ABI BREAKING"
    if "potential_breaking" in cats:
        # "potential_breaking" covers both "api_break" (a real source
        # break) and "risk" (a risk promoted to blocking) — they share a
        # severity-config knob but are not the same claim, so resolve
        # via the raw severities rather than wording every gated risk as
        # a "source API break" (Codex review, PR #595).
        if "risk" in model.breaking_severities and (
            "api_break" not in model.breaking_severities
        ):
            return "⛔", "Compatibility risk blocks this PR"
        return "⛔", "Source API break blocks this PR"
    policy_header = _POLICY_ONLY_HEADER.get(cats)
    if policy_header is not None:
        return policy_header
    # No per-category tracking for this bucket (shouldn't normally
    # happen — every mode populates breaking_categories) — fall back to
    # the conservative default rather than under-stating a red check.
    return "❌", "ABI BREAKING"


def _review_headline(model: CommentModel) -> tuple[str, str]:
    """Wording for a non-empty Needs-review bucket.

    Names the actual reason instead of the generic "Review recommended"
    whenever every review-bucket finding agrees on one severity — a
    source-level API change (binary ABI unaffected) reads very differently
    from a risk finding, and a reviewer shouldn't have to open the section
    to tell which one this is.
    """
    review_sevs = frozenset(f.severity for f in model.review if f.severity)
    if review_sevs == {"api_break"}:
        return "⚠️", "Source API changed; binary ABI unchanged"
    if review_sevs == {"risk"}:
        return "⚠️", "Compatibility risk — review recommended"
    return "⚠️", "Review recommended"


def _quiet_headline(model: CommentModel, safe_count: int) -> tuple[str, str]:
    """Wording once no bucket above claimed the headline.

    Advisory limitations still outrank "nothing to report": a clean set of
    compared members says nothing about the members that were not compared.
    """
    if model.has_incomplete:
        return "⚠️", "Analysis coverage reduced"
    if model.scope_notice is not None:
        # `warn` (the default): the compared members are clean, but the
        # headline may only say so for them, never for the whole scope.
        return "⚠️", "Compared members clean; scope incompletely checked"
    if safe_count:
        return "✅", "No compatibility impact detected"
    return "✅", "No ABI changes"


def headline(model: CommentModel) -> tuple[str, str]:
    """The comment's one-line claim, in strict priority order.

    Workstream D-S1 (vision-api-abi-evolution.md "D. Optional
    prebuilt-consumer lifecycle"): a supplied --used-by/--required-symbol
    consumer's own scoped verdict (`model.scoped_verdict`) does not override
    this headline -- it is reported separately (`_scoped_notes`). The
    headline always follows the full-library bucket counts and the
    orthogonal `incomplete_blocking` check below, which already folds in a
    contract-coverage failure, exactly as it would for a run with no
    consumer supplied.
    """
    early = _pre_comparison_headline(model)
    if early is not None:
        return early
    breaking_count, review_count, safe_count = model.counts
    if breaking_count:
        return _breaking_headline(model)
    if model.scope_blocking:
        # ADR-065 D6 under `--on-incomplete-scope block`: the scope axis
        # turned the check red on its own, ahead of merely-advisory review
        # findings, for the same reason the coverage/assurance axes do.
        return "🛑", "Comparison scope incompletely checked"
    if model.has_incomplete and model.incomplete_blocking:
        # A hard evidence-policy failure fails the run the same way a
        # genuine break does (ADR-033 D7 / a gated coverage risk), so it
        # takes the same headline priority as the Breaking bucket above —
        # ahead of a separate, merely-advisory review finding. Say so
        # explicitly rather than folding it into the generic "Review
        # recommended" wording, which would read as "this PR changed the
        # API," not "we couldn't check this PR."
        if model.mode == "aggregate":
            # "Source analysis" names an evidence layer a *comparison* was
            # short of. An aggregate's blocking limitation is a different
            # claim -- a target that never reported, a required target
            # missing, a member report that could not be read -- and
            # borrowing the compare wording for it would tell a reviewer to
            # go looking for a missing DWARF/header layer that is not the
            # problem.
            return "🛑", "Targets unchecked — this result is incomplete"
        return "🛑", "Source analysis incomplete"
    if review_count:
        # A real compatibility finding always keeps headline priority over a
        # merely-advisory coverage gap (Codex review) — an ungated
        # `layer_coverage_asymmetric` must not bury a genuine source-API
        # change behind "Analysis coverage reduced". `_incomplete_note`
        # still calls the coverage gap out separately.
        return _review_headline(model)
    return _quiet_headline(model, safe_count)


__all__ = ["headline"]
