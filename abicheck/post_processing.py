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

"""Post-processing pipeline for ABI change lists.

Each step is independently testable, reorderable, and self-documenting.
The pipeline transforms the raw detector output into the final change list
through filtering, deduplication, enrichment, and suppression.

Architecture review: Problem C — explicit pipeline replaces imperative chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .policy.disposition_ledger import Disposition, record_suppressed_change

# Split out when this module reached the 2000-line hard cap (see each new
# module's own docstring). `PipelineContext` is re-exported rather than
# relocated outright: 23 call sites import it from this historical path.
from .post_processing_context import (
    _ENUM_MEMBER_KINDS as _ENUM_MEMBER_KINDS,
    _PUBLIC_SOURCE_ABI_KINDS as _PUBLIC_SOURCE_ABI_KINDS,
    PipelineContext as PipelineContext,
)
from .post_processing_reachability import MarkReachability as MarkReachability

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot
    from .policy.disposition_ledger import DispositionLedger
    from .suppression import Suppression, SuppressionList


class PipelineStep(Protocol):
    """Protocol for a single post-processing step."""

    name: str

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Transform the change list, returning the updated list."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_index(snap: AbiSnapshot) -> bool:
    """Index ``snap`` for lookups, tolerating partial snapshots.

    Returns ``True`` when the snapshot indexed cleanly and is safe to read
    from, ``False`` otherwise. Keeping the swallowed exception out of a
    ``try/except/continue`` loop body avoids a silently-ignored-error pattern.
    """
    try:
        snap.index()
    except Exception:  # noqa: BLE001 — defensive; snapshots may be partial
        return False
    return True


def _matches_suppression_key(symbol: str, key: str) -> bool:
    """Return ``True`` iff *symbol* is suppressed by *key*.

    Used by :class:`DetectCppPatterns` to match per-symbol
    ``Change.symbol`` strings against the suppression set built by the
    grouped SYCL / ISA detectors.

    Match rule:

    * Always honour exact equality.
    * Allow substring match (``key in symbol``) only when the key is
      *structured enough* to be unambiguous — contains a namespace
      separator (``::``), an underscore (``_``), or is at least 12
      characters long. This guards against false suppressions where a
      short leaf name like ``compute`` would otherwise hit unrelated
      symbols (``precompute``, ``Recompute_xyz``).

    The substring fallback exists because ``Change.symbol`` can be a
    *different* mangled encoding from ``fn.mangled``: on Linux the
    castxml-derived Itanium mangled name; on Windows the PE export-
    table name (MSVC mangling). The demangled function name (e.g.
    ``kmeans_compute_avx512``) is a substring of both encodings.
    """
    if not key:
        return False
    if symbol == key:
        return True
    if len(key) < 12 and "::" not in key and "_" not in key:
        return False
    return key in symbol


# ---------------------------------------------------------------------------
# Concrete pipeline steps
# ---------------------------------------------------------------------------


class FilterReservedFieldRenames:
    """Suppress TYPE_FIELD_REMOVED false positives from reserved-field renames."""

    name = "filter_reserved_field_renames"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_reserved_field_renames

        return _filter_reserved_field_renames(changes)


class FilterOpaqueSizeChanges:
    """Suppress size-only growth for opaque pointer-handle types."""

    name = "filter_opaque_size_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_opaque_size_changes

        changes, filtered = _filter_opaque_size_changes(changes, ctx.old, ctx.new)
        ctx.opaque_filtered.extend(filtered)
        return changes


class DowngradeOpaqueStructChanges:
    """Downgrade changes for types opaque in both snapshots."""

    name = "downgrade_opaque_struct_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _downgrade_opaque_struct_changes

        return _downgrade_opaque_struct_changes(changes, ctx.old, ctx.new)


class DeduplicateAstDwarf:
    """Collapse AST/DWARF duplicate findings."""

    name = "deduplicate_ast_dwarf"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _deduplicate_ast_dwarf

        return _deduplicate_ast_dwarf(changes, ctx.old, ctx.new)


class DeduplicateCrossDetector:
    """Collapse overlapping reports from different detectors."""

    name = "deduplicate_cross_detector"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _deduplicate_cross_detector

        # Pass the snapshots so the enum bare/qualified-name bridge can run
        # here (before EnrichSourceLocations, which runs later in this
        # pipeline) — see _deduplicate_cross_detector's own docstring.
        return _deduplicate_cross_detector(changes, ctx.old, ctx.new)


class DowngradeOpaqueTypeChanges:
    """Suppress structural changes for opaque types."""

    name = "downgrade_opaque_type_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _downgrade_opaque_type_changes

        return _downgrade_opaque_type_changes(changes, ctx.old, ctx.new)


class AnnotateLayoutUnverifiableCoveredByVtableChanged:
    """Cross-reference a ``LAYOUT_UNVERIFIABLE`` finding with a
    ``TYPE_VTABLE_CHANGED`` on the *exact same type* that reports the
    identical asymmetric-evidence gap — never removing either finding from
    ``changes``.

    ``diff_types_vtable._vtable_transition_is_evidenced`` and
    ``diff_layout._check_layout_unverifiable`` both key off the same "one
    side has real layout evidence, the other has none" condition. The
    vtable detector correctly stays BREAKING for it (an unknown size
    "corroborates nothing but also refutes nothing", so the finding must be
    kept — see AGENTS.md's "Findings emitted from absent evidence" entry for
    why demoting it is unsafe: a real removal of a class's last virtual
    method, with the new side's debug info happening not to cover it, is
    indistinguishable from this same gap). The layout detector reports the
    identical gap as calm, non-escalating ``LAYOUT_UNVERIFIABLE`` RISK. Both
    are individually correct, but landing on the same type in the same
    report reads as two detectors disagreeing about one piece of evidence
    (reproducible with zero real ABI change — scanning a binary against a
    dump of itself).

    This step deliberately does **not** try to resolve that presentation
    problem by removing either finding from the scored set — three earlier
    revisions did (first demoting ``TYPE_VTABLE_CHANGED``, then folding
    ``LAYOUT_UNVERIFIABLE`` into ``ctx.redundant`` unconditionally, then
    only when policy-subsumed), and each was independently shown unsafe by
    review (see AGENTS.md's own account of all of these). The last and
    decisive one (Codex review, fresh evidence): the fold's "is the
    covering finding severe enough to make this one redundant" comparison
    was made against the *policy verdict* axis (BREAKING/API_BREAK/RISK/
    COMPATIBLE), but ``DiffResult.changes`` is separately gated by the
    *severity-scheme* axis (``SeverityConfig``: abi_breaking/
    potential_breaking/quality_issues/addition, each independently
    error/warning/info) — and that axis is chosen by a caller entirely
    *after* ``compare()`` returns (``cli.py``'s ``--severity-preset``/
    severity settings, applied to an already-built ``DiffResult``).
    No decision made inside ``compare()`` can be correct for a
    configuration ``compare()`` never sees: a policy-unconfigured run with
    ``abi_breaking=info``/``potential_breaking=error`` reproduces the exact
    same "the gate misses the finding" bug with **no policy override
    involved at all**, which the previous, policy-axis-only fold check had
    no way to detect. Once two independent axes (policy *and* severity
    config) can each defeat a fold decided at ``compare()``-time, and each
    was found only by discovering the previous fix's blind spot, there is
    no principled reason to believe a third axis (a future gate mechanism)
    won't do the same. The only fix immune to *every* current and future
    consumer of ``DiffResult.changes`` is to never remove information from
    it for this reason at all.

    So instead: both findings stay exactly where the detectors put them,
    contributing independently and correctly to every verdict, severity,
    JSON, SARIF, and release-bundle computation exactly as if this step did
    not exist. The only change is ``Change.correlated_change_kind`` — set on
    the ``LAYOUT_UNVERIFIABLE`` finding to ``"type_vtable_changed"`` — the
    same generic cross-reference field ADR-041 already uses for a different
    finding pair, so a JSON/SARIF consumer (``reporter.py``/``sarif.py``
    already serialize it) can render "see also" instead of two
    unexplained, seemingly-contradictory findings, without this codebase
    ever having to correctly anticipate what a downstream consumer will
    do with the list.

    Correlated via ``Change.qualified_name`` (the type's real, namespaced
    identity — both producers set it for exactly this purpose), not the
    bare ``Change.symbol`` two distinct same-named records in different
    namespaces could share; and gated on the dedicated
    ``Change.vtable_covers_unverifiable_layout_gap`` marker (not
    ``modulation_reason`` — see that field's own docstring for why
    overloading the modulation audit trail here would be wrong), so a
    co-occurring but independently-evidenced ``TYPE_VTABLE_CHANGED`` (a
    real reorder, a real size delta, a real virtual-base change) is never
    cross-referenced as if it shared the same evidence gap.
    """

    name = "annotate_layout_unverifiable_covered_by_vtable_changed"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind

        covered_types = {
            c.qualified_name
            for c in changes
            if c.kind == ChangeKind.TYPE_VTABLE_CHANGED
            and c.vtable_covers_unverifiable_layout_gap
            and c.qualified_name
        }
        if not covered_types:
            return changes
        for c in changes:
            if (
                c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
                and c.qualified_name in covered_types
                and not c.correlated_change_kind
            ):
                c.correlated_change_kind = ChangeKind.TYPE_VTABLE_CHANGED.value
        return changes


class EnrichSourceLocations:
    """Add source location metadata for suppression matching."""

    name = "enrich_source_locations"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _enrich_source_locations

        _enrich_source_locations(changes, ctx.old, ctx.new)
        return changes


def _snapshot_export_ids(snap: AbiSnapshot) -> set[str]:
    """Every identifier (name + mangled) under which a real export appears.

    Used by manifest scoping to tell a concrete exported symbol (subject to the
    committed-surface filter) from a loader/dynamic pseudo-symbol like
    ``DT_SONAME`` (which is not an export and must survive scoping).

    Includes the platform export tables (ELF ``.dynsym``, PE/Mach-O export
    directories), not just the DWARF-derived ``functions``/``variables``: a
    private ``__pp_*`` helper can appear only in ELF/PE/Mach-O metadata (e.g. a
    header-scoped or no-debug snapshot), and it must still be recognized as a
    concrete export so its findings are demoted rather than kept. Dynamic-section
    pseudo-symbols (``DT_SONAME``/``DT_NEEDED``) are not symbol-table entries, so
    they stay out of this set and survive scoping.
    """
    ids: set[str] = set()
    for coll in (snap.functions, snap.variables):
        for s in coll:
            for attr in ("mangled", "name"):
                val = getattr(s, attr, "")
                if val:
                    ids.add(val)
    for meta, attr in (
        (snap.elf, "symbols"),
        (snap.pe, "exports"),
        (snap.macho, "exports"),
    ):
        for s in getattr(meta, attr, None) or ():
            name = getattr(s, "name", "")
            if name:
                ids.add(name)
    return ids


def _change_matches_symbols(change: Change, symbols: set[str] | frozenset[str]) -> bool:
    """True if *change*'s symbol matches the widening allowlist.

    Matches the raw symbol (mangled or demangled, as recorded on the change)
    and — for qualified names — the trailing ``::`` segment, so an entry like
    ``foo`` matches ``ns::foo`` as well as the exact spelling.
    """
    sym = change.symbol or ""
    if not sym:
        return False
    if sym in symbols:
        return True
    return "::" in sym and sym.rsplit("::", 1)[1] in symbols


class FilterNonPublicSurface:
    """Move findings outside the public-header surface to an audit ledger.

    Opt-in (``ctx.scope_to_public_surface``). Mirrors what libabigail
    ``--headers-dir`` / abi-compliance-checker do: a change to a symbol or
    type that is not part of the public-header-scoped ABI surface is not a
    public-compatibility break. Per ADR-024 §D4/D5 these findings are
    *recorded* (``ctx.out_of_surface``), never silently dropped, and
    internal-leak findings are exempt.
    """

    name = "filter_non_public_surface"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        # Manifest-scoped mode (`compare --post-manifest`) takes precedence: the
        # manifest's committed `pp_*`/ufunc-loop set *is* the authoritative
        # public surface, so there is no header-provenance walk to do.
        if ctx.public_surface_allowlist is not None:
            return self._run_allowlist(changes, ctx)
        if not ctx.scope_to_public_surface:
            return changes

        from .surface import (
            classify_change_surface,
            compute_public_surface,
            surface_unions,
        )

        surf_old = compute_public_surface(ctx.old)
        surf_new = compute_public_surface(ctx.new)
        # Cache for reuse (surface_scope_confidence) — avoids a second walk.
        ctx.surf_old = surf_old
        ctx.surf_new = surf_new
        if not (surf_old.resolvable or surf_new.resolvable):
            # No header-derived surface to scope against — keep everything and
            # record the fallback so the verdict is not mistaken for a
            # confidently-clean public surface (issue #235).
            ctx.scope_fell_back = True
            return changes
        force_public = ctx.force_public_symbols
        # Compute the old∪new surface universes once for the whole pass; doing
        # this per change is O(findings × surface) and makes large comparisons
        # quadratic.
        unions = surface_unions(surf_old, surf_new)
        kept: list[Change] = []
        for c in changes:
            # Widening overlay (ADR-024 §D6): a user-guaranteed public symbol
            # stays in-surface regardless of provenance/export classification.
            if force_public and _change_matches_symbols(c, force_public):
                kept.append(c)
                continue
            in_surface, reason = classify_change_surface(
                c, surf_old, surf_new, unions=unions
            )
            if in_surface:
                kept.append(c)
            else:
                # Tag with the ledger reason (ADR-024 §D5.1) before demoting.
                c.surface_exclusion_reason = reason
                ctx.out_of_surface.append(c)
        return kept

    @staticmethod
    def _run_allowlist(changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Scope against an explicit committed-surface allowlist (POST manifest).

        A finding is demoted to ``out_of_surface`` only when it is a *concrete*
        exported symbol (a function/variable present in either snapshot's export
        universe) not in the committed set — e.g. churn on a private ``__pp_*``
        kernel symbol. Kept conservatively (ADR-024 §D5): type-level,
        never-filter (leak), and hidden-friend findings (never a real export —
        Codex review); findings with no symbol; and loader/dynamic pseudo-name
        findings (``DT_SONAME``, ``DT_NEEDED``), which break linked clients
        independently of the POST export set. This mirrors the header path,
        where an unknown (non-exported) symbol is kept.
        """
        from .surface import is_hidden_friend_finding, is_symbol_level_finding

        allow = ctx.public_surface_allowlist or set()
        force_public = ctx.force_public_symbols
        export_ids = _snapshot_export_ids(ctx.old) | _snapshot_export_ids(ctx.new)
        kept: list[Change] = []
        for c in changes:
            sym = c.symbol or ""
            if (
                not sym
                or not is_symbol_level_finding(c)
                or is_hidden_friend_finding(c)
                or sym not in export_ids
            ):
                kept.append(c)  # not a concrete export subject to the filter
                continue
            # The manifest allowlist is a set of *exact* C export names, so match
            # exactly — the suffix-tolerant `_change_matches_symbols` would let an
            # uncommitted namespaced helper (`internal::pp_foo`) pass as committed
            # `pp_foo`, contradicting the `--post-manifest` contract. The
            # `force_public` widening overlay is a header-scoping concept and is
            # only honored when header scoping is also on — the CLI warns it is
            # ignored under `--no-scope-public-headers`, so applying it here would
            # contradict that warning (e.g. force a private `__pp_impl` back in).
            if sym in allow or (
                ctx.scope_to_public_surface
                and force_public
                and _change_matches_symbols(c, force_public)
            ):
                kept.append(c)
            else:
                c.surface_exclusion_reason = "not in POST manifest committed surface"
                ctx.out_of_surface.append(c)
        return kept


#: Native C/C++ finding kinds whose *symbol* is an exported function/variable or
#: whose subject is an internal type — the API-content axis. For a CPython
#: extension module (which exports only ``PyInit_``) these are not part of any
#: ``import`` consumer's contract. Load- and linkage-level kinds (``needed_*`` /
#: ``soname_*`` / security / symbol-version) are deliberately NOT here: they
#: affect whether the ``.so`` loads, which IS part of the contract.
_EXT_INTERNAL_SYMBOL_PREFIXES = (
    "func_",
    "var_",
    "virtual_",
    "method_",
    "vtable_",
    "rtti_",
)


def _is_off_python_surface(c: Change, init_symbol: str | None) -> bool:
    """True when *c* is a native API-content finding off an extension's contract."""
    from .surface import _NEVER_FILTER_KIND_NAMES, _TYPE_LEVEL_KIND_NAMES

    v = c.kind.value
    # Authority: Python-level and CPython load-contract findings are the point.
    if v.startswith("python_"):
        return False
    # Leak / constant findings are never scoped out (ADR-024 §D5.2).
    if v in _NEVER_FILTER_KIND_NAMES:
        return False
    # The module's own init export is its one real native public symbol.
    if init_symbol and c.symbol and (c.symbol == init_symbol or "PyInit_" in c.symbol):
        return False
    return v in _TYPE_LEVEL_KIND_NAMES or v.startswith(_EXT_INTERNAL_SYMBOL_PREFIXES)


class DemoteOffPythonSurface:
    """Demote native C/C++ churn that is off a CPython extension's real contract.

    A CPython extension module's consumer contract is (a) its **Python-visible
    API** — functions/classes/methods recovered from its ``.pyi`` and diffed by
    :mod:`abicheck.diff_python_api` — and (b) its **native load contract** —
    imported ``Py*`` symbols / ``abi3`` conformance, checked by
    :mod:`abicheck.diff_python`. The module exports only ``PyInit_<mod>``; its
    other exported C/C++ symbols and internal type layout are implementation
    detail no ``import`` consumer can link or observe. When abicheck is run on
    such a module with debug info (or headers absent), the native detectors
    surface that internal churn as breaking — a **false positive** for the
    extension's real consumers.

    This step uses the recovered Python surface as the authoritative
    public-contract oracle: when the new snapshot is a recognised extension with
    a ``python_api`` surface and there is **no** C-header surface to scope
    against (headers being the stronger oracle, deferred to when present), native
    API-content findings (:func:`_is_off_python_surface`) are demoted to the
    audit ledger (``ctx.out_of_surface``, ADR-024 §D4/D5) — never dropped.

    Authority rule (ADR-028 D3): ``python_api_*`` and
    ``python_stable_abi_*``/``abi3``/``gil`` findings are never demoted here, and
    load/linkage/leak kinds are kept, so this can only ever remove native
    internal noise — never hide a real Python-level or load-contract break.
    Opt-in with ``ctx.scope_to_public_surface`` (on by default), so
    ``--no-scope-public-headers`` keeps every native finding.
    """

    name = "demote_off_python_surface"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.scope_to_public_surface:
            return changes
        new_ext = ctx.new.python_ext
        if new_ext is None or not new_ext.is_extension:
            return changes
        # Both sides must be extensions. Otherwise a normal native library that
        # is *replaced by* an extension (v1 exports `foo`; v2 is an extension
        # dropping it) would have its real `func_removed` demoted, hiding a
        # genuine break for the old library's C/C++ consumers. Only when the old
        # artifact was itself an extension is its native symbol surface known to
        # be implementation detail rather than a public contract.
        old_ext = ctx.old.python_ext
        if old_ext is None or not old_ext.is_extension:
            return changes
        # Defer to the C-header oracle when a public header surface resolved on
        # *either* side (hybrid modules that ship a real public C API):
        # FilterNonPublicSurface already scoped it. Checking both sides matters
        # for a hybrid that removes its last C API function — the old side's
        # header proves the dropped symbol was public, so its `func_removed`
        # must not be demoted just because the new side no longer resolves.
        if (ctx.surf_old is not None and ctx.surf_old.resolvable) or (
            ctx.surf_new is not None and ctx.surf_new.resolvable
        ):
            return changes
        # No recovered Python surface ⇒ no oracle ⇒ keep everything (honest
        # degradation, same posture as header-scoping's no-surface fallback).
        if ctx.new.python_api is None:
            return changes
        from .surface import REASON_OFF_PYTHON_SURFACE

        init_symbol = new_ext.init_symbol
        kept: list[Change] = []
        for c in changes:
            if _is_off_python_surface(c, init_symbol):
                c.surface_exclusion_reason = REASON_OFF_PYTHON_SURFACE
                ctx.out_of_surface.append(c)
            else:
                kept.append(c)
        return kept


class ApplySuppression:
    """Apply user-provided suppression rules.

    ADR-044 D2: a rule matches only when ``Suppression.matches()`` also passes
    its reachability/``allow_public_break`` gate — see that method for the
    semantics. A match refused by that gate is recorded as a
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic (ADR-044 D4) instead of
    being silently dropped, so the change stays visible *and* the suppression
    author sees why their rule did not apply.
    """

    name = "apply_suppression"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None:
            return changes
        filtered: list[Change] = []
        diagnostics: list[Change] = []
        for c in changes:
            outcome = ctx.suppression.evaluate(c)
            if outcome.suppressed:
                c.suppression_rule = outcome.rule_label()
                record_suppressed_change(
                    ctx.disposition_ledger,
                    c,
                    rule=outcome.matched_rule,
                    application_point=self.name,
                    suppression=ctx.suppression,
                )
                ctx.suppressed.append(c)
                continue
            filtered.append(c)
            if outcome.withheld_rule is not None:
                diagnostics.append(
                    _build_suppression_overreach_change(c, outcome.withheld_rule)
                )
            if outcome.withheld_unknown_rule is not None:
                diagnostics.append(
                    _build_suppression_unknown_reachability_change(
                        c, outcome.withheld_unknown_rule
                    )
                )
        filtered.extend(diagnostics)
        return filtered


def _build_suppression_overreach_change(change: Change, rule: Suppression) -> Change:
    """Build the ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic for *change*.

    ADR-044 D4. *rule* is the suppression whose selectors matched *change* but
    whose reachability/``allow_public_break`` gate withheld the match.
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    # would_withhold() only ever returns True for a *broad* rule (namespace/
    # entity_namespace/cause_namespace/source_location, no primary narrow
    # selector — see Suppression._is_broad_selector): a rule with symbol/
    # symbol_pattern/type_pattern set has _is_broad_selector=False, so
    # _passes_public_break_gate short-circuits True and would_withhold can
    # never fire for it. The selector fallback below only needs the three
    # broad-shaped fields; entity_namespace is the canonical spelling of
    # namespace (self-review finding: it was missing here even though the
    # equivalent SuppressionAudit string-building was already fixed).
    selector = (
        rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        or rule.source_location
        or "?"
    )
    proof = (
        f" via {change.reachability_proof_path}"
        if change.reachability_proof_path
        else ""
    )
    return Change(
        kind=ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: the symbol is "
            f"public-reachable{proof}. Add `allow_public_break: true` to this "
            "rule to suppress it anyway."
        ),
        caused_by_type=change.symbol,
    )


def _build_suppression_unknown_reachability_change(
    change: Change, rule: Suppression
) -> Change:
    """Build the ``SUPPRESSION_REACHABILITY_UNKNOWN`` diagnostic for *change*.

    impact-analysis-layer P0 slice. *rule* is the suppression whose selectors
    matched *change*, whose resolved ``reachability`` is
    ``"proven-unreachable-only"``, but whose graph coverage could not prove
    *change* unreachable (``Change.reachability_state`` is ``UNKNOWN``).
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    selector = (
        rule.symbol
        or rule.symbol_pattern
        or rule.type_pattern
        or rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        # Canonical (backend-independent) identity selector (Codex review,
        # fresh evidence, PR #753): mirrors the identical fix in
        # cli_compare_fold.py's _suppression_rule_label -- a finding_id-only
        # rule with no other selector previously rendered as the bare
        # fallback "?" here, indistinguishable from every other unlabeled
        # rule's diagnostic.
        or rule.finding_id
        or rule.source_location
        or "?"
    )
    return Change(
        kind=ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: graph coverage was "
            "insufficient to prove the change unreachable from the public ABI "
            "surface (reachability: proven-unreachable-only). Add "
            "`allow_unknown_reachability: true` to this rule to suppress it "
            "anyway once you have manually confirmed it is safe."
        ),
        caused_by_type=change.symbol,
    )


