"""Tests for ADR-003 DwarfSnapshotBuilder and data source architecture."""
from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit, Target
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.dwarf_snapshot import (
    _strip_type_decorators,
    build_snapshot_from_dwarf,
    show_data_sources,
)
from abicheck.dwarf_utils import _evaluate_location_expr
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import Visibility

# ── helpers ──────────────────────────────────────────────────────────────────

def _elf_meta_with_symbols(names: list[str]) -> ElfMetadata:
    """Create ElfMetadata with exported symbols."""
    return ElfMetadata(
        soname="libtest.so.1",
        symbols=[
            ElfSymbol(
                name=name,
                binding=SymbolBinding.GLOBAL,
                sym_type=SymbolType.FUNC,
            )
            for name in names
        ],
    )


def _elf_meta_with_objects(func_names: list[str], obj_names: list[str]) -> ElfMetadata:
    """Create ElfMetadata with both function and object symbols."""
    symbols = [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
        for n in func_names
    ] + [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)
        for n in obj_names
    ]
    return ElfMetadata(soname="libtest.so.1", symbols=symbols)


def _dwarf_meta(**kwargs: object) -> DwarfMetadata:
    m = DwarfMetadata(has_dwarf=True)
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _dwarf_adv() -> AdvancedDwarfMetadata:
    return AdvancedDwarfMetadata(has_dwarf=True)


# ── _strip_type_decorators ──────────────────────────────────────────────────

class TestStripTypeDecorators:
    def test_pointer(self) -> None:
        assert _strip_type_decorators("int *") == "int"

    def test_double_pointer(self) -> None:
        assert _strip_type_decorators("char **") == "char"

    def test_reference(self) -> None:
        assert _strip_type_decorators("Foo &") == "Foo"

    def test_rvalue_ref(self) -> None:
        assert _strip_type_decorators("Foo &&") == "Foo"

    def test_const(self) -> None:
        assert _strip_type_decorators("const int") == "int"

    def test_volatile(self) -> None:
        assert _strip_type_decorators("volatile int") == "int"

    def test_array(self) -> None:
        assert _strip_type_decorators("int[]") == "int"

    def test_plain_type(self) -> None:
        assert _strip_type_decorators("MyStruct") == "MyStruct"

    def test_combined(self) -> None:
        assert _strip_type_decorators("const Foo *") == "Foo"

    def test_const_volatile(self) -> None:
        """Multiple leading qualifiers should all be stripped."""
        assert _strip_type_decorators("const volatile int") == "int"

    def test_volatile_const_restrict(self) -> None:
        """Triple qualifier combination."""
        assert _strip_type_decorators("volatile const restrict int") == "int"

    def test_const_volatile_pointer(self) -> None:
        """Qualifiers + pointer suffix."""
        assert _strip_type_decorators("const volatile int *") == "int"

    def test_restrict_array(self) -> None:
        """Restrict qualifier + array suffix."""
        assert _strip_type_decorators("restrict int[]") == "int"


# ── _evaluate_location_expr ─────────────────────────────────────────────────

