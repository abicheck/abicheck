# SPDX-License-Identifier: Apache-2.0
"""Aggregate binary-provenance check for source-replay surfaces (ADR-030 D6).

Split out of ``source_diff`` to keep that module under its line budget: this
block is the one responsibility that compares a surface's public declarations
against its linked binary's exported symbols.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from .source_abi import EVIDENCE_TIER_L4, SourceAbiSurface

# A1 thresholds: the aggregate provenance signal only fires on a *strong* signal
# (almost the whole public surface fails to map) over a non-trivial surface, so a
# legitimately inline/template-heavy header is not mistaken for a wrong checkout.
_PROVENANCE_MIN_DECLS = 5
_PROVENANCE_MISS_THRESHOLD = 0.8
# Declaration kinds that produce a linker symbol (and so are *expected* to map to
# an export). Mirrors source_link's exportable-declaration routing — free
# functions/variables plus C++ class members (method/constructor/destructor),
# which `_route_entity` also treats as mappable declarations, so a method-only
# class API still trips the check (Codex). Excludes
# constexpr/typedef/record/enum/macro/inline/template, which do not export and
# would otherwise inflate the miss ratio.
_PROVENANCE_EXPORTABLE_KINDS = frozenset(
    {"function", "variable", "method", "constructor", "destructor"}
)
# C / extern "C" decls export under their plain qualified_name when the extractor
# leaves mangled_name empty, so a missing mangle is a usable fallback symbol for
# these kinds only. For C++ members (method/constructor/destructor) the
# qualified_name is the class-scoped name, NOT the export symbol — and castxml
# routinely leaves constructors/destructors unmangled — so we must require a real
# mangled symbol there rather than comparing the class name against L0's mangled
# ctor symbols and false-firing on a correct ctor-only API (Codex).
_PROVENANCE_QNAME_FALLBACK_KINDS = frozenset({"function", "variable"})


def _provenance_finding(side: str, surface: SourceAbiSurface) -> Change | None:
    """One aggregate provenance finding for a single surface, or ``None``.

    Fires when the surface has L0 exports but the large majority of its public
    declarations fail to map to any exported symbol — the checkout likely does
    not correspond to the binary. Inert when no exports are plumbed in or the
    surface is too small to judge.
    """
    exports = set(surface.roots.get("exported_symbols", []))
    if not exports:
        return None
    # Count only declarations that are *expected* to export: function/variable
    # decls, keyed by the same symbol the linker uses — ``mangled_name`` for C++,
    # the plain ``qualified_name`` for C / extern "C" decls whose extractor leaves
    # the mangled name empty (Codex). Inline bodies, uninstantiated templates,
    # macros and types live in their own surface buckets / non-exporting kinds and
    # legitimately map to nothing, so restricting to function/variable kinds keeps
    # a header-only or constexpr-heavy public surface from inflating the miss
    # ratio and false-firing against the correct binary (review).
    # Deduplicate by entity identity first: link_source_abi appends the same
    # public declaration once per including TU, so a header pulled into five
    # compile units would otherwise count one unmapped function five times and
    # clear _PROVENANCE_MIN_DECLS on a trivial surface (Codex). Key by identity
    # (the same key the rest of this module uses) so the threshold and miss ratio
    # are measured over the unique public surface.
    by_identity: dict[str, str] = {}
    for d in surface.reachable_declarations:
        if d.kind not in _PROVENANCE_EXPORTABLE_KINDS:
            continue
        key = d.identity()
        if not key:
            continue
        if d.kind in _PROVENANCE_QNAME_FALLBACK_KINDS:
            sym = d.mangled_name or d.qualified_name
        else:
            # C++ member: only a real mangled symbol is comparable to L0.
            sym = d.mangled_name
        if sym:
            by_identity[key] = sym
    expected = list(by_identity.values())
    if len(expected) < _PROVENANCE_MIN_DECLS:
        return None
    misses = sum(1 for sym in expected if sym not in exports)
    if misses / len(expected) < _PROVENANCE_MISS_THRESHOLD:
        return None
    return Change(
        kind=ChangeKind.SOURCE_BINARY_PROVENANCE_MISMATCH,
        symbol="",
        description=(
            f"{misses}/{len(expected)} exportable public declarations on the "
            f"{side} side do not map to any exported binary symbol — that source "
            "checkout likely does not correspond to its binary (wrong tag/commit). "
            "Treat the L4/L5 source findings for this pair as unreliable until the "
            "sources are checked out at the binary's build tag."
        ),
        old_value="",
        new_value=f"{side}: {misses}/{len(expected)} unmapped",
        source_location=f"[{EVIDENCE_TIER_L4}]",
    )


def _diff_provenance(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """A1: aggregate source↔binary correspondence check on *both* surfaces.

    A wrong checkout poisons the L4/L5 facts on whichever side it sits, and
    ``diff_source_abi`` receives both embedded surfaces, so the mapping-miss
    heuristic is run for the baseline *and* the current side (Codex review) — a
    mismatched baseline is just as untrustworthy as a mismatched target. Emits at
    most one aggregate RISK finding per side; the per-declaration mismatches are
    already covered by :func:`_diff_mappings`. Per ADR-028 D3 it is a context
    risk, never a proven binary break.
    """
    findings = [
        _provenance_finding("baseline", old),
        _provenance_finding("current", new),
    ]
    return [c for c in findings if c is not None]


# -- generated headers -------------------------------------------------------