def _record_dropped_duplicates(
    before: dict[int, Change],
    after: list[Change],
    ctx: PipelineContext,
    step_name: str,
) -> None:
    """Record findings a step discarded outright as ``deduplicated``.

    ADR-067 D1/D3: the raw total must count every atomically detected change,
    including the ones an early collapse removed. ``DeduplicateAstDwarf`` and
    ``DeduplicateCrossDetector`` run *before* ``FilterRedundant`` and simply
    return a shorter list, so without this the detected total silently
    undercounted by exactly the duplicates they folded away.

    Deliberately generic rather than a patch to those two steps: any future
    step that drops a finding is covered the same way, which is the failure
    class -- a collapse with no audit record -- not the two instances of it.

    A finding a step *moved* into one of the context's own side-output lists
    is not a discard and is skipped here: it keeps the disposition its own
    owner assigns (suppression records at the point it fires; the surface,
    redundancy and opaque buckets are labelled when the ledger is closed over
    the result). Since :meth:`DispositionLedger.record` is identity-keyed and
    first-write-wins, mislabelling one here would be permanent.
    """
    ledger = ctx.disposition_ledger
    if ledger is None:
        return
    survived = {id(c) for c in after}
    for bucket in (
        ctx.suppressed,
        ctx.redundant,
        ctx.opaque_filtered,
        ctx.out_of_surface,
    ):
        survived.update(id(c) for c in bucket)
    for key, change in before.items():
        if key not in survived:
            ledger.record(
                change,
                Disposition.DEDUPLICATED,
                application_point=step_name,
            )


