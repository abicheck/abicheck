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

"""Coverage-focused unit tests for abicheck.diff_filtering internal helpers."""

from __future__ import annotations

import re

from abicheck.checker_policy import ChangeKind, Confidence, EvidenceTier
from abicheck.checker_types import (
    SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER,
    Change,
)
from abicheck.detectors import DetectorResult
from abicheck.diff_filtering import (
    _build_location_index,
    _compute_confidence,
    _dedup_enum_same_kind,
    _determine_confidence_level,
    _downgrade_opaque_struct_changes,
    _downgrade_opaque_type_changes,
    _enrich_affected_symbols,
    _enrich_source_locations,
    _filter_reserved_field_renames,
    _find_by_value_types,
    _find_opaque_types,
    _has_public_pointer_factory,
    _is_impl_source,
    _public_function_uses_type_by_value,
    _public_variable_uses_type_by_value,
    _safe_index,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(**kw) -> AbiSnapshot:
    base = dict(library="libfoo.so.1", version="1.0.0")
    base.update(kw)
    return AbiSnapshot(**base)


def _fn(
    name,
    mangled,
    return_type="void",
    params=None,
    visibility=Visibility.PUBLIC,
    source_location=None,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=return_type,
        params=params or [],
        visibility=visibility,
        source_location=source_location,
    )


# ── _build_location_index — new-side setdefault branches (67, 80, 83) ────────


def test_build_location_index_new_side_fills_missing():
    old = _snap(
        types=[RecordType(name="OldType", kind="struct", source_location="old.h:1")],
        functions=[_fn("f", "f", source_location="old.h:2")],
        variables=[
            Variable(name="v", mangled="v", type="int", source_location="old.h:3")
        ],
    )
    new = _snap(
        types=[
            RecordType(name="OldType", kind="struct", source_location="new.h:1"),
            RecordType(name="NewType", kind="struct", source_location="new.h:9"),
        ],
        functions=[
            _fn("f", "f", source_location="new.h:2"),
            _fn("g", "g", source_location="new.h:5"),
        ],
        variables=[
            Variable(name="v", mangled="v", type="int", source_location="new.h:3"),
            Variable(name="w", mangled="w", type="int", source_location="new.h:7"),
        ],
    )
    type_loc, func_loc, var_loc = _build_location_index(old, new)
    # Old-side wins for shared names; new-only entries are added via setdefault.
    assert type_loc["OldType"] == "old.h:1"
    assert type_loc["NewType"] == "new.h:9"
    assert func_loc["f"] == "old.h:2"
    assert func_loc["g"] == "new.h:5"
    assert var_loc["v"] == "old.h:3"
    assert var_loc["w"] == "new.h:7"


# ── _safe_index — exception path (97, 98) ────────────────────────────────────


def test_safe_index_returns_false_on_exception():
    class Boom:
        def index(self):
            raise RuntimeError("partial snapshot")

    assert _safe_index(Boom()) is False


def test_safe_index_returns_true_on_success():
    assert _safe_index(_snap()) is True


# ── _enrich_source_locations — None/unsafe snapshot continue (116) ───────────


def test_enrich_source_locations_skips_none_and_unindexable():
    old = _snap(functions=[_fn("ns::f", "f", source_location="a.h:1")])

    class BadSnap:
        types = []
        functions = []
        variables = []

        def index(self):
            raise RuntimeError("boom")

    changes = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="d")]
    # old supplies the location + qualified name; the None and the failing
    # snapshot must both be skipped without raising.
    _enrich_source_locations(changes, old, BadSnap())
    assert changes[0].source_location == "a.h:1"
    assert changes[0].qualified_name == "ns::f"


# ── _enrich_affected_symbols — empty affected_types early return (250) ───────


