# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.

# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""validate_examples.py — standalone CLI validation of all abicheck example cases.

Reads expected verdicts from examples/ground_truth.json, compiles each
example with the platform's native compiler, runs abicheck dump+compare,
and reports results.

Cross-platform: respects the ``platforms`` field in ground_truth.json and
uses CMake when a CMakeLists.txt is present.  Falls back to direct
compilation for simple cases.

Usage:
    python tests/validate_examples.py                   # all cases
    python tests/validate_examples.py case01 case07     # filter by name substring
    python tests/validate_examples.py --fail-fast       # stop on first failure
    python tests/validate_examples.py --json            # machine-readable output

Exit codes:
    0  all pass (known gaps are xfail, not failures)
    1  one or more unexpected failures
    2  environment error (tools missing, etc.)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

REPO_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"

ARTIFACT_VARIANTS = (
    "debug-headers",
    "release-headers",
    "stripped-headers",
    "build-source",
)
DEFAULT_ARTIFACT_VARIANT = "debug-headers"
JSON_SCHEMA_VERSION = "validate_examples.v2"

SOURCE_LAYERS_BY_VARIANT = {
    "debug-headers": ("L0", "L1", "L2"),
    "release-headers": ("L0", "L2"),
    "stripped-headers": ("L0", "L2"),
    "build-source": ("L0", "L1", "L2", "L3", "L4", "L5"),
}


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
    """Return the shared-library suffix for the current platform."""
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"


SHARED_LIB_SUFFIX = _shared_lib_suffix()


def _find_compiler(is_cpp: bool = False) -> str | None:
    """Find a C/C++ compiler available on this platform."""
    if is_cpp:
        candidates = {
            "win32": ["cl", "g++", "clang++"],
            "darwin": ["clang++", "g++"],
        }.get(sys.platform, ["g++", "clang++"])
    else:
        candidates = {
            "win32": ["cl", "gcc", "clang"],
            "darwin": ["clang", "gcc"],
        }.get(sys.platform, ["gcc", "clang"])
    for cc in candidates:
        if shutil.which(cc):
            return cc
    return None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
class CaseResult(NamedTuple):
    name: str
    status: str          # PASS | FAIL | XFAIL | SKIP | ERROR
    expected: str | None
    got: str | None
    message: str
    variant: str = DEFAULT_ARTIFACT_VARIANT
    seconds: float = 0.0
    source_layers: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hdr(base: Path, stem: str) -> Path | None:
    """Find a header file (.h or .hpp) with the given stem in *base*."""
    for ext in (".h", ".hpp"):
        h = base / f"{stem}{ext}"
        if h.exists():
            return h
    return None


def _try_v1v2_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try v1/v2 layout: case_dir/v1.c(pp) + v2.c(pp)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"v1{ext}"
        if v1.exists():
            v2 = case_dir / f"v2{ext}"
            if not v2.exists() and case_dir.name == "case04_no_change":
                v2 = v1
            if v2.exists():
                return v1, v2, _hdr(case_dir, "v1"), _hdr(case_dir, "v2")
    return None