def _merge_findings_respecting_suppression(
    changes: list[Change],
    new_findings: list[Change],
    ctx: PipelineContext,
) -> None:
    """Append deduplicated ``new_findings`` to ``changes``, respecting suppression.

    Mutates ``changes`` in place. Shared by every post-``ApplySuppression``
    detector that builds fresh ``Change`` objects (``DetectCppPatterns``,
    ``DetectTemplatePatterns``, ``DetectNamespacePatterns``) — those findings
    never passed through ``ApplySuppression`` itself, so they must run their
    own suppression check here.

    Uses :meth:`SuppressionList.evaluate` (not the cheaper ``is_suppressed``)
    so a broad rule whose selectors matched but was withheld by the
    reachability/``allow_public_break`` gate still emits the same
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic ``ApplySuppression``
    produces for changes it sees directly (ADR-044 D4; Codex review, fresh
    evidence) — otherwise a late finding that a broad rule *would* have
    hidden, had it been reachable earlier, is silently kept with no
    explanation of why the matching rule didn't apply.
    """
    seen_keys = {(c.kind, c.symbol) for c in changes}
    diagnostics: list[Change] = []
    for c in new_findings:
        if ctx.suppression is not None:
            outcome = ctx.suppression.evaluate(c)
            if outcome.suppressed:
                c.suppression_rule = outcome.rule_label()
                record_suppressed_change(
                    ctx.disposition_ledger,
                    c,
                    rule=outcome.matched_rule,
                    application_point="merge_late_findings",
                    suppression=ctx.suppression,
                )
                ctx.suppressed.append(c)
                continue
            if outcome.withheld_rule is not None:
                diagnostics.append(
                    _build_suppression_overreach_change(c, outcome.withheld_rule)
                )
            if outcome.withheld_unknown_rule is not None:
                diagnostics.append(
                    _build_suppression_unknown_reachability_change(
                        c, outcome.withheld_unknown_rule
                    )
                )
        key = (c.kind, c.symbol)
        if key in seen_keys:
            continue
        changes.append(c)
        seen_keys.add(key)
    changes.extend(diagnostics)


