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

"""The stored snapshot sections each detector reads (ADR-062 D14/D15).

A loaded snapshot may hold a section this build could not use: it names a
must-understand extension this build does not understand, it is written at
a newer ``section_schema_version``, or its extraction ``failed``
(``AbiSnapshot.unreadable_sections``, filled by the storage loader). Its
fields then hold defaults, not evidence, so every analysis that reads it is
*not comparable*: the registry records it ``not_evaluated`` with
:func:`not_comparable_reason` instead of running it on that input, while
every other detector still runs.

``DETECTOR_SECTIONS`` names, per registered detector, the sections it reads.
``tests/test_section_readers_contract.py`` derives the same table from an
AST scan of each detector's reachable code and the storage layer's section
partition, so it cannot rot. Section kinds are plain strings here: ``compare``
may not import ``storage``.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "DETECTOR_SECTIONS",
    "RULE_SECTIONS",
    "SURFACE_METRICS_RULE",
    "not_comparable_reason",
    "unreadable_sections_read",
]

DETECTOR_SECTIONS: Mapping[str, frozenset[str]] = {
    "abi_surface": frozenset(["binary"]),
    "abi_tag": frozenset(["binary", "declarations"]),
    "access_levels": frozenset(
        ["binary", "declarations", "layout", "provenance", "semantic_ir", "types"]
    ),
    "advanced_dwarf": frozenset(["debug"]),
    "anon_fields": frozenset(["binary", "provenance", "types"]),
    "atomic_qualifier": frozenset(["binary", "declarations", "provenance", "types"]),
    "bit_int_width": frozenset(["binary", "declarations", "provenance", "types"]),
    "char8t_migration": frozenset(["binary", "declarations", "provenance", "types"]),
    "const_overloads": frozenset(["binary", "declarations"]),
    "constants": frozenset(["debug", "declarations", "semantic_ir"]),
    "ctor_overload_ambiguity": frozenset(["declarations", "semantic_ir", "types"]),
    "dwarf": frozenset(["binary", "debug", "declarations", "provenance", "types"]),
    "dwarf_layout_coherence": frozenset(["layout", "provenance"]),
    "elf": frozenset(["binary", "debug", "declarations", "provenance"]),
    "elf_deleted_fallback": frozenset(["binary", "declarations", "layout"]),
    "elf_layout": frozenset(["binary", "provenance"]),
    "enum_deprecated": frozenset(["binary", "declarations", "provenance"]),
    "enum_renames": frozenset(["binary", "declarations", "provenance"]),
    "enums": frozenset(
        ["binary", "debug", "declarations", "provenance", "semantic_ir", "types"]
    ),
    "field_default_initializer": frozenset(
        ["binary", "debug", "declarations", "provenance", "types"]
    ),
    "field_deprecated": frozenset(["binary", "declarations", "provenance", "types"]),
    "field_qualifiers": frozenset(
        ["binary", "debug", "declarations", "provenance", "types"]
    ),
    "field_renames": frozenset(["binary", "declarations", "provenance", "types"]),
    "fingerprint_renames": frozenset(["binary", "provenance"]),
    "func_deprecated": frozenset(
        ["binary", "declarations", "layout", "provenance", "semantic_ir", "types"]
    ),
    "func_override_specifier": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "func_return_pointee_qualifier": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "functions": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "graph",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "glibcxx_dual_abi": frozenset(["declarations"]),
    "inline_namespace": frozenset(["declarations"]),
    "integer_model": frozenset(["binary", "declarations"]),
    "kabi": frozenset(["binary"]),
    "layout_descriptor": frozenset(["debug", "types"]),
    "long_double": frozenset(["binary", "declarations", "provenance"]),
    "macho": frozenset(["binary", "layout"]),
    "method_qualifiers": frozenset(
        ["binary", "declarations", "layout", "provenance", "types"]
    ),
    "overload_additions": frozenset(
        ["binary", "declarations", "layout", "provenance", "types"]
    ),
    "overload_ambiguity": frozenset(
        ["binary", "debug", "declarations", "layout", "provenance", "types"]
    ),
    "param_defaults": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "param_pointee_qualifier": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "param_renames": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "param_restrict": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "param_va_list": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "pe": frozenset(["binary", "layout"]),
    "pointer_levels": frozenset(
        ["binary", "declarations", "layout", "provenance", "semantic_ir", "types"]
    ),
    "protected_visibility": frozenset(["binary"]),
    "python_api": frozenset(["declarations"]),
    "python_ext": frozenset(["declarations"]),
    "reserved_fields": frozenset(["binary", "declarations", "provenance", "types"]),
    "stdlib_impl": frozenset(["binary", "declarations", "types"]),
    "sycl": frozenset(["declarations"]),
    "symbol_renames": frozenset(
        ["binary", "declarations", "layout", "provenance", "types"]
    ),
    "symbol_version_alias": frozenset(["binary"]),
    "target_isa": frozenset(["binary"]),
    "template_inner_types": frozenset(
        ["binary", "declarations", "layout", "provenance", "types"]
    ),
    "time64_abi": frozenset(["binary", "declarations", "types"]),
    "tls_checks": frozenset(["binary"]),
    "type_deprecated": frozenset(["binary", "declarations", "provenance", "types"]),
    "type_kind_changes": frozenset(["binary", "declarations", "provenance", "types"]),
    "typedefs": frozenset(
        ["binary", "debug", "declarations", "provenance", "semantic_ir", "types"]
    ),
    "types": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "graph",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "undeclared_exports": frozenset(["binary", "declarations", "provenance", "types"]),
    "unions": frozenset(["binary", "debug", "declarations", "provenance", "types"]),
    "unnamed_types": frozenset(["binary"]),
    "var_access": frozenset(
        ["binary", "debug", "declarations", "layout", "provenance", "semantic_ir"]
    ),
    "var_deprecated": frozenset(
        ["binary", "debug", "declarations", "layout", "provenance", "semantic_ir"]
    ),
    "var_values": frozenset(["binary", "declarations", "layout", "provenance"]),
    "variables": frozenset(
        [
            "binary",
            "debug",
            "declarations",
            "graph",
            "layout",
            "provenance",
            "semantic_ir",
            "types",
        ]
    ),
    "visibility_leak": frozenset(["binary", "declarations", "provenance"]),
    "vtable_identity": frozenset(["binary", "provenance"]),
    "vtable_layout": frozenset(
        ["binary", "debug", "declarations", "provenance", "types"]
    ),
}

#: Non-detector rules that read stored sections, keyed like the detectors.
SURFACE_METRICS_RULE = "surface_metrics"
RULE_SECTIONS: Mapping[str, frozenset[str]] = {
    # `diff_surface_metrics`: public declaration counts per side.
    SURFACE_METRICS_RULE: frozenset({"debug", "declarations", "provenance", "types"}),
}


def unreadable_sections_read(
    name: str, *snapshots: object
) -> tuple[tuple[str, str], ...]:
    """``(section, diagnostic)`` for each section detector *name* reads that
    one of *snapshots* could not use. An unregistered name reads every
    section (fail closed)."""
    reads = DETECTOR_SECTIONS.get(name, RULE_SECTIONS.get(name))
    found: dict[str, str] = {}
    for snap in snapshots:
        for section, diagnostic in getattr(snap, "unreadable_sections", ()):
            if reads is None or section in reads:
                found.setdefault(section, diagnostic)
    return tuple(found.items())


def not_comparable_reason(blocked: tuple[tuple[str, str], ...]) -> str:
    """The one spelling of a section-blocked detector's ``coverage_gap``."""
    return "not comparable: " + "; ".join(diagnostic for _s, diagnostic in blocked)
