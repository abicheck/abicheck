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

"""Tests for the ADR-035 D7 points-of-interest work-list (G19.5, Phase 3b).

Pure tests over in-memory snapshots and string sets — the cheap evidence the POI
builder consumes to focus the expensive scan. Default lane.
"""

from __future__ import annotations

from abicheck.buildsource.pattern_scan import (
    EscalationTrigger,
    PatternCategory,
    PatternKind,
)
from abicheck.buildsource.poi import (
    POIKind,
    POIReason,
    build_points_of_interest,
)
from abicheck.buildsource.risk import score_changed_paths
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)


def _snap(
    *sym_names: str, decls: list[Function] | None = None, from_headers: bool = False
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=from_headers,
        functions=list(decls or []),
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in sym_names]),
    )


def _pub_func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def test_floor_includes_every_changed_path_unconditionally() -> None:
    poi = build_points_of_interest(changed_paths=["src/a.cpp", "include/b.h"])
    assert set(poi.changed_paths()) == {"src/a.cpp", "include/b.h"}
    assert all(p.reason is POIReason.CHANGED_PATH for p in poi.points)


def test_risk_score_only_adds_never_removes_floor() -> None:
    # A docs-only (negative) risk score must not drop a real changed TU (floor).
    risk = score_changed_paths(["docs/x.md"])
    assert risk.total < 0
    poi = build_points_of_interest(changed_paths=["src/a.cpp"], risk=risk)
    assert "src/a.cpp" in poi.changed_paths()
    # Negative score adds no risk-escalation marker.
    assert POIReason.RISK_ESCALATION.value not in poi.counts_by_reason()


def test_positive_risk_adds_escalation_marker_entity() -> None:
    risk = score_changed_paths(["include/foo.h"])
    assert risk.total > 0
    poi = build_points_of_interest(changed_paths=["include/foo.h"], risk=risk)
    markers = [p for p in poi.points if p.reason is POIReason.RISK_ESCALATION]
    assert len(markers) == 1
    assert markers[0].kind is POIKind.ENTITY


def test_pattern_triggers_contribute_focus_paths() -> None:
    trig = EscalationTrigger(
        kind=PatternKind.PRAGMA_PACK,
        category=PatternCategory.LAYOUT,
        recommended_method="s5",
        count=3,
        sample_location="include/packed.h:7",
        reason="pragma pack",
    )
    poi = build_points_of_interest(changed_paths=[], pattern_triggers=[trig])
    assert "include/packed.h" in poi.changed_paths()
    assert poi.counts_by_reason()[POIReason.PATTERN_TRIGGER.value] == 1


def test_pattern_trigger_without_path_is_dropped() -> None:
    # An in-memory scan yields a bare line number ("7"), no path → no POI.
    trig = EscalationTrigger(
        kind=PatternKind.PRAGMA_PACK,
        category=PatternCategory.LAYOUT,
        recommended_method="s5",
        count=1,
        sample_location="7",
        reason="pragma pack",
    )
    poi = build_points_of_interest(pattern_triggers=[trig])
    assert poi.changed_paths() == []


def test_export_delta_flags_added_and_removed_symbols() -> None:
    old = _snap("_Z3foov", "_Z3barv")
    new = _snap("_Z3foov", "_Z3bazv")
    poi = build_points_of_interest(baseline=old, candidate=new)
    syms = poi.symbols()
    assert "_Z3bazv" in syms  # added
    assert "_Z3barv" in syms  # removed
    reasons = poi.counts_by_reason()
    assert reasons.get(POIReason.EXPORT_ADDED.value) == 1
    assert reasons.get(POIReason.EXPORT_REMOVED.value) == 1


def test_exported_no_decl_flagged_when_provenance_present() -> None:
    # New exports _Z3foov (declared) and _Z6secretv (no public decl).
    new = _snap(
        "_Z3foov",
        "_Z6secretv",
        decls=[_pub_func("foo", "_Z3foov")],
        from_headers=True,
    )
    old = _snap("_Z3foov", "_Z6secretv", from_headers=True)
    poi = build_points_of_interest(baseline=old, candidate=new)
    # No added/removed exports (same set), so _Z6secretv is flagged as no-decl.
    assert POIReason.EXPORTED_NO_DECL.value in poi.counts_by_reason()
    assert "_Z6secretv" in poi.symbols()


def test_template_export_seed_on_added_instantiation() -> None:
    old = _snap("_Z3foov")
    new = _snap("_Z3foov", "_Z3barIiEvv")  # added template instantiation
    poi = build_points_of_interest(baseline=old, candidate=new)
    reasons = poi.counts_by_reason()
    assert reasons.get(POIReason.TEMPLATE_EXPORT.value) == 1


def test_deterministic_for_fixed_inputs() -> None:
    args = dict(
        changed_paths=["src/a.cpp", "src/b.cpp"],
        risk=score_changed_paths(["src/a.cpp"]),
    )
    a = build_points_of_interest(**args).to_dict()
    b = build_points_of_interest(**args).to_dict()
    assert a == b


def test_empty_inputs_yield_empty_worklist() -> None:
    poi = build_points_of_interest()
    assert not poi
    assert poi.to_dict()["total"] == 0
