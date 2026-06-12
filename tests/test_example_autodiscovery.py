# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Integration tests — auto-discovery of all example cases.

Replaces the hard-coded CASES list in test_abi_examples.py with directory
scanning so every new example added to examples/ is automatically tested
without touching this file.

Layout support:
  • v1/v2     — examples/caseXX/v1.c(pp)  + v2.c(pp)  [+ v1.h/.hpp]
  • old/new   — examples/caseXX/old/lib.c + new/lib.c  [+ lib.h/.hpp]
  • good/bad  — examples/caseXX/bad.c (v1) + good.c (v2)  [bad=before, good=fixed]
  • libfoo    — examples/caseXX/libfoo_v1.c + libfoo_v2.c

Expected verdicts are loaded from examples/ground_truth.json (single source
of truth). Set a case to null in ground_truth.json to skip it entirely.

Cross-platform: cases declare supported platforms in ground_truth.json.
Build uses CMake when a CMakeLists.txt is present, falling back to direct
compilation otherwise.

Marked `@pytest.mark.integration` — requires a C/C++ compiler + castxml in PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import filelock as _filelock  # explicit dev dependency; used for xdist build locking
except ImportError:
    _filelock = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_DIR / "examples"

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------
def _current_platform() -> str:
    """Return the platform tag used in ground_truth.json."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform

CURRENT_PLATFORM = _current_platform()

def _shared_lib_suffix() -> str:
    """Return the shared library file extension for the current platform."""
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"

SHARED_LIB_SUFFIX = _shared_lib_suffix()

# ---------------------------------------------------------------------------
# Expected verdicts and known gaps — loaded from ground_truth.json.
# Single source of truth: add cases / known_gap fields there, not here.
# To skip a case, set its "expected" value to null in ground_truth.json.
# ---------------------------------------------------------------------------
_GT_PATH = REPO_DIR / "examples" / "ground_truth.json"
_gt_data = json.loads(_GT_PATH.read_text())
EXPECTED: dict[str, str | None] = {
    k: v.get("expected") for k, v in _gt_data["verdicts"].items()
}
# platforms: case_name → list of supported platforms
PLATFORMS: dict[str, list[str]] = {
    k: v.get("platforms", ["linux", "macos", "windows"])
    for k, v in _gt_data["verdicts"].items()
}
# known_gap: case_name → xfail reason (sourced from ground_truth.json)
KNOWN_GAPS: dict[str, str] = {
    k: v["known_gap"]
    for k, v in _gt_data["verdicts"].items()
    if "known_gap" in v
}
# requires_feature: case_name → compiler feature that must be available, else
# the case is skipped (the fixture cannot even compile on toolchains lacking
# it — e.g. C23 _BitInt needs GCC 14+, so GCC 13 on ubuntu-latest skips it).
REQUIRES_FEATURE: dict[str, str] = {
    k: v["requires_feature"]
    for k, v in _gt_data["verdicts"].items()
    if "requires_feature" in v
}
# scope_public_headers: case_name → bool. When true the case is compared with
# ADR-024 public-surface scoping enabled (demonstrates that backward-compatible
# changes to non-public API/ABI produce no findings).
SCOPE_PUBLIC_HEADERS: dict[str, bool] = {
    k: bool(v.get("scope_public_headers", False))
    for k, v in _gt_data["verdicts"].items()
}
# build_info: cases that ship per-side compile_commands.json and assert an L3
# build-evidence finding (a runtime-model flip). This pipeline validates via the
# Python compare() API, which does NOT run the build-evidence diff — only the CLI
# path does — so these are validated by tests/validate_examples.py and skipped
# here rather than producing a spurious "no change" verdict.
BUILD_INFO_CASES: set[str] = {
    k for k, v in _gt_data["verdicts"].items() if v.get("build_info")
}


# ---------------------------------------------------------------------------
# Layout detection helpers
# ---------------------------------------------------------------------------
def _hdr(base_dir: Path, stem: str) -> Path | None:
    """Find a header file (.h or .hpp) with the given stem in *base_dir*."""
    for ext in (".h", ".hpp"):
        h = base_dir / f"{stem}{ext}"
        if h.exists():
            return h
    return None


def _find_v1v2_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try v1/v2 layout: case_dir/v1.c(pp) + v2.c(pp)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"v1{ext}"
        if v1.exists():
            v2 = case_dir / f"v2{ext}"
            if not v2.exists():
                if case_dir.name == "case04_no_change":
                    v2 = v1  # intentional: identical sources → NO_CHANGE
                else:
                    pytest.fail(f"{case_dir.name}: v2 source missing — broken fixture")
            return v1, v2, _hdr(case_dir, "v1"), _hdr(case_dir, "v2")
    return None


def _find_old_new_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try old/new layout: case_dir/old/lib.c(pp) + new/lib.c(pp)."""
    old_dir, new_dir = case_dir / "old", case_dir / "new"
    if not (old_dir.is_dir() and new_dir.is_dir()):
        return None
    for ext in (".c", ".cpp"):
        v1 = old_dir / f"lib{ext}"
        if v1.exists():
            v2 = new_dir / f"lib{ext}"
            if not v2.exists():
                pytest.fail(f"{case_dir.name}: new/lib{ext} missing — broken fixture")
            return v1, v2, _hdr(old_dir, "lib"), _hdr(new_dir, "lib")
    return None


