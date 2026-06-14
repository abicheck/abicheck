"""AbiSnapshot → JSON → AbiSnapshot roundtrip tests.

Verifies:
  1. AbiSnapshot serialises to JSON and deserialises back to an equal object.
  2. compare(snapshot, snapshot) == NO_CHANGE.
  3. Serialisation is stable (same input → same JSON bytes).
  4. All AbiSnapshot fields survive the roundtrip faithfully.
"""
from __future__ import annotations

import json
from pathlib import Path

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_snap(ver: str = "1.0") -> AbiSnapshot:
    """Minimal AbiSnapshot — only required fields."""
    return AbiSnapshot(library="libtest.so", version=ver)


def _rich_snap() -> AbiSnapshot:
    """Feature-rich snapshot covering all model fields."""
    snap = AbiSnapshot(library="librich.so", version="2.5.0")

    # Functions
    snap.functions = [
        Function(
            name="compute",
            mangled="_Z7computei",
            return_type="int",
            params=[
                Param(name="x", type="int", kind=ParamKind.VALUE),
                Param(name="buf", type="char", kind=ParamKind.POINTER, pointer_depth=1),
            ],
            visibility=Visibility.PUBLIC,
            is_virtual=False,
            is_noexcept=True,
            is_extern_c=False,
            access=AccessLevel.PUBLIC,
        ),
        Function(
            name="Foo::bar",
            mangled="_ZN3Foo3barEv",
            return_type="void",
            visibility=Visibility.HIDDEN,
            is_virtual=True,
            vtable_index=0,
            source_location="foo.h:42",
        ),
    ]

    # Variables
    snap.variables = [
        Variable(
            name="g_count",
            mangled="_Z7g_count",
            type="int",
            visibility=Visibility.PUBLIC,
            is_const=True,
        ),
    ]

    # Types
    snap.types = [
        RecordType(
            name="Point",
            kind="struct",
            size_bits=64,
            alignment_bits=32,
            fields=[
                TypeField("x", "int", 0, access=AccessLevel.PUBLIC),
                TypeField("y", "int", 32, access=AccessLevel.PUBLIC),
            ],
        ),
        RecordType(
            name="Widget",
            kind="class",
            size_bits=128,
            bases=["Base"],
            virtual_bases=["Mixin"],
            vtable=["_ZN6Widget5applyEv"],
        ),
    ]

    # Enums
    snap.enums = [
        EnumType(
            name="Status",
            members=[EnumMember("OK", 0), EnumMember("FAIL", 1), EnumMember("RETRY", 2)],
            underlying_type="int",
        ),
    ]

    # Typedefs
    snap.typedefs = {"size_t": "unsigned long", "handle_t": "void *"}

    # Constants
    snap.constants = {"MAX_SIZE": "1024", "VERSION_MAJOR": "2"}

    # elf_only_mode
    snap.elf_only_mode = False

    return snap


def _roundtrip(snap: AbiSnapshot) -> AbiSnapshot:
    """Serialise → deserialise and return the reconstructed snapshot."""
    json_str = snapshot_to_json(snap)
    d = json.loads(json_str)
    return snapshot_from_dict(d)


# ---------------------------------------------------------------------------
# 1. Basic roundtrip
# ---------------------------------------------------------------------------

