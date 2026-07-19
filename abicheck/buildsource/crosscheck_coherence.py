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

"""Evidence-coherence cross-checks (AC-008 / AC-009).

Split out of ``crosscheck.py`` to keep that module under the AI-readiness
2000-line hard cap, the same pattern as ``export_accounting.py``. Holds the two
checks that validate the *coherence* of the merged evidence sources — that a
build target's L3 compile units agree on their ABI-relevant compile context
(``compile_context_conflict``) and that the linked L4 source surface actually
describes the analyzed binary (``source_surface_dso_mismatch``).

These are ordinary ``run_crosschecks`` runners; ``crosscheck.py`` imports them
inside ``run_crosschecks`` when assembling the runner table. The shared
finding/coverage plumbing (``_change``, ``_CheckOutput``,
``_exported_symbol_names``, the provider constants) comes from the leaf
``crosscheck_base`` — **not** from ``crosscheck`` — so this module never depends
on the engine and the ``crosscheck`` → check-module dependency stays
one-directional (no import cycle; CLAUDE.md "M1-3"). Neither check reads the
``CrosscheckConfig`` argument, so it is typed loosely to avoid importing the
config type from ``crosscheck``.
"""
from __future__ import annotations

from typing import Any

from ..checker_policy import ChangeKind
from ..checker_types import Change
from ..model import AbiSnapshot
from .crosscheck_base import (
    PROVIDER_BINARY_EXPORTS,
    PROVIDER_BUILD_CONFIG,
    PROVIDER_SOURCE_INDEX,
    _change,
    _CheckOutput,
    _linked_export_symbols,
)

#: ABI-relevant compiler-flag families whose positive/negative both change the
#: produced ABI. A build target whose compile units disagree on one of these
#: compiled part of itself under a different ABI (the AC-008 oneTBB
#: ``-fno-rtti`` umbrella case). All three are **on by default and C++-only**:
#: C translation units have no RTTI / thread-safe-static-guard ABI and their
#: exception model is a separate axis, so a C unit never participates in a
#: family's conflict (Codex review).
_ABI_FLAG_FAMILIES: tuple[tuple[str, str], ...] = (
    ("-frtti", "-fno-rtti"),
    ("-fexceptions", "-fno-exceptions"),
    ("-fthreadsafe-statics", "-fno-threadsafe-statics"),
)

#: ``CompileUnit.language`` spellings that mean C++ (the adapter emits ``"CXX"``;
#: accept common aliases defensively).
_CPP_LANGUAGES: frozenset[str] = frozenset({"CXX", "C++", "CPP", "OBJC++", "OBJECTIVE-C++"})


def _effective_flag_mode(flags: list[str], pos: str, neg: str) -> str | None:
    """Return the *effective* mode of one flag family within a unit's ``argv``.

    ``abi_relevant_flags`` preserves compiler argument order, so a later token
    overrides an earlier one (``-fno-rtti -frtti`` is effectively RTTI-on). Return
    ``"pos"``/``"neg"`` for the last token of the family seen, or ``None`` when the
    unit names neither spelling (a raw membership test would misread the
    override as a conflict — Codex review)."""
    mode: str | None = None
    for f in flags:
        if f == pos:
            mode = "pos"
        elif f == neg:
            mode = "neg"
    return mode