def _find_good_bad_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try good/bad layout: case_dir/bad.c(pp) (v1) + good.c(pp) (v2).

    Convention: bad=v1 (before, problematic state), good=v2 (after, fixed state).
    Comparing bad->good reveals symbol removals = BREAKING for callers.
    """
    for ext in (".c", ".cpp"):
        bad = case_dir / f"bad{ext}"
        if bad.exists():
            good = case_dir / f"good{ext}"
            if not good.exists():
                pytest.fail(f"{case_dir.name}: good{ext} missing — broken fixture")
            return bad, good, _hdr(case_dir, "bad"), _hdr(case_dir, "good")
    return None


def _find_libfoo_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try libfoo_v1/v2 layout: case_dir/libfoo_v1.c(pp) + libfoo_v2.c(pp)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"libfoo_v1{ext}"
        if v1.exists():
            v2 = case_dir / f"libfoo_v2{ext}"
            if not v2.exists():
                pytest.fail(f"{case_dir.name}: libfoo_v2{ext} missing — broken fixture")
            return v1, v2, _hdr(case_dir, "foo_v1"), _hdr(case_dir, "foo_v2")
    return None


def _find_sources(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None]:
    """Return (v1_src, v2_src, v1_hdr, v2_hdr).

    Raises pytest.skip() if no recognised layout is found or if a required
    v2 source is missing (only case04_no_change legitimately has no v2).
    """
    for finder in (
        _find_v1v2_layout,
        _find_old_new_layout,
        _find_good_bad_layout,
        _find_libfoo_layout,
    ):
        result = finder(case_dir)
        if result is not None:
            return result

    pytest.skip(f"{case_dir.name}: no recognised source layout")


def _find_compiler() -> str | None:
    """Find a C compiler available on this platform."""
    if sys.platform == "win32":
        # Prefer cl.exe on Windows, fall back to gcc (MinGW)
        for cc in ("cl", "gcc", "clang"):
            if shutil.which(cc):
                return cc
    elif sys.platform == "darwin":
        for cc in ("clang", "gcc"):
            if shutil.which(cc):
                return cc
    else:
        for cc in ("gcc", "clang"):
            if shutil.which(cc):
                return cc
    return None


def _find_cxx_compiler() -> str | None:
    """Find a C++ compiler available on this platform."""
    if sys.platform == "win32":
        for cxx in ("cl", "g++", "clang++"):
            if shutil.which(cxx):
                return cxx
    elif sys.platform == "darwin":
        for cxx in ("clang++", "g++"):
            if shutil.which(cxx):
                return cxx
    else:
        for cxx in ("g++", "clang++"):
            if shutil.which(cxx):
                return cxx
    return None


def _compile_shared(src: Path, out: Path) -> None:
    """Compile *src* into a shared library at *out*.

    Cross-platform: uses the appropriate compiler and flags for the current OS.
    Raises ``pytest.fail`` (not skip) on compiler errors so that broken
    fixtures are surfaced immediately rather than silently green-skipped.
    Skip is only appropriate when the *tool* (compiler) is absent.
    """
    is_cpp = src.suffix in (".cpp",)

    if is_cpp:
        compiler = _find_cxx_compiler()
    else:
        compiler = _find_compiler()

    if not compiler:
        lang = "C++" if is_cpp else "C"
        pytest.skip(f"No {lang} compiler found in PATH")

    if compiler == "cl":
        # MSVC
        args = [compiler, "/LD", "/Zi", "/Fe:" + str(out), str(src)]
    elif sys.platform == "darwin":
        args = [compiler, "-dynamiclib", "-g", "-Og", "-fvisibility=default",
                "-install_name", "@rpath/lib.dylib",
                "-o", str(out), str(src)]
    else:
        args = [compiler, "-shared", "-fPIC", "-g", "-Og", "-fvisibility=default",
                "-o", str(out), str(src)]

    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.fail(
            f"Compile failed for {src.name} (exit {r.returncode}):\n{r.stderr[:800]}"
        )


def _build_with_cmake(
    case_dir: Path,
    build_dir: Path,
    *,
    already_configured: bool = False,
) -> tuple[Path, Path]:
    """Build a case using CMake. Returns (v1_lib, v2_lib) paths.

    When *already_configured* is True the expensive ``cmake -S … -B …``
    configure step is skipped — the caller guarantees that the build tree
    has already been configured (e.g. via the session-scoped
    ``shared_cmake_build_dir`` fixture).
    """
    cmake = shutil.which("cmake")
    if not cmake:
        pytest.skip("cmake not found in PATH")

    case_name = case_dir.name
    case_out = build_dir / case_name

    # Configure — only when no pre-configured build tree is provided
    if not already_configured:
        r = subprocess.run(
            [cmake, "-S", str(case_dir.parent), "-B", str(build_dir),
             "-DCMAKE_BUILD_TYPE=Debug"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            pytest.fail(f"cmake configure failed for {case_name}:\n{r.stderr[:600]}")

    # Build only this case's targets (--config for multi-config generators).
    # Under pytest-xdist, serialize cmake --build calls on the shared build
    # tree to avoid concurrent writes to shared CMake internal state (e.g.
    # .ninja_log, CMakeCache reads) which can be flaky on Windows/NTFS.
    v1_target = f"{case_name}_v1"
    v2_target = f"{case_name}_v2"
    build_cmd = [cmake, "--build", str(build_dir), "--target", v1_target,
                 v2_target, "--config", "Debug"]

    is_xdist = os.environ.get("PYTEST_XDIST_WORKER") is not None
    if is_xdist and already_configured and _filelock is not None:
        lock_path = build_dir.parent / "cmake_build.lock"
        with _filelock.FileLock(str(lock_path), timeout=300):
            r = subprocess.run(
                build_cmd, capture_output=True, text=True, timeout=120,
            )
    else:
        r = subprocess.run(
            build_cmd, capture_output=True, text=True, timeout=120,
        )
    if r.returncode != 0:
        pytest.fail(f"cmake build failed for {case_name}:\n{r.stderr[:600]}")

    # Find the built libraries
    v1_lib = _find_built_lib(case_out, "v1")
    v2_lib = _find_built_lib(case_out, "v2")

    if not v1_lib or not v2_lib:
        pytest.fail(
            f"{case_name}: CMake build did not produce libv1/libv2 in {case_out}\n"
            f"Contents: {list(case_out.iterdir()) if case_out.exists() else 'dir missing'}"
        )

    return v1_lib, v2_lib


def _find_built_lib(directory: Path, name: str) -> Path | None:
    """Find a shared library named *name* in *directory* (any platform extension).

    Also checks common multi-config generator subdirectories (Debug/, Release/)
    in case the per-config output directory overrides were not applied.
    """
    if not directory.exists():
        return None
    # Directories to search: the directory itself, then config subdirs
    search_dirs = [directory]
    for cfg in ("Debug", "Release", "RelWithDebInfo", "MinSizeRel"):
        sub = directory / cfg
        if sub.is_dir():
            search_dirs.append(sub)
    for search_dir in search_dirs:
        for prefix in ("lib", ""):
            for suffix in (".so", ".dylib", ".dll"):
                lib = search_dir / f"{prefix}{name}{suffix}"
                if lib.exists():
                    return lib
    return None


# ---------------------------------------------------------------------------
# Auto-discovery: build test parameter list
# ---------------------------------------------------------------------------
def _collect_cases() -> list[tuple[str, str | None]]:
    cases = []
    for d in sorted(EXAMPLES_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("case"):
            continue
        expected = EXPECTED.get(d.name, "UNKNOWN")
        cases.append((d.name, expected))
    return cases


_ALL_CASES = _collect_cases()

# ---------------------------------------------------------------------------
# Parametrized integration test
# ---------------------------------------------------------------------------
def _header_list(hdr: Path | None) -> list[Path]:
    """Return a single-element list if *hdr* exists, else an empty list."""
    return [hdr] if hdr and hdr.exists() else []


def _build_via_cmake(
    case_dir: Path,
    tmp_path: Path,
    shared_cmake_build_dir: Path | None,
) -> tuple[Path, Path] | None:
    """Try to build with CMake. Returns (v1_lib, v2_lib) or None."""
    has_cmake_file = (case_dir / "CMakeLists.txt").exists()
    has_cmake = bool(shutil.which("cmake"))
    if not has_cmake_file or not has_cmake:
        return None

    if shared_cmake_build_dir is not None:
        return _build_with_cmake(
            case_dir, shared_cmake_build_dir, already_configured=True,
        )
    build_dir = tmp_path / "cmake_build"
    return _build_with_cmake(case_dir, build_dir)


def _build_direct_or_skip(
    case_name: str,
    case_dir: Path,
    tmp_path: Path,
    v1_src: Path,
    v2_src: Path,
) -> tuple[Path, Path]:
    """Compile directly, or skip if CMakeLists.txt needs special flags."""
    if (case_dir / "CMakeLists.txt").exists():
        cmake_text = (case_dir / "CMakeLists.txt").read_text()
        _special = ("FORCE_INCLUDE", "LINK_OPTIONS", "COMPILE_OPTIONS",
                    "fvisibility", "version-script", "soname")
        if any(tok in cmake_text for tok in _special):
            pytest.skip(
                f"{case_name} requires cmake (CMakeLists.txt has special "
                f"build flags) but cmake is not in PATH"
            )
    v1_lib = tmp_path / f"libv1{SHARED_LIB_SUFFIX}"
    v2_lib = tmp_path / f"libv2{SHARED_LIB_SUFFIX}"
    _compile_shared(v1_src, v1_lib)
    _compile_shared(v2_src, v2_lib)
    return v1_lib, v2_lib


def _build_libs_for_case(
    case_name: str,
    case_dir: Path,
    tmp_path: Path,
    shared_cmake_build_dir: Path | None,
    v1_src: Path,
    v2_src: Path,
) -> tuple[Path, Path]:
    """Build v1 and v2 shared libraries using the best available strategy."""
    cmake_result = _build_via_cmake(case_dir, tmp_path, shared_cmake_build_dir)
    if cmake_result is not None:
        return cmake_result
    return _build_direct_or_skip(case_name, case_dir, tmp_path, v1_src, v2_src)


def _normalize_verdict(v: str) -> str:
    """Normalize verdict for comparison."""
    return "COMPATIBLE" if v in ("API_BREAK", "COMPATIBLE") else v


def _dump_and_compare(
    case_name: str,
    v1_lib: Path,
    v2_lib: Path,
    headers_v1: list[Path],
    headers_v2: list[Path],
    scope_to_public_surface: bool = False,
) -> tuple[str, list]:
    """Run abicheck dump+compare and return (verdict, changes)."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    try:
        snap1 = dump(v1_lib, headers=headers_v1, version="v1")
        snap2 = dump(v2_lib, headers=headers_v2, version="v2")
    except Exception as exc:
        pytest.fail(f"{case_name}: dump failed: {exc}")

    result = compare(snap1, snap2, scope_to_public_surface=scope_to_public_surface)
    return result.verdict.value.upper(), result.changes