class TestSnapshotRoundtrip:
    """AbiSnapshot ↔ JSON roundtrip correctness."""

    def test_minimal_roundtrip_library_version(self) -> None:
        """Minimal snapshot preserves library and version."""
        orig = _minimal_snap("3.1.4")
        restored = _roundtrip(orig)
        assert restored.library == orig.library
        assert restored.version == orig.version

    def test_functions_survive_roundtrip(self) -> None:
        """Function list preserved through JSON roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        assert len(restored.functions) == len(orig.functions)
        for f_orig, f_rest in zip(orig.functions, restored.functions):
            assert f_rest.name == f_orig.name
            assert f_rest.mangled == f_orig.mangled
            assert f_rest.return_type == f_orig.return_type
            assert f_rest.visibility == f_orig.visibility

    def test_params_survive_roundtrip(self) -> None:
        """Function parameters preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        orig_f = orig.functions[0]
        rest_f = restored.functions[0]
        assert len(rest_f.params) == len(orig_f.params)
        assert rest_f.params[0].name == orig_f.params[0].name
        assert rest_f.params[0].type == orig_f.params[0].type

    def test_types_survive_roundtrip(self) -> None:
        """RecordType list preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        assert len(restored.types) == len(orig.types)
        p_orig = orig.types[0]
        p_rest = restored.types[0]
        assert p_rest.name == p_orig.name
        assert p_rest.kind == p_orig.kind
        assert p_rest.size_bits == p_orig.size_bits
        assert len(p_rest.fields) == len(p_orig.fields)

    def test_enums_survive_roundtrip(self) -> None:
        """EnumType list preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        assert len(restored.enums) == len(orig.enums)
        e_orig = orig.enums[0]
        e_rest = restored.enums[0]
        assert e_rest.name == e_orig.name
        assert e_rest.underlying_type == e_orig.underlying_type
        assert [(m.name, m.value) for m in e_rest.members] == \
               [(m.name, m.value) for m in e_orig.members]

    def test_typedefs_survive_roundtrip(self) -> None:
        """Typedef dict preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        assert restored.typedefs == orig.typedefs

    def test_constants_survive_roundtrip(self) -> None:
        """Constants dict preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        assert restored.constants == orig.constants

    def test_elf_only_mode_false_preserved(self) -> None:
        """elf_only_mode=False preserved."""
        orig = _minimal_snap()
        orig.elf_only_mode = False
        restored = _roundtrip(orig)
        assert restored.elf_only_mode is False

    def test_elf_only_mode_true_preserved(self) -> None:
        """elf_only_mode=True preserved."""
        orig = _minimal_snap()
        orig.elf_only_mode = True
        restored = _roundtrip(orig)
        assert restored.elf_only_mode is True

    def test_bases_and_virtual_bases_preserved(self) -> None:
        """Type bases and virtual_bases preserved through roundtrip."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        w_orig = orig.types[1]
        w_rest = restored.types[1]
        assert w_rest.bases == w_orig.bases
        assert w_rest.virtual_bases == w_orig.virtual_bases

    def test_elf_import_version_soname_preserved(self) -> None:
        """ElfImport.version_soname survives the JSON roundtrip.

        The per-symbol verneed provider drives bundle external-import
        classification; a snapshot/cache round-trip that dropped it would let
        colliding version labels fall back to the label-level scan and
        misclassify external imports (regression guard for PR #400).
        """
        from abicheck.elf_metadata import ElfImport, ElfMetadata

        orig = _minimal_snap()
        orig.elf = ElfMetadata(
            soname="libalgo.so.1",
            imports=[
                ElfImport(name="core_op", version="FOO_1.0", version_soname="libcore.so.1"),
                ElfImport(name="syscall", version="GLIBC_2.2.5", version_soname="libc.so.6"),
            ],
        )
        restored = _roundtrip(orig)
        assert restored.elf is not None
        by_name = {imp.name: imp for imp in restored.elf.imports}
        assert by_name["core_op"].version_soname == "libcore.so.1"
        assert by_name["syscall"].version_soname == "libc.so.6"


# ---------------------------------------------------------------------------
# 2. compare(snap, snap) == NO_CHANGE
# ---------------------------------------------------------------------------

class TestCompareIdentical:
    """compare(s, s) must always return NO_CHANGE."""

    def test_minimal_snap_no_change(self) -> None:
        s = _minimal_snap()
        result = compare(s, s)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []

    def test_rich_snap_no_change(self) -> None:
        s = _rich_snap()
        result = compare(s, s)
        assert result.verdict == Verdict.NO_CHANGE, (
            f"Expected NO_CHANGE, got {result.verdict}. Changes: {result.changes}"
        )

    def test_roundtripped_snap_no_change(self) -> None:
        """After roundtrip, compare(restored, restored) == NO_CHANGE."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        result = compare(restored, restored)
        assert result.verdict == Verdict.NO_CHANGE

    def test_compare_orig_vs_roundtripped_no_change(self) -> None:
        """compare(original, roundtripped) == NO_CHANGE (semantically identical)."""
        orig = _rich_snap()
        restored = _roundtrip(orig)
        result = compare(orig, restored)
        assert result.verdict == Verdict.NO_CHANGE, (
            f"Expected NO_CHANGE comparing original vs roundtripped, "
            f"got {result.verdict}. Changes: {result.changes}"
        )


# ---------------------------------------------------------------------------
# 3. Stable serialisation
# ---------------------------------------------------------------------------

class TestStableSerialization:
    """Same input → same JSON bytes (deterministic output)."""

    def test_serialization_is_deterministic(self) -> None:
        """snapshot_to_json is deterministic for same input."""
        orig = _rich_snap()
        json1 = snapshot_to_json(orig)
        json2 = snapshot_to_json(orig)
        assert json1 == json2

    def test_serialization_stable_across_multiple_calls(self) -> None:
        """Multiple calls produce identical JSON."""
        orig = _rich_snap()
        results = {snapshot_to_json(orig) for _ in range(5)}
        assert len(results) == 1, "Expected deterministic JSON output"

    def test_dict_representation_is_json_serializable(self) -> None:
        """snapshot_to_dict output can be round-tripped through json.dumps."""
        orig = _rich_snap()
        d = snapshot_to_dict(orig)
        # Should not raise
        json_str = json.dumps(d)
        assert isinstance(json_str, str)

    def test_json_is_valid_json(self) -> None:
        """snapshot_to_json output is valid JSON."""
        orig = _rich_snap()
        json_str = snapshot_to_json(orig)
        # Should not raise
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed["library"] == orig.library
        assert parsed["version"] == orig.version

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """save_snapshot / load_snapshot produces semantically identical snapshot."""
        orig = _rich_snap()
        path = tmp_path / "snap.json"
        save_snapshot(orig, path)
        loaded = load_snapshot(path)

        # Core fields
        assert loaded.library == orig.library
        assert loaded.version == orig.version
        assert len(loaded.functions) == len(orig.functions)
        assert len(loaded.types) == len(orig.types)
        assert len(loaded.enums) == len(orig.enums)
        assert loaded.typedefs == orig.typedefs
        assert loaded.constants == orig.constants

    def test_empty_lists_serialized_as_arrays(self) -> None:
        """Empty list fields serialize as [] not null."""
        orig = _minimal_snap()
        d = snapshot_to_dict(orig)
        assert d.get("functions") == []
        assert d.get("types") == []
        assert d.get("enums") == []


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases in roundtrip handling."""

    def test_empty_snapshot_roundtrip(self) -> None:
        """Completely empty snapshot survives roundtrip."""
        orig = _minimal_snap()
        restored = _roundtrip(orig)
        assert restored.library == orig.library
        assert restored.functions == []
        assert restored.types == []

    def test_snapshot_with_only_enums(self) -> None:
        """Snapshot with only enum types roundtrips correctly."""
        orig = AbiSnapshot(library="libenum.so", version="1.0")
        orig.enums = [
            EnumType("A", [EnumMember("X", 0), EnumMember("Y", 1)]),
            EnumType("B", [EnumMember("P", 100)], underlying_type="unsigned int"),
        ]
        restored = _roundtrip(orig)
        assert len(restored.enums) == 2
        assert restored.enums[1].underlying_type == "unsigned int"

    def test_unicode_names_preserved(self) -> None:
        """Unicode characters in names survive roundtrip."""
        orig = AbiSnapshot(library="libunicode.so", version="1.0")
        orig.typedefs = {"café_type": "unsigned char", "naïve_t": "int"}
        restored = _roundtrip(orig)
        assert restored.typedefs == orig.typedefs

    def test_large_constant_values(self) -> None:
        """Large integer constants survive roundtrip as strings."""
        orig = _minimal_snap()
        orig.constants = {
            "ULLONG_MAX": "18446744073709551615",
            "NEG": "-9223372036854775808",
        }
        restored = _roundtrip(orig)
        assert restored.constants == orig.constants

    def test_function_with_many_params(self) -> None:
        """Function with many params roundtrips completely."""
        params = [
            Param(name=f"p{i}", type="int", kind=ParamKind.VALUE)
            for i in range(20)
        ]
        orig = AbiSnapshot(library="libmany.so", version="1.0")
        orig.functions = [Function(
            name="multi_param",
            mangled="_Z11multi_param" + "i" * 20,
            return_type="void",
            params=params,
        )]
        restored = _roundtrip(orig)
        assert len(restored.functions[0].params) == 20
