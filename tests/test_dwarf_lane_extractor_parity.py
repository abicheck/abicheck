"""The DWARF-only lane records what the binary and its debug info say, no less.

Three defects, one class (``extraction.dwarf_lane_drops_a_recorded_fact``): a
DWARF-only dump dropped a fact the binary or its DWARF carries, and compare
read the gap as a change or as no change.

- An export whose DWARF DIE has another name (a ``.symver`` implementation, an
  IFUNC) had no record, so the other side's declaration read
  ``func_removed`` (``BREAKING``) although the symbol is still exported.
- ``Function.is_variadic`` was never set from ``DW_TAG_unspecified_parameters``,
  so adding or removing ``...`` read ``NO_CHANGE``.
- ``-funsigned-char``/``-fsigned-char`` in ``DW_AT_producer`` were not read, so
  a plain-``char`` signedness flip read ``NO_CHANGE``.

Unit tests check each rule against an independent oracle over a small domain;
the integration tests build real libraries with gcc and clang.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.toolchain_flags import (
    _diff_char_signedness,
    _diff_toolchain_flags,
)
from abicheck.extract.dwarf_export_gaps import undescribed_export_records
from abicheck.extract.export_symbol_identity import itanium_export_function
from abicheck.model import Function, Param, Variable, Visibility
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, ToolchainInfo

# The integration cells build ELF shared objects with DWARF (.symver, IFUNC,
# GNU version scripts) and read glibc-style DW_AT_producer: Linux only. A failing
# build on Linux stays a failure.
_ELF_HOST = pytest.mark.skipif(
    sys.platform != "linux", reason="builds ELF .so files with DWARF"
)

# --- export gaps (unit) ---------------------------------------------------------

_EXPORTS = (
    "plain_c",  # an ordinary C function
    "_Z3fooi",  # an ordinary C++ function
    "_ZN1WC1Ev",  # complete-object constructor (DWARF names the C2 variant)
    "_ZN1WD1Ev",  # complete-object destructor
    "_ZTV1W",  # vtable: class metadata, not a declaration
    "_ZTI1W",  # typeinfo
    "_ZGVZ3barvE1x",  # guard variable
    "_ZZ3barvE1x",  # function-local static
)


def _fn(mangled: str) -> Function:
    return Function(name=mangled, mangled=mangled, return_type="int")


def _expected_gap(name: str, described: set[str]) -> bool:
    """Independent statement of the rule: a declaration-shaped export that no
    described name covers, where a ctor/dtor's family is its C1/D1 spelling."""
    if name.startswith(("_ZT", "_ZGV", "_ZZ")):
        return False
    families = {d.replace("C2E", "C1E").replace("D2E", "D1E") for d in described}
    return name.replace("C2E", "C1E").replace("D2E", "D1E") not in families


_DESCRIBED_POOL = ("plain_c", "_Z3fooi", "_ZN1WC2Ev", "_ZN1WD2Ev")


@pytest.mark.parametrize(
    "described",
    [
        set(combo)
        for r in range(len(_DESCRIBED_POOL) + 1)
        for combo in itertools.combinations(_DESCRIBED_POOL, r)
    ],
)
def test_only_undescribed_declaration_exports_get_a_record(described: set[str]) -> None:
    new_f, _ = undescribed_export_records([_fn(d) for d in described], [], _EXPORTS, ())
    got = {f.mangled for f in new_f}
    assert got == {n for n in _EXPORTS if _expected_gap(n, described)}
    for f in new_f:
        assert f.visibility is Visibility.ELF_ONLY and f.return_type == "?"


def test_variables_follow_the_same_rule() -> None:
    _, new_v = undescribed_export_records(
        [],
        [Variable(name="g_known", mangled="g_known", type="int")],
        (),
        ("g_known", "g_alias", "_ZTV1W", "_ZZ3barvE1x", "_ZN2ns1vE"),
    )
    assert {v.mangled for v in new_v} == {"g_alias", "_ZN2ns1vE"}


# --- an export-only record has no signature to compare (unit) --------------------