class TestEvaluateLocationExpr:
    def test_empty_list(self) -> None:
        assert _evaluate_location_expr([]) == 0

    def test_single_int(self) -> None:
        """Bare integer treated as constant."""
        assert _evaluate_location_expr([42]) == 42

    def test_plus_uconst_raw(self) -> None:
        """DW_OP_plus_uconst (0x23) with raw int operand."""
        assert _evaluate_location_expr([0x23, 16]) == 16

    def test_constu_raw(self) -> None:
        """DW_OP_constu (0x10) with raw int operand."""
        assert _evaluate_location_expr([0x10, 8]) == 8

    def test_consts_raw(self) -> None:
        """DW_OP_consts (0x11) with raw int operand."""
        assert _evaluate_location_expr([0x11, 12]) == 12

    def test_lit_opcodes(self) -> None:
        """DW_OP_lit0..DW_OP_lit31."""
        # DW_OP_lit5 = 0x35
        assert _evaluate_location_expr([0x35]) == 5

    def test_plus_op(self) -> None:
        """DW_OP_plus (0x22) pops and adds two stack values."""
        # Push 10 via constu, push 20 via constu, then plus
        assert _evaluate_location_expr([0x10, 10, 0x10, 20, 0x22]) == 30

    def test_tuple_plus_uconst(self) -> None:
        """Tuple-style (opcode, operand) as emitted by some pyelftools versions."""
        assert _evaluate_location_expr([(0x23, 24)]) == 24

    def test_tuple_constu(self) -> None:
        """Tuple-style DW_OP_constu."""
        assert _evaluate_location_expr([(0x10, 32)]) == 32

    def test_tuple_lit(self) -> None:
        """Tuple-style DW_OP_lit7."""
        assert _evaluate_location_expr([(0x37, 0)]) == 7

    def test_tuple_plus(self) -> None:
        """Tuple-style DW_OP_plus."""
        result = _evaluate_location_expr([(0x10, 5), (0x10, 3), (0x22, 0)])
        assert result == 8

    def test_non_int_tuple_elements(self) -> None:
        """Tuple with non-int elements should be skipped gracefully."""
        assert _evaluate_location_expr([("foo", "bar")]) == 0

    def test_non_int_non_tuple_item(self) -> None:
        """Non-int, non-tuple items (e.g. strings) should be skipped."""
        assert _evaluate_location_expr(["not_an_opcode"]) == 0

    def test_plus_insufficient_stack(self) -> None:
        """DW_OP_plus with only one stack value should not crash."""
        # Stack has implicit 0, then lit3 pushes 3; plus pops both → 3
        assert _evaluate_location_expr([0x33, 0x22]) == 3

    def test_tuple_plus_insufficient_stack(self) -> None:
        """Tuple DW_OP_plus with insufficient stack."""
        # Only implicit 0 on stack, DW_OP_plus needs 2
        result = _evaluate_location_expr([(0x22, 0)])
        assert isinstance(result, int)

    def test_unknown_tuple_opcode(self) -> None:
        """Unknown tuple opcode should be skipped."""
        # 0xFF is not a recognized opcode
        assert _evaluate_location_expr([(0xFF, 99)]) == 0

    def test_unknown_raw_opcode(self) -> None:
        """Unknown raw int opcode treated as constant."""
        # 0x01 (DW_OP_addr-ish) is not handled, treated as constant
        assert _evaluate_location_expr([0x01]) == 0x01

    def test_dwarf_expr_op_namedtuple(self) -> None:
        """pyelftools DWARFExprOp (namedtuple with .op/.args) must be decoded."""
        from elftools.dwarf.dwarf_expr import DWARFExprOp

        op = DWARFExprOp(op=0x23, op_name="DW_OP_plus_uconst", args=[48], offset=0)
        assert _evaluate_location_expr([op]) == 48

    def test_dwarf_expr_op_constu(self) -> None:
        """DWARFExprOp DW_OP_constu decoded correctly."""
        from elftools.dwarf.dwarf_expr import DWARFExprOp

        op = DWARFExprOp(op=0x10, op_name="DW_OP_constu", args=[64], offset=0)
        assert _evaluate_location_expr([op]) == 64

    def test_dwarf_expr_op_multi(self) -> None:
        """Multiple DWARFExprOp entries: constu + plus."""
        from elftools.dwarf.dwarf_expr import DWARFExprOp

        ops = [
            DWARFExprOp(op=0x10, op_name="DW_OP_constu", args=[10], offset=0),
            DWARFExprOp(op=0x10, op_name="DW_OP_constu", args=[20], offset=2),
            DWARFExprOp(op=0x22, op_name="DW_OP_plus", args=[], offset=4),
        ]
        assert _evaluate_location_expr(ops) == 30

    def test_raw_leb128_plus_uconst(self) -> None:
        """DW_OP_plus_uconst with multi-byte ULEB128 operand: 130 = 0x82 0x01."""
        assert _evaluate_location_expr([0x23, 0x82, 0x01]) == 130

    def test_raw_leb128_constu(self) -> None:
        """DW_OP_constu with ULEB128 operand: 300 = 0xAC 0x02."""
        assert _evaluate_location_expr([0x10, 0xAC, 0x02]) == 300


# ── show_data_sources ───────────────────────────────────────────────────────

class TestShowDataSources:
    def test_all_layers(self) -> None:
        elf = _elf_meta_with_symbols(["foo", "bar"])
        dwarf = _dwarf_meta(structs={"S": StructLayout("S", 8)})
        output = show_data_sources(Path("libtest.so"), elf, dwarf, has_headers=True)
        assert "L0 Binary metadata: ELF" in output
        assert "L1 Debug info:      DWARF" in output
        assert "L2 Header AST:      available" in output
        assert "L3 Build context:   not collected" in output
        assert "Headers mode (artifact + public header evidence)" in output

    def test_evidence_layers(self, tmp_path: Path) -> None:
        elf = _elf_meta_with_symbols(["foo"])
        pack = BuildSourcePack.empty(tmp_path / "evidence")
        pack.build_evidence = BuildEvidence(
            targets=[Target(id="target://libtest", name="libtest")],
            compile_units=[CompileUnit(id="cu://lib.c", source="lib.c")],
        )
        pack.source_abi = SourceAbiSurface(
            reachable_declarations=[
                SourceEntity(id="source://foo", kind="function", qualified_name="foo")
            ]
        )
        pack.source_graph = SourceGraphSummary(
            nodes=[GraphNode(id="file://lib.c", kind="file", label="lib.c")]
        )

        output = show_data_sources(
            Path("libtest.so"),
            elf,
            None,
            has_headers=True,
            build_source_pack=pack,
        )
        assert "L3 Build context: present (1 compile units, 1 targets)" in output
        assert "L4 Source ABI: present (1 declarations)" in output
        assert "L5 Source graph: present (1 nodes)" in output

    def test_partial_evidence_manifest_is_preserved(self, tmp_path: Path) -> None:
        elf = _elf_meta_with_symbols(["foo"])
        pack = BuildSourcePack.empty(tmp_path / "evidence")
        pack.source_abi = SourceAbiSurface(library="libtest.so")
        pack.manifest.coverage = [
            LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value,
                status=CoverageStatus.PARTIAL,
                detail="extractor unavailable",
            )
        ]

        output = show_data_sources(
            Path("libtest.so"),
            elf,
            None,
            has_headers=True,
            build_source_pack=pack,
        )
        assert "L4 Source ABI: partial (extractor unavailable)" in output

    def test_dwarf_only_mode(self) -> None:
        elf = _elf_meta_with_symbols(["foo"])
        dwarf = _dwarf_meta()
        output = show_data_sources(Path("libtest.so"), elf, dwarf, has_headers=False)
        assert "DWARF-only mode" in output
        assert "#define constants" in output

    def test_symbols_only_mode(self) -> None:
        elf = _elf_meta_with_symbols(["foo"])
        output = show_data_sources(Path("libtest.so"), elf, None, has_headers=False)
        assert "Symbols-only mode" in output

    def test_dwarf_present_but_no_dwarf_flag(self) -> None:
        """DwarfMetadata with has_dwarf=False should show 'not available'."""
        elf = _elf_meta_with_symbols(["foo"])
        dwarf = DwarfMetadata(has_dwarf=False)
        output = show_data_sources(Path("libtest.so"), elf, dwarf, has_headers=False)
        assert "not available" in output or "Symbols-only" in output

    def test_no_elf_meta(self) -> None:
        output = show_data_sources(Path("libtest.so"), None, None, has_headers=False)
        assert "not available" in output