def _assert_verdict(
    case_name: str,
    expected_verdict: str,
    got: str,
    changes: list,
) -> None:
    """Assert that the verdict matches, handling known gaps as xfail."""
    if case_name in KNOWN_GAPS:
        if _normalize_verdict(got) != _normalize_verdict(expected_verdict):
            pytest.xfail(KNOWN_GAPS[case_name])

    assert _normalize_verdict(got) == _normalize_verdict(expected_verdict), (
        f"{case_name}: expected={expected_verdict!r}, got={got!r}\n"
        f"Changes:\n" +
        "\n".join(f"  {c.kind.value}: {c.description}" for c in changes)
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "case_name,expected_verdict",
    [(c, e) for c, e in _ALL_CASES if e is not None],
    ids=[c for c, e in _ALL_CASES if e is not None],
)
def test_example_pipeline(
    case_name: str,
    expected_verdict: str,
    tmp_path: Path,
    shared_cmake_build_dir: Path | None,
) -> None:
    """Compile → dump → compare for every example case."""
    # --- Platform filter ---
    case_platforms = PLATFORMS.get(case_name, ["linux", "macos", "windows"])
    if CURRENT_PLATFORM not in case_platforms:
        pytest.skip(
            f"{case_name} not supported on {CURRENT_PLATFORM} "
            f"(requires {case_platforms})"
        )

    if case_name in BUILD_INFO_CASES:
        pytest.skip(
            f"{case_name}: L3 build-info case — validated by validate_examples.py "
            "(the Python compare() API used here does not run the build-evidence diff)"
        )

    if not shutil.which("castxml"):
        pytest.skip("castxml not found in PATH")

    # --- Compiler-feature filter ---
    # Some cases use language features the native toolchain may not implement
    # (e.g. C23 _BitInt needs GCC 14+). The fixture cannot compile at all on
    # such toolchains, so skip rather than fail.
    feature = REQUIRES_FEATURE.get(case_name)
    if feature is not None:
        from tests.feature_probe import compiler_supports
        if not compiler_supports(feature):
            pytest.skip(
                f"{case_name} requires compiler feature {feature!r} which the "
                f"native toolchain does not support"
            )

    case_dir = EXAMPLES_DIR / case_name
    assert case_dir.is_dir(), f"Case directory not found: {case_dir}"

    v1_src, v2_src, v1_hdr, v2_hdr = _find_sources(case_dir)

    v1_lib, v2_lib = _build_libs_for_case(
        case_name, case_dir, tmp_path, shared_cmake_build_dir, v1_src, v2_src,
    )

    headers_v1 = _header_list(v1_hdr)
    headers_v2 = _header_list(v2_hdr)

    got, changes = _dump_and_compare(
        case_name, v1_lib, v2_lib, headers_v1, headers_v2,
        scope_to_public_surface=SCOPE_PUBLIC_HEADERS.get(case_name, False),
    )

    _assert_verdict(case_name, expected_verdict, got, changes)