class SuppressRenamedPairs:
    """Suppress FUNC_REMOVED + FUNC_ADDED pairs when a FUNC_LIKELY_RENAMED exists.

    When the fingerprint rename detector identifies a rename (old_name → new_name),
    the corresponding FUNC_REMOVED(old_name) and FUNC_ADDED(new_name) are redundant
    noise.  This step moves them to ctx.redundant and annotates the rename change
    with caused_count.
    """

    name = "suppress_renamed_pairs"

    @staticmethod
    def _build_rename_maps(
        changes: list[Change],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, Change]]:
        """Return (renamed_old, renamed_new, rename_changes) from FUNC_LIKELY_RENAMED entries."""
        from .checker_policy import ChangeKind

        renamed_old: dict[str, str] = {}  # old_value → new_value
        renamed_new: dict[str, str] = {}  # new_value → old_value
        rename_changes: dict[str, Change] = {}  # old_value → the rename Change
        # LONG_DOUBLE_ABI_CHANGED re-pairs a removed↔added symbol pair (its
        # old_value/new_value are the mangled symbols), so its redundant
        # func_removed/func_added halves collapse into it just like a rename.
        _pairing_kinds = (
            ChangeKind.FUNC_LIKELY_RENAMED,
            ChangeKind.LONG_DOUBLE_ABI_CHANGED,
        )
        for c in changes:
            if c.kind in _pairing_kinds and c.old_value and c.new_value:
                renamed_old[c.old_value] = c.new_value
                renamed_new[c.new_value] = c.old_value
                rename_changes[c.old_value] = c
        return renamed_old, renamed_new, rename_changes

    @staticmethod
    def _try_suppress_removed(
        c: Change,
        renamed_old: dict[str, str],
        rename_changes: dict[str, Change],
        ctx: PipelineContext,
    ) -> bool:
        """Suppress a FUNC_REMOVED/FUNC_REMOVED_ELF_ONLY change if it belongs to a rename pair.

        Returns True when the change was suppressed (caller should skip appending it).
        """
        old_name = c.old_value or c.symbol
        if old_name not in renamed_old:
            return False
        c.caused_by_type = f"rename:{old_name}→{renamed_old[old_name]}"
        ctx.redundant.append(c)
        rc = rename_changes.get(old_name)
        if rc is not None:
            rc.caused_count += 1
        return True

    @staticmethod
    def _try_suppress_added(
        c: Change,
        renamed_new: dict[str, str],
        rename_changes: dict[str, Change],
        ctx: PipelineContext,
    ) -> bool:
        """Suppress a FUNC_ADDED change if it belongs to a rename pair.

        Returns True when the change was suppressed (caller should skip appending it).
        """
        new_name = c.new_value or c.symbol
        if new_name not in renamed_new:
            return False
        old_name = renamed_new[new_name]
        c.caused_by_type = f"rename:{old_name}→{new_name}"
        ctx.redundant.append(c)
        rc = rename_changes.get(old_name)
        if rc is not None:
            rc.caused_count += 1
        return True

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind

        renamed_old, renamed_new, rename_changes = self._build_rename_maps(changes)
        if not renamed_old:
            return changes

        removed_kinds = (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY)
        kept: list[Change] = []
        for c in changes:
            if c.kind in removed_kinds:
                if self._try_suppress_removed(c, renamed_old, rename_changes, ctx):
                    continue
            elif c.kind == ChangeKind.FUNC_ADDED:
                if self._try_suppress_added(c, renamed_new, rename_changes, ctx):
                    continue
            kept.append(c)
        return kept


