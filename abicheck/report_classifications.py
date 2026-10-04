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

Centralises the frozensets and helpers the report generators share, so
they are defined once.
"""

from __future__ import annotations

from .checker import _BREAKING_KINDS as _CHECKER_BREAKING_KINDS_ENUM

# ---------------------------------------------------------------------------
# Change-kind classification
# ---------------------------------------------------------------------------

#: Kinds that count as "removed" (symbol no longer available).
REMOVED_KINDS: frozenset[str] = frozenset(
    {
        "func_removed",
        # Pre-existing omission, found by `test_evidence_tier_registry_
        # parity.py` while registering this kind's *addition* counterpart: an
        # export that disappeared is a removal whether the headers declared
        # it or not, so HTML and the compat XML were rendering it as a
        # "changed" symbol and counting zero removals for it.
        "func_removed_elf_only",
        "var_removed",
        "var_removed_elf_only",
        "type_removed",
        "typedef_removed",
        "union_field_removed",
        "enum_member_removed",
    }
)

#: Kinds that count as "added" (new API surface — compatible).
ADDED_KINDS: frozenset[str] = frozenset(
    {
        "func_added",
        "func_added_elf_only",
        "var_added",
        "var_added_elf_only",
        "type_added",
        "func_virtual_added",
        "enum_member_added",
        "union_field_added",
        "type_field_added",
        "type_field_added_compatible",
    }
)

#: Environment / toolchain drift kinds — findings caused by the *build
#: environment* (compiler, binutils/linker defaults, glibc/sysroot version)
#: rather than by a source-level interface change. Reporters group these into
#: a dedicated section so a reader can immediately separate "the API moved"
#: from "the build environment moved". Membership answers "did the environment
#: cause it", not "is it safe" — the severity axis is orthogonal.
ENVIRONMENT_DRIFT_KINDS: frozenset[str] = frozenset(
    {
        # Runtime deployment envelope (glibc & friends)
        "runtime_floor_raised",
        "symbol_version_required_added",
        "symbol_version_required_added_compat",
        "symbol_version_required_removed",
        # Linker (binutils) default drift
        "dt_relr_introduced",
        "dt_relr_removed",
        "rpath_type_changed",
        "hash_style_removed",
        "cet_protection_weakened",
        "cet_protection_improved",
        "branch_protection_weakened",
        "branch_protection_improved",
        "static_tls_introduced",
        "static_tls_removed",
        # Compiler / standard library / sysroot drift
        "toolchain_version_changed",
        "toolchain_flag_drift",
        "stdlib_implementation_changed",
        "stdlib_debug_mode_changed",
        "libcpp_abi_version_changed",
        "glibcxx_dual_abi_flip_detected",
        "time64_abi_changed",
        "integer_model_changed",
        "long_double_abi_changed",
        "vector_abi_changed",
    }
)

#: Canonical breaking kinds (single source of truth from checker_policy).
BREAKING_KINDS: frozenset[str] = frozenset(
    k.value for k in _CHECKER_BREAKING_KINDS_ENUM
)

#: Kinds that are breaking but neither a simple removal nor addition.
CHANGED_BREAKING_KINDS: frozenset[str] = frozenset(
    {
        "func_params_changed",
        "func_return_changed",
        "func_virtual_removed",
        "func_virtual_became_pure",
        "func_pure_virtual_added",
        "func_static_changed",
        "func_cv_changed",
        "var_type_changed",
        "type_size_changed",
        "type_alignment_changed",
        "type_field_removed",
        "type_field_offset_changed",
        "type_field_type_changed",
        "type_base_changed",
        "type_vtable_changed",
        "enum_member_value_changed",
        "enum_underlying_size_changed",
        "struct_size_changed",
        "struct_field_offset_changed",
        "struct_field_removed",
        "struct_field_type_changed",
        "struct_alignment_changed",
        "field_bitfield_changed",
        "calling_convention_changed",
        "struct_packing_changed",
        "struct_return_convention_changed",
        "func_visibility_changed",
        "typedef_base_changed",
        "union_field_type_changed",
        "type_visibility_changed",
        "symbol_type_changed",
        "symbol_size_changed",
        "symbol_version_defined_removed",
    }
)

# ---------------------------------------------------------------------------
# ABICC severity mapping
# ---------------------------------------------------------------------------

HIGH_SEVERITY_KINDS: frozenset[str] = frozenset(
    {
        "func_removed",
        # Pre-existing omission, found by `test_evidence_tier_registry_
        # parity.py` while registering this kind's *addition* counterpart: an
        # export that disappeared is a removal whether the headers declared
        # it or not, so HTML and the compat XML were rendering it as a
        # "changed" symbol and counting zero removals for it.
        "func_removed_elf_only",
        "var_removed",
        "var_removed_elf_only",
        "type_removed",
        "typedef_removed",
        "type_size_changed",
        "type_vtable_changed",
        "type_base_changed",
        "struct_size_changed",
        "func_virtual_removed",
        "func_pure_virtual_added",
        "func_virtual_became_pure",
        "base_class_position_changed",
        "base_class_virtual_changed",
        "type_kind_changed",
        "func_deleted",
    }
)

MEDIUM_SEVERITY_KINDS: frozenset[str] = frozenset(
    {
        "func_return_changed",
        "func_params_changed",
        "type_field_offset_changed",
        "type_field_type_changed",
        "type_field_removed",
        "type_alignment_changed",
        "struct_field_offset_changed",
        "struct_field_removed",
        "struct_field_type_changed",
        "struct_alignment_changed",
        "var_type_changed",
        "calling_convention_changed",
        "struct_return_convention_changed",
        "soname_changed",
        "symbol_type_changed",
        "symbol_version_defined_removed",
        "return_pointer_level_changed",
        "param_pointer_level_changed",
        "union_field_removed",
        "union_field_type_changed",
        "typedef_base_changed",
        "struct_packing_changed",
    }
)

# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

#: Category buckets for summary tables — mirrors ABICC section headers.
CATEGORY_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("Functions", ("func_",)),
    ("Variables", ("var_",)),
    ("Types", ("type_", "struct_", "union_", "field_", "typedef_")),
    ("Enums", ("enum_",)),
    (
        "ELF / DWARF",
        (
            "soname_",
            "symbol_",
            "needed_",
            "rpath_",
            "runpath_",
            "ifunc_",
            "common_",
            "dwarf_",
        ),
    ),
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
