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

"""Shared change-kind classification constants for report generators.

Centralises the frozensets and helpers used by both ``html_report.py`` and
``compat/xml_report.py`` to avoid maintaining duplicate definitions.
"""
from __future__ import annotations

from .checker import _BREAKING_KINDS as _CHECKER_BREAKING_KINDS_ENUM

# ---------------------------------------------------------------------------
# Change-kind classification
# ---------------------------------------------------------------------------

#: Kinds that count as "removed" (symbol no longer available).
REMOVED_KINDS: frozenset[str] = frozenset({
    "func_removed", "var_removed", "type_removed", "typedef_removed",
    "union_field_removed",
    "enum_member_removed",
})

#: Kinds that count as "added" (new API surface — compatible).
ADDED_KINDS: frozenset[str] = frozenset({
    "func_added", "var_added", "type_added", "func_virtual_added",
    "enum_member_added", "union_field_added", "type_field_added",
    "type_field_added_compatible",
})

#: Binary-only kinds (excluded from source compatibility section).
#: These are derived from ELF metadata or DWARF debug info and have no
#: source-level visibility — recompiling from the same source with the
#: same flags cannot produce these changes.
BINARY_ONLY_KINDS: frozenset[str] = frozenset({
    "soname_changed", "needed_added", "needed_removed",
    "rpath_changed", "runpath_changed",
    "symbol_binding_changed", "symbol_binding_strengthened",
    "symbol_type_changed", "symbol_size_changed", "symbol_size_changed_internal",
    "symbol_size_changed_const_object",
    "ifunc_introduced", "ifunc_removed", "common_symbol_risk",
    "symbol_version_defined_removed",
    "symbol_version_required_added", "symbol_version_required_removed",
    "dwarf_info_missing", "toolchain_flag_drift",
    # DWARF-derived calling convention and frame register changes (#117)
    "calling_convention_changed", "value_abi_trait_changed",
    "struct_return_convention_changed",
    "frame_register_changed",
    # DWARF producer-derived vector-function (SIMD clone) ABI flag drift
    "vector_abi_changed",
})

#: Canonical breaking kinds (single source of truth from checker_policy).
BREAKING_KINDS: frozenset[str] = frozenset(k.value for k in _CHECKER_BREAKING_KINDS_ENUM)

#: Kinds that are breaking but neither a simple removal nor addition.
CHANGED_BREAKING_KINDS: frozenset[str] = frozenset({
    "func_params_changed", "func_return_changed",
    "func_virtual_removed", "func_virtual_became_pure",
    "func_pure_virtual_added", "func_static_changed", "func_cv_changed",
    "var_type_changed",
    "type_size_changed", "type_alignment_changed",
    "type_field_removed", "type_field_offset_changed", "type_field_type_changed",
    "type_base_changed", "type_vtable_changed",
    "enum_member_value_changed",
    "enum_underlying_size_changed",
    "struct_size_changed", "struct_field_offset_changed", "struct_field_removed",
    "struct_field_type_changed", "struct_alignment_changed",
    "field_bitfield_changed",
    "calling_convention_changed", "struct_packing_changed",
    "struct_return_convention_changed",
    "func_visibility_changed",
    "typedef_base_changed",
    "union_field_type_changed",
    "type_visibility_changed",
    "symbol_type_changed", "symbol_size_changed",
    "symbol_version_defined_removed",
})

# ---------------------------------------------------------------------------
# ABICC severity mapping
# ---------------------------------------------------------------------------

HIGH_SEVERITY_KINDS: frozenset[str] = frozenset({
    "func_removed", "var_removed", "type_removed", "typedef_removed",
    "type_size_changed", "type_vtable_changed", "type_base_changed",
    "struct_size_changed", "func_virtual_removed",
    "func_pure_virtual_added", "func_virtual_became_pure",
    "base_class_position_changed", "base_class_virtual_changed",
    "type_kind_changed", "func_deleted",
})

MEDIUM_SEVERITY_KINDS: frozenset[str] = frozenset({
    "func_return_changed", "func_params_changed",
    "type_field_offset_changed", "type_field_type_changed",
    "type_field_removed", "type_alignment_changed",
    "struct_field_offset_changed", "struct_field_removed",
    "struct_field_type_changed", "struct_alignment_changed",
    "var_type_changed", "calling_convention_changed",
    "struct_return_convention_changed",
    "soname_changed", "symbol_type_changed",
    "symbol_version_defined_removed",
    "return_pointer_level_changed", "param_pointer_level_changed",
    "union_field_removed", "union_field_type_changed",
    "typedef_base_changed", "struct_packing_changed",
})

# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

#: Prefixes for type-related problem kinds.
TYPE_PROBLEM_PREFIXES: tuple[str, ...] = (
    "type_", "struct_", "union_", "field_", "typedef_", "enum_", "base_class_",
)

#: Prefixes for symbol/interface-related problem kinds.
SYMBOL_PROBLEM_PREFIXES: tuple[str, ...] = (
    "func_", "var_",
)

#: Category buckets for summary tables — mirrors ABICC section headers.
CATEGORY_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("Functions", ("func_",)),
    ("Variables", ("var_",)),
    ("Types", ("type_", "struct_", "union_", "field_", "typedef_")),
    ("Enums", ("enum_",)),
    ("ELF / DWARF", ("soname_", "symbol_", "needed_", "rpath_", "runpath_",
                     "ifunc_", "common_", "dwarf_")),
]


# ---------------------------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------------------------

def kind_str(change: object) -> str:
    """Extract the string value of a change's kind."""
    kind = getattr(change, "kind", None)
    return kind.value if kind is not None and hasattr(kind, "value") else str(kind)


def is_breaking(change: object) -> bool:
    """Return True if the change is classified as breaking."""
    return kind_str(change) in BREAKING_KINDS


def category(kind_s: str) -> str:
    """Classify a change kind string into a category label."""
    for label, prefixes in CATEGORY_PREFIXES:
        if any(kind_s.startswith(p) for p in prefixes):
            return label
    return "Other"


def severity(kind_s: str) -> str:
    """Map a change kind to ABICC severity tier."""
    if kind_s in HIGH_SEVERITY_KINDS:
        return "High"
    if kind_s in MEDIUM_SEVERITY_KINDS:
        return "Medium"
    return "Low"


def is_type_problem(kind_s: str) -> bool:
    """Return True if the kind relates to a type problem."""
    return any(kind_s.startswith(p) for p in TYPE_PROBLEM_PREFIXES)


def is_symbol_problem(kind_s: str) -> bool:
    """Return True if the kind relates to a symbol/interface problem."""
    return any(kind_s.startswith(p) for p in SYMBOL_PROBLEM_PREFIXES)
