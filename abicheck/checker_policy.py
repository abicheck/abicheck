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

"""Central change policy registry and verdict computation.

Classification sets (BREAKING_KINDS, COMPATIBLE_KINDS, etc.) and IMPACT_TEXT
are now DERIVED from the single-declaration registry in ``change_registry.py``.
Adding a new ChangeKind requires only one entry there — no shotgun surgery.

Hierarchy (5-tier):
    BREAKING_KINDS      → category 1: binary ABI incompatibilities
    API_BREAK_KINDS     → category 2a: source-level breaks (recompilation required)
    RISK_KINDS          → category 2b: binary-compatible but deployment risk present
    QUALITY_KINDS       → category 3: problematic behaviors (COMPATIBLE minus additions)
    ADDITION_KINDS      → category 4: new API surface (subset of COMPATIBLE_KINDS)

    COMPATIBLE_KINDS    = ADDITION_KINDS ∪ QUALITY_KINDS

Cross-references:
    abicheck/change_registry.py — single-declaration metadata registry
    examples/ground_truth.json  — expected verdicts per example case
    tests/test_example_autodiscovery.py — reads from ground_truth.json
    tests/test_abi_examples.py  — hardcoded expectations (cases 01-18)
    examples/README.md          — case index table
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .change_registry import REGISTRY as _REGISTRY, Verdict as Verdict

# Imported from the canonical model-layer location (ADR-061 D9's target
# owner) rather than via change_registry's re-export, so this doesn't grow
# change_registry.py past its 2000-line adoption-debt ceiling for a name
# nothing external imports through that path. ChangeKind/HasKind follow the
# identical precedent (ADR-061 D9 model-vs-policy split): nothing outside
# this module currently imports either via change_registry, so there is no
# reason to route them through it either. See kinds.py's own docstring for
# why ChangeKind moved and how it's assembled.
from .model.change_catalog.kinds import ChangeKind as ChangeKind, HasKind as HasKind
from .model.change_catalog.registry import VALID_BASE_POLICIES as VALID_BASE_POLICIES

# Verdict is imported from change_registry (single source of truth).


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
      the one place a *comparison-level* signal (not the finding's own
      kind) is allowed to downgrade the status, because it doesn't
      re-litigate whether the *kind itself* is classified correctly (the
      BREAKING_KINDS/API_BREAK_KINDS/RISK_KINDS partition stays untouched)
      — it only refuses to claim proof by an artifact that provably was
      never looked at (P0 evidence-provider audit).

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


# ---------------------------------------------------------------------------
# Classification sets — DERIVED from change_registry.py (single source of truth)
# ---------------------------------------------------------------------------
# These sets are computed from the registry entries. To add a new ChangeKind,
# add ONE entry in change_registry.py — these sets update automatically.


def _kinds_for(verdict_val: str) -> set[ChangeKind]:
    """Map registry verdict string values back to ChangeKind enum members."""
    raw = _REGISTRY.kinds_for_verdict(getattr(Verdict, verdict_val))
    return {ChangeKind(v) for v in raw}


BREAKING_KINDS: set[ChangeKind] = _kinds_for("BREAKING")

COMPATIBLE_KINDS: set[ChangeKind] = _kinds_for("COMPATIBLE")

RISK_KINDS: frozenset[ChangeKind] = frozenset(_kinds_for("COMPATIBLE_WITH_RISK"))

API_BREAK_KINDS: set[ChangeKind] = _kinds_for("API_BREAK")

# ---------------------------------------------------------------------------
# Compatible sub-categories: additions vs quality/behavioral issues
# ---------------------------------------------------------------------------

ADDITION_KINDS: frozenset[ChangeKind] = frozenset(
    ChangeKind(v) for v in _REGISTRY.addition_kinds()
)

#: Quality / behavioral issues — COMPATIBLE_KINDS that are NOT additions.
QUALITY_KINDS: frozenset[ChangeKind] = frozenset(COMPATIBLE_KINDS - ADDITION_KINDS)

# ---------------------------------------------------------------------------
# Policy-specific downgrade sets — DERIVED from change_registry policy_overrides
# ---------------------------------------------------------------------------


def _policy_override_kinds(policy: str) -> frozenset[ChangeKind]:
    """Return kinds that have a policy override for the given policy name."""
    return frozenset(ChangeKind(v) for v in _REGISTRY.policy_overrides_for(policy))


# sdk_vendor: source-level-only kinds downgraded API_BREAK → COMPATIBLE.
SDK_VENDOR_COMPAT_KINDS: frozenset[ChangeKind] = _policy_override_kinds("sdk_vendor")

# Deprecated alias kept for external consumers; will be removed in v2.0.
SDK_VENDOR_DOWNGRADED_KINDS: frozenset[ChangeKind] = SDK_VENDOR_COMPAT_KINDS

# plugin_abi: calling-convention kinds downgraded BREAKING → COMPATIBLE.
PLUGIN_ABI_DOWNGRADED_KINDS: frozenset[ChangeKind] = _policy_override_kinds(
    "plugin_abi"
)

# Integrity assertions: catch miscategorisation at import time.
# Use explicit raises (not assert) so these are never stripped by python -O.
# All checks below use ``if not …: raise`` instead of ``assert`` so that
# running under ``python -O`` does not silently disable them.
if not SDK_VENDOR_COMPAT_KINDS <= API_BREAK_KINDS:
    raise AssertionError(
        "SDK_VENDOR_COMPAT_KINDS must be a strict subset of API_BREAK_KINDS; "
        f"offending kinds: {SDK_VENDOR_COMPAT_KINDS - API_BREAK_KINDS}"
    )
if not PLUGIN_ABI_DOWNGRADED_KINDS <= BREAKING_KINDS:
    raise AssertionError(
        "PLUGIN_ABI_DOWNGRADED_KINDS must be a strict subset of BREAKING_KINDS; "
        f"offending kinds: {PLUGIN_ABI_DOWNGRADED_KINDS - BREAKING_KINDS}"
    )
if not ADDITION_KINDS <= COMPATIBLE_KINDS:
    raise AssertionError(
        "ADDITION_KINDS must be a subset of COMPATIBLE_KINDS; "
        f"offending kinds: {ADDITION_KINDS - COMPATIBLE_KINDS}"
    )
if ADDITION_KINDS | QUALITY_KINDS != COMPATIBLE_KINDS:
    raise AssertionError(
        "ADDITION_KINDS | QUALITY_KINDS must equal COMPATIBLE_KINDS; "
        f"missing: {COMPATIBLE_KINDS - (ADDITION_KINDS | QUALITY_KINDS)}, "
        f"extra: {(ADDITION_KINDS | QUALITY_KINDS) - COMPATIBLE_KINDS}"
    )

if not RISK_KINDS.isdisjoint(BREAKING_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with BREAKING_KINDS; "
        f"offending kinds: {RISK_KINDS & BREAKING_KINDS}"
    )
if not RISK_KINDS.isdisjoint(COMPATIBLE_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with COMPATIBLE_KINDS; "
        f"offending kinds: {RISK_KINDS & COMPATIBLE_KINDS}"
    )
if not RISK_KINDS.isdisjoint(API_BREAK_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with API_BREAK_KINDS; "
        f"offending kinds: {RISK_KINDS & API_BREAK_KINDS}"
    )

# Completeness check: every ChangeKind must be classified in exactly one set.
# Unclassified kinds silently default to BREAKING at runtime (fail-safe), but
# this makes the *intent* invisible and risks false negatives if a new kind is
# added but forgotten here.  Use explicit raise (not assert) so this is never
# stripped by python -O.
_ALL_CLASSIFIED: frozenset[ChangeKind] = (
    frozenset(BREAKING_KINDS)
    | frozenset(COMPATIBLE_KINDS)
    | frozenset(API_BREAK_KINDS)
    | RISK_KINDS
)
_UNCLASSIFIED = set(ChangeKind) - _ALL_CLASSIFIED
if _UNCLASSIFIED:
    raise AssertionError(
        "Every ChangeKind must appear in exactly one of BREAKING_KINDS, "
        "COMPATIBLE_KINDS, API_BREAK_KINDS, or RISK_KINDS. "
        f"Unclassified kinds (will default to BREAKING at runtime): {_UNCLASSIFIED}"
    )

# No kind should appear in more than one primary set (BREAKING, COMPATIBLE,
# API_BREAK).  RISK_KINDS disjointness is already checked above.
_BREAKING_COMPAT_OVERLAP = frozenset(BREAKING_KINDS) & frozenset(COMPATIBLE_KINDS)
if _BREAKING_COMPAT_OVERLAP:
    raise AssertionError(
        "BREAKING_KINDS and COMPATIBLE_KINDS must be disjoint; "
        f"offending kinds: {_BREAKING_COMPAT_OVERLAP}"
    )
_BREAKING_API_OVERLAP = frozenset(BREAKING_KINDS) & frozenset(API_BREAK_KINDS)
if _BREAKING_API_OVERLAP:
    raise AssertionError(
        "BREAKING_KINDS and API_BREAK_KINDS must be disjoint; "
        f"offending kinds: {_BREAKING_API_OVERLAP}"
    )
_COMPAT_API_OVERLAP = frozenset(COMPATIBLE_KINDS) & frozenset(API_BREAK_KINDS)
if _COMPAT_API_OVERLAP:
    raise AssertionError(
        "COMPATIBLE_KINDS and API_BREAK_KINDS must be disjoint; "
        f"offending kinds: {_COMPAT_API_OVERLAP}"
    )


@dataclass(frozen=True)
class PolicyEntry:
    default_verdict: Verdict
    severity: str
    doc_slug: str
    impact: str = ""  # human-readable impact explanation


# Impact explanations — DERIVED from change_registry.py
IMPACT_TEXT: dict[ChangeKind, str] = {
    ChangeKind(k): v for k, v in _REGISTRY.impact_text().items()
}


POLICY_REGISTRY: dict[ChangeKind, PolicyEntry] = (
    {
        k: PolicyEntry(Verdict.BREAKING, "error", k.value, IMPACT_TEXT.get(k, ""))
        for k in BREAKING_KINDS
    }
    | {
        k: PolicyEntry(Verdict.API_BREAK, "warning", k.value, IMPACT_TEXT.get(k, ""))
        for k in API_BREAK_KINDS
    }
    | {
        k: PolicyEntry(
            Verdict.COMPATIBLE_WITH_RISK, "warning", k.value, IMPACT_TEXT.get(k, "")
        )
        for k in RISK_KINDS
    }
    | {
        k: PolicyEntry(Verdict.COMPATIBLE, "warning", k.value, IMPACT_TEXT.get(k, ""))
        for k in COMPATIBLE_KINDS
    }
)


def policy_for(kind: ChangeKind) -> PolicyEntry:
    """Get policy metadata for a ChangeKind.

    Unknown kinds are treated as BREAKING by default (fail-safe).
    """
    return POLICY_REGISTRY.get(kind, PolicyEntry(Verdict.BREAKING, "error", kind.value))


def impact_for(kind: ChangeKind) -> str:
    """Return human-readable impact explanation for a ChangeKind, or empty string."""
    return IMPACT_TEXT.get(kind, "")


def policy_registry_markdown() -> str:
    """Build a markdown snippet for docs from the policy registry."""
    lines = [
        "| ChangeKind | Default verdict | Severity | Doc slug |",
        "|---|---|---|---|",
    ]
    for kind in sorted(ChangeKind, key=lambda k: k.value):
        entry = policy_for(kind)
        lines.append(
            f"| `{kind.value}` | `{entry.default_verdict.value}` | "
            f"`{entry.severity}` | `{entry.doc_slug}` |"
        )
    return "\n".join(lines)


def policy_kind_sets(
    policy: str,
) -> tuple[
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
]:
    """Return (breaking, api_break, compatible, risk) kind sets for the given policy name.

    This is the single source of truth for policy → kind-set mapping.
    Used by compute_verdict(), DiffResult properties, and report classification.
    Unknown policy names fall back to strict_abi.
    """
    if policy == "sdk_vendor":
        return (
            frozenset(BREAKING_KINDS),
            frozenset(API_BREAK_KINDS - SDK_VENDOR_COMPAT_KINDS),
            frozenset(COMPATIBLE_KINDS | SDK_VENDOR_COMPAT_KINDS),
            frozenset(RISK_KINDS),
        )
    if policy == "plugin_abi":
        # plugin_abi is for in-process host/plugin contracts.
        # Deployment-floor increases (e.g. new GLIBC requirement) can prevent
        # plugin loading in the host environment and are treated as BREAKING
        # under this policy (not COMPATIBLE_WITH_RISK).
        return (
            frozenset((BREAKING_KINDS - PLUGIN_ABI_DOWNGRADED_KINDS) | RISK_KINDS),
            frozenset(API_BREAK_KINDS),
            frozenset(COMPATIBLE_KINDS | PLUGIN_ABI_DOWNGRADED_KINDS),
            frozenset(),
        )
    return (
        frozenset(BREAKING_KINDS),
        frozenset(API_BREAK_KINDS),
        frozenset(COMPATIBLE_KINDS),
        frozenset(RISK_KINDS),
    )


def apply_policy_file_overrides(
    kind_sets: tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ],
    overrides: Mapping[ChangeKind, Verdict] | None,
) -> tuple[
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
]:
    """Move each overridden kind into its target verdict's set.

    *kind_sets* is ``(breaking, api_break, compatible, risk)`` — typically
    :func:`policy_kind_sets`'s own return value, but callable on any kind-set
    tuple in that shape. *overrides* is a ``PolicyFile.overrides`` mapping
    (kind -> the verdict a document explicitly pins that kind to); a falsy
    value (``None`` or empty) returns *kind_sets* unchanged.

    ADR-061 Phase 4 (checker_types.py's own module docstring history): this
    was previously the *inline* body of ``checker_types.DiffResult.
    _effective_kind_sets`` — real policy-resolution logic (not a data lookup)
    executing directly inside a ``model``-owned dataclass's own method,
    independent of and unaffected by the ``PolicyFileProtocol`` field-typing
    fix `model/policy_file_protocol.py` already closed (that fix narrows
    what the ``policy_file`` *field's declared type* can be; it does nothing
    for an algorithm living in a method body). Moved here so ``DiffResult``
    only ever *consumes* an already-computed kind-set tuple — its own method
    becomes a single delegating call, with zero local branching/looping —
    closing the ADR's own recorded gap rather than leaving it as a re-stated
    known limitation. Not a behavior change: the override-application rule
    (discard from every set, then add to the target verdict's set; an
    override naming a verdict outside the four is silently ignored, matching
    the pre-existing lenient ``.get(verdict)`` lookup) is unchanged, only its
    location moved.
    """
    breaking, api_break, compatible, risk = kind_sets
    if not overrides:
        return breaking, api_break, compatible, risk

    b, a, c, r = set(breaking), set(api_break), set(compatible), set(risk)
    verdict_to_set_idx = {
        Verdict.BREAKING: 0,
        Verdict.API_BREAK: 1,
        Verdict.COMPATIBLE: 2,
        Verdict.COMPATIBLE_WITH_RISK: 3,
    }
    sets = [b, a, c, r]
    for kind, verdict in overrides.items():
        for s in sets:
            s.discard(kind)
        idx = verdict_to_set_idx.get(verdict)
        if idx is not None:
            sets[idx].add(kind)
    return frozenset(b), frozenset(a), frozenset(c), frozenset(r)


def effective_category(
    change: HasKind,
    breaking: frozenset[ChangeKind],
    api_break: frozenset[ChangeKind],
    compatible: frozenset[ChangeKind],
    risk: frozenset[ChangeKind],
) -> Verdict:
    """The verdict category a single *change* contributes (ADR-025 D4.1).

    This is the **one** place a finding's category is decided. When the finding
    carries a per-finding ``effective_verdict`` override (set by the A4
    pattern-aware modulation pass), that wins; otherwise the category derives
    from ``change.kind``'s membership in the policy kind sets — exactly today's
    behaviour. Unclassified kinds fail safe to ``BREAKING``.

    Every classification site (``compute_verdict``, the ``DiffResult``
    properties, the reporter, the severity helpers, and the bundle verdict) must
    route through this helper so a demotion is honoured consistently across all
    outputs and both exit-code paths.
    """
    # Require a real Verdict: ``isinstance`` (not ``is not None``) rejects
    # MagicMock test doubles whose attribute access auto-creates a truthy mock,
    # mirroring the ``frozen_namespace_violation`` guard in policy_file.
    override = getattr(change, "effective_verdict", None)
    if isinstance(override, Verdict):
        return override
    kind = change.kind
    if kind in breaking:
        return Verdict.BREAKING
    if kind in api_break:
        return Verdict.API_BREAK
    if kind in risk:
        return Verdict.COMPATIBLE_WITH_RISK
    if kind in compatible:
        return Verdict.COMPATIBLE
    return Verdict.BREAKING  # unclassified → fail-safe


def evidence_status_for_change(change: HasKind) -> EvidenceStatus | None:
    """The :class:`EvidenceStatus` label for *change* — a **pure function of
    its ``kind``**, deliberately independent of every verdict-modulation
    mechanism (unlike ``severity``/the exit code).

    Earlier revisions honoured a per-finding ``Change.effective_verdict``
    override, reasoning that (unlike a blanket named-policy kind-set swap) it
    represented a decision about *this specific finding*. That reasoning
    doesn't hold: ``effective_verdict`` is *also* the mechanism
    ``buildsource.evidence_policy.apply_evidence_policy`` uses to sweep an
    entire category of findings (build-context / source-only) to a uniform
    verdict per a ``PolicyFile`` ``evidence_policy`` knob (``build_context_drift``
    / ``source_only_findings`` / ``graph_risk_findings``, ADR-033 D7) — the
    same kind of blanket gating sweep as a named policy's kind-set
    reassignment, just implemented through a different field. There is no
    field-level way to tell "a detector individually re-examined this one
    finding" apart from "an operator's evidence-tier ceiling swept a whole
    bucket" — so, to stay honest, **no** verdict-modulation mechanism moves
    this. This always classifies against the kind's own
    **strict_abi-intrinsic** category (:data:`BREAKING_KINDS` /
    :data:`API_BREAK_KINDS` / :data:`RISK_KINDS`), the same partition every
    kind is registered under regardless of the active policy, PolicyFile
    overrides, or any per-finding ``effective_verdict``.

    ``EVIDENCE_REQUIRED_MISSING`` (ADR-033 D7) is the one kind-level
    exception: it **is** the "missing evidence" signal, not a break, so it
    always reads ``NOT_CHECKABLE``.

    ``CONSUMER_PROVEN`` (appcompat/runtime-demonstrated) is never returned
    here: it isn't derivable from a finding's own classification at all, so
    callers that reclassify a finding via consumer evidence
    (``reporter.appcompat_to_json``) set it explicitly instead.
    """
    kind = getattr(change, "kind", None)
    if kind == ChangeKind.EVIDENCE_REQUIRED_MISSING:
        return EvidenceStatus.NOT_CHECKABLE
    if kind in BREAKING_KINDS:
        return EvidenceStatus.ARTIFACT_PROVEN
    if kind in API_BREAK_KINDS:
        return EvidenceStatus.SOURCE_CONTRACT
    if kind in RISK_KINDS:
        return EvidenceStatus.CONTEXTUAL_RISK
    return None


def evidence_status_for_result(
    change: HasKind, evidence_tiers: Sequence[str] = ()
) -> EvidenceStatus | None:
    """:func:`evidence_status_for_change`, refined by one comparison-level
    fact: whether this comparison ever actually examined a real binary
    artifact (``DiffResult.evidence_tiers``, P0 evidence-provider audit).

    ``ARTIFACT_PROVEN`` means "L0/L1/L2 artifact evidence confirms a shipped
    ABI break" (see :class:`EvidenceStatus`) — but the kind-only classifier
    can't see whether *this run* actually had that evidence, only that the
    detector emitting this kind is only ever supposed to run with it. A
    comparison built from hand-loaded/hand-built snapshots and never routed
    through a real binary (``evidence_tiers == ["header"]``, e.g. a direct
    Python-API caller) can still surface a BREAKING_KINDS finding — the
    partition itself isn't wrong, but claiming that specific run's finding
    is "artifact_proven" would be. Downgrades exactly that case to
    :attr:`EvidenceStatus.UNATTRIBUTED`; every other kind/tier combination is
    unchanged from :func:`evidence_status_for_change`.

    *evidence_tiers* defaults to ``()`` — the "unknown" case
    :func:`has_binary_evidence` already treats as "assume evidence was
    examined", so a caller that can't easily thread
    ``DiffResult.evidence_tiers`` through gets the exact same result as
    calling :func:`evidence_status_for_change` directly.
    """
    status = evidence_status_for_change(change)
    if status is EvidenceStatus.ARTIFACT_PROVEN and not has_binary_evidence(
        evidence_tiers
    ):
        return EvidenceStatus.UNATTRIBUTED
    return status


def compute_verdict(
    changes: Sequence[HasKind], *, policy: str = "strict_abi"
) -> Verdict:
    """Compute verdict from a list of changes, honoring the given policy profile.

    Policy profiles:
    - ``strict_abi`` (default): full BREAKING / API_BREAK sets apply.
    - ``sdk_vendor``: source-level-only kinds (rename, access) downgraded
      from API_BREAK → COMPATIBLE (no warning for SDK consumers).
    - ``plugin_abi``: calling-convention kinds (CALLING_CONVENTION_CHANGED,
      FRAME_REGISTER_CHANGED, VALUE_ABI_TRAIT_CHANGED) downgraded from
      BREAKING → COMPATIBLE. Only valid when plugin and host are always
      rebuilt together from the same toolchain.

    Unknown policy names fall back to ``strict_abi``.
    """
    if not changes:
        return Verdict.NO_CHANGE

    sets = policy_kind_sets(policy)
    # Per-finding effective category (ADR-025 D4.1): a finding's own
    # ``effective_verdict`` override wins over its kind's category; the overall
    # verdict is the worst contributed category. With no overrides this is
    # identical to the historical kind-set intersection.
    verdicts = {effective_category(c, *sets) for c in changes}
    if Verdict.BREAKING in verdicts:
        return Verdict.BREAKING
    if Verdict.API_BREAK in verdicts:
        return Verdict.API_BREAK
    if Verdict.COMPATIBLE_WITH_RISK in verdicts:
        return Verdict.COMPATIBLE_WITH_RISK  # binary-compat, deployment risk only
    return Verdict.COMPATIBLE


# ---------------------------------------------------------------------------
# Deprecated aliases — kept for external consumers; will be removed in v2.0
# ---------------------------------------------------------------------------
#: Deprecated: use :data:`Verdict.API_BREAK`
SOURCE_BREAK: Verdict = Verdict.API_BREAK  # deprecated alias

#: Deprecated: use :data:`API_BREAK_KINDS`
SOURCE_BREAK_KINDS = API_BREAK_KINDS  # noqa: E305
