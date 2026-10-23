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

"""Which snapshot facts each ``--depth`` projection may clear.

The other half of ``compare/detector_inputs.py``: that module declares the
facts each detector reads; this one declares, per depth rung, the facts
:func:`~abicheck.policy.depth_projection.project_snapshot_to_depth` may clear
(set to ``None``/empty/``False``). A fact absent from a rung's set is kept
there. Bug class ``evidence.depth_projection_drops_lower_layer_detector_input``
(H2a): ``--depth headers`` cleared ``surface_graph`` and silently starved
``TemplateInstanceAbsence`` of its L2 input. Stated as a table, the contract
test (``tests/test_depth_detector_input_contract.py``) can check every
detector against every rung, and an emptied required input disables the
detector with a coverage reason instead of running it.

Fact names are ``AbiSnapshot`` attribute names, or ``declarations.<field>``
for a field of the declaration store. *Degraded but kept* facts (at
``binary``, ``declarations.functions``/``.variables`` lose their signatures
and the snapshot is flagged ``elf_only_mode``) are not listed: the
``elf_only_mode`` flag is the existing, explicit signal for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

#: Facts the ``headers``/``build`` rungs may clear: the build pack and build
#: mode below ``build``, and ``surface_graph`` -- the pack-less L5 fallback
#: (ADR-063 Phase 10) -- below ``source``. Its L2 declaration-node role
#: survives as ``header_declaration_ids`` (see :data:`SUBSTITUTES`).
_BELOW_SOURCE = frozenset({"surface_graph"})
_BELOW_BUILD = _BELOW_SOURCE | {"build_source", "build_mode"}

#: Everything L2+ ``_strip_header_and_above_evidence`` blanks at ``binary``.
#: The ``declarations.types``..``typedef_entity_ids`` group is cleared only
#: for a pair that is not jointly DWARF-sourced; listed as "may clear".
_BELOW_HEADERS = _BELOW_BUILD | {
    "canonical_ir",
    "python_api",
    "contract",
    "from_headers",
    "declarations.types",
    "declarations.enums",
    "declarations.typedefs",
    "declarations.typedefs_qualified",
    "declarations.typedef_entity_ids",
    "declarations.constants",
    "declarations.constant_entity_ids",
}

#: Depth rung -> the facts its projection may clear.
DEPTH_CLEARABLE_FACTS: dict[str, frozenset[str]] = {
    "binary": frozenset(_BELOW_HEADERS),
    "headers": frozenset(_BELOW_BUILD),
    "build": _BELOW_SOURCE,
    "source": frozenset(),
}

#: Every fact some rung may clear -- the facts ``compare/detector_inputs.py``
#: must account for.
PROJECTABLE_FACTS: frozenset[str] = frozenset().union(*DEPTH_CLEARABLE_FACTS.values())

#: ``{(depth, cleared fact): substitute fact}`` -- a role of a cleared fact
#: the projection keeps under another name at that rung.
SUBSTITUTES: dict[tuple[str, str], str] = {
    ("headers", "surface_graph"): "header_declaration_ids",
    ("build", "surface_graph"): "header_declaration_ids",
}


def fact_present(snap: AbiSnapshot, fact: str) -> bool:
    """Whether *snap* carries a non-empty value for *fact*.

    Never decodes a lazily-stored graph: a pending ``surface_graph`` counts
    as present, read from the instance dict, not through the descriptor.
    """
    if fact.startswith("declarations."):
        value: object = getattr(snap.declarations, fact.split(".", 1)[1], None)
    elif fact == "canonical_ir":
        value = snap.canonical_ir
    else:
        value = vars(snap).get(fact)
    return (
        bool(value)
        if isinstance(value, (bool, dict, list, set, frozenset, tuple, str))
        else value is not None
    )


def cleared_facts(
    before: AbiSnapshot, after: AbiSnapshot, depth: str
) -> frozenset[str]:
    """The facts of *depth*'s clearable set *before* carried and *after*
    (its projection) no longer does."""
    return frozenset(
        fact
        for fact in DEPTH_CLEARABLE_FACTS.get(depth, frozenset())
        if fact_present(before, fact) and not fact_present(after, fact)
    )