class ClearOrphanedVtableGapCorrelation:
    """Clear ``Change.correlated_change_kind`` on a ``LAYOUT_UNVERIFIABLE``
    finding whose covering ``TYPE_VTABLE_CHANGED`` no longer survives in
    ``changes`` by the time suppression has run.

    ``AnnotateLayoutUnverifiableCoveredByVtableChanged`` above runs early —
    deliberately *before* ``ApplySuppression`` (see that step's own
    docstring for why it must also run before ``MarkReachability``,
    two steps earlier). A suppression rule can target only the covering
    ``TYPE_VTABLE_CHANGED`` (e.g. an ``allow_public_break`` waiver on that
    one finding) without touching the co-reported ``LAYOUT_UNVERIFIABLE`` —
    when that happens, the earlier annotation is left pointing at a finding
    ``ApplySuppression`` already moved out of ``changes`` into
    ``ctx.suppressed``. Left uncorrected, JSON/SARIF would keep publishing
    a "see also: type_vtable_changed" reference to a finding the same
    report never actually shows, and Markdown/HTML would render a "See
    also" note pointing at nothing (Codex review, fresh evidence).

    Runs *after* ``ApplySuppression``/``SuppressRenamedPairs`` so it sees
    the settled post-suppression ``changes`` list, and *never* re-sets a
    correlation that wasn't already there — only clears one whose covering
    finding has since vanished. A ``LAYOUT_UNVERIFIABLE`` finding that is
    itself suppressed needs no handling here: it left ``changes`` along
    with its (now-irrelevant) correlation.

    Also updates a cached ``Change.impact_assessment`` (set by
    ``MarkReachability``, two steps before the original annotation, when a
    configured suppression needs reachability evidence) to keep it in sync
    with the cleared flat field — ``impact.engine.assess_change()`` prefers
    a cached assessment's own ``correlated_change_kind`` over the flat
    field once one exists, so leaving the cache untouched here would
    resurrect the exact dangling reference this step exists to remove, just
    inside the unified ``impact_assessment`` object instead of the
    top-level field.
    """

    name = "clear_orphaned_vtable_gap_correlation"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        import dataclasses

        from .checker_policy import ChangeKind

        surviving_covering_types = {
            c.qualified_name
            for c in changes
            if c.kind == ChangeKind.TYPE_VTABLE_CHANGED
            and c.vtable_covers_unverifiable_layout_gap
            and c.qualified_name
        }
        for c in changes:
            if (
                c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
                and c.correlated_change_kind == ChangeKind.TYPE_VTABLE_CHANGED.value
                and c.qualified_name not in surviving_covering_types
            ):
                c.correlated_change_kind = None
                if c.impact_assessment is not None:
                    c.impact_assessment = dataclasses.replace(
                        c.impact_assessment, correlated_change_kind=None
                    )
        return changes


class FilterRedundant:
    """Split changes into kept + redundant (derived from root type changes)."""

    name = "filter_redundant"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_redundant

        kept, redundant = _filter_redundant(changes)
        ctx.redundant.extend(redundant)
        # opaque_filtered are kept separate - they are compatible changes that should not affect verdict
        ctx.kept = kept
        return kept


class EnrichAffectedSymbols:
    """For type changes, find functions that use the affected type."""

    name = "enrich_affected_symbols"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _enrich_affected_symbols

        _enrich_affected_symbols(changes, ctx.old)
        return changes


class AttributeStdlibEmbedding:
    """Attribute an unattributed owner size/offset change to an embedded ``std::``
    member by value (the layout-closure case the redundancy filter can't link)."""

    name = "attribute_stdlib_embedding"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _attribute_stdlib_embedding

        _attribute_stdlib_embedding(changes, ctx.new)
        return changes


class DetectCppPatterns:
    """Run the C++ library-family detectors added in PR #239 (case77–case89).

    Each individual detector lives in :mod:`abicheck.diff_cpp_patterns`;
    this pipeline step wires them together, dedupes findings against the
    existing change list, and respects user suppression.

    Detectors run:

    * ``detect_serialization_tag_changes``
    * ``detect_missing_instantiations``
    * ``detect_sycl_overload_set_removal`` (also suppresses redundant
      per-symbol ``func_removed`` children)
    * ``detect_cpu_dispatch_isa_dropped`` (likewise)
    * ``detect_tag_type_renamed``
    * ``detect_default_template_arg_changed``
    * ``detect_inline_body_renamed_member``
    """

    name = "detect_cpp_patterns"

    @staticmethod
    def _run_all_detectors(
        ctx: PipelineContext,
        changes: list[Change],
    ) -> tuple[list[Change], set[str]]:
        """Invoke every sub-detector and return ``(new_findings, suppressed_keys)``.

        ``suppressed_keys`` is the union of the per-symbol keys emitted by the
        SYCL and ISA grouped detectors; these identify ``FUNC_REMOVED`` children
        that must be moved to ``ctx.suppressed`` so they don't inflate the verdict.
        """
        from .diff_cpp_patterns import (
            detect_cpu_dispatch_isa_dropped,
            detect_default_template_arg_changed,
            detect_inline_body_renamed_member,
            detect_sycl_overload_set_removal,
            detect_tag_type_renamed,
        )
        from .diff_serialization import detect_serialization_tag_changes
        from .diff_templates import detect_missing_instantiations

        new_findings: list[Change] = []
        new_findings.extend(detect_serialization_tag_changes(ctx.old, ctx.new))
        new_findings.extend(detect_missing_instantiations(ctx.old, ctx.new))

        sycl_findings, sycl_suppressed = detect_sycl_overload_set_removal(
            ctx.old, ctx.new
        )
        new_findings.extend(sycl_findings)

        isa_findings, isa_suppressed = detect_cpu_dispatch_isa_dropped(ctx.old, ctx.new)
        new_findings.extend(isa_findings)

        new_findings.extend(detect_tag_type_renamed(ctx.old, ctx.new))
        new_findings.extend(detect_default_template_arg_changed(ctx.old, ctx.new))
        new_findings.extend(
            detect_inline_body_renamed_member(ctx.old, ctx.new, changes)
        )

        return new_findings, sycl_suppressed | isa_suppressed

    @staticmethod
    def _suppress_grouped_children(
        changes: list[Change],
        suppressed_keys: set[str],
        ctx: PipelineContext,
    ) -> None:
        """Remove FUNC_REMOVED children subsumed by a grouped SYCL/ISA finding.

        Mutates ``changes`` in place (via slice assignment) and appends the
        removed entries to ``ctx.suppressed``.

        Two reasons to use ``ctx.suppressed`` (not ``ctx.redundant``):
        (a) ``compare()`` computes verdict on ``kept + redundant`` —
            redundant items still drive the verdict. Putting the
            children there would let per-symbol BREAKING outrank the
            grouped RISK finding. ``ctx.suppressed`` is excluded from
            verdict computation, which is what we want for children
            subsumed by a grouped finding.
        (b) ``FilterRedundant`` (earlier in the pipeline) sets
            ``ctx.kept = changes`` — that's a *reference* to this same
            list. If we rebind ``changes`` to a new filtered list,
            ``ctx.kept`` still points at the old one and our
            suppression is silently lost. Mutate in place instead.

        Matching uses BOTH exact equality and a guarded substring containment
        (see ``_matches_suppression_key`` for the unambiguity rules).
        """
        from .checker_policy import ChangeKind

        to_keep: list[Change] = []
        for ch in changes:
            if ch.kind == ChangeKind.FUNC_REMOVED and any(
                _matches_suppression_key(ch.symbol, key) for key in suppressed_keys
            ):
                ctx.suppressed.append(ch)
                continue
            to_keep.append(ch)
        changes[:] = to_keep

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        new_findings, suppressed_keys = self._run_all_detectors(ctx, changes)

        if suppressed_keys:
            self._suppress_grouped_children(changes, suppressed_keys, ctx)

        if new_findings:
            _merge_findings_respecting_suppression(changes, new_findings, ctx)

        return changes