_DESCRIBED_SHAPES = [
    Function(
        name="g", mangled="g", return_type="int", params=[Param(name="a", type="int")]
    ),
    Function(name="g", mangled="g", return_type="void"),
    Function(
        name="S::g",
        mangled="_ZN1S1gEv",
        return_type="int",
        is_virtual=True,
        is_const=True,
        is_noexcept=True,
    ),
    Function(name="g", mangled="g", return_type="char*", is_variadic=True),
]


@pytest.mark.parametrize("described", _DESCRIBED_SHAPES, ids=lambda f: f.mangled)
@pytest.mark.parametrize("export_only_side", ["old", "new"])
def test_pairing_with_an_export_only_record_reports_no_signature_change(
    described: Function, export_only_side: str
) -> None:
    from abicheck.diff_symbols import _check_function_signature

    stub = itanium_export_function(described.mangled)
    old, new = (stub, described) if export_only_side == "old" else (described, stub)
    kinds = {c.kind for c in _check_function_signature(described.mangled, old, new)}
    signature_kinds = {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.FUNC_VIRTUAL_REMOVED,
        ChangeKind.FUNC_VIRTUAL_ADDED,
        ChangeKind.FUNC_CV_CHANGED,
        ChangeKind.FUNC_NOEXCEPT_REMOVED,
        ChangeKind.FUNC_NOEXCEPT_ADDED,
        ChangeKind.FUNC_VARIADIC_ADDED,
        ChangeKind.FUNC_VARIADIC_REMOVED,
    }
    assert not kinds & signature_kinds, kinds


@pytest.mark.parametrize(
    "visibility", [Visibility.PUBLIC, Visibility.ELF_ONLY], ids=lambda v: v.value
)
@pytest.mark.parametrize("old_params", [0, 1])
def test_two_described_functions_still_compare_their_signatures(
    visibility: Visibility, old_params: int
) -> None:
    # ELF_ONLY with a parsed signature (a header-AST declaration found in
    # .symtab only, or a legacy record) is described, not a stub: only the
    # stub's own shape ("?" return, no parameters) skips the comparison.
    from abicheck.diff_symbols import _check_function_signature

    old = Function(
        name="g",
        mangled="g",
        return_type="int",
        params=[Param(name="a", type="int")] * old_params,
        visibility=visibility,
    )
    new = Function(name="g", mangled="g", return_type="long", visibility=visibility)
    kinds = {c.kind for c in _check_function_signature("g", old, new)}
    assert ChangeKind.FUNC_RETURN_CHANGED in kinds


# --- plain-char signedness (unit) ------------------------------------------------

_FLAG_SETS = {
    "none": set(),
    "signed": {"-fsigned-char"},
    "unsigned": {"-funsigned-char"},
    "no-signed": {"-fno-signed-char"},
    "no-unsigned": {"-fno-unsigned-char"},
    "conflict": {"-fsigned-char", "-funsigned-char"},
}
_ARCHES = ("x64", "aarch64", "mystery")


def _meta(flags: set[str], arch: str) -> AdvancedDwarfMetadata:
    return AdvancedDwarfMetadata(
        has_dwarf=True,
        target_arch=arch,
        toolchain=ToolchainInfo(
            producer_string="GNU C17", abi_flags=set(flags) | {"-m64"}
        ),
    )


def _oracle_signed(flags: str, arch: str) -> bool | None:
    explicit = {
        "signed": True,
        "no-unsigned": True,
        "unsigned": False,
        "no-signed": False,
        "conflict": None,
    }
    if flags in explicit:
        return explicit[flags]
    return {"x64": True, "aarch64": False}.get(arch)


@pytest.mark.parametrize(
    "old,new",
    list(itertools.product(itertools.product(_FLAG_SETS, _ARCHES), repeat=2)),
    ids=str,
)
def test_char_signedness_flip_matches_an_independent_table(
    old: tuple[str, str], new: tuple[str, str]
) -> None:
    o, n = _meta(_FLAG_SETS[old[0]], old[1]), _meta(_FLAG_SETS[new[0]], new[1])
    want_o, want_n = _oracle_signed(*old), _oracle_signed(*new)
    flips = want_o is not None and want_n is not None and want_o != want_n
    got = _diff_char_signedness(o, n)
    assert bool(got) is flips
    # Never double-reported as generic flag drift.
    assert all("signed-char" not in str(r) for r in _diff_toolchain_flags(o, n))


