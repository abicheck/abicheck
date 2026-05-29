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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot
    from .suppression import SuppressionList


@dataclass
class PipelineContext:
    """Shared state passed through the pipeline."""

    old: AbiSnapshot
    new: AbiSnapshot
    suppression: SuppressionList | None = None
    # Glob patterns identifying contractually frozen namespaces (e.g.
    # ``**::detail::r1``). Threaded in from PolicyFile.frozen_namespaces.
    # Consumed by EscalateFrozenNamespaceViolations to tag matching
    # findings with Change.frozen_namespace_violation.
    frozen_namespaces: list[str] = field(default_factory=list)
    # Accumulated side-outputs
    opaque_filtered: list[Change] = field(default_factory=list)
    suppressed: list[Change] = field(default_factory=list)
    redundant: list[Change] = field(default_factory=list)
    kept: list[Change] = field(default_factory=list)


class PipelineStep(Protocol):
    """Protocol for a single post-processing step."""

    name: str

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Transform the change list, returning the updated list."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches_suppression_key(symbol: str, key: str) -> bool:
    """Return ``True`` iff *symbol* is suppressed by *key*.

    Used by :class:`DetectOneDALPatterns` to match per-symbol
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

        return _deduplicate_ast_dwarf(changes)


class DeduplicateCrossDetector:
    """Collapse overlapping reports from different detectors."""

    name = "deduplicate_cross_detector"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _deduplicate_cross_detector

        return _deduplicate_cross_detector(changes)


class DowngradeOpaqueTypeChanges:
    """Suppress structural changes for opaque types."""

    name = "downgrade_opaque_type_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _downgrade_opaque_type_changes

        return _downgrade_opaque_type_changes(changes, ctx.old, ctx.new)


class EnrichSourceLocations:
    """Add source location metadata for suppression matching."""

    name = "enrich_source_locations"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _enrich_source_locations

        _enrich_source_locations(changes, ctx.old, ctx.new)
        return changes


class ApplySuppression:
    """Apply user-provided suppression rules."""

    name = "apply_suppression"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None:
            return changes
        filtered: list[Change] = []
        for c in changes:
            if ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
            else:
                filtered.append(c)
        return filtered


class SuppressRenamedPairs:
    """Suppress FUNC_REMOVED + FUNC_ADDED pairs when a FUNC_LIKELY_RENAMED exists.

    When the fingerprint rename detector identifies a rename (old_name → new_name),
    the corresponding FUNC_REMOVED(old_name) and FUNC_ADDED(new_name) are redundant
    noise.  This step moves them to ctx.redundant and annotates the rename change
    with caused_count.
    """

    name = "suppress_renamed_pairs"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind

        # Build rename mappings: old_name → new_name and new_name → old_name
        renamed_old: dict[str, str] = {}  # old_value → new_value
        renamed_new: dict[str, str] = {}  # new_value → old_value
        rename_changes: dict[str, Change] = {}  # old_value → the rename Change
        for c in changes:
            if c.kind == ChangeKind.FUNC_LIKELY_RENAMED and c.old_value and c.new_value:
                renamed_old[c.old_value] = c.new_value
                renamed_new[c.new_value] = c.old_value
                rename_changes[c.old_value] = c

        if not renamed_old:
            return changes

        kept: list[Change] = []
        for c in changes:
            if c.kind in (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY):
                old_name = c.old_value or c.symbol
                if old_name in renamed_old:
                    c.caused_by_type = f"rename:{old_name}→{renamed_old[old_name]}"
                    ctx.redundant.append(c)
                    rc = rename_changes.get(old_name)
                    if rc is not None:
                        rc.caused_count += 1
                    continue
            elif c.kind == ChangeKind.FUNC_ADDED:
                new_name = c.new_value or c.symbol
                if new_name in renamed_new:
                    c.caused_by_type = f"rename:{renamed_new[new_name]}→{new_name}"
                    ctx.redundant.append(c)
                    rc = rename_changes.get(renamed_new[new_name])
                    if rc is not None:
                        rc.caused_count += 1
                    continue
            kept.append(c)
        return kept


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


class DetectOneDALPatterns:
    """Run the oneDAL-shaped detectors added in PR #239 (case77–case89).

    Each individual detector lives in :mod:`abicheck.diff_onedal`; this
    pipeline step wires them together, dedupes findings against the
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

    name = "detect_onedal_patterns"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind

        # Generic detectors live in dedicated modules after PR-D; the
        # remaining oneDAL-shaped ones stay in diff_onedal.
        from .diff_onedal import (
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
            ctx.old,
            ctx.new,
        )
        new_findings.extend(sycl_findings)

        isa_findings, isa_suppressed = detect_cpu_dispatch_isa_dropped(
            ctx.old,
            ctx.new,
        )
        new_findings.extend(isa_findings)

        new_findings.extend(detect_tag_type_renamed(ctx.old, ctx.new))
        new_findings.extend(
            detect_default_template_arg_changed(
                ctx.old,
                ctx.new,
            )
        )
        new_findings.extend(
            detect_inline_body_renamed_member(
                ctx.old,
                ctx.new,
                changes,
            )
        )

        # Filter out per-symbol ``func_removed`` findings that are
        # children of the grouped SYCL/ISA detectors.
        #
        # Two reasons to use ``ctx.suppressed`` (not ``ctx.redundant``):
        # (a) ``compare()`` computes verdict on ``kept + redundant`` —
        #     redundant items still drive the verdict. Putting the
        #     children there would let per-symbol BREAKING outrank the
        #     grouped RISK finding. ``ctx.suppressed`` is excluded from
        #     verdict computation, which is what we want for children
        #     subsumed by a grouped finding.
        # (b) ``FilterRedundant`` (earlier in the pipeline) sets
        #     ``ctx.kept = changes`` — that's a *reference* to this same
        #     list. If we rebind ``changes`` to a new filtered list,
        #     ``ctx.kept`` still points at the old one and our
        #     suppression is silently lost. Mutate in place instead.
        #
        # We match the per-symbol ``Change.symbol`` against the
        # suppression set using BOTH exact equality and a guarded
        # substring containment. On Linux ``diff_symbols._diff_functions``
        # emits ``Change.symbol = fn.mangled`` (Itanium mangling); on
        # Windows ``diff_platform._diff_pe`` emits
        # ``Change.symbol = e.name`` (PE export-table name = MSVC
        # mangling), which is a sibling encoding of the same underlying
        # function but a different string. The demangled function name
        # (e.g. ``kmeans_compute_avx512``) is a substring of both
        # mangled forms, so substring containment is the platform-
        # portable signal — *but* only when the key is structured enough
        # to be unambiguous. A generic short leaf like ``compute`` would
        # falsely match unrelated symbols such as ``precompute`` or
        # ``Recompute_xyz``. The ``_matches_suppression_key`` helper
        # requires the key to contain a namespace separator, an
        # underscore, or be at least 12 chars before allowing substring
        # match. Exact equality is always honoured.
        suppressed_keys = sycl_suppressed | isa_suppressed
        if suppressed_keys:
            to_keep: list[Change] = []
            for ch in changes:
                if ch.kind == ChangeKind.FUNC_REMOVED and any(
                    _matches_suppression_key(ch.symbol, key)
                    for key in suppressed_keys
                ):
                    ctx.suppressed.append(ch)
                    continue
                to_keep.append(ch)
            changes[:] = to_keep

        if not new_findings:
            return changes
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)
        return changes


