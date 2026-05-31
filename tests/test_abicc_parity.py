# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""ABICC parity tests.

Verifies that abicheck and abi-compliance-checker agree on ABI verdict
for canonical C/C++ library change scenarios. Tests are compiled locally
(requires gcc/g++, castxml) and compared with both tools.

Three test classes mirror the libabigail parity structure:
- test_confirmed_parity: both tools agree on verdict (full parity).
- test_abicheck_correct: abicheck detects the break; ABICC misses it.
- test_known_divergence: intentional stable divergences.
- test_risk: potentially breaking — abicheck emits API_BREAK/COMPATIBLE; ABICC may vary.

Requires: abi-compliance-checker, gcc/g++, castxml.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Cases:
#   (name, src_v1, src_v2, hdr_v1, hdr_v2, lang,
#    abicheck_expected, abicc_expected, category)
#
# category values:
#   "parity"    -> both tools must agree (full parity)
#   "correct"   -> abicheck is authoritative; ABICC misses it
#   "divergence"-> intentional stable divergence
# ---------------------------------------------------------------------------
PARITY_CASES: list[tuple[str, str, str, str | None, str | None, str, str, str, str]] = [
    (
        "fn_removed",
        "int add(int a, int b) { return a + b; }\nint sub(int a, int b) { return a - b; }",
        "int add(int a, int b) { return a + b; }",
        "int add(int a, int b);\nint sub(int a, int b);",
        "int add(int a, int b);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    (
        "fn_added",
        "int add(int a, int b) { return a + b; }",
        "int add(int a, int b) { return a + b; }\nint mul(int a, int b) { return a*b; }",
        "int add(int a, int b);",
        "int add(int a, int b);\nint mul(int a, int b);",
        "c", "COMPATIBLE", "COMPATIBLE", "parity",
    ),
    (
        "no_change",
        "int add(int a, int b) { return a + b; }",
        "int add(int a, int b) { return a + b; }",
        "int add(int a, int b);",
        "int add(int a, int b);",
        "c", "NO_CHANGE", "NO_CHANGE", "parity",
    ),
    (
        "return_type",
        "int  get_val(void) { return 42; }",
        "long get_val(void) { return 42; }",
        "int  get_val(void);",
        "long get_val(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    (
        "param_type",
        "void set_val(int  x) { (void)x; }",
        "void set_val(long x) { (void)x; }",
        "void set_val(int  x);",
        "void set_val(long x);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # enum value change: ABICC detects with headers
    (
        "enum_value",
        "typedef enum { RED=0, GREEN=1, BLUE=2 } Color;\n"
        "Color get_color(void) { return RED; }",
        "typedef enum { RED=0, GREEN=10, BLUE=2 } Color;\n"
        "Color get_color(void) { return RED; }",
        "typedef enum { RED=0, GREEN=1, BLUE=2 } Color;\nColor get_color(void);",
        "typedef enum { RED=0, GREEN=10, BLUE=2 } Color;\nColor get_color(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # visibility change
    (
        "visibility_hidden",
        '__attribute__((visibility("default"))) int helper() { return 1; }\n'
        '__attribute__((visibility("default"))) int api()    { return helper(); }',
        '__attribute__((visibility("hidden")))  int helper() { return 1; }\n'
        '__attribute__((visibility("default"))) int api()    { return helper(); }',
        '__attribute__((visibility("default"))) int helper();\n'
        '__attribute__((visibility("default"))) int api();',
        '__attribute__((visibility("hidden")))  int helper();\n'
        '__attribute__((visibility("default"))) int api();',
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── abicheck correct: vtable_reorder — ABICC misses in XML descriptor mode ──
    # ABICC without abi-dumper doesn't detect vtable reordering from headers+libs;
    # it reports NO_CHANGE (all zeros).  abicheck+castxml detects it as BREAKING.
    (
        "vtable_reorder",
        "struct Base {\n"
        "  virtual int foo() { return 1; }\n"
        "  virtual int bar() { return 2; }\n"
        "  virtual ~Base() {}\n"
        "};\nBase* make() { return new Base(); }",
        "struct Base {\n"
        "  virtual int bar() { return 2; }\n"
        "  virtual int foo() { return 1; }\n"
        "  virtual ~Base() {}\n"
        "};\nBase* make() { return new Base(); }",
        "struct Base { virtual int foo(); virtual int bar(); virtual ~Base(); };\n"
        "Base* make();",
        "struct Base { virtual int bar(); virtual int foo(); virtual ~Base(); };\n"
        "Base* make();",
        "cpp", "BREAKING", "NO_CHANGE", "correct",
    ),
    # ── abicheck correct: struct_size — ABICC misses in XML descriptor mode ──
    # ABICC in XML descriptor mode reports COMPATIBLE (sees field additions but
    # doesn't flag as breaking). abicheck correctly detects the layout change.
    (
        "struct_size",
        "typedef struct { int x; } Point;\n"
        "Point make_point(int x) { Point p = {x}; return p; }",
        "typedef struct { int x; int y; } Point;\n"
        "Point make_point(int x) { Point p = {x, 0}; return p; }",
        "typedef struct { int x; } Point;\nPoint make_point(int x);",
        "typedef struct { int x; int y; } Point;\nPoint make_point(int x);",
        "c", "BREAKING", "COMPATIBLE", "correct",
    ),
    # ── parity: multiple functions removed ──
    (
        "multi_fn_removed",
        "int a(void) { return 1; }\nint b(void) { return 2; }\nint c(void) { return 3; }",
        "int a(void) { return 1; }",
        "int a(void);\nint b(void);\nint c(void);",
        "int a(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── parity: multiple functions added ──
    (
        "multi_fn_added",
        "int a(void) { return 1; }",
        "int a(void) { return 1; }\nint b(void) { return 2; }\nint c(void) { return 3; }",
        "int a(void);",
        "int a(void);\nint b(void);\nint c(void);",
        "c", "COMPATIBLE", "COMPATIBLE", "parity",
    ),
    # ── parity: enum member removed ──
    (
        "enum_member_removed",
        "typedef enum { A=0, B=1, C=2 } E;\nE get_e(void) { return A; }",
        "typedef enum { A=0, C=2 } E;\nE get_e(void) { return A; }",
        "typedef enum { A=0, B=1, C=2 } E;\nE get_e(void);",
        "typedef enum { A=0, C=2 } E;\nE get_e(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── global variable removed — parity confirmed ──
    # abicheck now agrees with ABICC: removing exported global variable is BREAKING.
    # Fixed in PR #94: castxml C-mode now uses -x c -std=gnu11 to avoid the
    # -std=gnu++17 injection that prevented Variable elements from appearing in the AST.
    (
        "var_removed",
        "int api_version = 1;\nint get_version(void) { return api_version; }",
        "int get_version(void) { return 2; }",
        "extern int api_version;\nint get_version(void);",
        "int get_version(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── issue#128: non-trivial destructor changes calling convention (x64 SysV ABI) ──
    # Adding a user-defined destructor to a struct makes it non-trivial under the
    # Itanium C++ ABI.  On x64 System V ABI, non-trivial structs cannot be returned
    # in SSE registers and must be passed via a hidden pointer instead — an
    # ABI-breaking change.  abicheck now detects this via DWARF value-ABI trait
    # analysis (VALUE_ABI_TRAIT_CHANGED).  ABICC 2.3 still misses it (NO_CHANGE).
    # Recorded as correct (abicheck better than ABICC).
    # See: https://github.com/lvc/abi-compliance-checker/issues/128
    (
        "nontrivial_dtor_calling_convention",
        "struct v { float a; float b; };\n"
        "v get_v(void) { v x; x.a = 1.0f; x.b = 2.0f; return x; }",
        "struct v { float a; float b; ~v() {} };\n"
        "v get_v(void) { v x; x.a = 1.0f; x.b = 2.0f; return x; }",
        "struct v { float a; float b; };\nv get_v(void);",
        "struct v { float a; float b; ~v(); };\nv get_v(void);",
        "cpp", "BREAKING", "NO_CHANGE", "correct",
    ),
    # ── PR#109: typedef→derived class false positive in base detection ──
    # ABICC had a bug where a typedef pointing to a derived class caused a false
    # positive "added base" report even when nothing changed between versions.
    # abicheck correctly reports NO_CHANGE; ABICC 2.3 also reports NO_CHANGE,
    # indicating that the fix from PR#109 is present in this version (or the
    # specific scenario does not trigger the original bug).
    # See: https://github.com/lvc/abi-compliance-checker/pull/109
    (
        "typedef_derived_false_base_change",
        "struct Base { int x; };\n"
        "struct Derived : public Base { int y; };\n"
        "typedef Derived MyType;\n"
        "MyType* get_obj(void) { static MyType obj; return &obj; }",
        "struct Base { int x; };\n"
        "struct Derived : public Base { int y; };\n"
        "typedef Derived MyType;\n"
        "MyType* get_obj(void) { static MyType obj; return &obj; }",
        "struct Base { int x; };\n"
        "struct Derived : public Base { int y; };\n"
        "typedef Derived MyType;\n"
        "MyType* get_obj(void);",
        "struct Base { int x; };\n"
        "struct Derived : public Base { int y; };\n"
        "typedef Derived MyType;\n"
        "MyType* get_obj(void);",
        "cpp", "NO_CHANGE", "NO_CHANGE", "parity",
    ),
    # ── Issue #100 — = delete not detected ───────────────────────────────────
    # Both abicheck and ABICC miss the = delete change through castxml/header path.
    # castxml does not emit deleted functions/methods at all, so neither tool can
    # detect the = delete annotation. This is a known gap in both tools for this
    # class of change. Both tools return NO_CHANGE (parity — but incorrectly so).
    # The "correct" behavior would be BREAKING, but neither tool achieves it here.
    # See: https://github.com/lvc/abi-compliance-checker/issues/100
    # Future: detect = delete via a different mechanism (e.g. DWARF or ELF mangled names)
    (
        "func_deleted_marker",
        "class Foo { public: Foo(); Foo(const Foo&); };\n"
        "Foo* make_foo(int x) { (void)x; static Foo f; return &f; }",
        "class Foo { public: Foo(); Foo(const Foo&) = delete; };\n"
        "Foo* make_foo(int x) { (void)x; static Foo f; return &f; }",
        "class Foo { public: Foo(); Foo(const Foo&); };\n"
        "Foo* make_foo(int x);",
        "class Foo { public: Foo(); Foo(const Foo&) = delete; };\n"
        "Foo* make_foo(int x);",
        "cpp", "NO_CHANGE", "NO_CHANGE", "parity",
    ),
    # ── Issue #96 — Incomplete type → complete type (type became opaque) ─────
    # When a struct goes from complete definition to forward-declaration only,
    # callers can no longer sizeof/use it directly → BREAKING.
    # abicheck detects TYPE_BECAME_OPAQUE; ABICC detects this too.
    # See: https://github.com/lvc/abi-compliance-checker/issues/96
    # Note: v2 src still has the full definition (to produce a valid .so);
    # the opaque change is only visible in the header (what callers see).
    (
        "type_became_opaque",
        "struct Blob { int x; int y; };\n"
        "struct Blob* alloc_blob(void) { static struct Blob b; return &b; }",
        "struct Blob { int x; int y; };\n"
        "struct Blob* alloc_blob(void) { static struct Blob b; return &b; }",
        "struct Blob { int x; int y; };\n"
        "struct Blob* alloc_blob(void);",
        "struct Blob;\n"
        "struct Blob* alloc_blob(void);",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── Issue #125 — Inline functions not checked ─────────────────────────────
    # When the header marks a function as inline, callers may stop linking against
    # the DSO symbol (depending on optimizer). If the symbol later disappears from
    # the DSO (e.g., due to -fvisibility=hidden or LTO), existing binaries that
    # expected to call via PLT break. abicheck detects FUNC_BECAME_INLINE (API_BREAK).
    # ABICC does NOT check inline annotations — it sees both .so files are identical
    # and reports NO_CHANGE. abicheck is more conservative and correct here.
    # See: https://github.com/lvc/abi-compliance-checker/issues/125
    # Note: both v1/v2 .so compiled from same source (symbol still exported);
    # only the header annotation differs.
    (
        "func_became_inline",
        "int compute(int x) { return x * 2; }",
        "int compute(int x) { return x * 2; }",
        "int compute(int x);",
        "inline int compute(int x) { return x * 2; }",
        "cpp", "API_BREAK", "NO_CHANGE", "correct",
    ),
    # ── Issue #125 — Function loses inline attribute ──────────────────────────
    # When an inline function loses the inline attribute in the header, existing
    # binaries with baked-in inline copies still work. New binaries will link the
    # exported symbol. abicheck emits COMPATIBLE (FUNC_LOST_INLINE); ABICC NO_CHANGE.
    # Recorded as "risk" — potentially breaking for callers that relied on inlining.
    (
        "func_lost_inline",
        "int fast_compute(int x) { return x + 1; }",
        "int fast_compute(int x) { return x + 1; }",
        "inline int fast_compute(int x) { return x + 1; }",
        "int fast_compute(int x);",
        "cpp", "COMPATIBLE", "NO_CHANGE", "risk",
    ),
    # ── Issue #128 — Non-trivial destructor changes calling convention ────────
    # Adding a non-trivial destructor to a class affects whether the object is
    # returned in registers vs via hidden pointer (System V AMD64 ABI §3.2.3).
    # abicheck now detects via DWARF value-ABI trait
    # See: https://github.com/lvc/abi-compliance-checker/issues/128
    (
        "nontrivial_dtor_calling_convention_widget",
        "class Widget {\n"
        "public:\n"
        "    int value;\n"
        "    Widget(int v) : value(v) {}\n"
        "};\n"
        "Widget make_widget(int v) { return Widget(v); }",
        "class Widget {\n"
        "public:\n"
        "    int value;\n"
        "    Widget(int v) : value(v) {}\n"
        "    ~Widget() { value = -1; }  // non-trivial destructor\n"
        "};\n"
        "Widget make_widget(int v) { return Widget(v); }",
        "class Widget {\n"
        "public:\n"
        "    int value;\n"
        "    Widget(int v);\n"
        "};\n"
        "Widget make_widget(int v);",
        "class Widget {\n"
        "public:\n"
        "    int value;\n"
        "    Widget(int v);\n"
        "    ~Widget();\n"
        "};\n"
        "Widget make_widget(int v);",
        "cpp", "BREAKING", "NO_CHANGE", "correct",
    ),
    # ── Risk: global var type widened (int→long, same size LP64) ─────────────
    # On LP64 (Linux x86-64), int=4 bytes, long=8 bytes.
    # Changing an exported global from int to long changes its binary size and
    # may break callers that access it. abicheck emits BREAKING via VAR_TYPE_CHANGED;
    # ABICC may miss it if only the header descriptor is used (no abi-dumper).
    # In practice ABICC catches it in full mode — recorded as "risk" to track
    # cases where ABICC's verdict varies by invocation mode.
    (
        "global_var_type_widened",
        "int api_level = 3;",
        "long api_level = 3;",
        "extern int api_level;",
        "extern long api_level;",
        "c", "BREAKING", "BREAKING", "parity",
    ),
    # ── parity: no spurious visibility change ─────────────────────────────────
    # A class with a method that has an explicit visibility annotation should NOT
    # produce a spurious visibility change report if nothing actually changed.
    # Regression test: abicheck must report NO_CHANGE here.
    (
        "no_spurious_visibility_change",
        '__attribute__((visibility("default"))) int public_api(int x) { return x; }',
        '__attribute__((visibility("default"))) int public_api(int x) { return x; }',
        '__attribute__((visibility("default"))) int public_api(int x);',
        '__attribute__((visibility("default"))) int public_api(int x);',
        "c", "NO_CHANGE", "NO_CHANGE", "parity",
    ),
]

_CONFIRMED = [c for c in PARITY_CASES if c[8] == "parity"]
_CORRECT = [c for c in PARITY_CASES if c[8] == "correct"]
_DIVERGE = [c for c in PARITY_CASES if c[8] == "divergence"]
# "risk" tests: abicheck emits API_BREAK or COMPATIBLE; ABICC verdict may vary by version.
# These represent potentially breaking changes that need human review.
_RISK = [c for c in PARITY_CASES if c[8] == "risk"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.fail(f"Compilation failed: {r.stderr[:200]}")


def _write_abicc_descriptor(
    version: str,
    lib_path: Path,
    header_path: Path | None,
    desc_path: Path,
) -> None:
    """Write an ABICC XML descriptor file."""
    lines = [f"<version>{version}</version>"]
    if header_path is not None and header_path.exists():
        lines.append(f"<headers>{header_path}</headers>")
    lines.append(f"<libs>{lib_path}</libs>")
    desc_path.write_text("\n".join(lines), encoding="utf-8")


def _run_abicheck(
    old: Path,
    new: Path,
    hdr_v1: str | None,
    hdr_v2: str | None,
    lang: str,
    tmp_path: Path,
) -> str:
    """Run abicheck with headers when available, ELF-only otherwise."""
    try:
        from abicheck.checker import compare
        from abicheck.dumper import dump

        compiler = "cc" if lang == "c" else "c++"

        if hdr_v1 is not None:
            h1 = tmp_path / f"{old.stem}_hdr.h"
            h1.write_text(textwrap.dedent(hdr_v1).strip(), encoding="utf-8")
            headers_v1: list[Path] = [h1]
        else:
            headers_v1 = []

        if hdr_v2 is not None:
            h2 = tmp_path / f"{new.stem}_hdr.h"
            h2.write_text(textwrap.dedent(hdr_v2).strip(), encoding="utf-8")
            headers_v2: list[Path] = [h2]
        else:
            headers_v2 = []

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old_snap = dump(old, headers=headers_v1, version="v1", compiler=compiler, lang=lang)
            new_snap = dump(new, headers=headers_v2, version="v2", compiler=compiler, lang=lang)

        # Parity baseline predates default scoping (ADR-024 Phase 5); compare
        # unscoped for an apples-to-apples verdict against abi-compliance-checker.
        result = compare(old_snap, new_snap, scope_to_public_surface=False)
        return result.verdict.value
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def _run_abicc(
    old: Path,
    new: Path,
    hdr_v1: str | None,
    hdr_v2: str | None,
    tmp_path: Path,
) -> tuple[str, str]:
    """Run abi-compliance-checker; return (verdict, diagnostics).

    Verdict is one of BREAKING/COMPATIBLE/NO_CHANGE/ERROR(...)/TIMEOUT.
    Diagnostics is a string with stdout, stderr, and report excerpt for
    debugging verdict-parsing failures.

    ABICC exit codes:
      0 = compatible (no breaking changes)
      1 = incompatible (breaking changes found)
      other = error
    """
    # Write headers for ABICC descriptor
    old_desc = tmp_path / "old.xml"
    new_desc = tmp_path / "new.xml"

    h1_path = None
    if hdr_v1 is not None:
        h1_path = tmp_path / "old_hdr.h"
        h1_path.write_text(textwrap.dedent(hdr_v1).strip(), encoding="utf-8")

    h2_path = None
    if hdr_v2 is not None:
        h2_path = tmp_path / "new_hdr.h"
        h2_path.write_text(textwrap.dedent(hdr_v2).strip(), encoding="utf-8")

    _write_abicc_descriptor("1.0", old, h1_path, old_desc)
    _write_abicc_descriptor("2.0", new, h2_path, new_desc)

    report_path = tmp_path / "abicc_report.html"

    cmd = [
        "abi-compliance-checker",
        "-lib", "libtest",
        "-old", str(old_desc),
        "-new", str(new_desc),
        "-report-path", str(report_path),
    ]

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "subprocess timed out after 60s"

    # Build diagnostics string for debugging verdict-parsing failures.
    report_text = ""
    if report_path.exists():
        report_text = report_path.read_text(encoding="utf-8", errors="replace")
    diag_parts = [
        f"rc={r.returncode}",
        f"stdout={r.stdout[:1000]!r}",
        f"stderr={r.stderr[:1000]!r}",
        f"report_exists={report_path.exists()}",
        f"report_len={len(report_text)}",
        f"report_first_2000={report_text[:2000]!r}",
    ]
    diag = "\n".join(diag_parts)

    if r.returncode == 1:
        return "BREAKING", diag

    if r.returncode != 0:
        return f"ERROR(rc={r.returncode})", diag

    # ABICC returns 0 for both no-change and compatible-additions.
    # Parse the structured HTML comment ABICC embeds at the top of reports:
    #   <!-- kind:binary;verdict:compatible;affected:0;added:0;removed:0;
    #        type_problems_high:0;...;tool_version:2.3 -->
    # If any change-count field is non-zero, it's COMPATIBLE.
    change_fields = (
        "affected", "added", "removed",
        "type_problems_high", "type_problems_medium", "type_problems_low",
        "interface_problems_high", "interface_problems_medium",
        "interface_problems_low", "changed_constants",
    )
    for field in change_fields:
        m = re.search(rf"{field}:(\d+)", report_text)
        if m and int(m.group(1)) > 0:
            return "COMPATIBLE", diag

    return "NO_CHANGE", diag


def _setup(
    name: str, src_v1: str, src_v2: str,
    hdr_v1: str | None, hdr_v2: str | None,
    lang: str, tmp_path: Path,
) -> tuple[str, str, str]:
    """Compile .so files, run both tools.

    Returns (abicheck_verdict, abicc_verdict, abicc_diagnostics).
    """
    _require_tool("abi-compliance-checker")
    _require_tool("gcc" if lang == "c" else "g++")
    if hdr_v1 is not None or hdr_v2 is not None:
        _require_tool("castxml")

    v1 = tmp_path / f"lib{name}_v1.so"
    v2 = tmp_path / f"lib{name}_v2.so"
    _compile_so(src_v1, v1, lang)
    _compile_so(src_v2, v2, lang)

    ac = _run_abicheck(v1, v2, hdr_v1, hdr_v2, lang, tmp_path)
    cc, diag = _run_abicc(v1, v2, hdr_v1, hdr_v2, tmp_path)
    return ac, cc, diag


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

@pytest.mark.abicc
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abicc_exp,_",
    _CONFIRMED, ids=[c[0] for c in _CONFIRMED],
)
def test_confirmed_parity(
    name: str, src_v1: str, src_v2: str,
    hdr_v1: str | None, hdr_v2: str | None, lang: str,
    abicheck_exp: str, abicc_exp: str, _: str, tmp_path: Path,
) -> None:
    """Both tools must agree on verdict -- full parity enforced."""
    ac, cc, diag = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)
    assert ac == abicheck_exp, f"abicheck: expected {abicheck_exp}, got {ac}"
    assert cc == abicc_exp, f"abicc: expected {abicc_exp}, got {cc}\nDIAG:\n{diag}"
    assert ac == cc, f"PARITY BROKEN: abicheck={ac}, abicc={cc}"


@pytest.mark.abicc
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abicc_exp,_",
    _CORRECT, ids=[c[0] for c in _CORRECT],
)
def test_abicheck_correct(
    name: str, src_v1: str, src_v2: str,
    hdr_v1: str | None, hdr_v2: str | None, lang: str,
    abicheck_exp: str, abicc_exp: str, _: str, tmp_path: Path,
) -> None:
    """abicheck detects the break; ABICC misses it.

    If ABICC also becomes correct, move this case to _CONFIRMED.
    """
    ac, cc, diag = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)
    assert ac == abicheck_exp, f"abicheck: expected {abicheck_exp}, got {ac}"
    assert cc == abicc_exp, f"abicc: expected {abicc_exp}, got {cc}\nDIAG:\n{diag}"
    if ac == cc:
        pytest.fail(
            f"Full parity achieved on '{name}' (both={ac}). "
            "Move this case to _CONFIRMED."
        )


@pytest.mark.abicc
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abicc_exp,_",
    _DIVERGE, ids=[c[0] for c in _DIVERGE],
)
def test_known_divergence(
    name: str, src_v1: str, src_v2: str,
    hdr_v1: str | None, hdr_v2: str | None, lang: str,
    abicheck_exp: str, abicc_exp: str, _: str, tmp_path: Path,
) -> None:
    """Intentional stable divergences. Fails if pattern changes unexpectedly."""
    ac, cc, diag = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)

    if ac == cc == abicc_exp:
        pytest.fail(
            f"Gap closed on '{name}': abicheck now agrees with ABICC ({ac}). "
            "Move this case to _CONFIRMED and remove the divergence flag."
        )

    assert ac == abicheck_exp, (
        f"abicheck changed unexpectedly on '{name}': "
        f"expected {abicheck_exp}, got {ac}"
    )
    assert cc == abicc_exp, (
        f"ABICC changed unexpectedly on '{name}': "
        f"expected {abicc_exp}, got {cc}\nDIAG:\n{diag}"
    )


@pytest.mark.abicc
@pytest.mark.parametrize(
    "name,src_v1,src_v2,hdr_v1,hdr_v2,lang,abicheck_exp,abicc_exp,_",
    _RISK, ids=[c[0] for c in _RISK],
)
def test_risk(
    name: str, src_v1: str, src_v2: str,
    hdr_v1: str | None, hdr_v2: str | None, lang: str,
    abicheck_exp: str, abicc_exp: str, _: str, tmp_path: Path,
) -> None:
    """Potentially breaking changes (REVIEW_NEEDED / risk category).

    abicheck emits API_BREAK or COMPATIBLE; ABICC verdict may vary by version.
    These represent changes that need human review — analogous to libabigail's
    'potentially ABI-breaking' classification.

    If ABICC and abicheck both agree, move the case to _CONFIRMED.
    """
    ac, cc, diag = _setup(name, src_v1, src_v2, hdr_v1, hdr_v2, lang, tmp_path)
    assert ac == abicheck_exp, (
        f"abicheck changed unexpectedly on '{name}': "
        f"expected {abicheck_exp}, got {ac}"
    )
    # ABICC verdict is informational for risk cases — log but don't hard-fail
    if ac == cc:
        # If both agree, suggest promoting to parity
        warnings.warn(
            f"Risk case '{name}': abicheck and ABICC now agree ({ac}). "
            "Consider moving to _CONFIRMED.",
            stacklevel=2,
        )