class DetectTemplatePatterns:
    """Run the generic template / overload-set pattern detectors.

    Lives in :mod:`abicheck.diff_templates`. Covers internal-template
    leaks (function-template analogue of PR #238), CPO kind flips,
    overload-set rerouting, mandatory-template-param additions, and
    unspecified-return flips.
    """

    name = "detect_template_patterns"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        # Mirrors MarkReachability/DetectInternalLeaks/
        # DemoteUnreachableInternalChurn's own constructor (Codex review,
        # fresh evidence): detect_internal_template_leaks's
        # _INTERNAL_TEMPLATE_NAMESPACES is the same internal-implementation
        # convention those three steps use (detail/impl/internal/__detail/
        # _impl, plus __internal) -- unlike DetectNamespacePatterns's
        # unrelated experimental_namespaces, PolicyFile.internal_namespaces
        # should reach this step too.
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_templates import (
            _INTERNAL_TEMPLATE_NAMESPACES,
            detect_template_patterns,
        )

        namespaces = (
            self._namespaces or ctx.internal_namespaces or _INTERNAL_TEMPLATE_NAMESPACES
        )
        new_findings = detect_template_patterns(ctx.old, ctx.new, namespaces)
        if not new_findings:
            return changes
        _merge_findings_respecting_suppression(changes, new_findings, ctx)
        return changes


class DetectNamespacePatterns:
    """Run the generic namespace-shape detectors.

    These cover header-only / template-library failure modes that aren't
    bound to any one library: experimental graduations, silent removals
    from experimental namespaces, and ``using std::X;`` re-export drops.
    Lives in :mod:`abicheck.diff_namespaces`.
    """

    name = "detect_namespace_patterns"

    def __init__(
        self,
        experimental_namespaces: tuple[str, ...] | None = None,
    ) -> None:
        self._experimental_namespaces = experimental_namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_namespaces import (
            DEFAULT_EXPERIMENTAL_NAMESPACES,
            detect_namespace_patterns,
        )

        namespaces = self._experimental_namespaces or DEFAULT_EXPERIMENTAL_NAMESPACES
        new_findings = detect_namespace_patterns(
            ctx.old,
            ctx.new,
            experimental_namespaces=namespaces,
        )
        if not new_findings:
            return changes
        _merge_findings_respecting_suppression(changes, new_findings, ctx)
        return changes


class DetectInternalLeaks:
    """Detect internal-namespace (``detail::``, ``impl::``, …) types whose
    changes leak through the public ABI surface.

    Runs after dedup / redundancy filtering so the trigger set only
    contains semantically distinct findings. Emitted leak entries are
    added to the change list and become part of the verdict computation.
    """

    name = "detect_internal_leaks"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .internal_leak import (
            DEFAULT_INTERNAL_NAMESPACES,
            detect_call_graph_leaks,
            detect_internal_leaks,
        )

        namespaces = (
            self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
        )
        extra = detect_internal_leaks(changes, ctx.old, ctx.new, namespaces)
        # ADR-044 P1 items 1-2: the call-graph analogue, for a triggering
        # change with no layout/type-graph evidence at all (see
        # MarkReachability's own call-graph fallback, same namespaces).
        extra = extra + detect_call_graph_leaks(changes, ctx.old, ctx.new, namespaces)
        if not extra:
            return changes
        # Synthetic leak findings must respect user suppression rules too.
        # ``ApplySuppression`` ran earlier in the pipeline, so we apply the
        # same check by hand here (via the shared helper, which also emits
        # the withheld-rule diagnostic ``ApplySuppression`` would have)
        # rather than re-running the whole step.
        _merge_findings_respecting_suppression(changes, extra, ctx)
        return changes


class DemoteUnreachableInternalChurn:
    """Demote internal-namespace layout churn that is unreachable from the public API.

    The surface-scoping anti-hiding rule (``surface.classify_change_surface``)
    deliberately keeps every internal-namespace (``detail::``, ``impl::``,
    ``internal::``) type-level finding in-surface so :class:`DetectInternalLeaks`
    — which runs just before this step and seeds from a broader root set — can
    decide whether the type actually leaks through the public ABI.

    When that detector finds NO leak path for an internal type (no
    ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` finding for it), the raw layout churn
    on that type is truly private: it cannot be observed by any public consumer,
    so it must not drive a hard binary ABI verdict. This is the oneTBB case
    (ISSUE-15): ``tbb::detail::*`` / ``rml::internal::*`` DWARF-only churn with
    no exported-symbol impact, which libabigail also reports as ABI-clean.

    The demoted findings are recorded in ``ctx.out_of_surface`` (ADR-024 §D4/D5,
    audit ledger) — never silently dropped — and a genuine leak is still
    surfaced through the separate ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` finding,
    so this can only ever remove confirmed-private noise.
    """

    name = "demote_unreachable_internal_churn"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        import fnmatch

        from .checker_policy import ChangeKind
        from .internal_leak import (
            _LEAK_TRIGGERING_KINDS,
            DEFAULT_INTERNAL_NAMESPACES,
            _root_type_name_for_change,
            _strip_template_args,
            is_internal_type,
        )
        from .surface import REASON_PRIVATE_INTERNAL_UNREACHABLE

        namespaces = (
            self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
        )
        frozen = list(ctx.frozen_namespaces)

        def _is_frozen(type_name: str) -> bool:
            # A contractually frozen namespace (PolicyFile.frozen_namespaces) is
            # an explicit user declaration that changes there must NOT be
            # downgraded. Keep such a finding in-surface so the later
            # EscalateFrozenNamespaceViolations step can tag it and the verdict
            # honours the contract, even when it is otherwise unreachable.
            if not frozen:
                return False
            cand = _strip_template_args(type_name)
            while True:
                if any(fnmatch.fnmatchcase(cand, pat) for pat in frozen):
                    return True
                if "::" not in cand:
                    return False
                cand = cand.rsplit("::", 1)[0]

        # Internal types the leak detector confirmed DO leak through public API.
        leaked_types = {
            c.symbol
            for c in changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        }
        kept: list[Change] = []
        for c in changes:
            root = _root_type_name_for_change(c)
            if (
                c.kind in _LEAK_TRIGGERING_KINDS
                and is_internal_type(root, namespaces)
                and root not in leaked_types
                and not _is_frozen(root)
            ):
                c.surface_exclusion_reason = REASON_PRIVATE_INTERNAL_UNREACHABLE
                ctx.out_of_surface.append(c)
                continue
            kept.append(c)
        # Mutate in place: ``ctx.kept`` aliases this list (set by FilterRedundant
        # and appended to by DetectInternalLeaks), so rebinding would lose the
        # demotion. See DetectCppPatterns for the same in-place contract.
        changes[:] = kept
        return changes