class DetectTemplatePatterns:
    """Run the generic template / overload-set pattern detectors.

    Lives in :mod:`abicheck.diff_templates`. Covers internal-template
    leaks (function-template analogue of PR #238), CPO kind flips,
    overload-set rerouting, mandatory-template-param additions, and
    unspecified-return flips.
    """

    name = "detect_template_patterns"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_templates import detect_template_patterns

        new_findings = detect_template_patterns(ctx.old, ctx.new)
        if not new_findings:
            return changes
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)
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

        namespaces = (
            self._experimental_namespaces or DEFAULT_EXPERIMENTAL_NAMESPACES
        )
        new_findings = detect_namespace_patterns(
            ctx.old, ctx.new, experimental_namespaces=namespaces,
        )
        if not new_findings:
            return changes
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)
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
            detect_internal_leaks,
        )

        namespaces = self._namespaces or DEFAULT_INTERNAL_NAMESPACES
        extra = detect_internal_leaks(changes, ctx.old, ctx.new, namespaces)
        if not extra:
            return changes
        # Avoid duplicates if the pipeline is re-run.
        seen_symbols = {(c.kind, c.symbol) for c in changes}
        # Synthetic leak findings must respect user suppression rules
        # too. ``ApplySuppression`` ran earlier in the pipeline, so we
        # apply the same predicate by hand here rather than re-running
        # the whole step.
        for c in extra:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            if (c.kind, c.symbol) not in seen_symbols:
                changes.append(c)
                seen_symbols.add((c.kind, c.symbol))
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

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.frozen_namespaces:
            return changes
        # Imported lazily so this module stays free of import cycles.
        import fnmatch

        from .demangle import demangle
        from .internal_leak import _strip_template_args

        patterns = list(ctx.frozen_namespaces)

        # Pre-build mangled→qualified-name lookups so we can recover the
        # C++ namespace of ``extern "C"`` symbols whose ``Change.symbol``
        # is just the unqualified export name (e.g. ``dispatch`` for a
        # function declared in ``mylib::detail::r1::``). Both snapshots
        # are consulted because FUNC_REMOVED is only in old and
        # FUNC_ADDED only in new.
        qualified_lookup: dict[str, str] = {}
        for snap in (ctx.old, ctx.new):
            if snap is None:
                continue
            try:
                snap.index()
            except Exception:  # noqa: BLE001 — defensive; snapshots may be partial
                continue
            func_map = getattr(snap, "_func_by_mangled", None) or {}
            for mangled, fn in func_map.items():
                fname = getattr(fn, "name", None)
                if fname and "::" in fname and mangled not in qualified_lookup:
                    qualified_lookup[mangled] = fname

        def _match(name: str | None) -> str | None:
            if not name:
                return None
            # Collect every plausible C++-qualified form of *name*:
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
            qual = qualified_lookup.get(name)
            if qual:
                forms.append(qual)

            for form in forms:
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

        def _tag(c: Change) -> None:
            if c.frozen_namespace_violation is not None:
                # Already tagged by an earlier step (e.g. internal-leak
                # overlay that synthesised a finding with the field set).
                return
            pat = (
                _match(c.symbol)
                or _match(c.caused_by_type)
                or _match(c.qualified_name)
            )
            if pat is None:
                return
            c.frozen_namespace_violation = pat
            if not c.description.startswith("[frozen-namespace violation"):
                c.description = (
                    f"[frozen-namespace violation: {pat}] " + c.description
                )

        for c in changes:
            _tag(c)
        # ``compare()`` computes the verdict on kept + redundant, so
        # findings moved into ctx.redundant by FilterRedundant must also
        # be tagged — otherwise a downgrade override could silently
        # apply to a redundant-but-frozen finding.
        for c in ctx.redundant:
            _tag(c)
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
    ) -> PipelineContext:
        """Run all steps, returning the final PipelineContext."""
        ctx = PipelineContext(
            old=old,
            new=new,
            suppression=suppression,
            frozen_namespaces=list(frozen_namespaces or []),
        )
        for step in self.steps:
            changes = step.run(changes, ctx)
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
        ApplySuppression(),
        SuppressRenamedPairs(),
        FilterRedundant(),
        EnrichAffectedSymbols(),
        DetectInternalLeaks(),
        DetectOneDALPatterns(),
        DetectNamespacePatterns(),
        DetectTemplatePatterns(),
        # Runs last so it can tag both raw findings and the synthetic
        # overlays added by DetectInternalLeaks / DetectOneDALPatterns.
        EscalateFrozenNamespaceViolations(),
    ]
)