def _check_compile_context_conflict(
    snapshot: AbiSnapshot, cfg: Any
) -> _CheckOutput:
    """L3 compile units of one build target carry conflicting ABI contexts, RISK.

    Groups the L3 compile units by ``target_id`` (untargeted units share the one
    ``""`` bucket — exactly the aggregation that hides the conflict) and flags a
    target whose units disagree on an ABI-relevant flag family (``-frtti`` vs
    ``-fno-rtti`` …) or bind the same value-carrying define to two values.
    Aggregating such units into one synthetic context silently keeps one and
    drops the other (AC-008: oneTBB's ``__TBB*BUILD``/``-fno-rtti`` umbrella,
    oneDAL's per-value macros). Never an artifact-proven break — RISK. Skips
    cleanly when no L3 build evidence is present.
    """
    providers = [PROVIDER_BUILD_CONFIG]
    pack = snapshot.build_source
    build = pack.build_evidence if pack is not None else None
    if build is None or not build.compile_units:
        return _CheckOutput(
            [], "skipped", "no L3 build evidence on the snapshot", providers
        )

    by_target: dict[str, list[Any]] = {}
    for cu in build.compile_units:
        by_target.setdefault(cu.target_id or "", []).append(cu)

    findings: list[Change] = []
    for target_id, units in sorted(by_target.items()):
        if len(units) < 2:
            continue
        label = target_id or "(unscoped compile units)"
        for pos, neg in _ABI_FLAG_FAMILIES:
            # Compare *effective, language-qualified* per-TU modes, not raw flag
            # membership. Each of these families is ON by default and C++-only, so
            # the AC-008 umbrella case is: most C++ TUs default-on, one built
            # `-fno-rtti`. Two false positives a raw membership test hits (Codex
            # review): (1) a C TU (no RTTI ABI at all) counted as "positive" and
            # flagged against a C++ `-fno-rtti` TU; (2) a `-fno-rtti -frtti`
            # override read as negative. Both are avoided by taking each unit's
            # last-wins effective mode and only counting a unit that participates
            # in the family — a C++ unit (default-on when it names no token) or any
            # unit that carries an explicit token for this family (meaningful even
            # if its language was not recorded).
            pos_seen = neg_seen = False
            for cu in units:
                mode = _effective_flag_mode(cu.abi_relevant_flags, pos, neg)
                if mode is None:
                    if (cu.language or "").upper() in _CPP_LANGUAGES:
                        pos_seen = True  # C++ default: family on
                elif mode == "pos":
                    pos_seen = True
                else:
                    neg_seen = True
            if pos_seen and neg_seen:
                findings.append(
                    _change(
                        ChangeKind.COMPILE_CONTEXT_CONFLICT,
                        label,
                        f"Build target {label} has compile units that disagree on "
                        f"{pos}/{neg} (some built {neg}, others {pos} or the C++ "
                        "language default); aggregating them into one build context "
                        "silently picks one ABI. Scope the evidence to a single "
                        "link unit or pass a compile-DB filter.",
                        old_value=pos,
                        new_value=neg,
                        evidence_category="build_context",
                    )
                )
        define_values: dict[str, set[str]] = {}
        for cu in units:
            for k, v in cu.defines.items():
                if v:  # a bare -DFOO (no value) carries no conflicting value
                    define_values.setdefault(k, set()).add(v)
        for key, values in sorted(define_values.items()):
            if len(values) > 1:
                vs = ", ".join(sorted(values))
                findings.append(
                    _change(
                        ChangeKind.COMPILE_CONTEXT_CONFLICT,
                        label,
                        f"Build target {label} binds define {key!r} to conflicting "
                        f"values ({vs}) across its compile units; the aggregated "
                        "context keeps only one. Scope to a single build variant.",
                        old_value=key,
                        new_value=vs,
                        evidence_category="build_context",
                    )
                )
    n_targets = len(by_target)
    detail = (
        f"L3 compile-context coherence across {n_targets} target(s): "
        f"{len(findings)} conflict(s)"
    )
    return _CheckOutput(
        findings,
        "present",
        detail,
        providers,
        counters={"targets": n_targets, "conflicts": len(findings)},
    )


# ---------------------------------------------------------------------------
# source_surface_dso_mismatch (AC-009) — the L4 surface maps to none of this
# binary's exports, so it likely describes a different / shared DSO.
# ---------------------------------------------------------------------------


def _surface_mapped_symbols(surface: Any) -> set[str]:
    """The binary-symbol names the L4 surface actually attributed to a DSO.

    Reads the surface's own decl->export and synthesized/template/allocator
    attribution *mappings* (``link_source_abi`` populates these), not the summary
    ``coverage`` counters — those counters describe the export set the surface was
    **linked against**, which for a stale or shared surface is a *different* DSO,
    so a positive ``matched_symbols`` there can still mean "matched some other
    binary" (Codex review). The mapping values/keys are the concrete symbols to
    test against *this* binary's live export table."""
    m = surface.mappings or {}
    symbols: set[str] = {
        sym for sym in m.get("source_decl_to_binary_symbol", {}).values() if sym
    }
    for key in (
        "synthesized_symbol_to_owner",
        "template_instantiation_symbol_to_decl",
        "allocator_interposer_symbol_to_owner",
    ):
        symbols |= set(m.get(key, {}).keys())
    return symbols


def _check_source_surface_dso_mismatch(
    snapshot: AbiSnapshot, cfg: Any
) -> _CheckOutput:
    """The linked L4 surface maps to none of this binary's exports, RISK (AC-009).

    When a single source surface is folded from every target's sources and reused
    across DSOs, the surface applied to one DSO describes a different export set —
    its decl->export linking then maps to none of *this* binary's exports. Fires
    only when the surface has reachable declarations AND the binary has an export
    table AND none of the surface's *own* attributed symbols are in this binary's
    live export set, so a per-DSO-relinked surface (AC-003) or a source-only
    snapshot never trips it. Never an artifact-proven break — RISK.

    The match is computed by intersecting the surface's attribution *mappings*
    with the current binary exports, **not** the summary ``matched_symbols``
    counter — a stale/shared surface's counter describes whatever DSO it was
    linked against, so trusting it would return clean in exactly the mis-scoped
    case this check targets (Codex review).
    """
    providers = [PROVIDER_SOURCE_INDEX, PROVIDER_BINARY_EXPORTS]
    pack = snapshot.build_source
    surface = pack.source_abi if pack is not None else None
    if surface is None:
        return _CheckOutput(
            [], "skipped", "no L4 source surface on the snapshot", providers
        )
    if not surface.reachable_declarations:
        return _CheckOutput(
            [], "present", "L4 surface carries no reachable declarations", providers
        )
    # Use the L4 source-linker's export keyspace (not the double-stripped
    # `_exported_symbol_names`), so `mapped & exported` compares like-for-like on
    # Mach-O C++ dylibs where the surface mappings keep the `_Z…` spelling (Codex
    # review).
    exported = _linked_export_symbols(snapshot)
    if not exported:
        return _CheckOutput(
            [], "skipped", "no binary export table on the snapshot", providers
        )
    mapped = _surface_mapped_symbols(surface)
    linked = bool((surface.roots or {}).get("exported_symbols"))
    if not mapped and not linked:
        # No attributions *and* never linked against any export set — we cannot
        # tell "wrong DSO" from "link step never ran", so skip rather than
        # false-positive. (A surface linked against *this* binary that matched
        # nothing still has a non-empty roots['exported_symbols'], so it falls
        # through to the finding below — the original zero-match AC-009 case.)
        return _CheckOutput(
            [],
            "skipped",
            "L4 surface carries no decl->export attribution and was never linked",
            providers,
        )
    matched_here = mapped & exported
    if matched_here:
        return _CheckOutput(
            [],
            "present",
            f"L4 surface maps to {len(matched_here)} of the binary's exports",
            providers,
            counters={"matched_symbols": len(matched_here)},
        )
    n_decls = len(surface.reachable_declarations)
    finding = _change(
        ChangeKind.SOURCE_SURFACE_DSO_MISMATCH,
        surface.library or "(source surface)",
        f"The L4 source surface carries {n_decls} declaration(s) but none map to "
        f"this binary's {len(exported)} exported symbol(s) — it likely describes a "
        "different or shared DSO. Relink/rebuild the source surface per-DSO "
        "against this binary's own exports.",
        new_value=str(len(exported)),
    )
    return _CheckOutput(
        [finding],
        "present",
        f"L4 surface ↔ binary exports: 0/{len(exported)} matched",
        providers,
        facts=len(exported),
        counters={"exported": len(exported), "matched": 0, "declarations": n_decls},
    )


