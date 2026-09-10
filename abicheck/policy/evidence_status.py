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

"""Per-finding/per-comparison epistemic-status and evolution *enums*:
:class:`EvidenceStatus`, :class:`Confidence`, :class:`EvidenceTier`,
:class:`ReachabilityState`, :class:`FindingEvolution`, and
:class:`CrossSourceEvolution`, plus :func:`is_cross_source_resolved` and
:func:`has_binary_evidence`.

ADR-061 gap B (`abicheck/checker_policy.py`'s own split): this module is a
genuinely dependency-free leaf (zero ``abicheck`` imports, deliberately) —
the pair of functions that actually *derive* an :class:`EvidenceStatus` from
a finding's ``ChangeKind`` (:func:`~abicheck.policy.classification.
evidence_status_for_change`/:func:`~abicheck.policy.classification.
evidence_status_for_result`) need :mod:`abicheck.policy.classification`'s
kind-set data, so they live there instead — keeping them here would close an
import cycle (``classification`` already needs this module's
:class:`EvidenceStatus` for :func:`~abicheck.policy.classification.
impact_for`). Both modules are re-exported, unchanged, from the flat
``abicheck/checker_policy.py`` compatibility facade, which is the module
`model`-layer code (via the `model`-owned, legacy ``checker_types.
DiffResult``) continues to import — see that facade's own module docstring
for why it stays a flat, unclassified root module rather than a
`legacy_paths` entry under this package.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum


class Confidence(str, Enum):
    """Evidence confidence level for a comparison result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceTier(str, Enum):
    """Canonical analysis tier achieved for a comparison.

    Unlike :data:`DiffResult.evidence_tiers` — a list of the *raw* data
    sources that were available (``"elf"``, ``"dwarf"``, ``"header"``,
    ``"pe"``, ``"macho"``) — this is a single, ordered label summarizing
    *how deep* the analysis could go. Consumers should key trust decisions
    off this scalar rather than re-deriving depth from the raw list.

    Ordering (shallow → deep):

    - ``ELF_ONLY`` — symbol-table-only. Binary metadata is present
      (ELF/PE/Mach-O export tables) but there is no DWARF debug info and no
      header/AST surface. Only symbol add/remove and version changes are
      observable; struct layout, enum values, and type changes are not.
    - ``DWARF_AWARE`` — DWARF (or equivalent debug info) is present, enabling
      struct layout, enum, and calling-convention analysis, but no
      header/AST surface is available to cross-check declared API intent.
    - ``HEADER_AWARE`` — header/AST information (functions/types/enums from a
      parsed source surface) is present. This is the richest tier and the
      only one that can reason about declared-but-not-emitted API,
      inline/template changes, and macro contracts.
    """

    ELF_ONLY = "elf_only"
    DWARF_AWARE = "dwarf_aware"
    HEADER_AWARE = "header_aware"

    @property
    def rank(self) -> int:
        """Numeric depth (higher = deeper analysis). Useful for comparisons."""
        return _EVIDENCE_TIER_RANK[self]


_EVIDENCE_TIER_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.ELF_ONLY: 0,
    EvidenceTier.DWARF_AWARE: 1,
    EvidenceTier.HEADER_AWARE: 2,
}


class ReachabilityState(str, Enum):
    """Tri-state public-reachability verdict for a single ``Change`` (ADR-044
    follow-up — impact-analysis-layer P0 slice).

    ``MarkReachability`` (``post_processing.py``) used to tag a change with
    only a boolean (``Change.public_reachable``), which conflates two
    genuinely different situations under the same ``False`` value: "the
    reachability walk ran and positively proved this change is not part of
    the effective public ABI" versus "no walk — or an incomplete one — ever
    reached a verdict on this change at all". A broad suppression rule's
    default ``unreachable-only`` gate (:mod:`abicheck.suppression`) has
    always treated both as equivalent, which is safe for the common
    no-graph-evidence case (the layout/type-graph walk is a complete closure
    over the snapshot's own declarations) but is a real gap for the optional
    L5 source/call graph, whose coverage can be narrowed or degraded
    (``SourceGraphSummary.narrowed_passes``/``degraded_passes``) — absence of
    an edge there does not always prove absence of a dependency.

    ``Change.reachability_state`` makes the distinction explicit and
    available to any rule that opts into the stricter
    ``reachability: proven-unreachable-only`` gate; the existing
    ``unreachable-only`` default keeps its original boolean semantics
    unchanged for backward compatibility.
    """

    PROVEN_REACHABLE = "reachable"
    PROVEN_UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class FindingEvolution(str, Enum):
    """Where one finding sits across a chain of more than one comparison
    (ADR-068 Phase 1 item 2, ``one-comparison-product.md``).

    A single :func:`~abicheck.checker.compare` call only ever sees *one*
    pair of snapshots, so it has no way to know whether a finding it just
    emitted also appeared in a previous comparison, or whether a finding a
    previous comparison reported has since disappeared. This field exists
    for a caller that *does* have that context — a longitudinal chain
    (``workflows/history.py``, ADR-066 S1), a CI job comparing today's PR
    findings against the base branch's own last recorded run, or any other
    N>1-comparison consumer — to record that context on the canonical
    finding model rather than inventing a side channel per consumer.

    ``compare()`` itself never sets this field: every ordinary, single-
    comparison ``Change`` keeps the ``NOT_EVALUATED`` default. Reusing
    ADR-067 D3's ``not_evaluated`` convention (see
    ``report.disposition_audit.NotEvaluatedDetector``) deliberately, rather
    than inventing a second "capability never exercised" vocabulary: a
    finding whose evolution nobody computed reads as *not evaluated*, never
    as a silently-assumed ``persistent`` or an omitted field.

    - ``INTRODUCED`` — this finding's identity (see
      :func:`abicheck.finding_identity.report_finding_id`) did not appear in
      the previous comparison in the chain.
    - ``PERSISTENT`` — this finding's identity already appeared in the
      previous comparison and still appears now.
    - ``RESOLVED`` — a finding's identity that appeared in the previous
      comparison but no longer appears in the current one. Never carried as
      a member of ``DiffResult.changes`` (there is no current-side ``Change``
      to attach it to); see ``DiffResult.resolved_findings`` instead.
    - ``NOT_EVALUATED`` — no previous comparison was supplied to compare
      against, so evolution could not be determined. The default for every
      ``Change`` a plain, single comparison produces.
    """

    INTRODUCED = "introduced"
    RESOLVED = "resolved"
    PERSISTENT = "persistent"
    NOT_EVALUATED = "not_evaluated"


