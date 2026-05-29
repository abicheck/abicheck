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

"""Checker — diff two AbiSnapshots, classify changes, produce a verdict."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import (
    API_BREAK_KINDS as _API_BREAK_KINDS,
)
from .checker_policy import (
    BREAKING_KINDS as _BREAKING_KINDS,
)
from .checker_policy import (
    COMPATIBLE_KINDS as _COMPATIBLE_KINDS,
)
from .checker_policy import (
    RISK_KINDS as _RISK_KINDS,
)
from .checker_policy import (
    ChangeKind,
    Verdict,
    compute_verdict,
)
from .checker_types import (  # noqa: F401
    Change,
    DetectorSpec,
    DiffResult,
    LibraryMetadata,
)
from .detector_registry import registry as _detector_registry
from .diff_filtering import (  # noqa: F401
    _ROOT_TYPE_CHANGE_KINDS,
    _compute_confidence,
    _deduplicate_ast_dwarf,
    _deduplicate_cross_detector,
    _downgrade_opaque_struct_changes,
    _downgrade_opaque_type_changes,
    _enrich_affected_symbols,
    _enrich_source_locations,
    _filter_opaque_size_changes,
    _filter_redundant,
    _filter_reserved_field_renames,
    _match_root_type,
)
from .diff_platform import (  # noqa: F401
    _diff_dwarf,
    _diff_elf,
    _diff_elf_deleted_fallback,
    _diff_elf_symbol_metadata,
    _diff_leaked_dependency_symbols,
    _diff_macho,
    _diff_pe,
    _diff_struct_layouts,
    _diff_template_inner_types,
    _extract_template_args,
    _template_outer,
)
from .diff_sycl import _diff_sycl  # noqa: F401 — triggers detector registration
from .diff_symbols import _PUBLIC_VIS
from .diff_types import (  # noqa: F401
    _diff_const_overloads,
    _diff_enum_renames,
    _diff_enums,
    _diff_field_qualifiers,
    _diff_field_renames,
    _diff_method_qualifiers,
    _diff_reserved_fields,
    _diff_type_kind_changes,
    _diff_typedefs,
    _diff_types,
    _diff_unions,
    _diff_var_values,
    _is_version_stamped_typedef,
)
from .diff_versioning import (  # noqa: F401 — re-export for testing
    check_soname_bump_policy,
    detect_version_node_changes,
    detect_version_script_missing,
)
from .dwarf_advanced import (
    diff_advanced_dwarf,  # noqa: F401 — re-export for monkeypatching
)
from .model import AbiSnapshot
from .policy_file import PolicyFile

if TYPE_CHECKING:
    from .suppression import SuppressionList

__all__ = [
    "ChangeKind",
    "Verdict",
    "_BREAKING_KINDS",
    "_COMPATIBLE_KINDS",
    "_API_BREAK_KINDS",
    "_RISK_KINDS",
    "_SOURCE_BREAK_KINDS",  # deprecated alias
    "Change",
    "LibraryMetadata",
    "DiffResult",
    "compare",
    "_ROOT_TYPE_CHANGE_KINDS",
]

# Deprecated alias — kept for external consumers; will be removed in v2.0
_SOURCE_BREAK_KINDS = _API_BREAK_KINDS


# _DetectorSpec is now DetectorSpec in checker_types; keep alias for internal use.
_DetectorSpec = DetectorSpec


@_detector_registry.detector(
    "advanced_dwarf",
    requires_support=lambda o, n: (
        o.dwarf_advanced is not None and n.dwarf_advanced is not None,
        "missing DWARF advanced metadata",
    ),
)
def _diff_advanced_dwarf(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Sprint 4: calling convention, packing, toolchain flag drift.

    Kept in checker.py (not diff_platform) so that tests can monkeypatch
    ``checker_mod.diff_advanced_dwarf`` and have the patch take effect.
    """
    from .dwarf_advanced import AdvancedDwarfMetadata

    o: AdvancedDwarfMetadata = getattr(old, "dwarf_advanced", None) or AdvancedDwarfMetadata()
    n: AdvancedDwarfMetadata = getattr(new, "dwarf_advanced", None) or AdvancedDwarfMetadata()

    _kind_map = {
        "calling_convention_changed": ChangeKind.CALLING_CONVENTION_CHANGED,
        "value_abi_trait_changed": ChangeKind.VALUE_ABI_TRAIT_CHANGED,
        "struct_packing_changed": ChangeKind.STRUCT_PACKING_CHANGED,
        "toolchain_flag_drift": ChangeKind.TOOLCHAIN_FLAG_DRIFT,
        "vector_abi_changed": ChangeKind.VECTOR_ABI_CHANGED,
        "type_visibility_changed": ChangeKind.TYPE_VISIBILITY_CHANGED,
        "frame_register_changed": ChangeKind.FRAME_REGISTER_CHANGED,
    }

    return [
        Change(
            kind=_kind_map[kind_str],
            symbol=sym,
            description=desc,
            old_value=old_val,
            new_value=new_val,
        )
        for kind_str, sym, desc, old_val, new_val in diff_advanced_dwarf(o, n)
        if kind_str in _kind_map
    ]


