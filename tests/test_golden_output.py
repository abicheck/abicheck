"""Golden regression tests for CLI 'compare' command output.

Compares the markdown output of abicheck compare against known-good
golden files stored in tests/golden/.

Usage:
  # Run golden tests:
  pytest tests/test_golden_output.py

  # Update golden files after intentional output changes:
  pytest tests/test_golden_output.py --update-goldens
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.reporter import to_markdown

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).parent
GOLDEN_DIR = TESTS_DIR / "golden"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(ver: str = "1.0", funcs=None, types=None, enums=None) -> AbiSnapshot:
    s = AbiSnapshot(library="libfoo.so", version=ver)
    s.functions = funcs or []
    s.types = types or []
    s.enums = enums or []
    return s


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC)


def _run_golden(
    case_id: str,
    old: AbiSnapshot,
    new: AbiSnapshot,
    update: bool,
) -> None:
    """Run one golden comparison: either update or verify."""
    golden_path = GOLDEN_DIR / f"{case_id}.md"
    result = compare(old, new)
    actual = to_markdown(result)

    if update:
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden_path.write_text(actual, encoding="utf-8")
        pytest.skip(f"Updated golden: {golden_path.name}")
        return

    if not golden_path.exists():
        pytest.fail(
            f"Golden file missing: {golden_path}\n"
            f"Run with --update-goldens to create it.\n"
            f"Current output:\n{actual}"
        )

    expected = golden_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"{case_id}.expected",
                tofile=f"{case_id}.actual",
                n=3,
            )
        )
        pytest.fail(f"Golden mismatch for {case_id}:\n{diff}")


# ---------------------------------------------------------------------------
# Golden test cases
# ---------------------------------------------------------------------------

@pytest.mark.golden
def test_golden_no_change(update_goldens: bool) -> None:
    """Identical snapshots → NO_CHANGE output is stable."""
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("no_change", old, new, update_goldens)


@pytest.mark.golden
def test_golden_func_removed(update_goldens: bool) -> None:
    """Public function removal → BREAKING output is stable."""
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")])
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("func_removed", old, new, update_goldens)


@pytest.mark.golden
def test_golden_struct_size_change(update_goldens: bool) -> None:
    """Struct with new field → BREAKING size-change output is stable."""
    old = _snap(ver="1.0", types=[RecordType(
        name="Point", kind="struct", size_bits=64,
        fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)],
    )])
    new = _snap(ver="2.0", types=[RecordType(
        name="Point", kind="struct", size_bits=96,
        fields=[TypeField("x", "int", 0), TypeField("y", "int", 32), TypeField("z", "int", 64)],
    )])
    _run_golden("struct_size_change", old, new, update_goldens)


@pytest.mark.golden
def test_golden_enum_change(update_goldens: bool) -> None:
    """Enum member value change → BREAKING output is stable."""
    old = _snap(ver="1.0", enums=[EnumType(
        name="Color",
        members=[EnumMember("RED", 0), EnumMember("GREEN", 1), EnumMember("BLUE", 2)],
    )])
    new = _snap(ver="2.0", enums=[EnumType(
        name="Color",
        members=[EnumMember("RED", 0), EnumMember("GREEN", 5), EnumMember("BLUE", 2)],
    )])
    _run_golden("enum_change", old, new, update_goldens)


@pytest.mark.golden
def test_golden_compatible_addition(update_goldens: bool) -> None:
    """New public function → COMPATIBLE output is stable."""
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")])
    _run_golden("compatible_addition", old, new, update_goldens)


@pytest.mark.golden
def test_golden_compatible_with_risk(update_goldens: bool) -> None:
    """New GLIBC version requirement → COMPATIBLE_WITH_RISK output is stable."""
    from abicheck.elf_metadata import ElfMetadata

    old = _snap(ver="1.0")
    old.elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.5"]})
    new = _snap(ver="2.0")
    new.elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]})
    _run_golden("compatible_with_risk", old, new, update_goldens)


@pytest.mark.golden
def test_golden_leaked_dependency_symbol(update_goldens: bool) -> None:
    """Leaked dependency symbol removed → COMPATIBLE_WITH_RISK output is stable."""
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType

    def _elf_sym(name: str, origin: str | None = None) -> ElfSymbol:
        return ElfSymbol(
            name=name,
            binding=SymbolBinding.WEAK,
            sym_type=SymbolType.FUNC,
            size=0,
            version="",
            is_default=True,
            visibility="default",
            origin_lib=origin,
        )

    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    old.elf = ElfMetadata(
        soname="libfoo.so.1",
        needed=["libstdc++.so.6"],
        symbols=[
            _elf_sym("_Z7computei"),
            _elf_sym("_ZNSt6thread8_M_startEv", "libstdc++.so.6"),
        ],
    )

    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    new.elf = ElfMetadata(
        soname="libfoo.so.1",
        needed=["libstdc++.so.6"],
        symbols=[
            _elf_sym("_Z7computei"),
            # _ZNSt6thread8_M_startEv removed in new version
        ],
    )

    # This fixture models a header + ELF comparison: 'compute' is a clean
    # header-derived PUBLIC declaration and ELF metadata supplies the leaked
    # dependency symbol. Mark the surface as header-parsed so the evidence tier
    # is HEADER_AWARE — without it, the presence of ELF metadata would
    # (correctly) downgrade a symbols-only snapshot to ELF_ONLY.
    old.from_headers = True
    new.from_headers = True

    _run_golden("leaked_dependency_symbol", old, new, update_goldens)