class CrossSourceEvolution(str, Enum):
    """How a one-sided (candidate-side) finding behaves across OLD → NEW
    *within a single* :func:`~abicheck.checker.compare` **call**
    (ADR-068 D3; ``docs/contribute/plans/one-comparison-product.md`` P2).

    Not to be confused with :class:`FindingEvolution` above, which tracks a
    finding's identity across a *chain* of separate ``compare()`` calls over
    time — this enum instead states how a cross-source hygiene check
    (``buildsource.cross_source_checks.run_crosschecks`` and siblings), which
    evaluates one snapshot's evidence sources against each other and
    carries no baseline of its own, behaves when that check is run
    independently on OLD and NEW *inside the same* ``compare()`` call. Same
    four state names, deliberately narrower scope; `compare()` itself DOES
    set this field, automatically (ADR-068 D3/D4/D5 -- see
    ``cross_source_checks``, on by default, not a user-facing flag), unlike
    ``FindingEvolution``.

    - ``INTRODUCED``: absent on OLD (with OLD evidence sufficient to say
      so), present on NEW.
    - ``RESOLVED``: present on OLD, absent on NEW.
    - ``PERSISTENT``: present on both.
    - ``NOT_EVALUATED``: the check could not be run against at least one
      side's evidence (e.g. a stripped/ELF-only snapshot with no header
      provenance) with the other side flagging the finding, so neither
      "introduced" nor "resolved" nor "persistent" can be asserted. **This
      state is mandatory, not a convenience**: reporting a pre-existing
      hygiene problem as ``INTRODUCED`` merely because the baseline lacked
      the evidence to evaluate it would be a manufactured finding, which
      ``vision.md`` forbids outright.

    Authority is unchanged (ADR-028 D3 / ADR-035 D1): a finding's *category*
    -- which severity/kind bucket it belongs to -- stays whatever its
    ``ChangeKind`` already defaults to; this axis never promotes a finding
    toward ``BREAKING`` on its own. That is a *different* axis from whether
    it *gates* at all: a ``RESOLVED`` finding is deliberately excluded from
    the change gate outright (plan §7 F-9, "visible on a passing run") --
    the problem it names no longer exists on the candidate, so it must stay
    visible in the report and every disposition ledger without failing a
    run that has already fixed it. See :func:`is_cross_source_resolved`,
    the one predicate every gate/exit-code chokepoint
    (``checker.compare``'s ``all_unsuppressed``,
    ``policy.severity.gate_eligible_changes``,
    ``policy.severity.gate_contribution_for_change``) shares for that
    exclusion (Codex review, PR #1172, round 12: this state used to reach
    the gate exactly like ``PERSISTENT``/``INTRODUCED``, contradicting F-9).
    """

    INTRODUCED = "introduced"
    RESOLVED = "resolved"
    PERSISTENT = "persistent"
    NOT_EVALUATED = "not_evaluated"


def is_cross_source_resolved(change: object) -> bool:
    """Whether *change*'s :class:`CrossSourceEvolution` state alone excludes
    it from the change gate (plan §7 F-9).

    The one shared predicate every gate/exit-code chokepoint applies --
    duck-typed (``getattr``) so a lightweight test stub or a
    :class:`Change` reconstructed from JSON (which may not carry the
    attribute at all) both answer ``False`` rather than raising. A
    ``RESOLVED`` finding still keeps its normal ``ChangeKind`` category and
    stays fully present in ``DiffResult.changes``/every disposition
    ledger -- only its gate/exit-code contribution is zeroed.
    """
    return (
        getattr(change, "cross_source_evolution", None) == CrossSourceEvolution.RESOLVED
    )