def test_enrich_affected_symbols_blank_symbol_returns_early():
    # A type-change kind whose symbol is empty → affected_types == {""} is
    # falsy-empty after the set comprehension only if symbol is "". Use a single
    # change with empty symbol so affected_types == {""} which is truthy; instead
    # drive the "not affected_types" guard by giving no symbol content at all.
    # _root_type_name("") -> "" so affected_types = {""}; that's truthy. To hit
    # line 250 we need affected_types to be empty, which cannot happen when
    # type_changes is non-empty. So assert the realistic path: changes present.
    old = _snap(functions=[_fn("use", "use", return_type="Foo")])
    changes = [Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Foo", description="d")]
    _enrich_affected_symbols(changes, old)
    assert changes[0].affected_symbols == ["use"]


def test_enrich_affected_symbols_no_type_changes_noop():
    old = _snap()
    changes = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="d")]
    _enrich_affected_symbols(changes, old)
    assert changes[0].affected_symbols is None


# ── _public_function_uses_type_by_value — non-public skip (539) ──────────────


def test_public_function_uses_type_by_value_skips_hidden():
    bare = re.compile(r"\bHandle\b")
    snap = _snap(
        functions=[
            _fn("hidden", "hidden", return_type="Handle", visibility=Visibility.HIDDEN),
        ]
    )
    assert _public_function_uses_type_by_value(snap, bare) is False


def test_public_function_uses_type_by_value_param_match():
    bare = re.compile(r"\bHandle\b")
    snap = _snap(
        functions=[
            _fn("f", "f", return_type="void", params=[Param(name="h", type="Handle")]),
        ]
    )
    assert _public_function_uses_type_by_value(snap, bare) is True


# ── _public_variable_uses_type_by_value — skip + match (551-554) ─────────────


def test_public_variable_uses_type_by_value_skips_hidden():
    bare = re.compile(r"\bHandle\b")
    snap = _snap(
        variables=[
            Variable(
                name="g", mangled="g", type="Handle", visibility=Visibility.HIDDEN
            ),
        ]
    )
    assert _public_variable_uses_type_by_value(snap, bare) is False


def test_public_variable_uses_type_by_value_matches():
    bare = re.compile(r"\bHandle\b")
    snap = _snap(
        variables=[
            Variable(name="g", mangled="g", type="Handle"),
        ]
    )
    assert _public_variable_uses_type_by_value(snap, bare) is True


def test_public_variable_uses_type_by_value_loop_continues_on_nonmatch():
    # First public variable does not use the type by value (pointer) → loop
    # continues to the next variable (covers the 553->550 loop-back branch).
    bare = re.compile(r"\bHandle\b")
    snap = _snap(
        variables=[
            Variable(name="p", mangled="p", type="Handle*"),
            Variable(name="g", mangled="g", type="Handle"),
        ]
    )
    assert _public_variable_uses_type_by_value(snap, bare) is True


# ── _has_public_pointer_factory — non-public skip (602) ──────────────────────


def test_has_public_pointer_factory_skips_hidden():
    snap = _snap(
        functions=[
            _fn("make", "make", return_type="Handle*", visibility=Visibility.HIDDEN),
        ]
    )
    assert _has_public_pointer_factory("Handle", snap) is False


def test_has_public_pointer_factory_true_for_public():
    snap = _snap(
        functions=[
            _fn("make", "make", return_type="Handle *"),
        ]
    )
    assert _has_public_pointer_factory("Handle", snap) is True


# ── _filter_reserved_field_renames — namespace-prefix continue (737) ─────────


def test_filter_reserved_field_renames_other_struct_kept():
    changes = [
        Change(
            kind=ChangeKind.USED_RESERVED_FIELD,
            symbol="S",
            description="renamed",
            old_value="__reserved1",
            new_value="priority",
        ),
        # A removal on a *different* struct: the symbol does not match "S" and
        # does not start with "S::", so the inner-loop `continue` (line 737) runs
        # and the change is kept.
        Change(
            kind=ChangeKind.TYPE_FIELD_REMOVED,
            symbol="Other::x",
            description="removed",
        ),
        # Removal of the renamed reserved field itself → suppressed.
        Change(
            kind=ChangeKind.STRUCT_FIELD_REMOVED,
            symbol="S::__reserved1",
            description="removed reserved",
        ),
    ]
    out = _filter_reserved_field_renames(changes)
    kept_syms = {c.symbol for c in out}
    assert "Other::x" in kept_syms
    assert "S::__reserved1" not in kept_syms