# --- the DWARF lane's hook (unit) --------------------------------------------------


@pytest.mark.parametrize("table", ["absent", "empty", "read"])
def test_the_dwarf_lane_adds_records_only_from_a_read_symbol_table(table: str) -> None:
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
    from abicheck.model import AbiSnapshot
    from abicheck.workflows.dump.elf_fallback import _add_undescribed_exports

    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[Function(name="f", mangled="f", return_type="int")],
        variables=[],
    )
    meta = {
        "absent": None,
        "empty": ElfMetadata(),
        "read": ElfMetadata(
            symbols=[
                ElfSymbol(name="f", sym_type=SymbolType.FUNC, size=8),
                ElfSymbol(name="g", sym_type=SymbolType.FUNC, size=8),
                ElfSymbol(name="v", sym_type=SymbolType.OBJECT, size=4),
            ]
        ),
    }[table]
    _add_undescribed_exports(snap, meta, Path("libx.so"))
    funcs = {fn.name: fn for fn in snap.declarations.functions}
    names_v = {v.name for v in snap.declarations.variables}
    if table == "read":
        # g and v are exported and undescribed; f keeps its DWARF record.
        assert set(funcs) == {"f", "g"} and names_v == {"v"}
        assert funcs["g"].visibility is Visibility.ELF_ONLY
        assert funcs["f"].return_type == "int"
    else:
        # Nothing proves an export exists: the snapshot is left as DWARF built it.
        assert set(funcs) == {"f"} and names_v == set()


# --- the other flag families, through the public entry (unit) --------------------

_VEC_SETS = (
    frozenset(),
    frozenset({"-mveclibabi=svml"}),
    frozenset({"-fveclib=libmvec"}),
)
_WCHAR_SETS = (
    frozenset(),
    frozenset({"-fshort-wchar"}),
    frozenset({"-fno-short-wchar"}),
)


_ABI_SETS = (frozenset({"-m64"}), frozenset({"-m64", "-mabi=ms"}))


def _flag_meta(  # noqa: ANN202
    vec: frozenset[str],
    wchar: frozenset[str],
    abi: frozenset[str] = _ABI_SETS[0],
    *,
    dwarf: bool = True,
):
    return AdvancedDwarfMetadata(
        has_dwarf=dwarf,
        target_arch="x64",
        toolchain=ToolchainInfo(
            producer_string="GNU C17",
            abi_flags=set(abi),
            vector_abi_flags=set(vec),
            wchar_flags=set(wchar),
        ),
    )


@pytest.mark.parametrize(
    "old,new",
    list(
        itertools.product(
            itertools.product(_VEC_SETS, _WCHAR_SETS, _ABI_SETS), repeat=2
        )
    ),
    ids=str,
)
def test_flag_family_changes_match_the_flag_sets(
    old: tuple[frozenset[str], frozenset[str], frozenset[str]],
    new: tuple[frozenset[str], frozenset[str], frozenset[str]],
) -> None:
    from abicheck.compare.toolchain_flags import (
        advanced_dwarf_kind_map,
        toolchain_flag_changes,
    )

    got = toolchain_flag_changes(_flag_meta(*old), _flag_meta(*new))
    kinds = [r[0] for r in got]
    # Independent oracle: vector ABI changes iff the flag sets differ; the wchar
    # model changes iff exactly one side passes -fshort-wchar.
    assert ("vector_abi_changed" in kinds) is (old[0] != new[0])
    short = ("-fshort-wchar" in old[1], "-fshort-wchar" in new[1])
    assert ("wchar_model_changed" in kinds) is (short[0] != short[1])
    assert ("toolchain_flag_drift" in kinds) is (old[2] != new[2])
    assert len(kinds) == len(set(kinds))
    # The detector maps every kind it can be handed.
    assert set(kinds) <= set(advanced_dwarf_kind_map())
    for kind, _sym, desc, old_v, new_v in got:
        if kind == "vector_abi_changed":
            assert (old_v or "") == ",".join(sorted(old[0]))
            assert (new_v or "") == ",".join(sorted(new[0]))
            for f in old[0] ^ new[0]:
                assert f in desc


