# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The unobserved-dimension registry is the one owner of assurance gaps.

Structural: every module under ``policy/analysis_assurance/`` is either a
registered provider or explicitly classified here as a non-dimension axis, so
a new gap module cannot exist unwired (the ``scope_fallback`` incident read
``complete`` for a dump that discarded its headers).

Property: for every registered provider, any snapshot pair that triggers it
-- on either side, in combination with any other provider -- never yields
status ``complete``, and its notes land verbatim and in registry order.
"""

from __future__ import annotations

import dataclasses
import pkgutil
from collections.abc import Callable
from itertools import combinations

import pytest

import abicheck.policy.analysis_assurance as aa_pkg
from abicheck.checker import compare
from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot, Function
from abicheck.model.elf_facts import ElfMetadata
from abicheck.model.export_read import ExportReadState
from abicheck.policy.analysis_assurance import compute_analysis_assurance
from abicheck.policy.analysis_assurance._unobserved import (
    UNOBSERVED_DIMENSIONS,
    collect_unobserved,
)
from abicheck.workflows.analysis_assurance_attach import attach_analysis_assurance

# Modules that are assurance axes with their own status vocabulary (not
# unobserved-dimension gaps), plumbing, or the registry itself. A new module
# must be added either to the registry or, with a reason, here.
NON_DIMENSION_MODULES = {
    "_comparability",  # comparability axis: not_comparable / bounded
    "_compute",  # the rollup that iterates the registry
    "_context",  # header/DWARF/L3 context statuses (own vocabulary)
    "_export_accounting",  # pack accounting counters
    "_fact_set",  # fact-set comparability axis
    "_graph_completeness",  # graph completeness axis
    "_layout",  # layout_unverified_detectors: report field, never lowers status
    "_manifest_layers",  # pack-manifest layer statuses
    "_outputs",  # report projections
    "_result",  # the AnalysisAssurance dataclass
    "_status_ladder",  # the overall-status rollup over the sub-statuses
    "_target_accounting",  # target accounting axis
    "_translation_units",  # TU accounting axis
    "_unobserved",  # the registry
    "_unobserved_finding",  # the registry's leaf record type
}


def test_every_assurance_module_is_registered_or_classified() -> None:
    modules = {m.name for m in pkgutil.iter_modules(aa_pkg.__path__)}
    registered = {p.module.rsplit(".", 1)[1] for p in UNOBSERVED_DIMENSIONS}
    unclassified = modules - registered - NON_DIMENSION_MODULES
    assert not unclassified, (
        f"{sorted(unclassified)}: register it in _unobserved.UNOBSERVED_DIMENSIONS "
        "or classify it in NON_DIMENSION_MODULES with a reason"
    )
    assert not registered & NON_DIMENSION_MODULES
    assert registered <= modules
    assert not (NON_DIMENSION_MODULES - modules), "stale classification"


def test_dimension_ids_are_unique() -> None:
    ids = [p.dimension for p in UNOBSERVED_DIMENSIONS]
    assert len(ids) == len(set(ids))


def _base(version: str) -> AbiSnapshot:
    return AbiSnapshot(library="l", version=version)


# One trigger per registered dimension module: mutate a plain snapshot so the
# provider reports a status-lowering finding for that side.
TRIGGERS: dict[str, Callable[[AbiSnapshot], AbiSnapshot]] = {
    "_extraction_limitations": lambda s: dataclasses.replace(
        s, dump_provenance={"extraction_limitations": ["macro contracts not collected"]}
    ),
    "_template_defaults": lambda s: dataclasses.replace(
        s, ast_producer="castxml", template_default_header_scan=True
    ),
    "_concepts": lambda s: dataclasses.replace(s, cxx_concept_header_scan=True),
    "_scope_fallback": lambda s: dataclasses.replace(s, scope_fallback="no-match"),
    "_depth_projection": lambda s: dataclasses.replace(
        s, projection_evidence_gaps=("debug-info subprograms",)
    ),
    "_unreadable_sections": lambda s: dataclasses.replace(
        s, unreadable_sections=(("graph", "failed at extraction"),)
    ),
    "_export_read": lambda s: dataclasses.replace(
        s, elf=ElfMetadata(export_read=ExportReadState.PARTIAL)
    ),
    "_noexcept": lambda s: dataclasses.replace(
        s, functions=[Function("f", "_Z1fv", "void", is_noexcept=None)]
    ),
}


def test_every_registered_provider_has_a_trigger() -> None:
    registered = {p.module.rsplit(".", 1)[1] for p in UNOBSERVED_DIMENSIONS}
    assert registered == set(TRIGGERS)


def _apply(names: tuple[str, ...], snap: AbiSnapshot) -> AbiSnapshot:
    for name in names:
        snap = TRIGGERS[name](snap)
    return snap


_CASES = [
    (names, side)
    for k in (1, 2)
    for names in combinations(sorted(TRIGGERS), k)
    for side in ("old", "new", "both")
]


@pytest.mark.parametrize(("names", "side"), _CASES)
def test_a_triggered_dimension_never_reads_complete(
    names: tuple[str, ...], side: str
) -> None:
    old, new = _base("1"), _base("2")
    if side in ("old", "both"):
        old = _apply(names, old)
    if side in ("new", "both"):
        new = _apply(names, new)

    findings = collect_unobserved(old, new)
    triggered = {
        p.module.rsplit(".", 1)[1]
        for p in UNOBSERVED_DIMENSIONS
        for f in findings
        if f.dimension == p.dimension and f.lowers_status
    }
    assert triggered == set(names)
    expected_sides = {"old", "new"} if side == "both" else {side}
    found_sides = {f.side for f in findings if f.lowers_status}
    # Depth-projection items are de-duplicated across sides (attributed to the
    # first side that lost them), so "both" may name only one side there.
    assert found_sides and found_sides <= expected_sides
    if names != ("_depth_projection",):
        assert found_sides == expected_sides

    for status in (
        compute_analysis_assurance(
            DiffResult(old_version="1", new_version="2", library="l"), old, new
        ).status,
        compare(old, new).analysis_assurance.status,
    ):
        assert status != "complete"
        assert status in ("partial", "failed", "not_comparable")

    notes = compare(old, new).analysis_assurance.notes
    reasons = [f.reason for f in findings]
    # Verbatim, contiguous and in registry order.
    start = notes.index(reasons[0])
    assert list(notes[start : start + len(reasons)]) == reasons


def test_coverage_warnings_come_from_coverage_providers_only() -> None:
    old = _apply(tuple(TRIGGERS), _base("1"))
    new = _base("2")
    result = compare(old, new)
    attach_analysis_assurance(result, old, new)
    attach_analysis_assurance(result, old, new)  # idempotent
    coverage = {p.dimension for p in UNOBSERVED_DIMENSIONS if p.coverage_warning}
    expected = [
        f.reason for f in collect_unobserved(old, new) if f.dimension in coverage
    ]
    assert [w for w in result.coverage_warnings if w in expected] == expected
    others = [
        f.reason for f in collect_unobserved(old, new) if f.dimension not in coverage
    ]
    assert not set(others) & set(result.coverage_warnings)