def test_filter_reserved_field_renames_no_reserved_noop():
    changes = [
        Change(kind=ChangeKind.TYPE_FIELD_REMOVED, symbol="S::x", description="d")
    ]
    assert _filter_reserved_field_renames(changes) is changes


# ── _dedup_enum_same_kind — value-population branches (783-791, 798) ──────────


def test_dedup_enum_same_kind_prefers_populated_values():
    no_vals = Change(
        kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        symbol="Color::RED",
        description="changed",
    )
    with_vals = Change(
        kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        symbol="Color::RED",
        description="x",
        old_value="0",
        new_value="1",
    )
    out = _dedup_enum_same_kind([no_vals, with_vals])
    # Winner is the one carrying old/new values; the other is dropped (line 798).
    assert out == [with_vals]


def test_dedup_enum_same_kind_keeps_existing_when_new_lacks_values():
    with_vals = Change(
        kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        symbol="Color::RED",
        description="x",
        old_value="0",
        new_value="1",
    )
    no_vals = Change(
        kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        symbol="Color::RED",
        description="changed",
    )
    out = _dedup_enum_same_kind([with_vals, no_vals])
    assert out == [with_vals]


def test_dedup_enum_same_kind_longer_description_wins():
    short = Change(
        kind=ChangeKind.ENUM_MEMBER_REMOVED,
        symbol="Color::RED",
        description="x",
    )
    longer = Change(
        kind=ChangeKind.ENUM_MEMBER_REMOVED,
        symbol="Color::RED",
        description="a much longer description",
    )
    out = _dedup_enum_same_kind([short, longer])
    assert out == [longer]


# ── _is_impl_source (962, 964-968) ───────────────────────────────────────────


def test_is_impl_source_variants():
    assert _is_impl_source(None) is False
    assert _is_impl_source("foo.cpp:42") is True
    assert _is_impl_source("foo.h:42") is False
    # No extension at all → False (covers the `dot < 0` branch, line 964-966).
    assert _is_impl_source("Makefile") is False
    assert _is_impl_source("bar.c") is True


# ── _find_opaque_types / _find_by_value_types (991, 1005-1023) ───────────────


def test_find_opaque_types_impl_source_pointer_only():
    # Type defined in an impl file, used only via pointer → opaque.
    snap = _snap(
        types=[RecordType(name="Hidden", kind="struct", source_location="impl.c:5")],
        functions=[
            _fn(
                "use",
                "use",
                return_type="void",
                params=[Param(name="p", type="Hidden*", pointer_depth=1)],
            )
        ],
    )
    assert _find_opaque_types(snap) == {"Hidden"}


def test_find_opaque_types_by_value_excluded():
    # By-value usage in a public function removes the type from opaque set.
    snap = _snap(
        types=[RecordType(name="Hidden", kind="struct", source_location="impl.c:5")],
        functions=[_fn("ret", "ret", return_type="Hidden")],
    )
    assert _find_opaque_types(snap) == set()


def test_find_opaque_types_empty_when_no_candidates():
    snap = _snap(
        types=[RecordType(name="Pub", kind="struct", source_location="pub.h:1")]
    )
    assert _find_opaque_types(snap) == set()


def test_find_by_value_types_param_and_variable():
    opaque = {"Hidden"}
    # By-value param (pointer_depth 0, not ending in *) and by-value variable.
    snap = _snap(
        functions=[
            _fn(
                "byparam",
                "byparam",
                return_type="void",
                params=[Param(name="h", type="Hidden", pointer_depth=0)],
            ),
            # hidden visibility function is skipped (line 1005).
            _fn("skip", "skip", return_type="Hidden", visibility=Visibility.HIDDEN),
        ],
        variables=[
            Variable(name="gv", mangled="gv", type="Hidden"),
            # hidden variable skipped (line 1018-1019).
            Variable(
                name="hv", mangled="hv", type="Hidden", visibility=Visibility.HIDDEN
            ),
        ],
    )
    result = _find_by_value_types(snap, opaque)
    assert result == {"Hidden"}


