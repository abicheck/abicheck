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

"""Projection/detector input contract (bug class
``evidence.depth_projection_drops_lower_layer_detector_input``, H2a).

``--depth headers`` cleared ``surface_graph`` and silently starved
``TemplateInstanceAbsence``. The general invariant: for every depth rung and
every registered detector, each depth-projectable fact the detector reads is
either kept by that rung, explicitly tolerated (with a reason), or required
-- and a required fact the rung cleared makes the detector report
``"<fact> not available at depth <d>"`` instead of running on the
emptied input.

Three independent oracles keep the two declarations honest:

* the projection's *actual* behaviour on a snapshot carrying every
  projectable fact (``DEPTH_CLEARABLE_FACTS`` cannot drift from the code);
* an AST scan of what each detector's code really reads
  (``tests/_snapshot_read_scan.py``), so ``DETECTOR_INPUTS`` cannot rot;
* the real registry run over projected snapshots (the coverage block).
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.compare.detector_inputs import (
    DETECTOR_INPUTS,
    RULE_INPUTS,
    unavailable_required_input,
)
from abicheck.detector_registry import registry
from abicheck.evidence_depth import DEPTH_RANK
from abicheck.model import AbiSnapshot, EnumMember, EnumType, RecordType
from abicheck.model.build_mode_facts import BuildMode
from abicheck.model.extraction_contract import ExtractionContract
from abicheck.model.identity import entity_id_for_constant, entity_id_for_typedef
from abicheck.model.python_facts import PythonApiSurface
from abicheck.model.semantic_ir import SemanticIR
from abicheck.policy.depth_fact_availability import (
    DEPTH_CLEARABLE_FACTS,
    PROJECTABLE_FACTS,
    SUBSTITUTES,
    fact_present,
)
from abicheck.policy.depth_projection import project_pair_to_depth
from tests._snapshot_read_scan import snapshot_reads

registry.ensure_loaded()
_DETECTORS = {e.name: e for e in registry._detectors}
_DEPTHS = tuple(sorted(DEPTH_RANK, key=DEPTH_RANK.__getitem__))


def _full_snapshot(version: str, *, pack: bool) -> AbiSnapshot:
    """A header-derived (not DWARF-sourced) snapshot carrying every
    depth-projectable fact."""
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="type://T", kind="decl"))
    snap = AbiSnapshot(
        library="lib",
        version=version,
        types=[RecordType(name="S", kind="struct")],
        enums=[EnumType(name="E", members=[EnumMember(name="A", value=0)])],
        typedefs={"t": "int"},
        constants={"C": "1"},
        from_headers=True,
        surface_graph=graph,
        python_api=PythonApiSurface(module_name="m"),
        contract=ExtractionContract(profile_fingerprint="p", scope_fingerprint="s"),
        build_mode=BuildMode(),
        build_source=BuildSourcePack(root=Path("pack")) if pack else None,
    )
    snap.semantic_ir = SemanticIR()
    decls = snap.declarations
    decls.typedefs_qualified = {"t": "int"}
    decls.typedef_entity_ids = {"t": entity_id_for_typedef((), "t")}
    decls.constant_entity_ids = {"C": entity_id_for_constant((), "C")}
    return snap


def test_fixture_carries_every_projectable_fact() -> None:
    snap = _full_snapshot("1", pack=True)
    assert {f for f in PROJECTABLE_FACTS if not fact_present(snap, f)} == set()


@pytest.mark.parametrize("depth", _DEPTHS)
def test_depth_table_matches_the_projection(depth: str) -> None:
    """``DEPTH_CLEARABLE_FACTS[depth]`` is exactly what the projection clears
    (with and without a build pack -- ``surface_graph`` at ``build`` is
    cleared only pack-less), and is what it records on the snapshot."""
    cleared: set[str] = set()
    for pack in (True, False):
        before = _full_snapshot("1", pack=pack)
        old, _ = project_pair_to_depth(before, _full_snapshot("2", pack=pack), depth)
        actual = {
            f
            for f in PROJECTABLE_FACTS
            if fact_present(before, f) and not fact_present(old, f)
        }
        recorded = old.projection_unavailable
        assert (recorded[1] if recorded else frozenset()) == actual
        if recorded:
            assert recorded[0] == depth
        cleared |= actual
    assert cleared == DEPTH_CLEARABLE_FACTS[depth]


@pytest.mark.parametrize(("depth", "fact"), sorted(SUBSTITUTES))
def test_substitute_is_kept_where_its_fact_is_cleared(depth: str, fact: str) -> None:
    old, _ = project_pair_to_depth(
        _full_snapshot("1", pack=False), _full_snapshot("2", pack=False), depth
    )
    assert not fact_present(old, fact)
    assert getattr(old, SUBSTITUTES[(depth, fact)]) == frozenset({"type://T"})


def test_every_registered_detector_declares_its_inputs() -> None:
    assert set(DETECTOR_INPUTS) == set(_DETECTORS)


@pytest.mark.parametrize(
    "inputs",
    list(DETECTOR_INPUTS.values()) + list(RULE_INPUTS.values()),
)
def test_declarations_are_well_formed(inputs) -> None:
    assert inputs.reads <= PROJECTABLE_FACTS
    assert not inputs.requires & set(inputs.tolerates)
    assert all(len(reason) > 40 for reason in inputs.tolerates.values())


@pytest.mark.parametrize(
    ("depth", "name"), list(itertools.product(_DEPTHS, sorted(DETECTOR_INPUTS)))
)
def test_each_cleared_input_is_required_tolerated_or_substituted(
    depth: str, name: str
) -> None:
    """The pure table: no detector reads a fact its rung clears without a
    stated disposition."""
    inputs = DETECTOR_INPUTS[name]
    for fact in inputs.reads & DEPTH_CLEARABLE_FACTS[depth]:
        assert (
            fact in inputs.requires
            or fact in inputs.tolerates
            or (depth, fact) in SUBSTITUTES
        ), (depth, name, fact)


@pytest.mark.parametrize("name", sorted(DETECTOR_INPUTS))
def test_declared_reads_match_the_code(name: str) -> None:
    """AST oracle: the declared projectable reads are exactly what the
    detector's reachable code reads."""
    actual = snapshot_reads(_DETECTORS[name].fn) & PROJECTABLE_FACTS
    assert DETECTOR_INPUTS[name].reads == actual


