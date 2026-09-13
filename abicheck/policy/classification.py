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

"""Change-kind classification sets, the policy registry, and verdict
computation (ADR-061 gap B).

Classification sets (``BREAKING_KINDS``, ``COMPATIBLE_KINDS``, etc.) and
``IMPACT_TEXT`` are DERIVED from the single-declaration registry in
``change_registry.py``. Adding a new ``ChangeKind`` requires only one entry
there — no shotgun surgery.

Hierarchy (5-tier)::

    BREAKING_KINDS      -> category 1: binary ABI incompatibilities
    API_BREAK_KINDS     -> category 2a: source-level breaks (recompilation required)
    RISK_KINDS          -> category 2b: binary-compatible but deployment risk present
    QUALITY_KINDS       -> category 3: problematic behaviors (COMPATIBLE minus additions)
    ADDITION_KINDS      -> category 4: new API surface (subset of COMPATIBLE_KINDS)

    COMPATIBLE_KINDS    = ADDITION_KINDS | QUALITY_KINDS

This is the real owner of what used to be ``abicheck/checker_policy.py``'s
verdict/kind-set half (see :mod:`abicheck.policy.evidence_status` for the
epistemic-status/evolution half, split out purely to keep each module under
the 800-line new-file ceiling). ``abicheck/checker_policy.py`` is now a thin
compatibility facade re-exporting both modules' public surface unchanged —
canonical internal callers (this package, ``workflows``, ``report``,
``compare``) import this module directly; the flat facade remains for the
one caller that structurally cannot (``model``-owned, legacy
``checker_types.DiffResult`` — see the facade's own module docstring for
why it stays unclassified rather than becoming a `legacy_paths` entry here).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..change_registry import REGISTRY as _REGISTRY, Verdict as Verdict
from ..model.change_catalog.kinds import ChangeKind as ChangeKind, HasKind as HasKind
from ..model.change_catalog.registry import VALID_BASE_POLICIES as VALID_BASE_POLICIES
from .evidence_status import (
    EvidenceStatus,
    has_binary_evidence,
    is_cross_source_persistent,
)

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


#: Appended to a kind's static ``impact`` text when the finding's own
#: ``EvidenceStatus`` is ``UNATTRIBUTED`` (Finding C(ii)) -- several
#: ``BREAKING_KINDS`` impact strings assert an unconditional consequence
#: ("dynamic linker will refuse to load or crash", "heap/stack corruption")
#: that is only true when the run's evidence actually backs the finding
#: (see :func:`abicheck.policy.evidence_status.evidence_status_for_result`).
#: ``UNATTRIBUTED`` covers two distinct, evidence-type-varying cases -- an
#: ``"elf"``-tiered run that examined a real symbol table but found no
#: matching entry for *this* finding, and a run with no binary evidence at
#: all, of *any* kind, not only symbol-table-backed ones (a header-only
#: ``type_size_changed``/``enum_member_value_changed`` gets the identical
#: status). Deliberately kind-agnostic, evidence-type-agnostic wording --
#: neither "no matching symbol-table entry" (wrong for a struct-layout or
#: DWARF-only fact, confirmed by golden-output regression: a header-only
#: type_size_changed carried it too) nor "no search happened" (misstates
#: the elf-tiered case) is accurate for both cases at once (Codex review).
_UNATTRIBUTED_IMPACT_CAVEAT = (
    " (Evidence note: this run's available evidence does not fully confirm "
    "this specific finding -- treat the consequence above as plausible, "
    "not confirmed.)"
)


def impact_for(kind: ChangeKind, evidence_status: EvidenceStatus | None = None) -> str:
    """Return human-readable impact explanation for a ChangeKind, or empty string.

    *evidence_status*, when given as :attr:`EvidenceStatus.UNATTRIBUTED`,
    appends :data:`_UNATTRIBUTED_IMPACT_CAVEAT` -- see that constant's
    docstring. Optional and keyword-compatible with every pre-existing
    call site: omitting it (or passing ``None``/any other status) leaves
    the returned text byte-identical to before this parameter existed.
    """
    text = IMPACT_TEXT.get(kind, "")
    if text and evidence_status is EvidenceStatus.UNATTRIBUTED:
        text += _UNATTRIBUTED_IMPACT_CAVEAT
    return text


def impact_caveat_for(evidence_status: EvidenceStatus | None) -> str:
    """Return the standalone evidence caveat sentence, or ``""``.

    A thin public accessor over :data:`_UNATTRIBUTED_IMPACT_CAVEAT` for a
    caller that cannot embed a full :func:`impact_for` string into its own
    text (SARIF's per-result ``message.text``, whose matching rule-level
    ``fullDescription`` is shared across every finding of the same
    ``ChangeKind`` and so cannot itself carry a per-finding caveat).
    Returns ``""`` for any status other than
    :attr:`EvidenceStatus.UNATTRIBUTED`, so an unconditional call site stays
    a no-op for every other status -- including ``None``.
    """
    return (
        _UNATTRIBUTED_IMPACT_CAVEAT.strip()
        if evidence_status is EvidenceStatus.UNATTRIBUTED
        else ""
    )


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


def excluded_from_verdict_as_persistent_hygiene(
    change: HasKind,
    breaking: frozenset[ChangeKind],
    api_break: frozenset[ChangeKind],
    compatible: frozenset[ChangeKind],
    risk: frozenset[ChangeKind],
) -> bool:
    """Whether *change* is pre-existing hygiene debt that must not drive the
    pairwise verdict.

    Two conditions, both required, and the second is what keeps this safe:

    1. The finding is a cross-source hygiene finding stamped
       :attr:`~abicheck.policy.evidence_status.CrossSourceEvolution.PERSISTENT`
       -- the identical problem is present on OLD and on NEW, so this
       release changed nothing about it.
    2. It resolves, **under the active policy and including any per-finding
       ``effective_verdict`` override**, to ``COMPATIBLE_WITH_RISK``.

    Condition 2 is not a formality. Two of the eleven cross-source checks
    carry ``API_BREAK`` by default (``odr_type_variant``,
    ``header_build_context_mismatch``), and a persistent ODR violation is a
    real defect *in the candidate*, not bookkeeping -- excluding it would
    hide a genuine source-level break on the grounds that it was already
    there. Resolving the category through :func:`effective_category` rather
    than the kind's intrinsic set is what also makes a deliberate policy
    promotion win: a project that overrides ``exported_not_public`` to
    ``breaking`` has said it wants to be gated on it, and this predicate
    then answers ``False``.

    Deliberately parallel to the ``RESOLVED`` exclusion
    (``is_cross_source_resolved``, plan §7 F-9) but at a different
    chokepoint: ``RESOLVED`` is zeroed at the *gate/exit-code* layer,
    ``PERSISTENT`` at the *verdict* layer. A ``RESOLVED`` finding names a
    problem the candidate no longer has; a ``PERSISTENT`` one names a
    problem the candidate has and the baseline had too. Neither is a change,
    and neither is suppressed: both stay in the report, tagged.
    """
    if not is_cross_source_persistent(change):
        return False
    return (
        effective_category(change, breaking, api_break, compatible, risk)
        == Verdict.COMPATIBLE_WITH_RISK
    )


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


#: ``BREAKING_KINDS`` members whose removal/visibility-change detector
#: (``diff_symbols._check_removed_function``/``_var_removed``,
#: ``diff_platform._diff_elf_deleted_fallback``) always stamps
#: ``Change.symbol_binding`` from a real observed ELF/PE/Mach-O symbol table
#: entry when the finding is genuinely backed by one — see that field's own
#: docstring on ``checker_types.Change``. Scoped to exactly those kinds: most
#: ``BREAKING_KINDS`` members (e.g. ``TYPE_SIZE_CHANGED``) never populate
#: ``symbol_binding`` regardless of how solid their evidence is, so treating
#: an unset ``symbol_binding`` as suspect for every ``BREAKING_KINDS`` kind
#: would misclassify those as unattributed too.
#:
#: ``SYMBOL_RENAMED_BATCH`` (Codex review, Finding C(i)) is a rollup of
#: several removed/added pairs rather than one detector call, but
#: ``diff_symbols_renames.emit_prefix_batch_rename``/
#: ``compare.namespace_move.emit_namespace_move_batches`` stamp the same
#: field with the aggregated, weakest-link answer: truthy only when *every*
#: constituent pair's OLD-side declaration carries a real observed ELF
#: binding. Membership here is what makes the per-finding check below apply
#: that aggregation the same way it applies a single ``FUNC_REMOVED``'s own
#: binding.
_ELF_BINDING_STAMPED_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        ChangeKind.FUNC_DELETED_ELF_FALLBACK,
        ChangeKind.SYMBOL_RENAMED_BATCH,
    }
)


def evidence_status_for_result(
    change: HasKind, evidence_tiers: Sequence[str] = ()
) -> EvidenceStatus | None:
    """:func:`evidence_status_for_change`, refined by two facts neither the
    kind alone nor the comparison alone can see: whether this comparison
    ever actually examined a real binary artifact (``DiffResult.
    evidence_tiers``, P0 evidence-provider audit), and — independently —
    whether *this specific finding* was ever matched against one.

    ``ARTIFACT_PROVEN`` means "L0/L1/L2 artifact evidence confirms a shipped
    ABI break" (see :class:`EvidenceStatus`) — but the kind-only classifier
    can't see whether *this run* actually had that evidence, only that the
    detector emitting this kind is only ever supposed to run with it.

    - **Comparison-level gap**: a comparison built from hand-loaded/
      hand-built snapshots and never routed through a real binary
      (``evidence_tiers == ["header"]``, e.g. a direct Python-API caller)
      can still surface a BREAKING_KINDS finding — the partition itself
      isn't wrong, but claiming that specific run's finding is
      "artifact_proven" would be.
    - **Per-finding gap**: even a comparison that *did* examine a real ELF
      symbol table (``evidence_tiers`` includes ``"elf"``) can produce a
      ``BREAKING_KINDS`` finding synthesized from header-only evidence
      with no corresponding export — e.g. an overload-set member
      reconstructed from declarations alone, never actually matched
      against a mangled ELF symbol. For the kinds whose detector always
      stamps ``Change.symbol_binding`` from a real observed symbol-table
      entry when one backs the finding (:data:`_ELF_BINDING_STAMPED_KINDS`),
      an unset ``symbol_binding`` on an ``"elf"``-tiered run is the
      positive signal that no such entry was ever observed for *this*
      finding, independent of what the run as a whole examined. Scoped to
      ``"elf"`` specifically (not ``"pe"``/``"macho"``) because
      ``symbol_binding`` mirrors ``Function.elf_binding``/``Variable.
      elf_binding`` and is never populated on a PE/Mach-O run regardless
      of evidence quality — checking it there would misdowngrade every
      genuine PE/Mach-O removal.

    Both gaps downgrade to :attr:`EvidenceStatus.UNATTRIBUTED`; every other
    kind/tier/finding combination is unchanged from
    :func:`evidence_status_for_change`.

    *evidence_tiers* defaults to ``()`` — the "unknown" case
    :func:`has_binary_evidence` already treats as "assume evidence was
    examined", so a caller that can't easily thread
    ``DiffResult.evidence_tiers`` through gets the exact same result as
    calling :func:`evidence_status_for_change` directly.
    """
    status = evidence_status_for_change(change)
    if status is not EvidenceStatus.ARTIFACT_PROVEN:
        return status
    if not has_binary_evidence(evidence_tiers):
        return EvidenceStatus.UNATTRIBUTED
    # ``symbol_binding`` is ELF-specific (``Function.elf_binding``/
    # ``Variable.elf_binding``) -- it is never populated for a PE/Mach-O
    # comparison regardless of evidence quality, so the per-finding check
    # below only applies when this run's own evidence_tiers says "elf" was
    # actually examined. Without this guard, every genuine PE/Mach-O
    # BREAKING_KINDS removal would be misdowngraded to UNATTRIBUTED.
    kind = getattr(change, "kind", None)
    if (
        "elf" in evidence_tiers
        and kind in _ELF_BINDING_STAMPED_KINDS
        and not getattr(change, "symbol_binding", None)
    ):
        return EvidenceStatus.UNATTRIBUTED
    return status