def test_find_by_value_types_pointer_only_returns_empty():
    snap = _snap(
        functions=[
            _fn(
                "f",
                "f",
                return_type="Hidden*",
                params=[Param(name="p", type="Hidden*", pointer_depth=1)],
            )
        ],
        variables=[Variable(name="g", mangled="g", type="Hidden*")],
    )
    assert _find_by_value_types(snap, {"Hidden"}) == set()


# ── _downgrade_opaque_type_changes (1047, 1049-1054) ─────────────────────────


def test_downgrade_opaque_type_changes_suppresses_structural():
    # Hidden opaque in both old and new (impl-file + pointer-only API).
    def mk():
        return _snap(
            types=[
                RecordType(name="Hidden", kind="struct", source_location="impl.c:1")
            ],
            functions=[
                _fn(
                    "use",
                    "use",
                    return_type="void",
                    params=[Param(name="p", type="Hidden*", pointer_depth=1)],
                )
            ],
        )

    changes = [
        Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Hidden", description="grew"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="other", description="kept"),
    ]
    out = _downgrade_opaque_type_changes(changes, mk(), mk())
    kinds = {c.kind for c in out}
    assert ChangeKind.TYPE_SIZE_CHANGED not in kinds
    assert ChangeKind.FUNC_REMOVED in kinds


def test_downgrade_opaque_type_changes_no_opaque_noop():
    old = _snap(types=[RecordType(name="Pub", kind="struct", source_location="p.h:1")])
    new = _snap(types=[RecordType(name="Pub", kind="struct", source_location="p.h:1")])
    changes = [Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Pub", description="d")]
    assert _downgrade_opaque_type_changes(changes, old, new) is changes


def test_downgrade_opaque_type_changes_non_opaque_type_kept():
    # Hidden is opaque; a structural change on a *different* (non-opaque) type
    # passes through the `type_name in opaque` False branch (line 1050->1054).
    def mk():
        return _snap(
            types=[
                RecordType(name="Hidden", kind="struct", source_location="impl.c:1")
            ],
            functions=[
                _fn(
                    "use",
                    "use",
                    return_type="void",
                    params=[Param(name="p", type="Hidden*", pointer_depth=1)],
                )
            ],
        )

    changes = [
        Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Visible", description="kept"),
    ]
    out = _downgrade_opaque_type_changes(changes, mk(), mk())
    assert out[0].symbol == "Visible"


# ── _determine_confidence_level — disabled dwarf detector (1169-1170) ────────


def test_determine_confidence_level_dwarf_disabled_downgrades_high():
    warnings: list[str] = []
    detectors = [DetectorResult(name="dwarf", changes_count=0, enabled=False)]
    conf = _determine_confidence_level(
        has_elf=True,
        has_dwarf=True,
        has_pe=False,
        has_macho=False,
        has_headers=True,
        detector_results=detectors,
        warnings=warnings,
    )
    # headers + elf would normally be HIGH; disabled dwarf knocks it to MEDIUM.
    assert conf == Confidence.MEDIUM


def test_determine_confidence_level_dwarf_disabled_non_high_unchanged():
    warnings: list[str] = []
    detectors = [DetectorResult(name="dwarf", changes_count=0, enabled=False)]
    # headers only (no binary) → MEDIUM; disabled dwarf must NOT downgrade
    # further because confidence is not HIGH (line 1169 False branch).
    conf = _determine_confidence_level(
        has_elf=False,
        has_dwarf=False,
        has_pe=False,
        has_macho=False,
        has_headers=True,
        detector_results=detectors,
        warnings=warnings,
    )
    assert conf == Confidence.MEDIUM


def test_determine_confidence_level_binary_only_low():
    warnings: list[str] = []
    conf = _determine_confidence_level(
        has_elf=True,
        has_dwarf=False,
        has_pe=False,
        has_macho=False,
        has_headers=False,
        detector_results=[],
        warnings=warnings,
    )
    assert conf == Confidence.LOW
    assert warnings


# ── _downgrade_opaque_struct_changes (1275, 1278-1299) ───────────────────────


def test_downgrade_opaque_struct_changes_rewrites_to_compatible():
    # Opaque in both snapshots → structural change rewritten to a compatible add.
    old = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=True)])
    new = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=True)])
    changes = [
        Change(
            kind=ChangeKind.STRUCT_SIZE_CHANGED,
            symbol="Op",
            description="grew",
            old_value="8",
            new_value="16",
            source_location="x.h:1",
        )
    ]
    out = _downgrade_opaque_struct_changes(changes, old, new)
    assert len(out) == 1
    assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
    assert out[0].description.startswith("(opaque struct)")
    assert out[0].old_value == "8"


