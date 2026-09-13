"""Extended libabigail parity tests.

P2 gaps from tool-comparison-gap-analysis.md:
1. JSON snapshot round-trip comparison preservation (ABIXML analog)
   — Serialize old/new → deserialize → compare produces identical verdict.
2. Suppression specification parity
   — Ensures our YAML suppressions cover libabigail .abignore-equivalent patterns:
     symbol_pattern, type_pattern, source_location, change_kind filtering.

All tests build AbiSnapshot objects directly (no castxml/abidiff required).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.suppression import SuppressionList

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _var(name: str, mangled: str, type_: str, **kwargs: object) -> Variable:
    defaults: dict[str, object] = dict(visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Variable(name=name, mangled=mangled, type=type_, **defaults)  # type: ignore[arg-type]


def _roundtrip(snap: AbiSnapshot) -> AbiSnapshot:
    """Serialize → deserialize through JSON."""
    json_str = snapshot_to_json(snap)
    return snapshot_from_dict(json.loads(json_str))


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "suppressions.yaml"
    p.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return p


# ===========================================================================
# 1. JSON Snapshot Round-trip Comparison Preservation
#
# Analog of libabigail's ABIXML self-comparison test:
# serialize both snapshots → deserialize → compare → same verdict and changes.
# ===========================================================================


class TestSnapshotRoundtripComparison:
    """Verify that serialization/deserialization preserves comparison results."""

    def test_breaking_verdict_preserved(self) -> None:
        """BREAKING verdict survives JSON roundtrip of both snapshots."""
        old = _snap(
            functions=[
                _func("foo", "_foo"),
                _func("bar", "_bar"),
            ]
        )
        new = _snap(
            functions=[
                _func("foo", "_foo"),
            ]
        )
        # Direct comparison
        direct = compare(old, new)
        assert direct.verdict == Verdict.BREAKING

        # Roundtrip comparison
        old_rt = _roundtrip(old)
        new_rt = _roundtrip(new)
        rt_result = compare(old_rt, new_rt)
        assert rt_result.verdict == direct.verdict
        assert _kinds(rt_result) == _kinds(direct)

    def test_compatible_verdict_preserved(self) -> None:
        """COMPATIBLE verdict survives JSON roundtrip."""
        old = _snap(functions=[_func("foo", "_foo")])
        new = _snap(
            functions=[
                _func("foo", "_foo"),
                _func("bar", "_bar"),
            ]
        )
        direct = compare(old, new)
        assert direct.verdict == Verdict.COMPATIBLE

        old_rt = _roundtrip(old)
        new_rt = _roundtrip(new)
        rt_result = compare(old_rt, new_rt)
        assert rt_result.verdict == direct.verdict

    def test_no_change_preserved(self) -> None:
        """NO_CHANGE verdict survives roundtrip."""
        old = _snap(functions=[_func("foo", "_foo", return_type="int")])
        new = _snap(functions=[_func("foo", "_foo", return_type="int")])
        direct = compare(old, new)
        assert direct.verdict == Verdict.NO_CHANGE

        rt_result = compare(_roundtrip(old), _roundtrip(new))
        assert rt_result.verdict == Verdict.NO_CHANGE

    def test_type_changes_preserved_through_roundtrip(self) -> None:
        """Struct size change detected identically after roundtrip."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=32,
                    fields=[
                        TypeField(name="x", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[
                        TypeField(name="x", type="int", offset_bits=0),
                        TypeField(name="y", type="int", offset_bits=32),
                    ],
                )
            ]
        )
        direct = compare(old, new)
        rt_result = compare(_roundtrip(old), _roundtrip(new))
        assert rt_result.verdict == direct.verdict
        assert _kinds(rt_result) == _kinds(direct)

    def test_enum_changes_preserved_through_roundtrip(self) -> None:
        """Enum member value change survives roundtrip."""
        old = _snap(
            enums=[
                EnumType(
                    name="Color",
                    members=[
                        EnumMember("RED", 0),
                        EnumMember("GREEN", 1),
                    ],
                )
            ]
        )
        new = _snap(
            enums=[
                EnumType(
                    name="Color",
                    members=[
                        EnumMember("RED", 0),
                        EnumMember("GREEN", 10),
                    ],
                )
            ]
        )
        direct = compare(old, new)
        rt_result = compare(_roundtrip(old), _roundtrip(new))
        assert rt_result.verdict == direct.verdict
        assert _kinds(rt_result) == _kinds(direct)

    def test_api_break_preserved_through_roundtrip(self) -> None:
        """API_BREAK verdict (field renamed) survives roundtrip."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="count", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="total", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        direct = compare(old, new)
        rt_result = compare(_roundtrip(old), _roundtrip(new))
        assert rt_result.verdict == direct.verdict

    def test_complex_snapshot_roundtrip_stability(self) -> None:
        """Complex snapshot with all field types survives double roundtrip."""
        old = _snap(
            functions=[
                _func(
                    "compute",
                    "_Z7computei",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                    is_virtual=True,
                    vtable_index=0,
                ),
            ],
            variables=[_var("g_val", "_g_val", "int", is_const=True, value="42")],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    fields=[
                        TypeField(name="x", type="int", offset_bits=0),
                        TypeField(name="y", type="int", offset_bits=32),
                    ],
                )
            ],
            enums=[
                EnumType(
                    name="Status",
                    members=[
                        EnumMember("OK", 0),
                        EnumMember("FAIL", 1),
                    ],
                )
            ],
            constants={"MAX": "1024"},
        )
        # Double roundtrip
        rt1 = _roundtrip(old)
        rt2 = _roundtrip(rt1)
        # Serialize both — should produce identical JSON
        j1 = snapshot_to_json(rt1)
        j2 = snapshot_to_json(rt2)
        assert j1 == j2, "Double roundtrip produced different JSON"


# ===========================================================================
# 2. Suppression Specification Parity
#
# libabigail uses .abignore files with type/function suppression specs.
# abicheck uses YAML. These tests verify we can express all libabigail-style
# suppression patterns in our YAML format.
# ===========================================================================


class TestSuppressionParity:
    """Verify suppression specifications cover libabigail .abignore patterns."""

    def test_suppress_by_exact_symbol(self, tmp_path: Path) -> None:
        """Suppress a specific symbol by name (like [suppress_function] name = foo)."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3Foo3barEv"
                change_kind: "func_removed"
                reason: "intentional removal"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(functions=[_func("Foo::bar", "_ZN3Foo3barEv")])
        new = _snap(functions=[])
        result = compare(old, new, suppression=sl)
        # The func_removed change should be suppressed (moved to suppressed_changes)
        active_kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_REMOVED not in active_kinds
        assert any(c.kind == ChangeKind.FUNC_REMOVED for c in result.suppressed_changes)

    def test_suppress_by_symbol_regex(self, tmp_path: Path) -> None:
        """Suppress by symbol regex (like [suppress_function] name_regexp = .*detail.*)."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*detail.*"
                reason: "internal implementation detail"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(
            functions=[
                _func("ns::detail::helper", "_ZN2ns6detail6helperEv"),
                _func("ns::public_api", "_ZN2ns10public_apiEv"),
            ]
        )
        new = _snap(
            functions=[
                _func("ns::public_api", "_ZN2ns10public_apiEv"),
            ]
        )
        result = compare(old, new, suppression=sl)
        # detail::helper removal should be suppressed
        assert not any(c.kind == ChangeKind.FUNC_REMOVED for c in result.changes)

    def test_suppress_by_type_pattern(self, tmp_path: Path) -> None:
        """Suppress by type name regex (like [suppress_type] name_regexp = .*Internal.*)."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: ".*Internal.*"
                reason: "internal types"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(
            types=[
                RecordType(name="InternalState", kind="struct", size_bits=32),
                RecordType(name="PublicAPI", kind="struct", size_bits=32),
            ]
        )
        new = _snap(
            types=[
                RecordType(name="InternalState", kind="struct", size_bits=64),
                RecordType(name="PublicAPI", kind="struct", size_bits=64),
            ]
        )
        result = compare(old, new, suppression=sl)
        # InternalState change should be suppressed, PublicAPI should not
        active_symbols = {c.symbol for c in result.changes}
        assert "PublicAPI" in active_symbols
        suppressed_symbols = {c.symbol for c in result.suppressed_changes}
        assert "InternalState" in suppressed_symbols

    def test_suppress_by_change_kind(self, tmp_path: Path) -> None:
        """Suppress all changes of a specific kind (like libabigail's category filtering)."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*"
                change_kind: "type_size_changed"
                reason: "known size changes in this release"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(types=[RecordType(name="S", kind="struct", size_bits=32)])
        new = _snap(types=[RecordType(name="S", kind="struct", size_bits=64)])
        result = compare(old, new, suppression=sl)
        assert not any(c.kind == ChangeKind.TYPE_SIZE_CHANGED for c in result.changes)

    def test_suppress_by_source_location(self, tmp_path: Path) -> None:
        """Suppress by source file path (like libabigail's file_name_regexp).

        Uses fnmatch-style glob pattern.
        """
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - source_location: "*/internal/*"
                reason: "internal headers"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(
            functions=[
                _func(
                    "internal_fn",
                    "_internal_fn",
                    source_location="src/internal/helper.h:10",
                ),
                _func("public_fn", "_public_fn", source_location="include/public.h:20"),
            ]
        )
        new = _snap(
            functions=[
                _func("public_fn", "_public_fn", source_location="include/public.h:20"),
            ]
        )
        result = compare(old, new, suppression=sl)
        # internal_fn removal should be suppressed
        active_symbols = {c.symbol for c in result.changes}
        assert "_internal_fn" not in active_symbols

    def test_suppress_with_expiry(self, tmp_path: Path) -> None:
        """Suppression with expiry date (not in libabigail — abicheck extension)."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_removed_fn"
                change_kind: "func_removed"
                reason: "temporary workaround"
                expires: "2099-12-31"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(functions=[_func("removed_fn", "_removed_fn")])
        new = _snap(functions=[])
        result = compare(old, new, suppression=sl)
        # Should be suppressed (not expired yet)
        assert not any(c.kind == ChangeKind.FUNC_REMOVED for c in result.changes)

    def test_multiple_suppressions_combined(self, tmp_path: Path) -> None:
        """Multiple suppression rules applied together."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*detail.*"
                reason: "internal"
              - symbol_pattern: ".*deprecated.*"
                change_kind: "func_removed"
                reason: "deprecated API removal"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(
            functions=[
                _func("detail::impl", "_detail_impl"),
                _func("deprecated_api", "_deprecated_api"),
                _func("stable_api", "_stable_api"),
            ]
        )
        new = _snap(
            functions=[
                _func("stable_api", "_stable_api"),
            ]
        )
        result = compare(old, new, suppression=sl)
        # detail and deprecated should be suppressed
        active_symbols = {c.symbol for c in result.changes}
        assert "_detail_impl" not in active_symbols
        assert "_deprecated_api" not in active_symbols

    def test_unsuppressed_changes_still_detected(self, tmp_path: Path) -> None:
        """Suppressions don't affect unmatched changes."""
        yaml_path = _write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_foo"
                change_kind: "func_removed"
                reason: "known"
        """,
        )
        sl = SuppressionList.load(yaml_path)

        old = _snap(
            functions=[
                _func("foo", "_foo"),
                _func("bar", "_bar"),
            ]
        )
        new = _snap(functions=[])
        result = compare(old, new, suppression=sl)
        # _bar removal should NOT be suppressed
        assert any(
            c.symbol == "_bar" and c.kind == ChangeKind.FUNC_REMOVED
            for c in result.changes
        )