def _scheme_soname(snap: AbiSnapshot) -> str:
    """The *observed* ELF ``DT_SONAME`` for the versioned-scheme cross-check.

    Only an actual recorded SONAME is used — never the snapshot's ``library``
    name, which for source-only or hand-authored snapshots is just the input name
    and may differ from the runtime SONAME. Inferring a SONAME bump from a name
    change would overstate the relink requirement (the report's main visible
    finding under collapse), so absent ELF metadata yields "" and no relink note.
    """
    elf = getattr(snap, "elf", None)
    return (getattr(elf, "soname", "") or "").strip()


class DetectVersionedSymbolScheme:
    """Emit one advisory ``versioned_symbol_scheme_detected`` finding when most
    removed symbols reappear as added symbols differing only by a version token
    (field-eval P08: ICU ``u_*_75`` → ``u_*_78``). Additive by default — it
    explains the churn, the individual func_removed/func_added findings and their
    verdict are untouched.

    When ``ctx.collapse_versioned_symbols`` is set (opt-in, G15 second half), the
    matched version-rename pairs are additionally **reclassified as compatible**:
    moved to ``ctx.suppressed`` and dropped from the kept set, so the verdict
    reflects the real delta instead of the rename churn. This is deliberately
    behind a flag (authority rule: it downgrades artifact-level removals); a real
    SONAME bump or non-versioned removals still drive their own verdict."""

    name = "detect_versioned_symbol_scheme"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind
        from .versioned_symbol_scheme import analyze_versioned_scheme

        if any(c.kind is ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED for c in changes):
            return changes  # idempotent if the pipeline is re-run
        advisory, matched = analyze_versioned_scheme(changes)
        if advisory is None:
            return changes
        # G15: cross-check the version token against the SONAME. A versioned
        # scheme normally bumps the SONAME too (libicui18n.so.75 -> .78); the
        # rename churn is cosmetic, but a new SONAME still means dependents must
        # **relink** against the new shared object. Surface that relink signal on
        # the advisory so the collapse never hides it.
        old_so, new_so = _scheme_soname(ctx.old), _scheme_soname(ctx.new)
        if old_so and new_so and old_so != new_so:
            ctx.versioned_scheme_soname_relink_required = True
            advisory.description += (
                f" The SONAME also changed ({old_so} -> {new_so}): a new shared-object "
                "version, so dependents must relink against the new library even though "
                "the symbol churn is a version-rename."
            )
        # Codex review: shared helper attributes a matched suppression rule.
        _merge_findings_respecting_suppression(changes, [advisory], ctx)
        if ctx.collapse_versioned_symbols and matched:
            # G15: report the collapse count in the summary. caused_count is the
            # number of old-side version-rename pairs reclassified as compatible;
            # the reporter renders it ("N version-renames collapsed").
            old_side_kinds = (
                ChangeKind.FUNC_REMOVED,
                ChangeKind.FUNC_REMOVED_ELF_ONLY,
                ChangeKind.VAR_REMOVED,
                ChangeKind.FUNC_LIKELY_RENAMED,
            )
            advisory.caused_count = sum(1 for c in matched if c.kind in old_side_kinds)
            advisory.description += (
                f" [{advisory.caused_count} version-renames collapsed as compatible]"
            )
            matched_ids = {id(c) for c in matched}
            ctx.suppressed.extend(matched)
            kept = [c for c in changes if id(c) not in matched_ids]
            ctx.kept = kept  # keep verdict source in sync (set mid-pipeline by FilterRedundant)
            return kept
        return changes