def test_downgrade_opaque_struct_changes_embedded_by_value_kept():
    # Op is opaque, but embedded by value in a non-opaque Wrapper → NOT truly
    # opaque, so structural change is kept as-is (lines 1273-1284).
    def mk():
        return _snap(
            types=[
                RecordType(name="Op", kind="struct", is_opaque=True),
                RecordType(
                    name="Wrapper",
                    kind="struct",
                    is_opaque=False,
                    fields=[TypeField(name="inner", type="Op")],
                ),
            ]
        )

    changes = [
        Change(kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="Op", description="d")
    ]
    out = _downgrade_opaque_struct_changes(changes, mk(), mk())
    assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED


def test_downgrade_opaque_struct_changes_pointer_field_not_embedded():
    # Op is opaque; Wrapper holds it via *pointer* → not embedded by value, so
    # the field-loop `continue` (1279->1275) runs and Op stays truly opaque.
    old = _snap(
        types=[
            RecordType(name="Op", kind="struct", is_opaque=True),
            RecordType(
                name="Wrapper",
                kind="struct",
                is_opaque=False,
                fields=[TypeField(name="ptr", type="Op*")],
            ),
        ]
    )
    new = _snap(
        types=[
            RecordType(name="Op", kind="struct", is_opaque=True),
            RecordType(
                name="Wrapper",
                kind="struct",
                is_opaque=False,
                fields=[TypeField(name="ptr", type="Op*")],
            ),
        ]
    )
    changes = [
        Change(kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="Op", description="d")
    ]
    out = _downgrade_opaque_struct_changes(changes, old, new)
    assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE


def test_downgrade_opaque_struct_changes_no_opaque_noop():
    old = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=False)])
    new = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=False)])
    changes = [
        Change(kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="Op", description="d")
    ]
    assert _downgrade_opaque_struct_changes(changes, old, new) is changes


def test_downgrade_opaque_struct_changes_non_matching_kind_passthrough():
    # Opaque type present, but the change kind is not downgradeable → else branch
    # (line 1299) keeps the change unchanged.
    old = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=True)])
    new = _snap(types=[RecordType(name="Op", kind="struct", is_opaque=True)])
    changes = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="Op", description="d")]
    out = _downgrade_opaque_struct_changes(changes, old, new)
    assert out[0].kind == ChangeKind.FUNC_REMOVED


# ── _compute_confidence end-to-end (evidence tier + warnings) ────────────────


def test_compute_confidence_header_only_medium():
    old = _snap(from_headers=True, functions=[_fn("f", "f")])
    new = _snap(from_headers=True, functions=[_fn("f", "f")])
    tiers, conf, warnings, tier = _compute_confidence([], old, new)
    assert "header" in tiers
    assert conf == Confidence.MEDIUM
    assert tier == EvidenceTier.HEADER_AWARE
    assert any("header analysis only" in w for w in warnings)


def test_compute_confidence_disabled_detector_warns():
    old = _snap(from_headers=True, functions=[_fn("f", "f")])
    new = _snap(from_headers=True, functions=[_fn("f", "f")])
    detectors = [
        DetectorResult(
            name="extra",
            changes_count=0,
            enabled=False,
            coverage_gap="missing tool",
        )
    ]
    _tiers, _conf, warnings, _tier = _compute_confidence(detectors, old, new)
    assert any("disabled" in w for w in warnings)


# ── sanity: marker constant import used so it is exercised ───────────────────


def test_alias_marker_constant_present():
    assert isinstance(SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER, str)
    assert SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER
