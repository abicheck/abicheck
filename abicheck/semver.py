# SPDX-License-Identifier: Apache-2.0
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

"""Release-recommendation helper: verdict + change set → semver bump + SONAME action.

A library maintainer's first practical question after running a comparison is
*"what version do I release, and do I need to bump the SONAME?"*. abicheck
already has every signal needed to answer that — the policy-aware verdict and
the per-change classification — but historically left the mapping to the user.

This module derives a :class:`ReleaseRecommendation` from a :class:`DiffResult`:

==================  ===========  =====================  ==========================
Verdict             Bump         SONAME                 Why
==================  ===========  =====================  ==========================
NO_CHANGE           none         no_bump_needed         nothing changed
BREAKING            major        bump_required/…        binary ABI break
API_BREAK           major        no_bump_needed         source break, binary OK
COMPATIBLE_WITH_RISK minor/patch no_bump_needed         deployment-floor risk
COMPATIBLE (adds)   minor        no_bump_needed         new public API surface
COMPATIBLE (quality) patch       no_bump_needed         bad-practice only
==================  ===========  =====================  ==========================

The mapping follows the conventional split between the *API* contract (governs
the package's semantic version) and the *ABI* contract (governs the ELF SONAME /
Mach-O compatibility-version). A source-only break (``API_BREAK``) is a MAJOR
semver event but leaves the binary loadable, so the SONAME need not change; a
binary break (``BREAKING``) requires both.

Cross-references:
    abicheck/checker_policy.py   — Verdict, ADDITION_KINDS
    abicheck/diff_versioning.py  — emits SONAME_BUMP_RECOMMENDED / SONAME_CHANGED
    tests/test_semver_recommendation.py
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .checker_policy import ADDITION_KINDS, ChangeKind, Verdict, has_binary_evidence
from .checker_types import Change, DiffResult
from .contract_relevance_types import ContractRelevance


class SemverBump(str, Enum):
    """Recommended semantic-version increment for the next release."""

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    NONE = "none"


class SonameAction(str, Enum):
    """Recommended action for the binary soname / compatibility-version."""

    #: Binary ABI break detected and the soname does not appear to have moved —
    #: the maintainer must bump it.
    BUMP_REQUIRED = "bump_required"
    #: Binary ABI break detected *and* the soname was already changed in this
    #: revision — nothing more to do (informational, the good path).
    BUMP_PERFORMED = "bump_performed"
    #: Binary ABI break detected and abicheck explicitly observed the soname was
    #: left unchanged (SONAME_BUMP_RECOMMENDED fired) — a deployment hazard.
    BUMP_MISSING = "bump_missing"
    #: Binary remained compatible — no soname change is required.
    NO_BUMP_NEEDED = "no_bump_needed"
    #: A BREAKING verdict was reached with no binary-level evidence at all
    #: (the comparison's evidence tiers carry only "header" — e.g. a Python
    #: API caller compared hand-built/loaded snapshots that were never
    #: extracted from a real binary). SONAME is a binary-file concept; abicheck
    #: has no artifact to say whether it moved or should, so it does not
    #: pretend otherwise.
    NOT_DETERMINED = "not_determined"


class ReleaseRecommendationState(str, Enum):
    """How much weight the recommendation itself can bear.

    Distinct from ``SonameAction``/``SemverBump`` (*what* to do): this is
    *how confidently* abicheck can say so, given the evidence the comparison
    actually had.
    """

    #: The verdict and its evidence are unambiguous — act on the recommendation.
    ACTIONABLE = "actionable"
    #: A source/API-level break was found; a MAJOR release is likely right,
    #: but no binary-level evidence exists to confirm a SONAME action either
    #: way — a human should look, not automation.
    REVIEW = "review"
    #: The evidence backing a BREAKING verdict is insufficient to recommend a
    #: concrete binary action (no ELF/PE/Mach-O/DWARF evidence at all).
    UNAVAILABLE = "unavailable"


#: RISK-tier ``ChangeKind``s that flag the analyzed evidence itself as
#: internally inconsistent, not any particular ABI change — AC-008
#: (``COMPILE_CONTEXT_CONFLICT``: two L3 compile units of one build target
#: carried conflicting ABI-relevant flags that were silently aggregated) and
#: AC-009 (``SOURCE_SURFACE_DSO_MISMATCH``: the linked L4 source surface maps
#: to none of the analyzed binary's exports, so it likely describes a
#: different/shared DSO). Either one means *every* finding in this same
#: comparison — including a BREAKING verdict backed by binary evidence —
#: was reached over evidence abicheck cannot vouch for as self-consistent,
#: so a release recommendation must not present a confident SONAME/MAJOR
#: action as if the analysis were coherent (P0 evidence-coherence audit;
#: these two checks previously fed the verdict but were never consulted
#: here, so an incoherent build/source context could still yield an
#: unqualified "bump your SONAME"). ``HEADER_BINARY_CONTEXT_MISMATCH``
#: joins the same set for the same reason (P0 evidence-coherence audit,
#: follow-up): a DWARF-vs-header-AST layout-backfill record that couldn't
#: be corroborated as the same declaration is exactly the same class of
#: "this run's own evidence disagrees with itself" signal as the other two.
_COHERENCE_CONFLICT_KINDS = frozenset(
    {
        ChangeKind.COMPILE_CONTEXT_CONFLICT,
        ChangeKind.SOURCE_SURFACE_DSO_MISMATCH,
        ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
    }
)

#: The two ADR-049 relevance values that mean "the evidence ran out", as
#: opposed to ``PROVEN_OUT_OF_CONTRACT``, which is a positive determination.
#: Exactly the set the contract-coverage exit gates on, so a recommendation
#: and that exit code cannot disagree about whether the run established
#: anything.
_UNRESOLVED_CONTRACT_RELEVANCES = frozenset(
    {
        ContractRelevance.UNKNOWN_UNRESOLVED,
        ContractRelevance.UNKNOWN_UNPROVEN,
    }
)


@dataclass(frozen=True)
class ReleaseRecommendation:
    """A concrete, machine- and human-readable release recommendation."""

    bump: SemverBump
    soname: SonameAction
    rationale: str
    state: ReleaseRecommendationState = ReleaseRecommendationState.ACTIONABLE

    def to_dict(self) -> dict[str, str | None]:
        """Serialise for JSON reports (additive ``release_recommendation`` key).

        ``version_bump`` is ``null`` when :attr:`state` is ``UNAVAILABLE`` —
        the confident-looking ``"major"`` literal previously survived
        serialization even though the docstring/rationale explain abicheck
        could not confirm it, which let automation blindly act on a bump it
        had no real evidence for. The still-plausible bump is only in
        ``rationale`` prose then; ``self.bump`` itself (and ``headline()``)
        keep the pre-serialization value for human-facing callers that
        already gate on ``state``.

        ``possible_impact`` (schema 2.22, status-review follow-up) exposes
        that same still-plausible bump as a *separate*, always-non-null,
        machine-readable field — ``rationale`` prose is not something
        automation should parse to recover it. It equals ``version_bump``
        whenever ``state`` is ``ACTIONABLE`` (the two fields agree by
        construction); the split only matters for ``REVIEW``/``UNAVAILABLE``,
        where ``version_bump`` is deliberately withheld/nulled but a caller
        that wants to know "what would abicheck recommend if this were
        confirmed" (e.g. to size a review queue, not to auto-act) still has
        an answer. Never gate an automated release action on
        ``possible_impact`` alone — that is exactly the blind-trust failure
        mode ``version_bump: null`` exists to prevent; always check ``state``
        first.
        """
        return {
            "version_bump": (
                None
                if self.state == ReleaseRecommendationState.UNAVAILABLE
                else self.bump.value
            ),
            "possible_impact": self.bump.value,
            "soname_action": self.soname.value,
            "rationale": self.rationale,
            "state": self.state.value,
        }

    def headline(self) -> str:
        """One-line summary suitable for ``--stat`` output or a CI log."""
        if self.state == ReleaseRecommendationState.UNAVAILABLE:
            return "Recommended release: UNAVAILABLE (insufficient binary evidence)"
        if self.soname in (SonameAction.BUMP_REQUIRED, SonameAction.BUMP_MISSING):
            return f"Recommended release: {self.bump.value.upper()} + SONAME bump"
        return f"Recommended release: {self.bump.value.upper()}"


def _soname_action_for_break(kinds: set[ChangeKind]) -> tuple[SonameAction, str]:
    """Pick the soname action (and trailing rationale) for a BREAKING verdict."""
    if ChangeKind.SONAME_BUMP_RECOMMENDED in kinds:
        return (
            SonameAction.BUMP_MISSING,
            " The SONAME was not bumped despite the binary break — bump it "
            "(e.g. libfoo.so.1 → libfoo.so.2) so old binaries fail loudly instead "
            "of silently loading an incompatible library.",
        )
    if ChangeKind.SONAME_CHANGED in kinds:
        return (
            SonameAction.BUMP_PERFORMED,
            " The SONAME was already bumped in this revision — good.",
        )
    return (
        SonameAction.BUMP_REQUIRED,
        " Bump the SONAME (major) so old binaries do not silently load an "
        "incompatible library.",
    )


def _unresolved_contract_findings(result: DiffResult) -> list[Change]:
    """Findings the contract evaluator could not resolve, if any.

    ADR-049 splits "not evaluated" into two very different statements, and a
    release recommendation must not treat them alike: ``PROVEN_OUT_OF_CONTRACT``
    is a positive determination (the finding really is outside the promised
    contract, so it genuinely does not warrant a bump), while
    ``UNKNOWN_UNRESOLVED``/``UNKNOWN_UNPROVEN`` mean the evidence ran out. Only
    the latter are returned here -- the same split the orthogonal
    contract-coverage exit already makes, so the recommendation and that exit
    code cannot disagree about whether the run established anything.

    Empty for every run that did not pass ``--contract``: nothing
    carries a relevance, so nothing is unresolved.
    """
    from .contract_gating import contract_relevance_of

    return [
        c
        for c in result.changes
        if contract_relevance_of(c) in _UNRESOLVED_CONTRACT_RELEVANCES
    ]


def _suppressed_major_class_recommendation(
    result: DiffResult,
) -> ReleaseRecommendation | None:
    """ADR-067: a major-class break a suppression rule hid is still a break.

    Before this, ``recommend_release`` read only ``result.verdict``/
    ``result.changes`` -- i.e. the *post*-disposition list -- so a rule with
    ``allow_public_break: true`` covering a removed public symbol degraded the
    release advice to "no version bump required" with no trace that a
    MAJOR-worthy break had been accepted. The conserved disposition ledger
    (ADR-067 C-S1) is the input that fixes it: it records every atomically
    detected change *before* any disposition applied, together with the rule
    that hid each one.

    A suppression is not proof the tool was wrong (D2), and without an
    explicit ``intent:`` (D5, S3 -- every existing rule migrates to
    ``unspecified``) abicheck cannot tell a claimed false positive from a
    deliberate waiver. So this states the conserved fact and hands the call to
    a human (``REVIEW``) rather than either silently dropping the break or
    asserting a MAJOR release outright.

    ``None`` -- and therefore no change at all -- whenever the post-suppression
    verdict is *already* major-class: the recommendation then names the break
    on its own, and this would only restate it.
    """
    from .policy.disposition_ledger import ledger_for

    if result.verdict in (Verdict.BREAKING, Verdict.API_BREAK):
        return None
    hidden = ledger_for(result).suppressed_gating_records()
    if not hidden:
        return None
    binary_break = any(r.verdict_class == Verdict.BREAKING.value for r in hidden)
    rules = sorted(
        {r.rule.rule_id for r in hidden if r.rule is not None and r.rule.rule_id}
    )
    attribution = f" (rule(s): {', '.join(rules)})" if rules else ""
    kinds = ", ".join(sorted({r.kind for r in hidden}))
    return ReleaseRecommendation(
        SemverBump.MAJOR,
        SonameAction.NOT_DETERMINED,
        f"{len(hidden)} major-class finding(s) ({kinds}) were suppressed "
        f"(intent: unspecified){attribution}, so this comparison is "
        "**not** a proven-compatible release: the "
        f"{'binary ABI' if binary_break else 'source API'} break was hidden "
        "from the verdict, not shown to be absent. Record an explicit "
        "acknowledgment, or drop the rule and re-run, before treating this "
        "as anything less than a MAJOR release.",
        state=ReleaseRecommendationState.REVIEW,
    )


def recommend_release(result: DiffResult) -> ReleaseRecommendation:
    """Derive a :class:`ReleaseRecommendation` from a comparison result.

    The recommendation is driven by the *policy-aware* verdict already computed
    on ``result`` (so ``--policy sdk_vendor`` / ``plugin_abi`` and custom policy
    files are honoured), refined by which change kinds are present (additions vs
    quality-only) and by the soname signals.

    It reads one thing the verdict alone cannot tell it: the *conserved*
    change set (ADR-067). A suppressed major-class break is reported as such
    rather than as "no bump needed" -- see
    :func:`_suppressed_major_class_recommendation`.
    """
    suppressed_major = _suppressed_major_class_recommendation(result)
    if suppressed_major is not None:
        return suppressed_major
    verdict = result.verdict
    kinds = {c.kind for c in result.changes}
    has_additions = bool(kinds & ADDITION_KINDS)
    incoherent_kinds = kinds & _COHERENCE_CONFLICT_KINDS
    unresolved = _unresolved_contract_findings(result)

    if verdict == Verdict.NO_CHANGE:
        # ADR-049 D1: a NO_CHANGE verdict reached with findings the contract
        # evaluator could not resolve is not the same claim as one reached
        # with nothing to report. Compatibility policy never scored those
        # findings, and the orthogonal coverage axis exits 1 precisely
        # because the evidence did not close -- so "no version bump required"
        # would be automation-grade advice resting on the one thing the run
        # itself says it could not establish (Codex review, reproduced under
        # `--contract exports` with no export table). A *proven* exclusion is
        # deliberately not treated this way: there the finding really is
        # outside the promised contract, "no bump" is well-founded, and the
        # recommendation stays actionable.
        if unresolved:
            return ReleaseRecommendation(
                SemverBump.NONE,
                SonameAction.NOT_DETERMINED,
                "No ABI or API changes were scored, but this comparison could "
                f"not resolve {len(unresolved)} finding(s) against the "
                "selected contract domain "
                f"({', '.join(sorted({c.kind.value for c in unresolved}))}) — "
                "so abicheck cannot confirm that no version bump is required. "
                "Close the contract evidence (see the contract-coverage "
                "ledger) and re-run before treating this as a proven no-op.",
                state=ReleaseRecommendationState.REVIEW,
            )
        return ReleaseRecommendation(
            SemverBump.NONE,
            SonameAction.NO_BUMP_NEEDED,
            "No ABI or API changes detected; no version bump required.",
        )

    if verdict == Verdict.BREAKING:
        if incoherent_kinds:
            return ReleaseRecommendation(
                SemverBump.MAJOR,
                SonameAction.NOT_DETERMINED,
                "Binary ABI break detected, but this comparison also flagged its "
                "own evidence as internally inconsistent "
                f"({', '.join(sorted(k.value for k in incoherent_kinds))}) — a "
                "MAJOR release is still likely warranted, but abicheck cannot "
                "confirm a SONAME action while the build/source context backing "
                "the analysis does not coherently describe one binary. Resolve "
                "the coherence finding(s) and re-run before treating this as a "
                "confirmed binary break.",
                state=ReleaseRecommendationState.UNAVAILABLE,
            )
        if not has_binary_evidence(result.evidence_tiers):
            return ReleaseRecommendation(
                SemverBump.MAJOR,
                SonameAction.NOT_DETERMINED,
                "Binary ABI break detected, but no ELF/PE/Mach-O/DWARF evidence "
                "backs this comparison (header/declaration surface only) — a "
                "MAJOR release is still warranted, but abicheck cannot confirm "
                "a SONAME action without ever having examined a real binary "
                "artifact.",
                state=ReleaseRecommendationState.UNAVAILABLE,
            )
        soname, extra = _soname_action_for_break(kinds)
        return ReleaseRecommendation(
            SemverBump.MAJOR,
            soname,
            "Binary ABI break detected — release a new MAJOR version." + extra,
        )

    if verdict == Verdict.API_BREAK:
        if incoherent_kinds:
            return ReleaseRecommendation(
                SemverBump.MAJOR,
                SonameAction.NO_BUMP_NEEDED,
                "Source-level API break detected, but this comparison also "
                "flagged its own evidence as internally inconsistent "
                f"({', '.join(sorted(k.value for k in incoherent_kinds))}) — "
                "review the coherence finding(s) before relying on this "
                "comparison's scope.",
                state=ReleaseRecommendationState.UNAVAILABLE,
            )
        return ReleaseRecommendation(
            SemverBump.MAJOR,
            SonameAction.NO_BUMP_NEEDED,
            "Source-level API break (recompilation required) with no binary-layout "
            "change — release a new MAJOR version. The SONAME need not change "
            "because already-linked binaries remain loadable.",
            state=ReleaseRecommendationState.REVIEW,
        )

    if verdict == Verdict.COMPATIBLE_WITH_RISK:
        bump = SemverBump.MINOR if has_additions else SemverBump.PATCH
        return ReleaseRecommendation(
            bump,
            SonameAction.NO_BUMP_NEEDED,
            f"Binary-compatible, but a deployment risk was detected — a "
            f"{bump.value.upper()} release is appropriate; review the risk "
            f"findings (e.g. a raised runtime/toolchain floor) before shipping.",
        )

    # Verdict.COMPATIBLE
    if has_additions:
        return ReleaseRecommendation(
            SemverBump.MINOR,
            SonameAction.NO_BUMP_NEEDED,
            "Backward-compatible additions to the public API — release a new "
            "MINOR version.",
        )
    return ReleaseRecommendation(
        SemverBump.PATCH,
        SonameAction.NO_BUMP_NEEDED,
        "Only quality / bad-practice findings with no API or ABI surface change — "
        "a PATCH release is sufficient.",
    )
