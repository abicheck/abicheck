"""Integration tests for ABI check examples (legacy — cases 01-18).

Superseded by test_example_autodiscovery.py which auto-discovers all cases.
Kept for backward compatibility on Linux.  Skipped in CI integration runs
to avoid duplicate cmake-configure overhead (especially costly on Windows
where each configure adds ~30 s).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

EXAMPLES_DIR = example_catalog.CASES_DIR

# (case_dir_name, expected_verdict, header_v1, header_v2)
#
# header_v1/header_v2 are paths relative to the case directory, or ``None``
# for a case with no public header at all (dump falls back to DWARF/
# symbol-only evidence, no ``-H`` flag passed).
#
# Expected verdicts reflect what abicheck currently detects with castxml+ELF analysis:
#   DETECTED  — abicheck catches the break reliably
#   LIMITATION — break exists but abicheck cannot detect it yet (documented gap)
#   POLICY   — not a binary break; SONAME/versioning are policy issues
#
CASES = [
    # Symbol removed from ELF dynsym → FUNC_REMOVED → BREAKING
    ("case01_symbol_removal", "BREAKING", "v1.c", "v2.c"),
    # Parameter type change visible via castxml → FUNC_PARAMS_CHANGED → BREAKING
    ("case02_param_type_change", "BREAKING", "v1.c", "v2.c"),
    # New symbol added → FUNC_ADDED → COMPATIBLE
    # (restructured into old/new subdirs; header is lib.h, not v1.c/v2.c)
    ("case03_compat_addition", "COMPATIBLE", "old/lib.h", "new/lib.h"),
    # Identical libs → NO_CHANGE
    ("case04_no_change", "NO_CHANGE", "v1.c", "v1.c"),
    # SONAME is a policy attribute, not tracked as a binary ABI break.
    # (no public header in this case at all — DWARF/symbol-only dump)
    ("case05_soname", "COMPATIBLE", None, None),
    # internal_helper/another_impl hidden in good.c → removed from dynsym → BREAKING
    ("case06_visibility", "BREAKING", "bad.c", "good.c"),
    # Struct size change detected via castxml → TYPE_SIZE_CHANGED → BREAKING
    # (the public header is v1.h/v2.h — v1.c/v2.c define their own local
    # `struct Point` that lacks get_y(), so passing the .c file as -H
    # silently dumped the wrong declarations)
    ("case07_struct_layout", "BREAKING", "v1.h", "v2.h"),
    # Enum member value changes detected via _diff_enums() → BREAKING
    ("case08_enum_value_change", "BREAKING", "v1.c", "v2.c"),
    # vtable reorder/change detected → TYPE_VTABLE_CHANGED → BREAKING
    ("case09_cpp_vtable", "BREAKING", "v1.cpp", "v2.cpp"),
    # Return type change detected via castxml → FUNC_RETURN_CHANGED → BREAKING
    ("case10_return_type", "BREAKING", "v1.c", "v2.c"),
    # Global var type change → BREAKING
    ("case11_global_var_type", "BREAKING", "v1.c", "v2.c"),
    # Function inlined away → disappears from .so → FUNC_REMOVED → BREAKING
    ("case12_function_removed", "BREAKING", "v1.c", "v2.c"),
    # Symbol versioning: checker strips @-suffix → symbols match → COMPATIBLE
    # (no public header in this case at all — DWARF/symbol-only dump)
    ("case13_symbol_versioning", "COMPATIBLE", None, None),
    # Class size change (private member added) → TYPE_SIZE_CHANGED → BREAKING
    ("case14_cpp_class_size", "BREAKING", "v1.cpp", "v2.cpp"),
    # noexcept removed → FUNC_NOEXCEPT_REMOVED (COMPATIBLE) + SYMBOL_VERSION_REQUIRED_ADDED
    # (GLIBCXX bump from throw in v2) → COMPATIBLE_WITH_RISK
    ("case15_noexcept_change", "COMPATIBLE_WITH_RISK", "v1.cpp", "v2.cpp"),
    # Symbol appears in v2 that was inline in v1 → FUNC_ADDED → COMPATIBLE
    # (restructured into old/new subdirs; header is lib.hpp, not v1.hpp/v2.hpp)
    ("case16_inline_to_non_inline", "COMPATIBLE", "old/lib.hpp", "new/lib.hpp"),
    # Explicit-instantiated template size grows → TYPE_SIZE_CHANGED → BREAKING
    ("case17_template_abi", "BREAKING", "v1.hpp", "v2.hpp"),
    # castxml processes headers transitively: ThirdPartyHandle (4→8 bytes) → BREAKING
    ("case18_dependency_leak", "BREAKING", "foo_v1.h", "foo_v2.h"),
]


def _shared_lib_suffix() -> str:
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"


def _find_compiler(is_cpp: bool = False) -> str | None:
    if is_cpp:
        candidates = {
            "win32": ["cl", "g++", "clang++"],
            "darwin": ["clang++", "g++"],
        }.get(sys.platform, ["g++", "clang++"])
    else:
        candidates = {"win32": ["cl", "gcc", "clang"], "darwin": ["clang", "gcc"]}.get(
            sys.platform, ["gcc", "clang"]
        )
    for cc in candidates:
        if shutil.which(cc):
            return cc
    return None


def _compile_shared(src: Path, out: Path) -> str | None:
    """Compile *src* into a shared library at *out*.

    Returns None on success, ``"no_compiler"`` when no compiler is found,
    or an error message string when compilation fails.
    """
    is_cpp = src.suffix == ".cpp"
    compiler = _find_compiler(is_cpp)
    if not compiler:
        return "no_compiler"

    if compiler == "cl":
        args = [compiler, "/LD", "/Zi", "/Fe:" + str(out), str(src)]
    elif sys.platform == "darwin":
        args = [
            compiler,
            "-dynamiclib",
            "-g",
            "-Og",
            "-fvisibility=default",
            "-install_name",
            "@rpath/lib.dylib",
            "-o",
            str(out),
            str(src),
        ]
    else:
        args = [
            compiler,
            "-shared",
            "-fPIC",
            "-g",
            "-Og",
            "-fvisibility=default",
            "-o",
            str(out),
            str(src),
        ]

    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return f"compile failed (exit {r.returncode}):\n{r.stderr[:500]}"
    return None


def _find_lib(directory: Path, name: str) -> Path | None:
    """Find a shared library in *directory* across platforms.

    Search order:
    1. Direct children of *directory*
    2. Common multi-config generator subdirectories (Debug/, Release/, …)
    3. Recursive glob fallback — catches lib/, nested generator layouts, etc.
    """
    if not directory.exists():
        return None
    # 1+2: explicit directory + well-known config subdirs
    search_dirs = [directory]
    for cfg in ("Debug", "Release", "RelWithDebInfo", "MinSizeRel"):
        sub = directory / cfg
        if sub.is_dir():
            search_dirs.append(sub)
    for search_dir in search_dirs:
        for prefix in ("lib", ""):
            for suffix in (".so", ".dylib", ".dll"):
                p = search_dir / f"{prefix}{name}{suffix}"
                if p.exists():
                    return p
    # 3: recursive glob fallback for unusual generator layouts
    for prefix in ("lib", ""):
        for suffix in (".so", ".dylib", ".dll"):
            hits = list(directory.rglob(f"{prefix}{name}{suffix}"))
            if hits:
                return hits[0]
    return None


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


# Load platform info from ground_truth.json
_gt_path = example_catalog.GROUND_TRUTH_PATH
_gt_data = json.loads(_gt_path.read_text()) if _gt_path.exists() else {"verdicts": {}}
_PLATFORMS: dict[str, list[str]] = {
    k: v.get("platforms", ["linux", "macos", "windows"])
    for k, v in _gt_data["verdicts"].items()
}


def _current_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _find_source(d: Path, hint: str | None, side: str) -> Path:
    """Resolve a compilable source from a hint that may be a header.

    *side* is ``"v1"``/``"v2"`` — used only when *hint* is ``None`` (a case
    with no public header at all), to search the conventional ``old/``
    (v1) / ``new/`` (v2) subdirectory for a source file.
    """
    if hint is None:
        sub = d / ("old" if side == "v1" else "new")
        for ext in (".c", ".cpp"):
            src = sub / f"lib{ext}"
            if src.exists():
                return src
        return sub / "lib.c"  # best effort
    p = d / hint
    if p.suffix in (".c", ".cpp"):
        return p
    # hint is a header — look for matching source *next to it* (its own
    # parent, not necessarily `d` itself — a header under `old/`/`new/`
    # has its sibling source in that same subdirectory).
    stem = p.stem
    for ext in (".c", ".cpp"):
        src = p.parent / f"{stem}{ext}"
        if src.exists():
            return src
    # old/new pattern: old/lib.h → old/lib.c (already covered by the loop
    # above via p.parent when stem == "lib"); libfoo pattern: foo_v1.h →
    # libfoo_v1.c, searched relative to the header's own directory too.
    if stem.endswith(("_v1", "_v2")):
        base = stem[:-3]  # e.g. "foo"
        tag = stem[-3:]  # e.g. "_v1"
        for ext in (".c", ".cpp"):
            src = p.parent / f"lib{base}{tag}{ext}"
            if src.exists():
                return src
    return p  # best effort


def _try_cmake_build(
    case_name: str,
    case_dir: Path,
    tmp_path: Path,
) -> tuple[Path | None, Path | None]:
    """Attempt to build with CMake. Returns (libv1, libv2) or (None, None)."""
    cmake_file = case_dir / "CMakeLists.txt"
    if not cmake_file.exists():
        pytest.skip(f"{case_name}: no CMakeLists.txt found in {case_dir}")
    if not shutil.which("cmake"):
        pytest.skip(f"{case_name}: cmake not found in PATH")

    cmake_build = tmp_path / "cmake_build"
    r = subprocess.run(
        [
            "cmake",
            "-S",
            str(case_dir.parent),
            "-B",
            str(cmake_build),
            "-DCMAKE_BUILD_TYPE=Debug",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        pytest.fail(
            f"{case_name}: cmake configure failed (rc={r.returncode}):\n"
            f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
        )

    r = subprocess.run(
        [
            "cmake",
            "--build",
            str(cmake_build),
            "--target",
            f"{case_name}_v1",
            f"{case_name}_v2",
            "--config",
            "Debug",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        pytest.fail(
            f"{case_name}: cmake build failed (rc={r.returncode}):\n"
            f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
        )

    out_dir = cmake_build / case_name
    return _find_lib(out_dir, "v1"), _find_lib(out_dir, "v2")


def _compile_fallback(
    case_name: str,
    build_dir: Path,
    tmp_path: Path,
    hdr_v1: str | None,
    hdr_v2: str | None,
) -> tuple[Path, Path]:
    """Direct compilation fallback. Returns (libv1, libv2)."""
    suffix = _shared_lib_suffix()
    src_v1 = _find_source(build_dir, hdr_v1, "v1")
    src_v2 = _find_source(build_dir, hdr_v2, "v2")
    libv1 = tmp_path / f"libv1{suffix}"
    libv2 = tmp_path / f"libv2{suffix}"

    for label, src, lib in [("v1", src_v1, libv1), ("v2", src_v2, libv2)]:
        err = _compile_shared(src, lib)
        if err == "no_compiler":
            pytest.skip(f"no compiler found for {case_name} {label}")
        elif err:
            pytest.fail(f"{case_name} {label}: {err}")

    return libv1, libv2


def _run_dump(
    case_name: str,
    lib: Path,
    header: Path | None,
    snap: Path,
    label: str,
) -> None:
    """Run abicheck dump for a single version; skip or fail on error.

    *header* is ``None`` for a case with no public header at all (e.g.
    case05_soname, case13_symbol_versioning) — the ``-H`` flag is omitted
    entirely rather than pointed at a nonexistent path, so the dump falls
    back to DWARF/symbol-only evidence.
    """
    cmd = [sys.executable, "-m", "abicheck.cli", "dump", str(lib)]
    if header is not None:
        cmd += ["-H", str(header)]
    cmd += ["-o", str(snap)]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if r.returncode != 0:
        combined = r.stderr.lower() + r.stdout.lower()
        if "castxml" in combined:
            pytest.skip(f"castxml unavailable for {case_name}:\n{r.stderr[:300]}")
        pytest.fail(f"abicheck dump {label} failed in {case_name}:\n{r.stderr[:500]}")


def _run_compare_and_assert(
    case_name: str,
    expected_verdict: str,
    snap1: Path,
    snap2: Path,
) -> None:
    """Run abicheck compare and assert the verdict matches."""
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "compare",
            str(snap1),
            str(snap2),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    try:
        result = json.loads(rc.stdout)
        verdict = result.get("verdict", "")
    except json.JSONDecodeError:
        pytest.fail(
            f"abicheck compare produced invalid JSON for {case_name} "
            f"(returncode={rc.returncode}):\n{rc.stdout[:500]}"
        )

    assert verdict == expected_verdict, (
        f"{case_name}: expected verdict={expected_verdict!r}, got {verdict!r}\n"
        f"stdout: {rc.stdout[:1000]}"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "case_name,expected_verdict,hdr_v1,hdr_v2", CASES, ids=[c[0] for c in CASES]
)
def test_abi_example(case_name, expected_verdict, hdr_v1, hdr_v2, tmp_path):
    _require_tool("castxml")

    # Platform filter
    platforms = _PLATFORMS.get(case_name, ["linux", "macos", "windows"])
    current = _current_platform()
    if current not in platforms:
        pytest.skip(f"{case_name} not supported on {current}")

    case_dir = example_catalog.case_dir(case_name)
    assert case_dir.is_dir(), f"Case directory not found: {case_dir}"

    build_dir = tmp_path / case_name
    shutil.copytree(str(case_dir), str(build_dir))

    # Build strategy: CMake > direct compilation
    libv1, libv2 = _try_cmake_build(case_name, case_dir, tmp_path)
    if not libv1 or not libv2:
        libv1, libv2 = _compile_fallback(
            case_name,
            build_dir,
            tmp_path,
            hdr_v1,
            hdr_v2,
        )

    snap1 = tmp_path / "snap1.json"
    snap2 = tmp_path / "snap2.json"

    _run_dump(case_name, libv1, build_dir / hdr_v1 if hdr_v1 else None, snap1, "v1")
    _run_dump(case_name, libv2, build_dir / hdr_v2 if hdr_v2 else None, snap2, "v2")

    _run_compare_and_assert(case_name, expected_verdict, snap1, snap2)