@pytest.mark.parametrize("key", sorted(RULE_INPUTS))
def test_declared_rule_reads_match_the_code(key: str) -> None:
    import importlib

    module, qualname = key.split(":")
    rule = getattr(importlib.import_module(module), qualname)
    assert RULE_INPUTS[key].reads == snapshot_reads(rule) & PROJECTABLE_FACTS


@pytest.mark.parametrize("depth", _DEPTHS)
def test_registry_disables_a_detector_whose_required_input_was_cleared(
    depth: str,
) -> None:
    """The real registry over a projected pair: every detector requiring a
    cleared fact is ``not_evaluated`` with the depth reason; no other
    detector is disabled for a depth reason."""
    old, new = project_pair_to_depth(
        _full_snapshot("1", pack=False), _full_snapshot("2", pack=False), depth
    )
    _, results = registry.run_all(old, new)
    gone = old.projection_unavailable[1] if old.projection_unavailable else set()
    for result in results:
        requires = DETECTOR_INPUTS[result.name].requires
        missing = sorted(requires & gone)
        if missing:
            assert result.not_evaluated and not result.enabled, result
            assert result.excluded_by_depth == depth
            assert result.coverage_gap == (
                f"{', '.join(missing)} not available at depth {depth}"
            )
        else:
            assert result.excluded_by_depth is None
    if depth == "binary":
        assert any(r.coverage_gap and "python_api" in r.coverage_gap for r in results)


def test_unprojected_snapshot_disables_nothing() -> None:
    snap = _full_snapshot("1", pack=True)
    assert all(
        unavailable_required_input(name, snap, snap) is None for name in DETECTOR_INPUTS
    )