@pytest.mark.parametrize("dwarf_side", ["old", "new"])
def test_no_flag_change_without_dwarf_on_both_sides(dwarf_side: str) -> None:
    from abicheck.compare.toolchain_flags import toolchain_flag_changes

    a = _flag_meta(_VEC_SETS[1], _WCHAR_SETS[1], dwarf=dwarf_side == "old")
    b = _flag_meta(_VEC_SETS[2], _WCHAR_SETS[0], dwarf=dwarf_side == "new")
    assert toolchain_flag_changes(a, b) == []
    assert {
        r[0]
        for r in toolchain_flag_changes(
            _flag_meta(_VEC_SETS[1], _WCHAR_SETS[1]),
            _flag_meta(_VEC_SETS[2], _WCHAR_SETS[0]),
        )
    } == {"vector_abi_changed", "wchar_model_changed"}


# --- real builds ------------------------------------------------------------------


def _compilers(lang: str) -> list[str]:
    names = ("gcc", "clang") if lang == "c" else ("g++", "clang++")
    return [c for c in names if shutil.which(c)]


def _build(tmp: Path, name: str, src: str, cc: str, *flags: str) -> Path:
    d = tmp / name
    d.mkdir()
    ext = "c" if cc in ("gcc", "clang") else "cpp"
    (d / f"s.{ext}").write_text(src, encoding="utf-8")
    so = d / "libx.so"
    cmd = [
        cc,
        "-g",
        "-O0",
        "-shared",
        "-fPIC",
        *flags,
        "-o",
        str(so),
        str(d / f"s.{ext}"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return so


def _compare(old: Path, new: Path):  # noqa: ANN202 - DiffResult
    from abicheck.dumper import dump

    return compare(dump(old, [], version="1"), dump(new, [], version="2"))


_BASE = "int f(int a) { return a; }\nint g(int a) { return a + 1; }\n"
_IFUNC = (
    "int f(int a) { return a; }\n"
    "static int g_impl(int a) { return a + 1; }\n"
    "static void *g_resolve(void) { return (void *)g_impl; }\n"
    'int g(int) __attribute__((ifunc("g_resolve")));\n'
)
_SYMVER = (
    "int f(int a) { return a; }\n"
    "int g_v1(int a) { return a + 1; }\n"
    "int g_v2(int a) { return a + 1; }\n"
    '__asm__(".symver g_v1, g@LIBX_1.0");\n'
    '__asm__(".symver g_v2, g@@LIBX_1.1");\n'
)
_MAP_V10 = "LIBX_1.0 { global: *; };\n"
_MAP_V11 = (
    "LIBX_1.0 { global: *; local: g_v1; g_v2; };\nLIBX_1.1 { global: g; } LIBX_1.0;\n"
)
_REMOVED = "int f(int a) { return a; }\n"


def _map(tmp: Path, name: str, text: str) -> str:
    p = tmp / f"{name}.map"
    p.write_text(text, encoding="utf-8")
    return f"-Wl,--version-script={p}"


@pytest.mark.integration
@_ELF_HOST
@pytest.mark.parametrize("cc", ["gcc", "clang"])
@pytest.mark.parametrize(
    "shape,src,new_map,still_exported",
    [
        ("ifunc", _IFUNC, None, True),
        ("symver-alias", _SYMVER, _MAP_V11, True),
        ("really-removed", _REMOVED, None, False),
    ],
)
def test_an_export_whose_die_has_another_name_is_not_removed(
    tmp_path: Path,
    cc: str,
    shape: str,
    src: str,
    new_map: str | None,
    still_exported: bool,
) -> None:
    if shutil.which(cc) is None:
        pytest.skip(f"{cc} is not installed")
    old_flags = [_map(tmp_path, "old", _MAP_V10)] if new_map else []
    new_flags = [_map(tmp_path, "new", new_map)] if new_map else []
    old = _build(tmp_path, "old", _BASE, cc, *old_flags)
    new = _build(tmp_path, "new", src, cc, *new_flags)
    # Reversed, an IFUNC that becomes a plain function is just as compatible; a
    # reversed symver pair drops version LIBX_1.1, a real (other) break.
    pairs = [(old, new), (new, old)] if shape == "ifunc" else [(old, new)]
    for a, b in pairs:
        result = _compare(a, b)
        kinds = {c.kind for c in result.changes}
        removed = kinds & {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY}
        assert bool(removed) is (not still_exported), (
            shape,
            sorted(k.value for k in kinds),
        )
        assert (result.verdict == Verdict.BREAKING) is (not still_exported)
        assert ChangeKind.FUNC_PARAMS_CHANGED not in kinds


@pytest.mark.integration
@_ELF_HOST
@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_variadic_arity_is_read_from_dwarf(tmp_path: Path, cc: str) -> None:
    if shutil.which(cc) is None:
        pytest.skip(f"{cc} is not installed")
    old = _build(
        tmp_path,
        "old",
        "int vs(int n) { return n; }\nint vf(int n, ...) { return n; }\nint k(int n) { return n; }\n",
        cc,
    )
    new = _build(
        tmp_path,
        "new",
        "int vs(int n, ...) { return n; }\nint vf(int n) { return n; }\nint k(int n) { return n; }\n",
        cc,
    )
    by_kind: dict[ChangeKind, set[str]] = {}
    for c in _compare(old, new).changes:
        by_kind.setdefault(c.kind, set()).add(c.symbol)
    assert by_kind.get(ChangeKind.FUNC_VARIADIC_ADDED) == {"vs"}
    assert by_kind.get(ChangeKind.FUNC_VARIADIC_REMOVED) == {"vf"}
    # Negative control: an unchanged fixed-arity function reports nothing.
    assert all("k" not in syms for syms in by_kind.values())


@pytest.mark.integration
@_ELF_HOST
@pytest.mark.parametrize(
    "old_flag,new_flag,flips",
    [
        ("", "-funsigned-char", True),
        ("-fsigned-char", "-funsigned-char", True),
        ("-funsigned-char", "", True),
        ("", "-fsigned-char", False),  # x86-64's default is signed
    ],
)
def test_a_char_signedness_rebuild_is_named(
    tmp_path: Path, old_flag: str, new_flag: str, flips: bool
) -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc is not installed")
    import platform

    if platform.machine() not in ("x86_64", "AMD64"):
        pytest.skip("the expected default (signed char) is x86-64's")
    src = "int cf(char c) { return c; }\n"
    old = _build(tmp_path, "old", src, "gcc", *([old_flag] if old_flag else []))
    new = _build(tmp_path, "new", src, "gcc", *([new_flag] if new_flag else []))
    result = _compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert (ChangeKind.CHAR_SIGNEDNESS_CHANGED in kinds) is flips
    assert ChangeKind.TOOLCHAIN_FLAG_DRIFT not in kinds


@pytest.mark.integration
@_ELF_HOST
@pytest.mark.known_gap_canary
def test_clang_char_signedness_flip_is_still_unread(tmp_path: Path) -> None:
    """Canary for known-gaps "A clang-built plain-char signedness flip reads
    NO_CHANGE": clang records no switches, and the char base type's encoding is
    not extracted yet. When this fails the gap moved: archive the entry."""
    if shutil.which("clang") is None:
        pytest.skip("clang is not installed")
    src = "int cf(char c) { return c; }\n"
    old = _build(tmp_path, "old", src, "clang")
    new = _build(tmp_path, "new", src, "clang", "-funsigned-char")
    kinds = {c.kind for c in _compare(old, new).changes}
    assert ChangeKind.CHAR_SIGNEDNESS_CHANGED not in kinds