def compare(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> DiffResult:
    """Diff two AbiSnapshots and return a DiffResult with verdict.

    Args:
        old: Old ABI snapshot.
        new: New ABI snapshot.
        suppression: Optional suppression list to filter known changes.
        policy: Policy profile name to use for verdict classification.
            Available: "strict_abi" (default), "sdk_vendor", "plugin_abi".
            Ignored when *policy_file* is provided.
        policy_file: Optional :class:`~abicheck.policy_file.PolicyFile` instance
            for user-defined per-kind verdict overrides.  When provided,
            *policy* is used only as the ``base_policy`` fallback inside the
            file (i.e. the file's own ``base_policy`` field takes precedence).
    """

    # Run all registered detectors via the self-registering registry.
    changes, detector_results = _detector_registry.run_all(old, new)

    # Post-detector: SONAME bump policy check (needs full change list).
    # Uses the module-level import of check_soname_bump_policy.
    from .elf_metadata import ElfMetadata as _ElfMetadata

    _old_elf = getattr(old, "elf", None) or _ElfMetadata()
    _new_elf = getattr(new, "elf", None) or _ElfMetadata()
    soname_changes = check_soname_bump_policy(changes, _old_elf, _new_elf)
    changes.extend(soname_changes)

    # Run the post-processing pipeline (filtering, dedup, enrichment, suppression).
    # PolicyFile.frozen_namespaces is threaded in so the late-stage
    # EscalateFrozenNamespaceViolations step can tag matching findings.
    from .post_processing import DEFAULT_PIPELINE
    frozen_ns = list(policy_file.frozen_namespaces) if policy_file is not None else []
    pp_ctx = DEFAULT_PIPELINE.run(
        changes, old, new, suppression=suppression, frozen_namespaces=frozen_ns,
    )
    kept = pp_ctx.kept
    redundant = pp_ctx.redundant
    opaque_filtered = pp_ctx.opaque_filtered
    suppressed = pp_ctx.suppressed

    # Verdict computed on unsuppressed semantic changes.
    # NOTE: opaque_filtered changes are intentionally excluded from verdict
    # (they are compatibility-preserving noise, e.g. opaque handle size drift).
    all_unsuppressed = kept + redundant
    verdict = policy_file.compute_verdict(all_unsuppressed) if policy_file is not None else compute_verdict(all_unsuppressed, policy=policy)
    effective_policy = policy_file.base_policy if policy_file is not None else policy

    # opaque_filtered changes are visible under --show-redundant for audit, but their
    # label in the reporter is distinct from true display-dedup redundant changes.
    # redundant_count reflects only the display-dedup set; opaque_filtered is additive.
    redundant_for_report = redundant + opaque_filtered
    true_redundant_count = len(redundant)  # dedup-only (not opaque); used for report label

    # Compute old_symbol_count once for downstream metrics (Bug 8)
    old_sym_count = sum(
        1 for f in old.functions if f.visibility in _PUBLIC_VIS
    ) + sum(
        1 for v in old.variables if v.visibility in _PUBLIC_VIS
    )

    # Compute evidence tiers and confidence from detector results.
    evidence_tiers, confidence, coverage_warnings = _compute_confidence(
        detector_results, old, new,
    )

    return DiffResult(
        old_version=old.version,
        new_version=new.version,
        library=old.library,
        changes=kept,
        verdict=verdict,
        suppressed_count=len(suppressed),
        suppressed_changes=suppressed,
        suppression_file_provided=suppression is not None,
        detector_results=detector_results,
        policy=effective_policy,
        policy_file=policy_file,
        redundant_changes=redundant_for_report,
        redundant_count=true_redundant_count,
        old_symbol_count=old_sym_count if old_sym_count > 0 else None,
        confidence=confidence,
        evidence_tiers=evidence_tiers,
        coverage_warnings=coverage_warnings,
    )