def _try_old_new_layout(
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
            if v2.exists():
                return v1, v2, _hdr(old_dir, "lib"), _hdr(new_dir, "lib")
    return None


def _try_good_bad_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try good/bad layout: case_dir/bad.c(pp) + good.c(pp)."""
    for ext in (".c", ".cpp"):
        bad = case_dir / f"bad{ext}"
        if bad.exists():
            good = case_dir / f"good{ext}"
            if good.exists():
                return bad, good, _hdr(case_dir, "bad"), _hdr(case_dir, "good")
    return None


def _try_libfoo_layout(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Try libfoo_v1/v2 layout: case_dir/libfoo_v1.c(pp) + libfoo_v2.c(pp)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"libfoo_v1{ext}"
        if v1.exists():
            v2 = case_dir / f"libfoo_v2{ext}"
            if v2.exists():
                return v1, v2, _hdr(case_dir, "foo_v1"), _hdr(case_dir, "foo_v2")
    return None


def _find_sources(
    case_dir: Path,
) -> tuple[Path, Path, Path | None, Path | None] | None:
    """Return (v1_src, v2_src, v1_hdr, v2_hdr) or None if no layout matched."""
    for finder in (
        _try_v1v2_layout,
        _try_old_new_layout,
        _try_good_bad_layout,
        _try_libfoo_layout,
    ):
        result = finder(case_dir)
        if result is not None:
            return result
    return None


def _compile(src: Path, out: Path, *, variant: str = DEFAULT_ARTIFACT_VARIANT) -> str | None:
    """Compile src → shared lib. Returns error string on failure, None on success."""
    is_cpp = src.suffix == ".cpp"
    compiler = _find_compiler(is_cpp)
    if not compiler:
        return f"no {'C++' if is_cpp else 'C'} compiler found"

    stock_variant = variant in {"release-headers", "build-source"}

    if compiler == "cl":
        args = [compiler, "/LD", "/Fe:" + str(out), str(src)]
        args.insert(2, "/O2" if stock_variant else "/Zi")
    elif sys.platform == "darwin":
        opt_flags = ["-O2"] if stock_variant else ["-g", "-Og"]
        args = [compiler, "-dynamiclib", *opt_flags, "-fvisibility=default",
                "-install_name", "@rpath/lib.dylib",
                "-o", str(out), str(src)]
    else:
        opt_flags = ["-O2"] if stock_variant else ["-g", "-Og"]
        args = [compiler, "-shared", "-fPIC", *opt_flags, "-fvisibility=default",
                "-o", str(out), str(src)]

    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return None if r.returncode == 0 else r.stderr[:600]


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


def _build_with_cmake(
    case_dir: Path,
    build_dir: Path,
    *,
    variant: str = DEFAULT_ARTIFACT_VARIANT,
) -> tuple[Path | None, Path | None, str]:
    """Build a case using CMake. Returns (v1_lib, v2_lib, error_msg)."""
    cmake = shutil.which("cmake")
    if not cmake:
        return None, None, "cmake not found"

    case_name = case_dir.name
    case_out = build_dir / case_name
    build_type = "Release" if variant in {"release-headers", "build-source"} else "Debug"

    r = subprocess.run(
        [cmake, "-S", str(case_dir.parent), "-B", str(build_dir),
         f"-DCMAKE_BUILD_TYPE={build_type}",
         "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None, None, f"cmake configure failed: {r.stderr[:300]}"

    v1_target = f"{case_name}_v1"
    v2_target = f"{case_name}_v2"
    r = subprocess.run(
        [cmake, "--build", str(build_dir), "--target", v1_target, v2_target,
         "--config", build_type],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return None, None, f"cmake build failed: {r.stderr[:300]}"

    v1_lib = _find_built_lib(case_out, "v1")
    v2_lib = _find_built_lib(case_out, "v2")

    if not v1_lib or not v2_lib:
        return None, None, f"CMake did not produce libv1/libv2 in {case_out}"

    return v1_lib, v2_lib, ""


def _normalize_verdict(v: str) -> str:
    """Normalize verdict for comparison.

    Must stay in sync with the _normalize helper in test_example_autodiscovery.py.
    API_BREAK and COMPATIBLE are treated as equivalent because the checker may
    return either depending on header availability and castxml parsing.
    """
    return "COMPATIBLE" if v in ("API_BREAK", "COMPATIBLE") else v


# ---------------------------------------------------------------------------
# Core: build / dump+compare / verdict helpers
# ---------------------------------------------------------------------------
def _build_libs(
    name: str,
    case_dir: Path,
    tmp: Path,
    v1_src: Path,
    v2_src: Path,
    *,
    variant: str = DEFAULT_ARTIFACT_VARIANT,
) -> tuple[Path | None, Path | None, str | None]:
    """Build v1 and v2 shared libraries. Returns (v1_so, v2_so, error_or_skip).

    *error_or_skip* is None on success, starts with "SKIP:" for skippable
    issues, or is a plain error message otherwise.
    """
    has_cmake_file = (case_dir / "CMakeLists.txt").exists()
    has_cmake = bool(shutil.which("cmake"))

    if has_cmake_file and has_cmake:
        cmake_build = tmp / "cmake_build"
        v1_so, v2_so, err = _build_with_cmake(case_dir, cmake_build, variant=variant)
        if err:
            return None, None, err
        if variant == "stripped-headers":
            strip_err = _strip_debug_info(v1_so, v2_so)
            if strip_err:
                return None, None, strip_err
        return v1_so, v2_so, None

    if has_cmake_file and not has_cmake:
        cmake_text = (case_dir / "CMakeLists.txt").read_text()
        _special = ("FORCE_INCLUDE", "LINK_OPTIONS", "COMPILE_OPTIONS",
                     "fvisibility", "version-script", "soname")
        if any(tok in cmake_text for tok in _special):
            return None, None, "SKIP:requires cmake (CMakeLists.txt has special build flags)"

    # Direct compilation (no CMakeLists.txt, or cmake absent but no special flags)
    v1_so = tmp / f"libv1{SHARED_LIB_SUFFIX}"
    v2_so = tmp / f"libv2{SHARED_LIB_SUFFIX}"
    err = _compile(v1_src, v1_so, variant=variant)
    if err:
        return None, None, f"compile v1 failed: {err[:200]}"
    err = _compile(v2_src, v2_so, variant=variant)
    if err:
        return None, None, f"compile v2 failed: {err[:200]}"
    if variant == "stripped-headers":
        strip_err = _strip_debug_info(v1_so, v2_so)
        if strip_err:
            return None, None, strip_err
    return v1_so, v2_so, None


def _strip_debug_info(*libs: Path) -> str | None:
    """Strip debug information from built libraries for release-with-headers checks."""
    strip = shutil.which("strip")
    if not strip:
        return "SKIP:strip tool not found"
    if sys.platform == "win32":
        return (
            "SKIP:Windows PE/PDB require a different strip tool/flags "
            "(not implemented)"
        )
    flags = ["-S"] if sys.platform == "darwin" else ["-g"]
    for lib in libs:
        r = subprocess.run([strip, *flags, str(lib)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return f"strip failed for {lib.name}: {r.stderr[:200]}"
    return None


def _build_info_path(
    case_dir: Path | None, stem: str, build_info: bool = False
) -> Path | None:
    """Return the per-side L3 build-info file for *stem* (``v1``/``v2``) if any.

    A case opts into L3 build-evidence comparison by declaring ``build_info: true``
    in ``ground_truth.json`` *and* shipping a ``<stem>.compile_commands.json`` next
    to its sources; the dump then embeds that build context so ``compare`` runs the
    build-evidence diff (the source of the runtime-model-flip findings). The
    ground-truth flag is the contract — file presence alone never silently upgrades
    a case to L3. Pure/​unit-testable: only checks for the file, no I/O beyond
    ``exists()``.
    """
    if case_dir is None or not build_info:
        return None
    candidate = case_dir / f"{stem}.compile_commands.json"
    return candidate if candidate.exists() else None


def _sources_path(
    case_dir: Path | None, stem: str, sources: bool = False
) -> Path | None:
    """Return the per-side L4/L5 source tree for *stem* (``v1``/``v2``) if any.

    A case opts into source ABI replay (L4) + the source graph (L5) by declaring
    ``sources: true`` in ``ground_truth.json`` *and* shipping a ``<stem>.sources/``
    directory next to its sources; ``dump --sources`` then runs the replay inline
    and embeds it. Like ``_build_info_path`` the ground-truth flag is the contract
    — directory presence alone never silently upgrades a case to L4. Replay needs
    a C++ front-end (clang/castxml); the runner skips the case when it is absent.
    Pure/unit-testable: only checks for the directory, no I/O beyond ``is_dir()``.
    """
    if case_dir is None or not sources:
        return None
    candidate = case_dir / f"{stem}.sources"
    return candidate if candidate.is_dir() else None


def _dump_and_compare(
    tmp: Path,
    v1_so: Path,
    v2_so: Path,
    v1_hdr: Path | None,
    v2_hdr: Path | None,
    scope_public_headers: bool = False,
    old_build_source: Path | None = None,
    new_build_source: Path | None = None,
    case_dir: Path | None = None,
    old_build_info: Path | None = None,
    new_build_info: Path | None = None,
    sources: bool = False,
) -> tuple[str | None, str | None]:
    """Run abicheck dump+compare. Returns (verdict, error_msg).

    On success *error_msg* is None. On failure *verdict* is None.
    """
    snap1 = tmp / "snap1.json"
    cmd1 = [sys.executable, "-m", "abicheck.cli", "dump", str(v1_so), "-o", str(snap1)]
    if v1_hdr and Path(v1_hdr).exists():
        cmd1 += ["-H", str(v1_hdr)]
        old_compile_db = tmp / "old_compile_commands.json"
        if old_build_source is not None and old_compile_db.exists():
            cmd1 += ["-p", str(old_compile_db)]
    if old_build_info is not None:
        cmd1 += ["--build-info", str(old_build_info)]
    sr1 = _sources_path(case_dir, "v1", sources)
    if sr1 is not None:
        cmd1 += ["--sources", str(sr1)]
    r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=120)
    if r1.returncode != 0:
        return None, f"dump v1 failed: {r1.stderr[:200]}"

    snap2 = tmp / "snap2.json"
    cmd2 = [sys.executable, "-m", "abicheck.cli", "dump", str(v2_so), "-o", str(snap2)]
    if v2_hdr and Path(v2_hdr).exists():
        cmd2 += ["-H", str(v2_hdr)]
        new_compile_db = tmp / "new_compile_commands.json"
        if new_build_source is not None and new_compile_db.exists():
            cmd2 += ["-p", str(new_compile_db)]
    if new_build_info is not None:
        cmd2 += ["--build-info", str(new_build_info)]
    sr2 = _sources_path(case_dir, "v2", sources)
    if sr2 is not None:
        cmd2 += ["--sources", str(sr2)]
    r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        return None, f"dump v2 failed: {r2.stderr[:200]}"

    compare_cmd = [
        sys.executable, "-m", "abicheck.cli", "compare", str(snap1), str(snap2), "--format", "json",
    ]
    if old_build_source is not None:
        compare_cmd += [
            "--old-build-info", str(old_build_source),
            "--old-sources", str(old_build_source),
        ]
    if new_build_source is not None:
        compare_cmd += [
            "--new-build-info", str(new_build_source),
            "--new-sources", str(new_build_source),
        ]
    # Scoping is on by default since ADR-024 Phase 5; ground_truth.json verdicts
    # are authored unscoped unless the case opts in, so be explicit either way.
    compare_cmd.append(
        "--scope-public-headers" if scope_public_headers else "--no-scope-public-headers"
    )
    rc = subprocess.run(
        compare_cmd,
        capture_output=True, text=True, timeout=60,
    )
    try:
        data = json.loads(rc.stdout)
        return data.get("verdict", "UNKNOWN"), None
    except json.JSONDecodeError:
        return None, f"invalid JSON from compare: {rc.stdout[:200]}"


def _write_compile_db(
    db_path: Path,
    *,
    src: Path,
    case_dir: Path,
    compiler: str,
) -> None:
    """Write a minimal compile_commands.json for source-evidence validation."""
    args = [compiler, "-I", str(case_dir), "-c", str(src)]
    db_path.write_text(json.dumps([{
        "directory": str(case_dir),
        "file": str(src),
        "arguments": args,
    }]))


def _write_source_compile_db(
    tmp: Path,
    side: str,
    src: Path,
    case_dir: Path,
    fallback_compiler: str,
    target_suffix: str,
) -> Path:
    """Write a side-specific compile DB, preserving CMake flags when available."""
    out = tmp / f"{side}_compile_commands.json"
    cmake_db = tmp / "cmake_build" / "compile_commands.json"
    if cmake_db.exists():
        entries = json.loads(cmake_db.read_text())
        src_resolved = src.resolve()
        case_resolved = case_dir.resolve()

        def entry_file(entry: dict) -> Path:
            return Path(str(entry.get("file", ""))).expanduser()

        def same_source(entry: dict) -> bool:
            try:
                return entry_file(entry).resolve() == src_resolved
            except OSError:
                return False

        def same_case_source(entry: dict) -> bool:
            path = entry_file(entry)
            try:
                resolved = path.resolve()
            except OSError:
                return False
            return (
                resolved.name == src.name
                and resolved.parent == case_resolved
            )

        def same_side_target(entry: dict) -> bool:
            args = [str(a) for a in entry.get("arguments", [])]
            command = str(entry.get("command", ""))
            needle = f"{case_dir.name}_{target_suffix}"
            return needle in " ".join([*args, command])

        selected = [
            e for e in entries
            if same_side_target(e)
        ]
        if not selected:
            selected = [
                e for e in entries
                if same_source(e)
            ]
        if not selected:
            selected = [
                e for e in entries
                if same_case_source(e)
            ]
        if selected:
            out.write_text(json.dumps(selected, indent=2))
            return out
    _write_compile_db(out, src=src, case_dir=case_dir, compiler=fallback_compiler)
    return out


def _resolved_build_info_path(
    tmp: Path,
    side: str,
    src: Path,
    case_dir: Path,
    *,
    enabled: bool,
    fallback_compiler: str,
    target_suffix: str,
) -> Path | None:
    """Return the build-info compile DB used for inline L3 comparisons.

    Prefer an explicit per-case ``v1/v2.compile_commands.json`` when the fixture
    carries one, otherwise derive the side-specific entry from the real CMake
    compile database produced during the current build. This keeps examples from
    needing checked-in compile DB files just to make build-mode flags visible.
    """
    if not enabled:
        return None
    explicit = _build_info_path(case_dir, "v1" if side == "old" else "v2", True)
    if explicit is not None:
        return explicit
    cmake_db = tmp / "cmake_build" / "compile_commands.json"
    if cmake_db.exists():
        return _write_source_compile_db(
            tmp,
            side,
            src,
            case_dir,
            fallback_compiler=fallback_compiler,
            target_suffix=target_suffix,
        )
    return None


def _registered_cli_command(*args: str) -> list[str]:
    """Run abicheck CLI with plugin-style commands registered in subprocesses."""
    return [
        sys.executable,
        "-c",
        (
            "import sys; import abicheck.cli_buildsource; "
            "from abicheck.cli import main; "
            "main(args=sys.argv[1:], prog_name='abicheck')"
        ),
        *args,
    ]


def _collect_build_source_evidence(
    tmp: Path,
    case_dir: Path,
    v1_src: Path,
    v2_src: Path,
    v1_so: Path,
    v2_so: Path,
) -> tuple[Path | None, Path | None, str | None]:
    """Collect L3/L4/L5 build-source packs for the build-source artifact variant."""
    if not shutil.which("castxml"):
        return None, None, "SKIP:castxml not found for source-ABI replay"

    results: list[Path] = []
    for side, src, binary in (("old", v1_src, v1_so), ("new", v2_src, v2_so)):
        compiler = _find_compiler(src.suffix == ".cpp")
        if not compiler:
            return None, None, f"SKIP:no compiler found for {side} source evidence"
        db_path = _write_source_compile_db(
            tmp,
            side,
            src,
            case_dir,
            fallback_compiler=compiler,
            target_suffix="v1" if side == "old" else "v2",
        )
        out_dir = tmp / f"{side}.buildsource"
        cmd = _registered_cli_command(
            "collect",
            "--binary", str(binary),
            "--compile-db", str(db_path),
            "--source-root", str(case_dir),
            "--source-abi",
            "--source-abi-extractor", "castxml",
            "--source-abi-scope", "full",
            "--source-graph", "summary",
            "-o", str(out_dir),
        )
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            detail = (r.stderr or r.stdout)[:300]
            return None, None, f"collect {side} failed: {detail}"
        results.append(out_dir)
    return results[0], results[1], None


def _embedded_present_layers(snap_path: Path) -> set[str]:
    """Short tags (``L3``/``L4``/``L5``) for layers the dumped snapshot's embedded
    build_source actually carries with ``present`` coverage.

    Directory/flag presence is not enough: ``dump --sources`` degrades to a
    partial/empty surface (exit 0) when the source-replay front-end is missing or
    no TU parses, so the inline opt-ins must be confirmed from the *real* embedded
    coverage rather than assumed (Codex). Pure JSON parsing — no abicheck import —
    so it stays unit-testable and robust to a hand-edited snapshot.
    """
    layer_tags = {"L3_build": "L3", "L4_source_abi": "L4", "L5_source_graph": "L5"}
    try:
        data = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    pack = data.get("build_source")
    if not isinstance(pack, dict):
        return set()
    coverage = (pack.get("manifest") or {}).get("coverage") or []
    present: set[str] = set()
    for row in coverage:
        if not isinstance(row, dict):
            continue
        tag = layer_tags.get(str(row.get("layer", "")))
        if tag and str(row.get("status", "")) == "present":
            present.add(tag)
    return present


def _source_layers_for_result(
    variant: str,
    *,
    v1_hdr: Path | None,
    v2_hdr: Path | None,
    old_build_source: Path | None,
    new_build_source: Path | None,
    sources: bool = False,
    build_info: bool = False,
) -> tuple[str, ...]:
    """Return the evidence layers actually supplied for this case result."""
    layers = ["L0"]
    if variant in {"debug-headers", "build-source"}:
        layers.append("L1")
    if v1_hdr and v1_hdr.exists() and v2_hdr and v2_hdr.exists():
        layers.append("L2")
    # Out-of-band build-source packs (build-source variant) carry L3/L4/L5.
    if old_build_source is not None and new_build_source is not None:
        layers.extend(["L3", "L4", "L5"])
    # The inline `--sources` opt-in (ground_truth `sources: true`) runs
    # `dump --sources`, which resolves a compile DB (L3), replays the source
    # ABI (L4), and folds the source graph (L5) inline — report those layers so
    # the JSON artifact does not under-count the case as L0/L1/L2 (Codex). The
    # decoupled `--build-info` opt-in supplies L3 only.
    if sources:
        layers.extend(["L3", "L4", "L5"])
    elif build_info:
        layers.append("L3")
    # De-duplicate while preserving first-seen order (the build-source variant
    # and inline --sources can both contribute L3/L4/L5).
    seen: dict[str, None] = {}
    for layer in layers:
        seen.setdefault(layer, None)
    return tuple(seen)


def _evaluate_verdict(
    name: str,
    expected_raw: str | None,
    got: str,
    known_gap: str | None,
    allow_risk_for_compatible: bool = False,
) -> CaseResult:
    """Compare *got* verdict against *expected_raw* and return a CaseResult."""
    expected = expected_raw or "UNKNOWN"
    if (
        allow_risk_for_compatible
        and expected == "COMPATIBLE"
        and got == "COMPATIBLE_WITH_RISK"
    ):
        return CaseResult(name, "PASS", expected_raw, got, "")
    if _normalize_verdict(got) == _normalize_verdict(expected):
        return CaseResult(name, "PASS", expected_raw, got, "")
    if known_gap:
        return CaseResult(name, "XFAIL", expected_raw, got, known_gap)
    return CaseResult(name, "FAIL", expected_raw, got, f"expected={expected!r} got={got!r}")


# ---------------------------------------------------------------------------
# Core: run one case
# ---------------------------------------------------------------------------
def _check_case_preconditions(
    name: str,
    entry: dict,
) -> CaseResult | None:
    """Check skip/platform preconditions. Returns a CaseResult to skip, or None to proceed."""
    expected_raw = entry.get("expected")

    if entry.get("skip", False):
        return CaseResult(name, "SKIP", expected_raw, None, entry.get("reason", "skip=true"))

    # Bundle cases (ADR-023) are multi-library and use a different layout
    # (per-side dirs under examples/<case>/{old,new}/<libname>.cpp).
    # The v1/v2-pair compile path in this script can't build them; they
    # have their own integration tests in tests/test_bundle.py.
    if entry.get("category") == "bundle" or entry.get("bundle") is True:
        return CaseResult(name, "SKIP", expected_raw, None,
                          "bundle case — exercised by tests/test_bundle.py (ADR-023)")

    platforms = entry.get("platforms", ["linux", "macos", "windows"])
    if CURRENT_PLATFORM not in platforms:
        return CaseResult(name, "SKIP", expected_raw, None,
                          f"not supported on {CURRENT_PLATFORM} (requires {platforms})")

    # Skip cases whose required compiler feature is unavailable (e.g. C23
    # _BitInt on GCC < 14): the fixture cannot compile, so it is not a FAIL.
    feature = entry.get("requires_feature")
    if feature is not None:
        from feature_probe import compiler_supports
        if not compiler_supports(feature):
            return CaseResult(name, "SKIP", expected_raw, None,
                              f"compiler lacks required feature {feature!r}")
    return None


def _resolve_case_sources(
    name: str,
    expected_raw: str | None,
) -> tuple[Path, tuple[Path, Path, Path | None, Path | None]] | CaseResult:
    """Resolve the case directory and source files.

    Returns (case_dir, sources_tuple) on success, or a CaseResult on error.
    """
    case_dir = EXAMPLES_DIR / name
    if not case_dir.is_dir():
        return CaseResult(name, "ERROR", expected_raw, None, "directory not found")

    sources = _find_sources(case_dir)
    if sources is None:
        return CaseResult(name, "ERROR", expected_raw, None,
                          "no recognised source layout (harness error — fix example or mark skip in ground_truth.json)")
    return case_dir, sources


def _handle_build_error(
    name: str,
    expected_raw: str | None,
    build_err: str,
) -> CaseResult:
    """Convert a build error string into the appropriate CaseResult."""
    if build_err.startswith("SKIP:"):
        return CaseResult(name, "SKIP", expected_raw, None, build_err[5:])
    return CaseResult(name, "ERROR", expected_raw, None, build_err)


def run_case(
    name: str,
    entry: dict,
    tmp_base: Path,
    fail_fast: bool = False,
    variant: str = DEFAULT_ARTIFACT_VARIANT,
) -> CaseResult:
    """Build, compare, and evaluate one example case."""
    expected_raw = entry.get("expected")
    known_gap = entry.get("known_gap")

    skip_result = _check_case_preconditions(name, entry)
    if skip_result is not None:
        return skip_result._replace(variant=variant)

    resolved = _resolve_case_sources(name, expected_raw)
    if isinstance(resolved, CaseResult):
        return resolved._replace(variant=variant)
    case_dir, (v1_src, v2_src, v1_hdr, v2_hdr) = resolved

    tmp = tmp_base / name
    if variant != DEFAULT_ARTIFACT_VARIANT:
        tmp = tmp_base / f"{name}__{variant}"
    tmp.mkdir(parents=True)

    # Build
    v1_so, v2_so, build_err = _build_libs(
        name, case_dir, tmp, v1_src, v2_src, variant=variant
    )
    if build_err is not None:
        res = _handle_build_error(name, expected_raw, build_err)
        return res._replace(variant=variant)

    old_build_source = new_build_source = None
    if variant == "build-source":
        old_build_source, new_build_source, ev_err = _collect_build_source_evidence(
            tmp, case_dir, v1_src, v2_src, v1_so, v2_so
        )
        if ev_err is not None:
            if ev_err.startswith("SKIP:"):
                return CaseResult(name, "SKIP", expected_raw, None, ev_err[5:], variant)
            return CaseResult(name, "ERROR", expected_raw, None, ev_err, variant)

    old_build_info = new_build_info = None
    if entry.get("build_info"):
        compiler = _find_compiler(v1_src.suffix == ".cpp")
        if compiler:
            old_build_info = _resolved_build_info_path(
                tmp,
                "old",
                v1_src,
                case_dir,
                enabled=True,
                fallback_compiler=compiler,
                target_suffix="v1",
            )
        compiler = _find_compiler(v2_src.suffix == ".cpp")
        if compiler:
            new_build_info = _resolved_build_info_path(
                tmp,
                "new",
                v2_src,
                case_dir,
                enabled=True,
                fallback_compiler=compiler,
                target_suffix="v2",
            )

    # Dump + compare
    got, dc_err = _dump_and_compare(
        tmp, v1_so, v2_so, v1_hdr, v2_hdr,
        scope_public_headers=bool(entry.get("scope_public_headers", False)),
        old_build_source=old_build_source,
        new_build_source=new_build_source,
        case_dir=case_dir,
        old_build_info=old_build_info,
        new_build_info=new_build_info,
        sources=bool(entry.get("sources", False)),
    )
    if dc_err is not None:
        return CaseResult(name, "ERROR", expected_raw, None, dc_err, variant)

    allow_risk = bool(entry.get("bad_practice") or entry.get("category") == "quality")
    # Report L3/L4/L5 from the inline opt-ins only when both dumped snapshots
    # actually embedded those layers with `present` coverage. Directory/flag
    # presence is insufficient: `_dump_and_compare` omits `--sources` when the
    # tree is missing, AND inline replay degrades to an empty surface (exit 0)
    # when the source front-end is absent, so a misconfigured/degraded case must
    # not claim source-replay/graph coverage it never produced (Codex).
    present1 = _embedded_present_layers(tmp / "snap1.json")
    present2 = _embedded_present_layers(tmp / "snap2.json")
    both_present = present1 & present2
    sources_present = "L4" in both_present
    build_info_present = "L3" in both_present
    source_layers = _source_layers_for_result(
        variant,
        v1_hdr=v1_hdr,
        v2_hdr=v2_hdr,
        old_build_source=old_build_source,
        new_build_source=new_build_source,
        sources=sources_present,
        build_info=build_info_present,
    )
    return _evaluate_verdict(
        name, expected_raw, got, known_gap,
        allow_risk_for_compatible=allow_risk,
    )._replace(variant=variant, source_layers=source_layers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _check_prerequisites() -> str | None:
    """Verify that required tools and modules are available.

    Returns an error message string on failure, or None if all prerequisites
    are satisfied.
    """
    cc = _find_compiler(False)
    cxx = _find_compiler(True)
    if not cc and not cxx:
        return "no C or C++ compiler found in PATH"
    if not shutil.which("castxml"):
        return "required tool 'castxml' not found in PATH"
    try:
        __import__("abicheck")
    except Exception as exc:
        return f"abicheck module import failed: {exc}"
    if not GROUND_TRUTH.exists():
        return f"{GROUND_TRUTH} not found"
    return None


def _run_all_cases(
    names: list[str],
    verdicts: dict[str, dict],
    *,
    fail_fast: bool = False,
    json_out: bool = False,
    variants: tuple[str, ...] = (DEFAULT_ARTIFACT_VARIANT,),
) -> list[CaseResult]:
    """Iterate over *names*, run each case, print progress, and return results."""
    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="validate_examples_") as tmp_root:
        tmp_base = Path(tmp_root)
        for variant in variants:
            for name in names:
                started = time.perf_counter()
                res = run_case(name, verdicts[name], tmp_base, variant=variant)
                res = res._replace(seconds=round(time.perf_counter() - started, 3))
                results.append(res)
                if not json_out:
                    icon = {"PASS": "\u2705", "FAIL": "\u274c", "XFAIL": "\u26a0\ufe0f ",
                            "SKIP": "\u23ed\ufe0f ", "ERROR": "\U0001f4a5"}.get(res.status, "?")
                    msg = f"  {res.message}" if res.message else ""
                    print(f"{icon} {res.name:<42}  {res.status} [{res.variant}]{msg}")
                if fail_fast and res.status == "FAIL":
                    return results
    return results


def _summary_counts(results: list[CaseResult]) -> dict[str, int]:
    """Count result statuses."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def _result_to_json(r: CaseResult) -> dict[str, object]:
    """Convert a case result to the JSON artifact schema."""
    d = r._asdict()
    d["component"] = "synthetic-example"
    d["case_id"] = r.name
    d["platform"] = CURRENT_PLATFORM
    d["mode"] = r.variant
    d["source_layers"] = list(
        r.source_layers or SOURCE_LAYERS_BY_VARIANT.get(r.variant, ())
    )
    d["evidence_asymmetry"] = "symmetric"
    d["manual_review_ok"] = r.status in {"XFAIL", "SKIP"}
    return d


def _json_payload(
    results: list[CaseResult],
    *,
    names: list[str],
    variants: tuple[str, ...],
    argv: list[str],
    total_ground_truth_cases: int,
) -> dict[str, object]:
    """Build the top-level JSON payload for a validation run."""
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "runner": "tests/validate_examples.py",
        "platform": CURRENT_PLATFORM,
        "command": [sys.executable, "tests/validate_examples.py", *argv],
        "ground_truth_cases": total_ground_truth_cases,
        "selected_cases": len(names),
        "artifact_variants": list(variants),
        "summary": _summary_counts(results),
        "results": [_result_to_json(r) for r in results],
    }