# ── build_snapshot_from_dwarf (integration via real ELF) ────────────────────

# Helper to compile a minimal C shared library with debug info
_GCC = "gcc"


def _can_compile() -> bool:
    """Check if GCC is available for ELF integration tests.

    These tests compile .so with -shared -fPIC -g and parse the result
    as ELF with pyelftools, so they require real GCC on Linux.
    On macOS ``gcc`` is a clang symlink that produces Mach-O, not ELF.
    """
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(
            [_GCC, "--version"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_HAS_GCC = _can_compile()


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDwarfSnapshotIntegration:
    """Integration tests: compile real .so with debug info, build snapshot from DWARF."""

    @pytest.fixture()
    def simple_lib(self, tmp_path: Path) -> Path:
        """Compile a simple C library with exported function and struct."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("""\
typedef struct {
    int x;
    int y;
} Point;

typedef enum {
    RED = 0,
    GREEN = 1,
    BLUE = 2,
} Color;

int global_var = 42;

int add(int a, int b) {
    return a + b;
}

Point make_point(int x, int y) {
    Point p = {x, y};
    return p;
}

Color get_color(int idx) {
    return (Color)idx;
}

static int internal_func(int x) {
    return x * 2;
}
""")
        so_path = tmp_path / "libtest.so"
        result = subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_snapshot_has_functions(self, simple_lib: Path) -> None:
        """DWARF snapshot should contain exported functions."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        assert snap.elf_only_mode is False
        assert snap.platform == "elf"
        func_names = {f.name for f in snap.functions}
        # Exported functions should be present
        assert "add" in func_names
        assert "make_point" in func_names
        # Static/internal functions should NOT be present
        assert "internal_func" not in func_names

    def test_snapshot_has_types(self, simple_lib: Path) -> None:
        """DWARF snapshot should contain struct types."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        type_names = {t.name for t in snap.types}
        assert "Point" in type_names

    def test_snapshot_has_enums(self, simple_lib: Path) -> None:
        """DWARF snapshot should contain enum types."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        enum_names = {e.name for e in snap.enums}
        assert "Color" in enum_names
        color_enum = next(e for e in snap.enums if e.name == "Color")
        member_names = {m.name for m in color_enum.members}
        assert "RED" in member_names
        assert "GREEN" in member_names
        assert "BLUE" in member_names

    def test_snapshot_has_variables(self, simple_lib: Path) -> None:
        """DWARF snapshot should contain exported variables."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        var_names = {v.name for v in snap.variables}
        assert "global_var" in var_names

    def test_function_params(self, simple_lib: Path) -> None:
        """DWARF snapshot functions should have parameter information."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        add_func = next((f for f in snap.functions if f.name == "add"), None)
        assert add_func is not None
        assert add_func.return_type != "?"
        assert len(add_func.params) == 2

    def test_function_visibility_is_public(self, simple_lib: Path) -> None:
        """Exported functions should have PUBLIC visibility."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        for func in snap.functions:
            assert func.visibility == Visibility.PUBLIC

    def test_snapshot_json_roundtrip(self, simple_lib: Path, tmp_path: Path) -> None:
        """DWARF snapshot should serialize/deserialize via JSON."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata
        from abicheck.serialization import load_snapshot, snapshot_to_json

        elf_meta = parse_elf_metadata(simple_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(simple_lib)

        snap = build_snapshot_from_dwarf(
            simple_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        json_path = tmp_path / "snap.json"
        json_path.write_text(snapshot_to_json(snap), encoding="utf-8")
        loaded = load_snapshot(json_path)
        assert loaded.library == snap.library
        assert len(loaded.functions) == len(snap.functions)


# ── Dumper fallback chain tests ─────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDumperFallbackChain:
    """Test the ADR-003 fallback chain in dumper.py."""

    @pytest.fixture()
    def debug_lib(self, tmp_path: Path) -> Path:
        """Compile a library WITH debug info (DWARF available)."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int exported_func(int x) { return x + 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        return so_path

    @pytest.fixture()
    def stripped_lib(self, tmp_path: Path) -> Path:
        """Compile a library WITHOUT debug info (no DWARF)."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int exported_func(int x) { return x + 1; }\n")
        so_path = tmp_path / "libtest.so"
        # -g0 disables all debug info; -Wl,--build-id=none prevents build-id note
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g0", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        # Strip all sections including debug
        subprocess.run(
            ["strip", "--strip-all", "--remove-section=.debug*",
             "--remove-section=.note.gnu.build-id", str(so_path)],
            capture_output=True, check=True, timeout=10,
        )
        return so_path

    def test_no_headers_with_dwarf_uses_dwarf_mode(self, debug_lib: Path) -> None:
        """No headers + DWARF available → DWARF-only mode (elf_only_mode=False)."""
        from abicheck.dumper import dump

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap = dump(so_path=debug_lib, headers=[])

        assert snap.elf_only_mode is False
        assert snap.platform == "elf"
        # Should have extracted function info from DWARF
        func_names = {f.name for f in snap.functions}
        assert "exported_func" in func_names

    def test_no_headers_no_dwarf_uses_symbol_mode(self, stripped_lib: Path) -> None:
        """No headers + no DWARF → symbols-only mode (elf_only_mode=True)."""
        from abicheck.dumper import dump

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap = dump(so_path=stripped_lib, headers=[])

        assert snap.elf_only_mode is True

    def test_dwarf_only_flag(self, debug_lib: Path) -> None:
        """--dwarf-only flag forces DWARF mode even without headers."""
        from abicheck.dumper import dump

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap = dump(so_path=debug_lib, headers=[], dwarf_only=True)

        assert snap.elf_only_mode is False


# ── CLI tests ───────────────────────────────────────────────────────────────

class TestCLIDwarfFlags:
    """Test CLI --dwarf-only and --show-data-sources flags exist and are accepted."""

    def test_dump_help_shows_dwarf_only(self) -> None:
        """dump --help should mention --dwarf-only."""
        result = subprocess.run(
            [sys.executable, "-c", "from abicheck.cli import main; main()", "dump", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert "--dwarf-only" in result.stdout

    def test_dump_help_shows_data_sources(self) -> None:
        """dump --help should mention --show-data-sources."""
        result = subprocess.run(
            [sys.executable, "-c", "from abicheck.cli import main; main()", "dump", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert "--show-data-sources" in result.stdout

    def test_compare_help_shows_dwarf_only(self) -> None:
        """compare --help should mention --dwarf-only."""
        result = subprocess.run(
            [sys.executable, "-c", "from abicheck.cli import main; main()", "compare", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert "--dwarf-only" in result.stdout

    @pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
    def test_show_data_sources_output(self, tmp_path: Path) -> None:
        """--show-data-sources should print diagnostic info and exit."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int foo(void) { return 0; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        result = subprocess.run(
            [sys.executable, "-c", "from abicheck.cli import main; main()", "dump", str(so_path),
             "--show-data-sources"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Data sources for" in result.stdout
        assert "L0 Binary metadata" in result.stdout


# ── Visibility filtering (hidden/internal symbols) ──────────────────────────

@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestVisibilityFiltering:
    """Test that hidden/internal ELF symbols are excluded from DWARF snapshot."""

    @pytest.fixture()
    def _visibility_lib(self, tmp_path: Path) -> Path:
        """Compile a lib with hidden-visibility symbols."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("""\
__attribute__((visibility("default"))) int public_func(int x) { return x; }
__attribute__((visibility("hidden"))) int hidden_func(int x) { return x * 2; }
""")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        return so_path

    def test_hidden_symbols_excluded(self, _visibility_lib: Path) -> None:
        """Hidden-visibility functions should not appear in DWARF snapshot."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(_visibility_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(_visibility_lib)

        snap = build_snapshot_from_dwarf(
            _visibility_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        func_names = {f.name for f in snap.functions}
        assert "public_func" in func_names
        assert "hidden_func" not in func_names


# ── Dumper _dump_macho dwarf_only warning ───────────────────────────────────

class TestDumpMachoDwarfOnlyWarning:
    """Test that _dump_macho warns when dwarf_only=True."""

    def test_macho_dwarf_only_warns(self, tmp_path: Path) -> None:
        """_dump_macho should emit a warning when dwarf_only=True."""
        from abicheck.dumper import _dump_macho

        fake_path = tmp_path / "libtest.dylib"
        fake_path.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                _dump_macho(
                    fake_path, headers=[], extra_includes=[],
                    version="1.0", compiler="c++", dwarf_only=True,
                )
            except Exception as exc:  # noqa: BLE001 - Expected to fail on fake binary
                assert str(exc)

        dwarf_warnings = [
            x for x in w
            if "dwarf_only=True is not supported for Mach-O" in str(x.message)
        ]
        assert len(dwarf_warnings) == 1


# ── Dumper variables-only fallback ──────────────────────────────────────────

@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDumperVariablesOnlyFallback:
    """Test that DWARF mode accepts snapshots with only variables (no functions)."""

    def test_variables_only_uses_dwarf_mode(self, tmp_path: Path) -> None:
        """Library with only exported variables should use DWARF mode."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int my_global = 42;\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.dumper import dump

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap = dump(so_path=so_path, headers=[])

        # Should use DWARF mode (not symbol-only) because variables are present
        assert snap.elf_only_mode is False
        var_names = {v.name for v in snap.variables}
        assert "my_global" in var_names


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDumperDwarfOnlyExplicit:
    """Test dump() with dwarf_only=True explicitly."""

    def test_dwarf_only_flag(self, tmp_path: Path) -> None:
        """dump(dwarf_only=True) forces DWARF mode."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int func_a(int x) { return x + 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.dumper import dump

        snap = dump(so_path=so_path, headers=[], dwarf_only=True)
        assert snap.elf_only_mode is False
        func_names = {f.name for f in snap.functions}
        assert "func_a" in func_names

    def test_dwarf_only_with_headers_warns(self, tmp_path: Path) -> None:
        """dump(dwarf_only=True, headers=[...]) warns about ignored headers."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int func_b(int x) { return x; }\n")
        hdr = tmp_path / "lib.h"
        hdr.write_text("int func_b(int x);\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.dumper import dump

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            snap = dump(so_path=so_path, headers=[hdr], dwarf_only=True)

        assert snap.elf_only_mode is False
        dwarf_only_warnings = [
            x for x in w
            if "ignoring provided headers" in str(x.message)
        ]
        assert len(dwarf_only_warnings) == 1

    def test_no_dwarf_falls_through(self, tmp_path: Path) -> None:
        """ELF without DWARF and no headers falls to symbol-only mode."""
        c_src = tmp_path / "lib.c"
        c_src.write_text("int func_c(int x) { return x; }\n")
        so_path = tmp_path / "libtest.so"
        # Compile WITHOUT -g (no debug info)
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.dumper import dump

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap = dump(so_path=so_path, headers=[])

        # Should fall back to symbol-only mode
        assert snap.elf_only_mode is True


# ── C++ integration tests (references, inheritance, bitfields, etc.) ────────

_GPP = "g++"


def _has_gpp() -> bool:
    """Check if g++ is available on Linux for ELF C++ integration tests."""
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(
            [_GPP, "--version"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_HAS_GPP = _has_gpp()


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfSnapshotCppIntegration:
    """C++ integration: exercise references, inheritance, bitfields, typedefs."""

    @pytest.fixture()
    def cpp_lib(self, tmp_path: Path) -> Path:
        """Compile a C++ library with various type constructs."""
        cpp_src = tmp_path / "lib.cpp"
        cpp_src.write_text("""\
// Base class with virtual method
struct Base {
    int base_field;
    virtual int get_value() { return base_field; }
    virtual ~Base() {}
};

// Derived with override
struct Derived : public Base {
    int derived_field;
    int get_value() override { return derived_field; }
};

// Struct with bitfields
struct Flags {
    unsigned int read : 1;
    unsigned int write : 1;
    unsigned int exec : 1;
    unsigned int reserved : 29;
};

// Struct with const and volatile fields
struct SensorData {
    const int config;
    volatile int status;
    int normal;
};

// Const global
const int MAGIC = 0xDEAD;

// Typedef chain
typedef int my_int;
typedef my_int my_int2;

// Anonymous struct typedef (C-style pattern)
typedef struct {
    int x;
    int y;
} Point;

// Enum with explicit values
enum Color { RED = 0, GREEN = 1, BLUE = 2 };

// Anonymous enum typedef
typedef enum { SMALL = 1, MEDIUM = 2, LARGE = 3 } SizeEnum;

// Reference parameters
extern "C" int add_ref(const int& a, const int& b) {
    return a + b;
}

// Rvalue reference parameter
extern "C" int consume_rval(int&& val) {
    return val;
}

// Pointer return
extern "C" int* get_ptr(int* p) {
    return p;
}

// Double pointer
extern "C" int** get_dptr(int** p) {
    return p;
}

// Array parameter-like
extern "C" int sum_arr(int arr[], int n) {
    int s = 0;
    for (int i = 0; i < n; i++) s += arr[i];
    return s;
}

// Function pointer parameter
extern "C" int apply_fn(int (*fn)(int), int v) {
    return fn(v);
}

// Use derived type to ensure it's in DWARF
extern "C" Derived* make_derived(int v) {
    static Derived d;
    d.derived_field = v;
    return &d;
}

// Use Flags to ensure bitfield struct is in DWARF
extern "C" Flags make_flags(int r, int w, int x) {
    Flags f;
    f.read = r;
    f.write = w;
    f.exec = x;
    f.reserved = 0;
    return f;
}

// Use typedef
extern "C" my_int2 convert(my_int2 v) { return v + 1; }

// Use Point (anonymous struct typedef)
extern "C" Point make_point(int x, int y) {
    Point p;
    p.x = x;
    p.y = y;
    return p;
}

// Use enum in signature
extern "C" Color get_color(int idx) { return (Color)idx; }

// Use SizeEnum
extern "C" SizeEnum get_size(int s) { return (SizeEnum)s; }

// Use SensorData
extern "C" SensorData make_sensor(int c, int s) {
    SensorData d = {c, s, 0};
    return d;
}
""")
        so_path = tmp_path / "libtest.so"
        result = subprocess.run(
            [_GPP, "-shared", "-fPIC", "-g", "-o", str(so_path), str(cpp_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_cpp_functions_extracted(self, cpp_lib: Path) -> None:
        """C++ functions with references and pointers should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        func_names = {f.name for f in snap.functions}
        assert "add_ref" in func_names
        assert "get_ptr" in func_names
        assert "sum_arr" in func_names
        assert "make_derived" in func_names
        assert "make_flags" in func_names
        assert "convert" in func_names

    def test_cpp_types_with_inheritance(self, cpp_lib: Path) -> None:
        """Struct with inheritance should be in snapshot types."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        type_names = {t.name for t in snap.types}
        assert "Base" in type_names or "Derived" in type_names

    def test_cpp_bitfield_struct(self, cpp_lib: Path) -> None:
        """Struct with bitfields should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        type_names = {t.name for t in snap.types}
        assert "Flags" in type_names
        flags_type = next(t for t in snap.types if t.name == "Flags")
        # Bitfields should have field entries
        assert len(flags_type.fields) >= 3
        # At least one should be a bitfield
        assert any(f.is_bitfield for f in flags_type.fields)

    def test_cpp_typedefs_extracted(self, cpp_lib: Path) -> None:
        """Typedef chains should be in snapshot."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        # At least one of our typedefs should be present
        assert "my_int" in snap.typedefs or "my_int2" in snap.typedefs

    def test_cpp_const_variable(self, cpp_lib: Path) -> None:
        """Const global variable should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        # MAGIC may or may not be exported depending on linker, but
        # the build_snapshot should not crash processing it
        assert snap is not None

    def test_version_and_language_profile(self, cpp_lib: Path) -> None:
        """build_snapshot_from_dwarf should accept version and language_profile."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
            version="2.0.0",
            language_profile="cpp",
        )

        assert snap.version == "2.0.0"
        assert snap.language_profile == "cpp"

    def test_rvalue_reference_param(self, cpp_lib: Path) -> None:
        """Rvalue reference parameters should be detected."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        func_names = {f.name for f in snap.functions}
        assert "consume_rval" in func_names

    def test_double_pointer_function(self, cpp_lib: Path) -> None:
        """Functions with double pointer params/return should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        func_names = {f.name for f in snap.functions}
        assert "get_dptr" in func_names

    def test_function_pointer_param(self, cpp_lib: Path) -> None:
        """Functions with function pointer params should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        func_names = {f.name for f in snap.functions}
        assert "apply_fn" in func_names

    def test_enum_extracted(self, cpp_lib: Path) -> None:
        """Enums used in function signatures should be in the snapshot."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        enum_names = {e.name for e in snap.enums}
        assert "Color" in enum_names
        color_enum = next(e for e in snap.enums if e.name == "Color")
        member_names = {m.name for m in color_enum.members}
        assert "RED" in member_names
        assert "GREEN" in member_names
        assert "BLUE" in member_names

    def test_anonymous_typedef_struct(self, cpp_lib: Path) -> None:
        """typedef struct { ... } Point should register under typedef name."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        type_names = {t.name for t in snap.types}
        # Point should be registered as a type (from anonymous struct typedef)
        assert "Point" in type_names

    def test_anonymous_typedef_enum(self, cpp_lib: Path) -> None:
        """typedef enum { ... } SizeEnum should register under typedef name."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        enum_names = {e.name for e in snap.enums}
        assert "SizeEnum" in enum_names
        size_enum = next(e for e in snap.enums if e.name == "SizeEnum")
        member_names = {m.name for m in size_enum.members}
        assert "SMALL" in member_names
        assert "LARGE" in member_names

    def test_volatile_const_fields(self, cpp_lib: Path) -> None:
        """Fields with const/volatile qualifiers should be extracted."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(cpp_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(cpp_lib)
        snap = build_snapshot_from_dwarf(
            cpp_lib, elf_meta, dwarf_meta, dwarf_adv,
        )

        type_names = {t.name for t in snap.types}
        assert "SensorData" in type_names
        sensor = next(t for t in snap.types if t.name == "SensorData")
        field_map = {f.name: f for f in sensor.fields}
        assert "config" in field_map
        assert "status" in field_map
        assert field_map["config"].is_const is True
        assert field_map["status"].is_volatile is True


# ── CLI tests via CliRunner (in-process for coverage) ────────────────────────

@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDwarfSnapshotCEnumsTypedefs:
    """C integration: enums, anonymous typedefs, const vars in pure C."""

    @pytest.fixture()
    def c_lib(self, tmp_path: Path) -> Path:
        c_src = tmp_path / "lib.c"
        c_src.write_text("""\
/* Anonymous struct typedef */
typedef struct {
    int x;
    int y;
} CPoint;

/* Named enum */
enum Direction { NORTH = 0, SOUTH = 1, EAST = 2, WEST = 3 };

/* Anonymous enum typedef */
typedef enum { OFF = 0, ON = 1 } Switch;

/* Typedef chain */
typedef int my_int_t;
typedef my_int_t my_int2_t;

/* Const exported variable */
const int VERSION_NUM = 42;

/* Exported variable */
int global_counter = 0;

CPoint make_cpoint(int x, int y) {
    CPoint p;
    p.x = x;
    p.y = y;
    return p;
}

enum Direction get_dir(int d) { return (enum Direction)d; }
Switch get_switch(int s) { return (Switch)s; }
my_int2_t add_typed(my_int2_t a, my_int2_t b) { return a + b; }
""")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        return so_path

    def test_c_enum_extraction(self, c_lib: Path) -> None:
        """Named C enums should be extracted with members."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        enum_names = {e.name for e in snap.enums}
        assert "Direction" in enum_names
        direction = next(e for e in snap.enums if e.name == "Direction")
        member_names = {m.name for m in direction.members}
        assert member_names == {"NORTH", "SOUTH", "EAST", "WEST"}

    def test_c_anonymous_typedef_struct(self, c_lib: Path) -> None:
        """C anonymous struct typedef should register type as CPoint."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        type_names = {t.name for t in snap.types}
        assert "CPoint" in type_names

    def test_c_anonymous_typedef_enum(self, c_lib: Path) -> None:
        """C anonymous enum typedef should register as Switch."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        enum_names = {e.name for e in snap.enums}
        assert "Switch" in enum_names

    def test_c_typedef_chains(self, c_lib: Path) -> None:
        """Typedef chains should be resolved."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        assert "my_int_t" in snap.typedefs or "my_int2_t" in snap.typedefs

    def test_c_exported_variable(self, c_lib: Path) -> None:
        """Exported C variables should appear in snapshot."""
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        var_names = {v.name for v in snap.variables}
        assert "global_counter" in var_names


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestCLIInProcess:
    """CLI tests using CliRunner for in-process coverage."""

    @pytest.fixture()
    def _debug_lib(self, tmp_path: Path) -> Path:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int foo(void) { return 0; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        return so_path

    def test_show_data_sources_via_runner(self, _debug_lib: Path) -> None:
        """--show-data-sources via CliRunner for in-process coverage."""
        from click.testing import CliRunner

        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["dump", str(_debug_lib), "--show-data-sources"])
        assert result.exit_code == 0
        assert "Data sources for" in result.output
        assert "L0 Binary metadata" in result.output
        assert "L1 Debug info" in result.output

    def test_dwarf_only_via_runner(self, _debug_lib: Path, tmp_path: Path) -> None:
        """--dwarf-only via CliRunner for in-process coverage."""
        from click.testing import CliRunner

        from abicheck.cli import main

        out = tmp_path / "snap.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "dump", str(_debug_lib), "--dwarf-only", "-o", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()

    def test_dump_no_headers_dwarf_mode(self, _debug_lib: Path) -> None:
        """dump without headers should use DWARF mode."""
        from click.testing import CliRunner

        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["dump", str(_debug_lib)])
        assert result.exit_code == 0
        # Should produce JSON output (not crash)
        assert '"library"' in result.output or '"functions"' in result.output

    def test_compare_dwarf_only(self, _debug_lib: Path) -> None:
        """compare --dwarf-only should work via CliRunner."""
        from click.testing import CliRunner

        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(_debug_lib), str(_debug_lib), "--dwarf-only",
        ])
        # Should succeed (comparing same lib to itself)
        assert result.exit_code == 0


# ── _print_data_sources direct call ──────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestPrintDataSourcesDirect:
    """Direct call to _print_data_sources for coverage."""

    def test_print_data_sources(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.cli import _print_data_sources

        _print_data_sources(so_path, has_headers=False)
        out = capsys.readouterr().out
        assert "Data sources for libtest.so" in out
        assert "L3 Build context:   not collected" in out

    def test_print_data_sources_with_headers(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.cli import _print_data_sources

        _print_data_sources(so_path, has_headers=True)
        out = capsys.readouterr().out
        assert "L2 Header AST:      available" in out
        assert "Headers mode (artifact + public header evidence)" in out

    def test_print_data_sources_with_build_source_pack(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        pack = BuildSourcePack.empty(tmp_path / "pack")
        pack.build_evidence = BuildEvidence(
            targets=[Target(id="target://libtest", name="libtest")],
            compile_units=[CompileUnit(id="cu://lib.c", source="lib.c")],
        )
        pack.write()

        from abicheck.cli import _print_data_sources

        _print_data_sources(so_path, has_headers=True, build_source_path=pack.root)
        out = capsys.readouterr().out
        assert "L3 Build context: present (1 compile units, 1 targets)" in out

    def test_print_data_sources_combines_build_info_and_sources(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        build_pack = BuildSourcePack.empty(tmp_path / "build-pack")
        build_pack.build_evidence = BuildEvidence(
            targets=[Target(id="target://libtest", name="libtest")],
            compile_units=[CompileUnit(id="cu://lib.c", source="lib.c")],
        )
        build_pack.write()
        source_pack = BuildSourcePack.empty(tmp_path / "source-pack")
        source_pack.source_abi = SourceAbiSurface(
            reachable_declarations=[
                SourceEntity(id="source://bar", kind="function", qualified_name="bar")
            ]
        )
        source_pack.source_graph = SourceGraphSummary(
            nodes=[GraphNode(id="file://lib.c", kind="file", label="lib.c")]
        )
        source_pack.write()

        from abicheck.cli import _print_data_sources

        _print_data_sources(
            so_path,
            has_headers=True,
            build_source_path=build_pack.root,
            sources_path=source_pack.root,
        )
        out = capsys.readouterr().out
        assert "L3 Build context: present (1 compile units, 1 targets)" in out
        assert "L4 Source ABI: present (1 declarations)" in out
        assert "L5 Source graph: present (1 nodes)" in out

    def test_print_data_sources_uses_fallback_pack_coverage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )
        full_pack = BuildSourcePack.empty(tmp_path / "full-pack")
        full_pack.build_evidence = BuildEvidence(
            targets=[Target(id="target://libtest", name="libtest")],
            compile_units=[CompileUnit(id="cu://lib.c", source="lib.c")],
        )
        full_pack.source_abi = SourceAbiSurface(
            reachable_declarations=[
                SourceEntity(id="source://bar", kind="function", qualified_name="bar")
            ]
        )
        full_pack.write()
        manifest_only = BuildSourcePack.empty(tmp_path / "manifest-only")
        manifest_only.write()

        from abicheck.cli import _print_data_sources

        _print_data_sources(
            so_path,
            has_headers=True,
            build_source_path=full_pack.root,
            sources_path=manifest_only.root,
        )
        out = capsys.readouterr().out
        assert "L4 Source ABI: present (1 declarations)" in out

    def test_print_data_sources_with_raw_build_source_input(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        c_src = tmp_path / "lib.c"
        c_src.write_text("int bar(void) { return 1; }\n")
        so_path = tmp_path / "libtest.so"
        subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True, check=True, timeout=30,
        )

        from abicheck.cli import _print_data_sources

        _print_data_sources(so_path, has_headers=True, build_source_path=tmp_path)
        captured = capsys.readouterr()
        assert "L3 Build context:   not collected" in captured.out
        assert "not collected in --show-data-sources" in captured.err


# ── Error handling tests ─────────────────────────────────────────────────────

class TestDwarfSnapshotErrorHandling:
    """Test error and edge-case handling."""

    def test_invalid_elf_file(self, tmp_path: Path) -> None:
        """build_snapshot_from_dwarf on invalid ELF should not crash."""
        fake_elf = tmp_path / "libfake.so"
        fake_elf.write_bytes(b"\x7fELF" + b"\x00" * 100)

        elf_meta = _elf_meta_with_symbols(["foo"])
        dwarf_meta = _dwarf_meta()
        dwarf_adv = _dwarf_adv()

        snap = build_snapshot_from_dwarf(
            fake_elf, elf_meta, dwarf_meta, dwarf_adv,
        )
        # Should return an empty but valid snapshot
        assert snap.elf_only_mode is False
        assert len(snap.functions) == 0

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """build_snapshot_from_dwarf on missing file should not crash."""
        missing = tmp_path / "nonexistent.so"

        elf_meta = _elf_meta_with_symbols(["foo"])
        dwarf_meta = _dwarf_meta()
        dwarf_adv = _dwarf_adv()

        snap = build_snapshot_from_dwarf(
            missing, elf_meta, dwarf_meta, dwarf_adv,
        )
        assert snap.elf_only_mode is False
        assert len(snap.functions) == 0

    def test_empty_elf_meta(self, tmp_path: Path) -> None:
        """build_snapshot_from_dwarf with no symbols in elf_meta."""
        fake_elf = tmp_path / "libfake.so"
        fake_elf.write_bytes(b"\x7fELF" + b"\x00" * 100)

        elf_meta = ElfMetadata(soname="libtest.so.1", symbols=[])
        dwarf_meta = _dwarf_meta()
        dwarf_adv = _dwarf_adv()

        snap = build_snapshot_from_dwarf(
            fake_elf, elf_meta, dwarf_meta, dwarf_adv,
        )
        assert snap is not None
        assert len(snap.functions) == 0
