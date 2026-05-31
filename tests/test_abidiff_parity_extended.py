"""Extended abidiff parity tests — additional C/C++ scenarios.

Extends test_abidiff_parity.py with more complex scenarios:
- Multiple function removals
- Variable changes
- Enum member additions
- Noexcept changes
- Base class / vtable changes
- Combined changes (multiple breaks)
- Pure additions (new symbols only)

Uses the same test infrastructure as the original parity tests.
Requires: abidiff (libabigail-tools), gcc/g++, castxml.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
import warnings
from pathlib import Path

import pytest

# ── Extended case tuples ─────────────────────────────────────────────────
# (name, src_v1, src_v2, hdr_v1, hdr_v2, lang, abicheck_exp, abidiff_exp, category)

EXTENDED_CASES: list[tuple[str, str, str, str | None, str | None, str, str, str, str]] = [
    # Multiple symbols removed at once
    (
        "multi_fn_removed",
        "int a(void) { return 1; }\nint b(void) { return 2; }\nint c(void) { return 3; }",
        "int a(void) { return 1; }",
        "int a(void);\nint b(void);\nint c(void);",
        "int a(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # Variable removed (global data symbol)
    (
        "var_removed",
        "int api_version = 1;\nint get_version(void) { return api_version; }",
        "int get_version(void) { return 1; }",
        "extern int api_version;\nint get_version(void);",
        "int get_version(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # Variable type widened (int → long)
    (
        "var_type_widened",
        "int counter = 0;\nvoid inc(void) { counter++; }",
        "long counter = 0;\nvoid inc(void) { counter++; }",
        "extern int counter;\nvoid inc(void);",
        "extern long counter;\nvoid inc(void);",
        "c", "BREAKING", "COMPATIBLE", "correct",
    ),
    # Pure addition: only new symbols
    (
        "pure_addition",
        "int existing(void) { return 1; }",
        "int existing(void) { return 1; }\nint brand_new(void) { return 42; }",
        "int existing(void);",
        "int existing(void);\nint brand_new(void);",
        "c", "COMPATIBLE", "COMPATIBLE", "parity",
    ),
    # Function parameter count changed
    (
        "param_count_changed",
        "void process(int x) {}",
        "void process(int x, int y) {}",
        "void process(int x);",
        "void process(int x, int y);",
        "c", "BREAKING", "COMPATIBLE", "correct",
    ),
    # Enum member added: abidiff sees NO_CHANGE at ELF level (enum values
    # are compile-time constants, not in .dynsym). abicheck with headers
    # detects the addition → COMPATIBLE. This is "correct" not "parity".
    (
        "enum_member_added",
        "typedef enum { OK=0, ERR=1 } Status;\n"
        "Status get_status(void) { return OK; }",
        "typedef enum { OK=0, ERR=1, WARN=2 } Status;\n"
        "Status get_status(void) { return OK; }",
        "typedef enum { OK=0, ERR=1 } Status;\nStatus get_status(void);",
        "typedef enum { OK=0, ERR=1, WARN=2 } Status;\nStatus get_status(void);",
        "c", "COMPATIBLE", "NO_CHANGE", "correct",
    ),
    # C++ virtual destructor added (vtable change)
    (
        "virtual_dtor_added",
        "class Obj {\npublic:\n  int val() const { return 42; }\n  ~Obj() {}\n};\n"
        "Obj* make() { return new Obj(); }",
        "class Obj {\npublic:\n  int val() const { return 42; }\n  virtual ~Obj() {}\n};\n"
        "Obj* make() { return new Obj(); }",
        "class Obj {\npublic:\n  int val() const;\n  ~Obj();\n};\nObj* make();",
        "class Obj {\npublic:\n  int val() const;\n  virtual ~Obj();\n};\nObj* make();",
        "cpp", "BREAKING", "BREAKING", "parity",
    ),
    # ELF-only no change with multiple symbols
    (
        "elf_only_multi_no_change",
        "int f1(void) { return 1; }\nint f2(void) { return 2; }\n"
        "int f3(void) { return 3; }",
        "int f1(void) { return 1; }\nint f2(void) { return 2; }\n"
        "int f3(void) { return 3; }",
        None, None,
        "c", "NO_CHANGE", "NO_CHANGE", "parity",
    ),
]

_CONFIRMED = [c for c in EXTENDED_CASES if c[8] == "parity"]
_CORRECT = [c for c in EXTENDED_CASES if c[8] == "correct"]


# ── Helpers (reuse from test_abidiff_parity.py) ──────────────────────────

def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(src: str, out: Path, lang: str) -> None:
    ext = ".c" if lang == "c" else ".cpp"
    src_file = out.with_suffix(ext)
    src_file.write_text(textwrap.dedent(src).strip(), encoding="utf-8")
    compiler = "gcc" if lang == "c" else "g++"
    cmd = [compiler, "-shared", "-fPIC", "-g", "-fvisibility=default",
           "-o", str(out), str(src_file)]
    if lang == "cpp":
        cmd.insert(1, "-std=c++17")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"Compilation failed: {r.stderr[:200]}")


def _run_abicheck(old, new, hdr_v1, hdr_v2, lang, tmp_path):
    from abicheck.checker import compare
    from abicheck.dumper import dump

    compiler = "cc" if lang == "c" else "c++"
    headers_v1 = []
    if hdr_v1 is not None:
        h1 = tmp_path / f"{old.stem}_hdr.h"
        h1.write_text(textwrap.dedent(hdr_v1).strip(), encoding="utf-8")
        headers_v1 = [h1]

    headers_v2 = []
    if hdr_v2 is not None:
        h2 = tmp_path / f"{new.stem}_hdr.h"
        h2.write_text(textwrap.dedent(hdr_v2).strip(), encoding="utf-8")
        headers_v2 = [h2]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old_snap = dump(old, headers=headers_v1, version="v1", compiler=compiler)
        new_snap = dump(new, headers=headers_v2, version="v2", compiler=compiler)

    # Parity baseline is abidiff without --headers-dir (unscoped); run abicheck
    # unscoped too (surface scoping is default-on since ADR-024 Phase 5).
    result = compare(old_snap, new_snap, scope_to_public_surface=False)
    return result.verdict.value


def _run_abidiff(old, new):
    r = subprocess.run(
        ["abidiff", "--no-show-locs", str(old), str(new)],
        capture_output=True, text=True, timeout=30,
    )
    code = r.returncode
    if code == 0:
        return "NO_CHANGE"
    # Check BREAKING (bit 3) before ERROR (bit 0) — a breaking result
    # with an error is still primarily a breaking result.
    if code & 8:
        return "BREAKING"
    if code & 4:
        return "COMPATIBLE"
    if code & 1:
        return "ERROR"
    return "NO_CHANGE"


def _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path):
    _require_tool("abidiff")
    _require_tool("gcc" if lang == "c" else "g++")
    if hdr_v1 is not None or hdr_v2 is not None:
        _require_tool("castxml")

    v1 = tmp_path / f"lib{name}_v1.so"
    v2 = tmp_path / f"lib{name}_v2.so"
    _compile_so(src_v1, v1, lang)
    _compile_so(src_v2, v2, lang)

    ac = _run_abicheck(v1, v2, hdr_v1, hdr_v2, lang, tmp_path)
    ab = _run_abidiff(v1, v2)
    return ac, ab


# ── Tests ─────────────────────────────────────────────────────────────────

@pytest.mark.libabigail
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abidiff_exp,_",
    _CONFIRMED, ids=[c[0] for c in _CONFIRMED],
)
def test_extended_parity(
    name, src_v1, src_v2, hdr_v1, hdr_v2, lang,
    abicheck_exp, abidiff_exp, _, tmp_path,
):
    """Extended scenarios where both tools must agree."""
    ac, ab = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)
    assert ac == abicheck_exp, f"abicheck: expected {abicheck_exp}, got {ac}"
    assert ab == abidiff_exp, f"abidiff:  expected {abidiff_exp}, got {ab}"
    assert ac == ab, f"PARITY BROKEN: abicheck={ac}, abidiff={ab}"


@pytest.mark.libabigail
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abidiff_exp,_",
    _CORRECT, ids=[c[0] for c in _CORRECT],
)
def test_extended_abicheck_correct(
    name, src_v1, src_v2, hdr_v1, hdr_v2, lang,
    abicheck_exp, abidiff_exp, _, tmp_path,
):
    """abicheck detects the break; abidiff is conservative."""
    ac, ab = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)
    assert ac == abicheck_exp, f"abicheck: expected {abicheck_exp}, got {ac}"
    assert ab == abidiff_exp, f"abidiff:  expected {abidiff_exp}, got {ab}"
    if ac == ab:
        pytest.fail(
            f"Full parity achieved on '{name}' (both={ac}). "
            "Move this case to _CONFIRMED."
        )
