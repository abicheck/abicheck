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

"""The depth-projectable snapshot facts each detector and rule reads.

Bug class ``evidence.depth_projection_drops_lower_layer_detector_input``
(H2a): ``--depth headers`` cleared ``surface_graph``, the input of
``TemplateInstanceAbsence`` (consulted by the ``functions``/``variables``/
``types`` detectors), so the rule silently stopped applying and the removal
path reported false ``*_removed`` findings. Nothing stated which facts a
detector reads, so nothing could notice that a projection emptied them.

Each entry below declares, for one registered detector (keyed by its
registry name) or one non-detector rule, every *depth-projectable* fact it
reads -- a fact some rung of ``policy/depth_fact_availability.
DEPTH_CLEARABLE_FACTS`` may clear -- split into:

* ``requires`` -- the fact is the detector's subject. When a projection
  cleared it (``AbiSnapshot.projection_unavailable``), the registry records
  the detector ``not_evaluated`` with ``"disabled: <fact> not available at
  depth <d>"`` instead of running it on emptied input.
* ``tolerates`` -- ``{fact: why its absence is safe}``: the detector reads it
  as optional enrichment and degrades without inventing a finding.

Facts no rung clears (``elf``, ``platform``, ``declarations.functions``, ...)
are deliberately not listed. ``tests/test_depth_detector_input_contract.py``
checks the table both ways: against every rung's clearable set, and against
an AST scan of the attribute reads each detector's code actually makes, so a
declaration cannot rot silently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectorInputs:
    """The depth-projectable facts one detector or rule reads."""

    requires: frozenset[str] = frozenset()
    tolerates: Mapping[str, str] = field(default_factory=dict)

    @property
    def reads(self) -> frozenset[str]:
        return self.requires | frozenset(self.tolerates)


_NONE = DetectorInputs()

_CANONICAL = (
    "optional canonical occurrences: None falls back to the declaration "
    "store, exactly as for any snapshot no normalizer ran on"
)
_FROM_HEADERS = (
    "a provenance flag the binary projection sets to the projected view's "
    "own truth (no header evidence), not an emptied input"
)
_LOOKUP = (
    "auxiliary type/typedef lookups enriching symbol checks; where binary "
    "clears them it also erases the signatures they enrich (elf_only_mode), "
    "so nothing is compared against the emptied tables"
)
_GRAPH = (
    "TemplateInstanceAbsence: the L2 declaration-node role survives below "
    "source as header_declaration_ids (H2a); the L5 instantiation origins "
    "(_graph_origins) are legitimately absent below source, and the rule "
    "then falls back to the castxml class-head reading"
)
_BUILD_MODE = (
    "re-derived from the always-present mangled symbols when the recorded "
    "L3 build mode is absent (_effective_build_mode)"
)
_DWARF_SCOPE = (
    "only scopes the raw DWARF layout pool; the binary projection pre-scopes "
    "that pool to the joint public scope before clearing these "
    "(_public_dwarf_scope), so the fallback cannot widen it"
)

#: Registry name -> the depth-projectable facts that detector reads. Every
#: registered detector has an entry, ``_NONE`` when it reads none.
DETECTOR_INPUTS: dict[str, DetectorInputs] = {
    "abi_tag": _NONE,
    "atomic_qualifier": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "bit_int_width": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "char8t_migration": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "integer_model": _NONE,
    "elf_layout": _NONE,
    "fingerprint_renames": _NONE,
    "functions": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
            "surface_graph": _GRAPH,
        },
    ),
    "ctor_overload_ambiguity": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
        },
    ),
    "variables": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "surface_graph": _GRAPH,
        },
    ),
    "param_defaults": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "param_renames": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "pointer_levels": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
        },
    ),
    "access_levels": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
        },
    ),
    "anon_fields": DetectorInputs(
        requires=frozenset(["declarations.types"]),
    ),
    "symbol_renames": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "param_restrict": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "param_pointee_qualifier": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "func_return_pointee_qualifier": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "func_deprecated": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
        },
    ),
    "func_override_specifier": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "var_deprecated": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
        },
    ),
    "param_va_list": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "constants": DetectorInputs(
        requires=frozenset(["declarations.constants"]),
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.constant_entity_ids": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "var_access": DetectorInputs(
        tolerates={
            "canonical_ir": _CANONICAL,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "kabi": _NONE,
    "layout_descriptor": DetectorInputs(
        requires=frozenset(["declarations.types"]),
    ),
    "dwarf_layout_coherence": _NONE,
    "long_double": _NONE,
    "template_inner_types": DetectorInputs(
        requires=frozenset(["declarations.types"]),
    ),
    "visibility_leak": _NONE,
    "elf": DetectorInputs(
        tolerates={
            "from_headers": _FROM_HEADERS,
        },
    ),
    "pe": _NONE,
    "macho": _NONE,
    "tls_checks": _NONE,
    "protected_visibility": _NONE,
    "symbol_version_alias": _NONE,
    "glibcxx_dual_abi": _NONE,
    "inline_namespace": _NONE,
    "vtable_identity": _NONE,
    "abi_surface": _NONE,
    "dwarf": DetectorInputs(
        tolerates={
            "declarations.enums": _DWARF_SCOPE,
            "declarations.typedefs": _DWARF_SCOPE,
            "declarations.typedefs_qualified": _DWARF_SCOPE,
            "declarations.types": _DWARF_SCOPE,
        },
    ),
    "elf_deleted_fallback": _NONE,
    "python_ext": _NONE,
    "python_api": DetectorInputs(
        requires=frozenset(["python_api"]),
    ),
    "stdlib_impl": DetectorInputs(
        tolerates={
            "build_mode": _BUILD_MODE,
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
        },
    ),
    "sycl": _NONE,
    "time64_abi": DetectorInputs(
        tolerates={
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
        },
    ),
    "var_values": _NONE,
    "type_kind_changes": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "reserved_fields": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "const_overloads": _NONE,
    "enum_renames": DetectorInputs(
        requires=frozenset(["declarations.enums"]),
    ),
    "field_qualifiers": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "field_default_initializer": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "field_deprecated": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "type_deprecated": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "enum_deprecated": DetectorInputs(
        requires=frozenset(["declarations.enums"]),
    ),
    "field_renames": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "types": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
            "from_headers": _FROM_HEADERS,
            "surface_graph": _GRAPH,
        },
    ),
    "overload_additions": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "enums": DetectorInputs(
        requires=frozenset(["declarations.enums"]),
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
        },
    ),
    "method_qualifiers": DetectorInputs(
        requires=frozenset(["declarations.types"]),
    ),
    "unions": DetectorInputs(
        requires=frozenset(["declarations.types"]),
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
        },
    ),
    "typedefs": DetectorInputs(
        requires=frozenset(["declarations.typedefs"]),
        tolerates={
            "canonical_ir": _CANONICAL,
            "declarations.enums": _LOOKUP,
            "declarations.typedef_entity_ids": _LOOKUP,
            "declarations.typedefs_qualified": _LOOKUP,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    "unnamed_types": _NONE,
    "vtable_layout": DetectorInputs(
        requires=frozenset(["declarations.types"]),
    ),
    "advanced_dwarf": _NONE,
    "undeclared_exports": DetectorInputs(
        tolerates={
            "declarations.types": _LOOKUP,
        },
    ),
    "overload_ambiguity": DetectorInputs(
        tolerates={
            "declarations.enums": _LOOKUP,
            "declarations.typedefs": _LOOKUP,
            "declarations.types": _LOOKUP,
            "from_headers": _FROM_HEADERS,
        },
    ),
    # The ISA target is read from the producer string only: a rung that clears
    # build_mode leaves no target to compare, so the detector must report that
    # as disabled rather than read the absence as "same target".
    "target_isa": DetectorInputs(requires=frozenset({"build_mode"})),
}

#: Non-detector rules consulted during compare, keyed ``module:qualname``.
#: Their reads are also folded into each consulting detector's entry above
#: (the AST scan follows the call); listed here so the rule's own contract is
#: stated where a new consumer would look for it.
RULE_INPUTS: dict[str, DetectorInputs] = {
    "abicheck.compare.template_instance_absence:TemplateInstanceAbsence": (
        DetectorInputs(
            tolerates={
                "surface_graph": _GRAPH,
                "declarations.types": (
                    "specialization enumeration only; at binary the type "
                    "removals it would excuse are not reported either"
                ),
            }
        )
    ),
}


def unavailable_required_input(name: str, *snapshots: object) -> str | None:
    """``"disabled: <fact> not available at depth <d>"`` when a depth
    projection cleared a fact detector *name* requires on any of
    *snapshots*, else ``None``."""
    inputs = DETECTOR_INPUTS.get(name)
    if inputs is None or not inputs.requires:
        return None
    for snap in snapshots:
        record = getattr(snap, "projection_unavailable", None)
        if record is None:
            continue
        depth, gone = record
        missing = sorted(inputs.requires & gone)
        if missing:
            return f"disabled: {', '.join(missing)} not available at depth {depth}"
    return None