class EscalateFrozenNamespaceViolations:
    """Tag findings whose symbol / caused_by_type lies in a contractually
    frozen namespace (e.g. ``**::detail::r1``).

    A "frozen namespace" is one that the library author has declared
    off-limits for changes: it is configured via
    :attr:`PolicyFile.frozen_namespaces` and threaded in through
    :attr:`PipelineContext.frozen_namespaces`.

    Action per matched change:

    * Set :attr:`Change.frozen_namespace_violation` to the matching glob
      pattern. The verdict computation (:meth:`PolicyFile.compute_verdict`)
      uses this field to refuse any policy_override that would downgrade
      the change.
    * Prefix the description with ``[frozen-namespace violation:
      <pattern>] `` so the reporter surfaces the policy context.

    No new ChangeKind is introduced — the underlying kind (e.g.
    ``FUNC_REMOVED``) is preserved so downstream tools that already know
    how to react to it continue to work unchanged.

    Matching uses :func:`fnmatch.fnmatchcase` against ``::``-joined name
    segments of the symbol (and, when set, ``caused_by_type``).  Template
    arguments are stripped before matching so
    ``ns::detail::r1::foo<int>(int)`` correctly matches
    ``**::detail::r1::*``.
    """

    name = "escalate_frozen_namespace_violations"

    @staticmethod
    def _candidate_forms(
        name: str,
        c: Change,
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> list[str]:
        """Collect every plausible C++-qualified form of *name*."""
        # Imported lazily so this module stays free of import cycles.
        from .demangle import demangle
        from .diff_filtering import _qualified_name_for_change

        # The plausible forms are:
        # 1. the raw value (mangled, demangled, or already qualified);
        # 2. the demangled form when the raw value looks Itanium-mangled;
        # 3. the snapshot-recorded qualified name (Function.name), which
        #    is the only form that recovers the namespace of an
        #    ``extern "C"`` symbol whose export name is unqualified.
        forms: list[str] = [name]
        if name.startswith("_Z"):
            dm = demangle(name)
            if dm:
                forms.append(dm)
        if name == c.symbol:
            qual = _qualified_name_for_change(c, old_qualified, new_qualified)
            if qual:
                forms.append(qual)
        return forms

    @classmethod
    def _match(
        cls,
        name: str | None,
        c: Change,
        patterns: list[str],
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> str | None:
        """Return the first frozen-namespace pattern matching *name*, or None."""
        # Imported lazily so this module stays free of import cycles.
        import fnmatch

        from .internal_leak import _strip_template_args

        if not name:
            return None
        for form in cls._candidate_forms(name, c, old_qualified, new_qualified):
            # Walk every ancestor prefix so ``**::detail::r1`` matches
            # both ``ns::detail::r1::foo`` and the deeper
            # ``ns::detail::r1::sub::foo``.
            candidate = _strip_template_args(form)
            while True:
                for pat in patterns:
                    if fnmatch.fnmatchcase(candidate, pat):
                        return pat
                if "::" not in candidate:
                    break
                candidate = candidate.rsplit("::", 1)[0]
        return None

    @classmethod
    def _tag(
        cls,
        c: Change,
        patterns: list[str],
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> None:
        """Tag *c* with the matching frozen-namespace pattern, if any."""
        if c.frozen_namespace_violation is not None:
            # Already tagged by an earlier step (e.g. internal-leak
            # overlay that synthesised a finding with the field set).
            return
        pat = (
            cls._match(c.symbol, c, patterns, old_qualified, new_qualified)
            or cls._match(c.caused_by_type, c, patterns, old_qualified, new_qualified)
            or cls._match(c.qualified_name, c, patterns, old_qualified, new_qualified)
        )
        if pat is None:
            return
        c.frozen_namespace_violation = pat
        if not c.description.startswith("[frozen-namespace violation"):
            c.description = f"[frozen-namespace violation: {pat}] " + c.description

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.frozen_namespaces:
            return changes
        # Imported lazily so this module stays free of import cycles.
        from .diff_filtering import _qualified_functions_by_mangled

        patterns = list(ctx.frozen_namespaces)
        old_qualified = _qualified_functions_by_mangled(ctx.old)
        new_qualified = _qualified_functions_by_mangled(ctx.new)

        for c in changes:
            self._tag(c, patterns, old_qualified, new_qualified)
        # ``compare()`` computes the verdict on kept + redundant, so
        # findings moved into ctx.redundant by FilterRedundant must also
        # be tagged — otherwise a downgrade override could silently
        # apply to a redundant-but-frozen finding.
        for c in ctx.redundant:
            self._tag(c, patterns, old_qualified, new_qualified)
        return changes


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


class PostProcessingPipeline:
    """Execute a sequence of post-processing steps on a change list.

    Each step receives the current change list and a shared context,
    and returns the (possibly modified) change list for the next step.
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self.steps = list(steps)

    def run(
        self,
        changes: list[Change],
        old: AbiSnapshot,
        new: AbiSnapshot,
        suppression: SuppressionList | None = None,
        frozen_namespaces: list[str] | None = None,
        scope_to_public_surface: bool = False,
        force_public_symbols: set[str] | None = None,
        collapse_versioned_symbols: bool = False,
        public_surface_allowlist: set[str] | None = None,
        # Appended after the existing optional parameters (Codex review) —
        # inserting it earlier would silently break a positional caller of
        # any parameter after it (e.g. `.run(c, old, new, sup, fns, True)`
        # for scope_to_public_surface would instead bind `True` here and
        # leave scoping disabled, with no error).
        internal_namespaces: tuple[str, ...] | None = None,
        # ADR-067 C-S1: appended last for the same positional-safety reason
        # the note above records.
        disposition_ledger: DispositionLedger | None = None,
    ) -> PipelineContext:
        """Run all steps, returning the final PipelineContext."""
        ctx = PipelineContext(
            old=old,
            new=new,
            suppression=suppression,
            frozen_namespaces=list(frozen_namespaces or []),
            internal_namespaces=internal_namespaces,
            scope_to_public_surface=scope_to_public_surface,
            force_public_symbols=set(force_public_symbols or set()),
            collapse_versioned_symbols=collapse_versioned_symbols,
            public_surface_allowlist=public_surface_allowlist,
            disposition_ledger=disposition_ledger,
        )
        # ``FilterRedundant`` sets ``ctx.kept = kept`` — an *aliasing* contract,
        # not a snapshot: every step from that point on is required to either
        # leave ``changes`` untouched, mutate it in place (``changes[:] = ...``),
        # or explicitly resync ``ctx.kept`` to whatever new list it returns (see
        # ``DetectVersionedSymbolScheme``). If a future step instead rebinds
        # ``changes = [c for c in changes if ...]`` without updating ``ctx.kept``,
        # ``ctx.kept`` silently keeps pointing at the stale pre-filter list and
        # any suppression/demotion recorded downstream is lost from the verdict
        # with no visible error (this happened once already — see
        # ``DetectCppPatterns``/``DemoteUnreachableInternalChurn``'s in-place
        # comments). Enforce the invariant here instead of trusting every future
        # step author to remember it.
        kept_tracking_active = False
        for step in self.steps:
            before = {id(c): c for c in changes}
            changes = step.run(changes, ctx)
            _record_dropped_duplicates(before, changes, ctx, step.name)
            if step.name == FilterRedundant.name:
                kept_tracking_active = True
            elif kept_tracking_active and ctx.kept is not changes:
                raise RuntimeError(
                    f"post-processing step {step.name!r} broke the ctx.kept "
                    "aliasing contract established by FilterRedundant: it "
                    "returned a `changes` list that is not the same object as "
                    "`ctx.kept`, which silently discards any suppression or "
                    "demotion tracked via ctx.kept from the verdict. Fix the "
                    "step to mutate `changes[:] = ...` in place, or to "
                    "explicitly resync `ctx.kept = changes` before returning."
                )
        # Ensure ctx.kept is set even if FilterRedundant didn't run
        if not ctx.kept and changes:
            ctx.kept = changes
        return ctx

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]


# Default pipeline matching the current compare() post-processing order.
DEFAULT_PIPELINE = PostProcessingPipeline(
    [
        FilterReservedFieldRenames(),
        FilterOpaqueSizeChanges(),
        DowngradeOpaqueStructChanges(),
        DeduplicateAstDwarf(),
        DeduplicateCrossDetector(),
        DowngradeOpaqueTypeChanges(),
        EnrichSourceLocations(),
        FilterNonPublicSurface(),
        # Runs immediately after FilterNonPublicSurface so it can read the
        # resolved C-header surface (ctx.surf_new) and defer to it; otherwise it
        # uses the recovered Python API as the extension's public-contract oracle.
        DemoteOffPythonSurface(),
        # Reads markers TYPE_VTABLE_CHANGED set at emission time (diff_types.py)
        # to cross-reference a LAYOUT_UNVERIFIABLE finding covering the identical
        # evidence gap via Change.correlated_change_kind. Never removes anything
        # from `changes` (see the step's own docstring for why an earlier,
        # removal-based design was unsafe), so its position relative to
        # scoping/suppression/FilterRedundant is not load-bearing the way a
        # filtering step's would be -- kept here simply to run once both
        # findings are guaranteed to exist in `changes` (i.e. after scoping
        # has settled which findings are even still present).
        #
        # Must run *before* MarkReachability, though (Codex review, fresh
        # evidence): that step caches each tagged change's whole
        # ImpactAssessment via impact.engine.assess_change(), and
        # assess_change() itself prefers a cached assessment's own
        # correlated_change_kind over the flat Change field once one exists
        # (impact/engine.py's own documented contract for reusing a cached
        # assessment). Running this step after MarkReachability would let it
        # set the flat field on a change whose impact_assessment was already
        # cached with correlated_change_kind=None -- silently stale for every
        # consumer of that unified object (JSON/SARIF's impact_assessment
        # block) even though the flat top-level field is correct.
        AnnotateLayoutUnverifiableCoveredByVtableChanged(),
        # ADR-044 D1: must run before ApplySuppression so a broad suppression
        # rule can see whether the change it is about to remove is part of the
        # effective public ABI.
        MarkReachability(),
        ApplySuppression(),
        SuppressRenamedPairs(),
        # Runs immediately after suppression settles `changes` so it sees
        # the final post-suppression set -- see its own docstring for why a
        # suppression targeting only the covering TYPE_VTABLE_CHANGED would
        # otherwise leave the earlier annotation dangling.
        ClearOrphanedVtableGapCorrelation(),
        FilterRedundant(),
        EnrichAffectedSymbols(),
        AttributeStdlibEmbedding(),
        DetectInternalLeaks(),
        # Must run immediately after DetectInternalLeaks: it consumes that step's
        # leak verdict to demote confirmed-unreachable internal-namespace churn.
        DemoteUnreachableInternalChurn(),
        DetectCppPatterns(),
        DetectNamespacePatterns(),
        DetectTemplatePatterns(),
        # Advisory overlay: explains a versioned-symbol-scheme churn (P08). Runs
        # after rename suppression so it only sees residual removed/added pairs.
        DetectVersionedSymbolScheme(),
        # Runs last so it can tag both raw findings and the synthetic
        # overlays added by DetectInternalLeaks / DetectCppPatterns.
        EscalateFrozenNamespaceViolations(),
    ]
)
