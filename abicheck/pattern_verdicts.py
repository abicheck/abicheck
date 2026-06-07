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

"""Pattern-aware verdict modulation (ADR-027 A4 / D4).

A post-processing pass that runs after detectors produce :class:`Change`
objects and before policy classification — the structural twin of ADR-024's
``FilterNonPublicSurface``. It uses the idiom/anti-pattern evidence from
:mod:`abicheck.idioms` (recomputed from both snapshots' declaration graphs) to:

- **demote** a layout change on a provably-opaque or PIMPL-hidden type to
  compatible (reason ``opaque-by-construction`` / ``pimpl-impl-hidden``), and
- **raise** new breaks when an opacity/handle guarantee callers relied on is
  *lost* (``OPAQUE_INVARIANT_BROKEN`` / ``HANDLE_TYPE_CHANGED``), and
- **annotate** findings sitting on a recognised ABI anti-pattern surface.

The governing contract is inherited verbatim from ADR-024: a modulation may
**demote with a disclosed reason** or **raise** a finding; it may **never
silently delete** one. Every modulation is recorded in the returned ledger,
attributed to the rule that made it, and reversed by ``--no-pattern-verdicts``.

Anti-hiding guards:

- Demotion is gated to the ``HEADER_AWARE`` evidence tier — the idiom evidence
  needs the AST (D4.1 "confidence floor by tier").
- A demotion requires the idiom to hold on **both** snapshots; if opaqueness
  was lost, the opaque demote does not fire and an ``OPAQUE_INVARIANT_BROKEN``
  break is emitted instead (never a silent demotion).
- A ``frozen_namespace_violation`` finding is never demoted.
- The PIMPL demote fires only when the wrapper's *own* layout is byte-identical
  across both snapshots and only the hidden pointee changed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .checker_policy import ChangeKind, EvidenceTier, Verdict
from .checker_types import Change
from .idioms import (
    AntiPattern,
    Idiom,
    IdiomTag,
    _public_pointer_only,
    detect_antipatterns,
    recognise_idioms,
)
from .model import AbiSnapshot
from .surface_graph import SurfaceGraph, build_surface_graph

logger = logging.getLogger(__name__)

# Layout-shaped findings the opaque/PIMPL demote rules may act on.
_LAYOUT_KINDS = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.FIELD_BITFIELD_CHANGED,
    }
)


@dataclass
class PatternModulation:
    """One disclosed pattern-aware modulation (ADR-027 D4.3 ledger row)."""

    symbol: str
    original_category: str
    new_category: str
    rule_id: str
    reason: str
    evidence_tier: str
    edges_matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "original_category": self.original_category,
            "new_category": self.new_category,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "evidence_tier": self.evidence_tier,
            "edges_matched": list(self.edges_matched),
        }


def _names(name: str) -> set[str]:
    """A name plus its unqualified short form, for fuzzy idiom matching."""
    return {name, name.rsplit("::", 1)[-1]}


def _tags_for(idioms: dict[str, list[IdiomTag]], name: str) -> list[IdiomTag]:
    out: list[IdiomTag] = []
    for n in _names(name):
        out.extend(idioms.get(n, []))
    return out


def _has_idiom(
    idioms: dict[str, list[IdiomTag]], name: str, idiom: Idiom
) -> IdiomTag | None:
    for t in _tags_for(idioms, name):
        if t.idiom == idiom:
            return t
    return None


def _verdict_label(v: Verdict) -> str:
    return v.value.lower()


def _record_by_name(snap: AbiSnapshot, name: str) -> object | None:
    short = name.rsplit("::", 1)[-1]
    for rec in snap.types:
        if rec.name == name or rec.name.rsplit("::", 1)[-1] == short:
            return rec
    return None


def apply_pattern_verdicts(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    evidence_tier: EvidenceTier,
    enabled: bool = True,
) -> list[dict[str, object]]:
    """Modulate *changes* in place using idiom evidence; return the ledger.

    Demotions set ``Change.effective_verdict`` / ``modulation_reason`` /
    ``modulation_rule`` on the existing finding (re-categorised in place, never
    dropped). Lost-invariant transitions are appended to *changes* as new
    ``OPAQUE_INVARIANT_BROKEN`` / ``HANDLE_TYPE_CHANGED`` breaks. The returned
    list is the JSON/SARIF ``pattern_modulations`` ledger.
    """
    if not enabled:
        return []

    old_graph = build_surface_graph(old)
    new_graph = build_surface_graph(new)
    old_idioms = recognise_idioms(old_graph)
    new_idioms = recognise_idioms(new_graph)
    old_aps = detect_antipatterns(old_graph)
    new_aps = detect_antipatterns(new_graph)
    tier = (
        evidence_tier.value if isinstance(evidence_tier, EvidenceTier) else "elf_only"
    )
    demote_allowed = (
        isinstance(evidence_tier, EvidenceTier)
        and evidence_tier.rank >= EvidenceTier.HEADER_AWARE.rank
    )

    ledger: list[PatternModulation] = []

    # 1. Lost-invariant transitions (raises) — emitted before demotion so a type
    #    that *lost* opaqueness is never both demoted and flagged.
    transitions = _emit_lost_invariants(
        changes, old, new, old_graph, new_graph, old_idioms, new_idioms, tier
    )
    changes.extend(t for t, _ in transitions)
    ledger.extend(m for _, m in transitions)

    # 2. Newly-introduced anti-patterns (old clean → new dirty), as RISK findings
    #    — pre-existing debt is not nagged about on every run (D2.2).
    new_ap_transitions = _emit_new_antipatterns(changes, old_aps, new_aps, tier)
    changes.extend(t for t, _ in new_ap_transitions)
    ledger.extend(m for _, m in new_ap_transitions)

    # 3. Per-finding modulation of existing changes.
    for c in changes:
        m = _modulate_change(
            c, old, new, old_idioms, new_idioms, new_aps, tier, demote_allowed
        )
        if m is not None:
            ledger.append(m)

    return [m.to_dict() for m in ledger]


def _emit_new_antipatterns(
    changes: list[Change],
    old_aps: list[AntiPattern],
    new_aps: list[AntiPattern],
    tier: str,
) -> list[tuple[Change, PatternModulation]]:
    """Emit RISK findings for anti-patterns present in new but not old."""
    old_keys = {(a.kind, a.symbol) for a in old_aps}
    existing = {(c.kind, c.symbol) for c in changes}
    out: list[tuple[Change, PatternModulation]] = []
    for ap in new_aps:
        key = (ap.kind, ap.symbol)
        if key in old_keys or key in existing:
            continue
        existing.add(key)
        change = Change(
            kind=ap.kind,
            symbol=ap.symbol,
            description=ap.description,
            modulation_reason="anti-pattern-introduced",
            modulation_rule="new-anti-pattern",
        )
        out.append(
            (
                change,
                PatternModulation(
                    symbol=ap.symbol,
                    original_category=_verdict_label(Verdict.COMPATIBLE),
                    new_category="risk",
                    rule_id="new-anti-pattern",
                    reason="anti-pattern-introduced",
                    evidence_tier=tier,
                    edges_matched=list(ap.evidence),
                ),
            )
        )
    return out


def _emit_lost_invariants(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_graph: SurfaceGraph,
    new_graph: SurfaceGraph,
    old_idioms: dict[str, list[IdiomTag]],
    new_idioms: dict[str, list[IdiomTag]],
    tier: str,
) -> list[tuple[Change, PatternModulation]]:
    """OPAQUE_INVARIANT_BROKEN + HANDLE_TYPE_CHANGED (D2.2 transitions)."""
    out: list[tuple[Change, PatternModulation]] = []
    existing = {(c.kind, c.symbol) for c in changes}

    # --- Opaque invariant lost --------------------------------------------
    # Scoped to OPAQUE_POINTER: those are the types whose *layout* callers
    # provably could not observe. A PIMPL wrapper is already a complete,
    # by-value-usable type, so "gains a by-value use" loses nothing for it; a
    # change to a PIMPL wrapper's own layout is caught by the normal layout
    # detectors (and is never demoted — see the PIMPL guard), so it needs no
    # separate transition here.
    for name, tags in old_idioms.items():
        if not any(t.idiom == Idiom.OPAQUE_POINTER for t in tags):
            continue
        new_rec = _record_by_name(new, name)
        if new_rec is None:
            continue  # removed entirely → handled by TYPE_REMOVED, not this
        # Opacity is lost only when callers can now observe the layout: either
        # the complete definition is now visible (no longer incomplete), or a
        # public function now crosses the type by value. A type that is still
        # incomplete and still crossed only by pointer (or no longer referenced
        # at all) keeps its opacity — emitting a break there is a false positive
        # (e.g. removing the last `Ctx*` use while keeping the forward decl).
        definition_now_visible = not getattr(new_rec, "is_opaque", False)
        referenced, only_pointer = _public_pointer_only(new_graph, name)
        by_value_use = referenced and not only_pointer
        if not (definition_now_visible or by_value_use):
            continue
        key = (ChangeKind.OPAQUE_INVARIANT_BROKEN, name)
        if key in existing:
            continue
        why = (
            "definition now visible"
            if definition_now_visible
            else "now passed by value"
        )
        edges = [f"{name} was opaque in old; {why} in new"]
        change = Change(
            kind=ChangeKind.OPAQUE_INVARIANT_BROKEN,
            symbol=name,
            description=(
                f"{name} was an opaque type callers could not observe; its "
                f"layout is now visible (definition exposed or passed by value), "
                f"so its size/fields are now part of the ABI"
            ),
            modulation_reason="opaque-invariant-broken",
            modulation_rule="lost-opaque-invariant",
        )
        existing.add(key)
        logger.warning("pattern-verdict raise: %s lost opaque invariant", name)
        out.append(
            (
                change,
                PatternModulation(
                    symbol=name,
                    original_category=_verdict_label(Verdict.COMPATIBLE),
                    new_category=_verdict_label(Verdict.BREAKING),
                    rule_id="lost-opaque-invariant",
                    reason="opaque-invariant-broken",
                    evidence_tier=tier,
                    edges_matched=edges,
                ),
            )
        )

    # --- Handle token type changed ----------------------------------------
    for alias, tags in old_idioms.items():
        if not any(t.idiom == Idiom.HANDLE for t in tags):
            continue
        if alias not in old.typedefs or alias not in new.typedefs:
            continue
        old_target = old.typedefs[alias].strip()
        new_target = new.typedefs[alias].strip()
        if old_target == new_target:
            continue
        key = (ChangeKind.HANDLE_TYPE_CHANGED, alias)
        if key in existing:
            continue
        edges = [f"handle typedef {alias}: {old_target!r} -> {new_target!r}"]
        change = Change(
            kind=ChangeKind.HANDLE_TYPE_CHANGED,
            symbol=alias,
            description=(
                f"opaque handle typedef {alias} changed its underlying token type "
                f"from {old_target!r} to {new_target!r}"
            ),
            old_value=old_target,
            new_value=new_target,
            modulation_reason="handle-token-changed",
            modulation_rule="handle-token-changed",
        )
        existing.add(key)
        logger.warning("pattern-verdict raise: handle %s token type changed", alias)
        out.append(
            (
                change,
                PatternModulation(
                    symbol=alias,
                    original_category=_verdict_label(Verdict.COMPATIBLE),
                    new_category=_verdict_label(Verdict.BREAKING),
                    rule_id="handle-token-changed",
                    reason="handle-token-changed",
                    evidence_tier=tier,
                    edges_matched=edges,
                ),
            )
        )

    return out


def _modulate_change(
    c: Change,
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_idioms: dict[str, list[IdiomTag]],
    new_idioms: dict[str, list[IdiomTag]],
    new_aps: list[AntiPattern],
    tier: str,
    demote_allowed: bool,
) -> PatternModulation | None:
    """Apply the per-finding modulation rules; return a ledger row or None."""
    # Never override a frozen-namespace break or a transition we just emitted.
    if c.frozen_namespace_violation is not None:
        return None
    if c.kind in (
        ChangeKind.OPAQUE_INVARIANT_BROKEN,
        ChangeKind.HANDLE_TYPE_CHANGED,
        ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE,
        ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR,
    ):
        return None
    if c.effective_verdict is not None:
        return None

    if c.kind in _LAYOUT_KINDS:
        # Rule: opaque-pointer layout (demote).
        if demote_allowed:
            tag_old = _has_idiom(old_idioms, c.symbol, Idiom.OPAQUE_POINTER)
            tag_new = _has_idiom(new_idioms, c.symbol, Idiom.OPAQUE_POINTER)
            if (
                tag_old is not None
                and tag_new is not None
                and tag_old.definition_hidden
                and tag_new.definition_hidden
            ):
                return _demote(
                    c,
                    "opaque-pointer-layout",
                    "opaque-by-construction",
                    tier,
                    list(tag_new.evidence),
                )
            # Rule: PIMPL pointee-only (demote).
            pimpl = _pimpl_pointee_match(c.symbol, old_idioms, new_idioms)
            if pimpl is not None:
                return _demote(
                    c,
                    "pimpl-pointee-only",
                    "pimpl-impl-hidden",
                    tier,
                    pimpl,
                )

    # Rule: anti-pattern raise (annotate; never hides).
    note = _antipattern_annotation(c, new_aps)
    if note is not None:
        rule_id, edges = note
        # Pure annotation: the finding's category is unchanged (a raise can
        # never hide), so original == new in the ledger.
        cat = "annotated"
        c.modulation_reason = c.modulation_reason or "anti-pattern-elevated-risk"
        c.modulation_rule = c.modulation_rule or rule_id
        return PatternModulation(
            symbol=c.symbol,
            original_category=cat,
            new_category=cat,
            rule_id=rule_id,
            reason="anti-pattern-elevated-risk",
            evidence_tier=tier,
            edges_matched=edges,
        )
    return None


def _demote(
    c: Change,
    rule_id: str,
    reason: str,
    tier: str,
    edges: list[str],
) -> PatternModulation:
    original = "breaking"
    c.effective_verdict = Verdict.COMPATIBLE
    c.modulation_reason = reason
    c.modulation_rule = rule_id
    logger.warning(
        "pattern-verdict demote: %s (%s) -> compatible [%s]",
        c.symbol,
        c.kind.value,
        reason,
    )
    return PatternModulation(
        symbol=c.symbol,
        original_category=original,
        new_category="compatible",
        rule_id=rule_id,
        reason=reason,
        evidence_tier=tier,
        edges_matched=edges,
    )


def _pimpl_pointee_match(
    pointee: str,
    old_idioms: dict[str, list[IdiomTag]],
    new_idioms: dict[str, list[IdiomTag]],
) -> list[str] | None:
    """Return evidence if *pointee* is the hidden impl of a PIMPL wrapper whose
    own layout is unchanged across both snapshots (D4.1 PIMPL guard)."""
    short = pointee.rsplit("::", 1)[-1]
    for wrapper, tags in old_idioms.items():
        for t in tags:
            if t.idiom != Idiom.PIMPL or t.hidden_pointee is None:
                continue
            if t.hidden_pointee not in (pointee, short):
                continue
            # Find the matching wrapper tag in new and require identical layout.
            new_tag = _has_idiom(new_idioms, wrapper, Idiom.PIMPL)
            if new_tag is None:
                continue
            if t.layout_signature != new_tag.layout_signature:
                # The wrapper's own layout changed — that is a real break.
                continue
            return [
                f"{pointee} is the hidden impl of PIMPL {wrapper}; "
                f"wrapper layout byte-identical across versions"
            ]
    return None


def _antipattern_annotation(
    c: Change, new_aps: list[AntiPattern]
) -> tuple[str, list[str]] | None:
    """If *c* sits on a recognised ABI anti-pattern surface, return (rule, edges)."""
    short = c.symbol.rsplit("::", 1)[-1]
    edges: list[str] = []
    rule_id = ""
    for ap in new_aps:
        if ap.symbol in (c.symbol, short) or ap.symbol.rsplit("::", 1)[-1] == short:
            edges.extend(ap.evidence)
            rule_id = "anti-pattern-raise"
    if edges:
        return rule_id, edges
    return None
