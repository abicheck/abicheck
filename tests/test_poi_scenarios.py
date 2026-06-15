# SPDX-License-Identifier: Apache-2.0
"""G20 Phase 3 — evidence-directed focusing scenarios (ADR-035 D7).

The interesting artifact here is the **scan plan**, not the verdict: cheap
L0/L1/L2 deltas steer the expensive L4/L5 scan. These assert directly on the
``build_points_of_interest`` work-list (a pure function — no engine plumbing
needed). The two highest-value guards are the **changed-path floor** (a
mis-weighted risk profile can never drop a directly-changed TU) and
export-delta targeting (a changed export points the scan at the source decl that
emits it).

Pure-Python synthetic ``AbiSnapshot``s; default lane.
"""

from __future__ import annotations

from abicheck.buildsource.poi import (
    POIKind,
    POIReason,
    build_points_of_interest,
)
from abicheck.buildsource.risk import RiskRules, score_changed_paths
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, ScopeOrigin


def _snap(*exports: str, functions=None, from_headers: bool = False) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=from_headers,
        elf=ElfMetadata(symbols=[ElfSymbol(name=e, is_default=True) for e in exports]),
    )
    if functions is not None:
        snap.functions = functions
    return snap


# --------------------------------------------------------------------------- #
# export-delta targeting: a new export seeds a SYMBOL POI for replay
# --------------------------------------------------------------------------- #
def test_export_delta_targets_the_changed_symbol_not_the_unrelated_body():
    baseline = _snap("_Z3oldv")
    candidate = _snap("_Z3oldv", "_Z6addedv")
    poi = build_points_of_interest(baseline=baseline, candidate=candidate)
    # The added export becomes a SYMBOL POI; the unchanged one does not.
    assert "_Z6addedv" in poi.symbols()
    assert "_Z3oldv" not in poi.symbols()
    reasons = {p.reason for p in poi.points if p.key == "_Z6addedv"}
    assert POIReason.EXPORT_ADDED in reasons


def test_exported_without_public_decl_is_seeded_for_the_scan():
    # A new export with no public declaration is exactly where the source scan
    # should look — the same signal exported_not_public reports after the fact.
    baseline = _snap("_Z3oldv")
    candidate = _snap(
        "_Z3oldv",
        "_Z6secretv",
        from_headers=True,
        functions=[
            Function(
                name="old",
                mangled="_Z3oldv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    poi = build_points_of_interest(baseline=baseline, candidate=candidate)
    reasons = {p.reason for p in poi.points if p.key == "_Z6secretv"}
    assert reasons & {POIReason.EXPORT_ADDED, POIReason.EXPORTED_NO_DECL}


# --------------------------------------------------------------------------- #
# template-instantiation seed
# --------------------------------------------------------------------------- #
def test_exported_template_instantiation_seeds_replay_targets():
    baseline = _snap()
    # Itanium template instantiation (carries I…E) → TEMPLATE_EXPORT seed.
    candidate = _snap("_Z3useISt6vectorIiEEvv")
    poi = build_points_of_interest(baseline=baseline, candidate=candidate)
    reasons = {p.reason for p in poi.points if p.key == "_Z3useISt6vectorIiEEvv"}
    assert POIReason.TEMPLATE_EXPORT in reasons


# --------------------------------------------------------------------------- #
# the D7 changed-path floor — focusing can never hide a real change
# --------------------------------------------------------------------------- #
def test_changed_path_floor_holds_under_misweighted_risk_profile():
    # A deliberately mis-weighted profile that scores src/** at zero must NOT be
    # able to drop the directly-changed TU: the floor adds it unconditionally.
    rules = RiskRules.from_dict({"src/**": 0})
    risk = score_changed_paths(["src/widget.cpp"], rules)
    poi = build_points_of_interest(
        changed_paths=["src/widget.cpp"],
        risk=risk,
        baseline=None,
        candidate=None,
    )
    assert "src/widget.cpp" in poi.changed_paths()
    floor = [
        p
        for p in poi.points
        if p.key == "src/widget.cpp" and p.reason is POIReason.CHANGED_PATH
    ]
    assert floor, "changed-path floor entry missing — focusing dropped a real change"
    assert floor[0].kind is POIKind.PATH


def test_changed_path_floor_survives_when_risk_only_adds():
    # Risk score only ever ADDS a marker POI; it never removes the floor.
    rules = RiskRules.from_dict({"include/**": 100})
    risk = score_changed_paths(["include/api.h"], rules)
    poi = build_points_of_interest(changed_paths=["include/api.h"], risk=risk)
    assert "include/api.h" in poi.changed_paths()
    # The risk marker is additive (an ENTITY POI), not a replacement.
    assert any(p.reason is POIReason.RISK_ESCALATION for p in poi.points)


def test_empty_inputs_yield_empty_worklist():
    poi = build_points_of_interest()
    assert not poi
    assert poi.changed_paths() == []
    assert poi.symbols() == []