class EvidenceStatus(str, Enum):
    """The epistemic status of a single finding — *how* it was proven, not just
    *what* it is (its ``Verdict``/severity already say that).

    A per-report-format overlay (JSON ``evidence_status`` / SARIF
    ``evidenceStatus``). Deliberately a **pure function of the finding's
    ``kind``** — never the policy-resolved ``Verdict``/severity and never a
    per-finding ``effective_verdict`` override, since *every* mechanism that
    sets one (a named policy's kind-set reassignment, a ``PolicyFile``
    override, ADR-033 D7's evidence-tier ceiling, ADR-027 A4 pattern
    modulation) is a gating decision about what fails the build, not new
    evidence about the finding — see :func:`evidence_status_for_change` for
    why none of them are trusted. Per the ADR-028 D3 authority rule (artifact
    evidence is authoritative; build/source evidence corroborates):

    - ``ARTIFACT_PROVEN`` — intrinsically a ``BREAKING_KINDS`` member:
      L0/L1/L2 artifact evidence confirms a shipped ABI break.
    - ``SOURCE_CONTRACT`` — intrinsically ``API_BREAK_KINDS``: a source-level
      break that needs a recompile or a policy decision, not necessarily a
      shipped ABI break.
    - ``CONTEXTUAL_RISK`` — intrinsically ``RISK_KINDS``: build/source/
      deployment context suggests risk without proving a break.
    - ``CONSUMER_PROVEN`` — not derivable from the finding's own
      classification at all: set explicitly when runtime/``appcompat``
      evidence demonstrates a *specific* consumer actually depends on what
      changed (see ``reporter.appcompat_to_json``).
    - ``NOT_CHECKABLE`` — the finding **is** the "missing evidence" signal
      (``ChangeKind.EVIDENCE_REQUIRED_MISSING``, ADR-033 D7), not a break.
    - ``UNATTRIBUTED`` — a kind-level ``ARTIFACT_PROVEN`` classification
      whose *comparison* is positively known to have never examined a real
      binary artifact at all (``DiffResult.evidence_tiers`` populated with
      only ``"header"``, e.g. a Python-API caller comparing hand-built or
      loaded snapshots) — see :func:`evidence_status_for_result`. This is
      one of two places a signal *other than the finding's own kind* is
      allowed to downgrade the status, because neither re-litigates
      whether the *kind itself* is classified correctly (the
      BREAKING_KINDS/API_BREAK_KINDS/RISK_KINDS partition stays untouched)
      — each only refuses to claim proof by an artifact that provably was
      never looked at. The first is comparison-level (P0 evidence-provider
      audit, above); the second is *per-finding* (see
      :func:`evidence_status_for_result`'s ``symbol_binding`` check) — a
      comparison can genuinely have examined a real ELF/PE/Mach-O symbol
      table while *this specific* ``BREAKING_KINDS`` finding was never
      matched against it (e.g. a header-only overload-set synthesis with
      no corresponding mangled export), and the comparison-level check
      alone cannot see that.

    ``COMPATIBLE``/``NO_CHANGE`` findings (additions, clean comparisons) carry
    no status — nothing to explain the epistemic strength of.
    """

    ARTIFACT_PROVEN = "artifact_proven"
    SOURCE_CONTRACT = "source_contract"
    CONTEXTUAL_RISK = "contextual_risk"
    CONSUMER_PROVEN = "consumer_proven"
    NOT_CHECKABLE = "not_checkable"
    UNATTRIBUTED = "unattributed"


#: Evidence tiers (``DiffResult.evidence_tiers``) that constitute real
#: binary-level evidence, as opposed to a pure header/declaration surface
#: with no artifact ever examined. Single source of truth for both
#: :func:`evidence_status_for_result` and ``semver.recommend_release`` —
#: mirrors ``confidence._detect_evidence_tiers``.
BINARY_EVIDENCE_TIERS: frozenset[str] = frozenset(
    {"elf", "dwarf", "dwarf_advanced", "pe", "macho"}
)


def has_binary_evidence(evidence_tiers: Sequence[str]) -> bool:
    """Whether *evidence_tiers* includes at least one binary-level source.

    An **empty** sequence means the field was never populated — typically a
    ``DiffResult`` built directly rather than via ``checker.compare()`` (many
    unit tests do this, as does any older caller). That is "unknown", not
    "absent": treated as having binary evidence so existing callers that
    don't populate this field keep their prior behaviour. Only a
    *non-empty* tier list containing nothing but ``"header"`` is a genuine,
    positive signal that no binary was ever examined.
    """
    if not evidence_tiers:
        return True
    return bool(set(evidence_tiers) & BINARY_EVIDENCE_TIERS)