def _print_summary(
    results: list[CaseResult],
    *,
    json_out: bool,
    json_payload: dict[str, object] | None = None,
) -> int:
    """Print summary of *results* and return exit code (0=pass, 1=fail)."""
    counts = _summary_counts(results)

    if json_out:
        print(json.dumps(json_payload or {
            "schema_version": JSON_SCHEMA_VERSION,
            "summary": counts,
            "results": [_result_to_json(r) for r in results],
        }, indent=2))
    else:
        total = len(results)
        sep = '\u2500' * 60
        print(f"\n{sep}")
        print(f"Total: {total}  " +
              "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    failures = counts.get("FAIL", 0) + counts.get("ERROR", 0)
    if failures:
        for r in results:
            if r.status in ("FAIL", "ERROR"):
                print(f"FAIL: {r.name}  expected={r.expected!r} got={r.got!r}  {r.message}",
                      file=sys.stderr)
    return 1 if failures else 0


def _selected_variants(raw: str) -> tuple[str, ...]:
    """Resolve the CLI artifact-variant selector."""
    if raw == "all":
        return ARTIFACT_VARIANTS
    return (raw,)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for validating example cases."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filters", nargs="*",
                    help="Name substrings to filter cases (default: all)")
    ap.add_argument("--fail-fast", action="store_true",
                    help="Stop after first FAIL")
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="Machine-readable JSON output")
    ap.add_argument("--category", metavar="CAT",
                    help="Filter by category: breaking, compatible, bad_practice, api_break")
    ap.add_argument(
        "--artifact-variant",
        choices=(*ARTIFACT_VARIANTS, "all"),
        default=DEFAULT_ARTIFACT_VARIANT,
        help=(
            "Example artifact profile to validate: debug-headers (current default), "
            "release-headers, stripped-headers, build-source, or all."
        ),
    )
    args = ap.parse_args(argv)

    prereq_err = _check_prerequisites()
    if prereq_err:
        print(f"ERROR: {prereq_err}", file=sys.stderr)
        return 2

    with open(GROUND_TRUTH) as f:
        gt = json.load(f)

    verdicts: dict[str, dict] = gt["verdicts"]

    # Filter cases
    names = sorted(verdicts.keys())
    if args.filters:
        names = [n for n in names if any(f in n for f in args.filters)]
    if args.category:
        names = [n for n in names if verdicts[n].get("category") == args.category]

    variants = _selected_variants(args.artifact_variant)
    results = _run_all_cases(names, verdicts,
                             fail_fast=args.fail_fast, json_out=args.json_out,
                             variants=variants)
    payload = _json_payload(
        results,
        names=names,
        variants=variants,
        argv=list(argv) if argv is not None else sys.argv[1:],
        total_ground_truth_cases=len(verdicts),
    )
    return _print_summary(results, json_out=args.json_out, json_payload=payload)


if __name__ == "__main__":
    sys.exit(main())
