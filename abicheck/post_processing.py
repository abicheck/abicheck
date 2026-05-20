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
    # Accumulated side-outputs
    opaque_filtered: list[Change] = field(default_factory=list)
    suppressed: list[Change] = field(default_factory=list)
    redundant: list[Change] = field(default_factory=list)
    kept: list[Change] = field(default_factory=list)


class PipelineStep(Protocol):
    """Protocol for a single post-processing step."""

    name: str

    def run(
        self, changes: list[Change], ctx: PipelineContext
    ) -> list[Change]:
        """Transform the change list, returning the updated list."""
        ...


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
        seen_symbols = {
            (c.kind, c.symbol) for c in changes
        }
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
    ) -> PipelineContext:
        """Run all steps, returning the final PipelineContext."""
        ctx = PipelineContext(old=old, new=new, suppression=suppression)
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
DEFAULT_PIPELINE = PostProcessingPipeline([
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
])
