#!/usr/bin/env python3
# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""
Benchmark: abicheck vs ABICC vs abidiff on abicheck examples.

Runs all tools on each example pair (v1/v2) and prints a comparison table.
abidiff is run twice: without headers (ELF-only) and with --headers-dir.
abicheck is run in two modes: compare (dump+compare pipeline) and compat (ABICC drop-in).

Two ABICC modes are supported:
  - abicc_xml:    legacy XML descriptor (no abi-dumper, fast but inaccurate)
  - abicc_dumper: proper abi-dumper workflow (compile with -g, dump ABI, compare)

Supports two case layouts:
  - v1/v2 layout: case_dir/v1.c + case_dir/v2.c (cases 01-18)
  - old/new layout: case_dir/old/lib.c + case_dir/new/lib.c (cases 19+)

Usage:
    python3 scripts/benchmark_comparison.py
    python3 scripts/benchmark_comparison.py --suite pinned74
    python3 scripts/benchmark_comparison.py --abicc-timeout 60
    python3 scripts/benchmark_comparison.py --abicc-mode dumper
    python3 scripts/benchmark_comparison.py --skip-abicc
    python3 scripts/benchmark_comparison.py --skip-compat
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_DIR / "examples"
REPORT_DIR = REPO_DIR / "benchmark_reports"
BUILD_DIR = REPORT_DIR / "_build"

# Evidence-tier model (five sources / L0–L4) lives in a sibling module so it is
# importable without a compiler. See scripts/evidence_tiers.py.
sys.path.insert(0, str(Path(__file__).parent))
import evidence_tiers  # noqa: E402

# Ensure we use abicheck from THIS repo, not any globally-installed version
# (abicheck CLI shebang may point to a different Python/site-packages)
os.environ.setdefault("PYTHONPATH", str(REPO_DIR))

_abicheck_bin = shutil.which("abicheck")
if _abicheck_bin:
    try:
        with open(_abicheck_bin) as _f:
            _first_line = _f.readline().strip()
        if _first_line.startswith("#!"):
            _tokens = shlex.split(_first_line.lstrip("#!"))
            # Handle `#!/usr/bin/env python3` → use token after "env", not "env" itself
            if _tokens and os.path.basename(_tokens[0]) == "env" and len(_tokens) > 1:
                _PYTHON = _tokens[1]
            elif _tokens:
                _PYTHON = _tokens[0]
            else:
                _PYTHON = sys.executable
        else:
            _PYTHON = sys.executable
    except (OSError, IsADirectoryError, IndexError, UnicodeDecodeError):
        _PYTHON = sys.executable
else:
    _PYTHON = sys.executable
_ABICHECK_ENV = {**os.environ, "PYTHONPATH": str(REPO_DIR)}
# True when abicheck CLI is importable via _PYTHON (even without installed bin)
def _abicheck_available() -> bool:
    import subprocess as _sp
    r = _sp.run([_PYTHON, "-m", "abicheck.cli", "--help"],
                capture_output=True, timeout=10, env=_ABICHECK_ENV)
    return r.returncode == 0

_HAS_ABICHECK: bool = _abicheck_available()


DEFAULT_ABICC_TIMEOUT = 90  # seconds; ABICC is the slowest tool and can hang
DEFAULT_ABICHECK_FULL_TIMEOUT = 90  # seconds per dump/compare call
DEFAULT_TIMEOUT = 90  # seconds; shared per-tool-call budget for one example run

# Historical release-pinned cross-tool benchmark:
# cases 01-73 plus the 26b compatible-union edge case.  The full catalog can
# grow freely, while this suite stays stable enough to compare abicheck,
# libabigail, and ABICC across releases.
PINNED_74_CASE_RE = re.compile(r"^case(?:0[1-9]|[1-6][0-9]|7[0-3])_|^case26b_")

# Expected verdicts loaded from ground_truth.json — single source of truth.
# To add/change a verdict, edit examples/ground_truth.json only.
_GT_PATH = Path(__file__).parent.parent / "examples" / "ground_truth.json"
try:
    _gt_data = json.loads(_GT_PATH.read_text())
    if "verdicts" not in _gt_data:
        raise ValueError("missing top-level verdicts key")
    for _k, _v in _gt_data["verdicts"].items():
        if "expected" not in _v:
            raise ValueError(f"case {_k!r} missing expected field")
except (FileNotFoundError, json.JSONDecodeError, ValueError) as _e:
    raise SystemExit(f"ERROR: cannot load {_GT_PATH}: {_e}") from _e

def _expected_or_unknown(value: object) -> str:
    """Return a printable/scorable expected verdict, or '?' for unscored cases."""
    return value if isinstance(value, str) and value else "?"


EXPECTED: dict[str, str] = {
    k: _expected_or_unknown(v["expected"]) for k, v in _gt_data["verdicts"].items()
}
# Per-tool overrides sourced from ground_truth.json:
#   expected_compat — compat mode can't emit API_BREAK (case31, case34)
#   expected_abicc  — ABICC can't emit NO_CHANGE; NO_CHANGE→COMPATIBLE for scoring
EXPECTED_COMPAT: dict[str, str] = {
    k: v["expected_compat"]
    for k, v in _gt_data["verdicts"].items()
    if "expected_compat" in v
}
EXPECTED_ABICC: dict[str, str] = {
    k: ("COMPATIBLE" if EXPECTED[k] == "NO_CHANGE" else EXPECTED[k])
    for k, v in _gt_data["verdicts"].items()
}


@dataclass
class ToolResult:
    verdict: str
    changes: list[str] = field(default_factory=list)
    raw_output: str = ""
    report_path: str = ""
    elapsed_ms: float = 0.0


@dataclass
class Tool:
    name: str
    run_fn: Callable[..., ToolResult]
    col_name: str
    col_width: int = 12
    expected_key: str = "expected"
    ms_key: str = ""
    label: str = ""
    show_slowest: bool = False

    def __post_init__(self) -> None:
        if not self.ms_key:
            self.ms_key = f"{self.name}_ms"
        if not self.label:
            self.label = f"{self.col_name:<20}"


# ── Platform helpers ──────────────────────────────────────────────────────────
def _current_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform

CURRENT_PLATFORM = _current_platform()

def _shared_lib_suffix() -> str:
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"

SHARED_LIB_SUFFIX = _shared_lib_suffix()

def _first_available_tool(*names: str) -> str | None:
    """Return the first available executable path from *names*."""
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None

# Load platform info from ground_truth.json
PLATFORMS: dict[str, list[str]] = {
    k: v.get("platforms", ["linux", "macos", "windows"])
    for k, v in _gt_data["verdicts"].items()
}

def _find_cmake_lib(directory: Path, name: str) -> Path | None:
    """Find a shared library named *name* built by CMake in *directory*.

    Also checks common multi-config generator subdirectories (Debug/, Release/).
    """
    if not directory.exists():
        return None
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


def _find_compiler(is_cpp: bool = False, preferred_family: str | None = None) -> str | None:
    if is_cpp:
        candidates = {"win32": ["cl", "g++", "clang++"],
                       "darwin": ["clang++", "g++"]}.get(sys.platform, ["g++", "clang++"])
    else:
        candidates = {"win32": ["cl", "gcc", "clang"],
                       "darwin": ["clang", "gcc"]}.get(sys.platform, ["gcc", "clang"])

    if preferred_family == "clang":
        if is_cpp:
            pref = ["clang++-18", "clang++", "g++", "cl"]
        else:
            pref = ["clang-18", "clang", "gcc", "cl"]
        # Keep only known candidates while preserving preference.
        candidates = [c for c in pref if c in set(candidates) or c.startswith("clang")]
    elif preferred_family == "gcc":
        if is_cpp:
            pref = ["g++", "clang++", "cl"]
        else:
            pref = ["gcc", "clang", "cl"]
        candidates = [c for c in pref if c in set(candidates)]

    for cc in candidates:
        if shutil.which(cc):
            return cc
    return None


# ── Compile ───────────────────────────────────────────────────────────────────
def compile_so(
    src: Path,
    out_so: Path,
    *,
    preferred_family: str | None = None,
    extra_link_opts: list[str] | None = None,
) -> bool:
    is_cpp = src.suffix == ".cpp"
    compiler = _find_compiler(is_cpp, preferred_family=preferred_family)
    if not compiler:
        print(f"    [compile error] no {'C++' if is_cpp else 'C'} compiler found")
        return False

    if compiler == "cl":
        args = [compiler, "/LD", "/Zi", "/Fe:" + str(out_so), str(src)]
    elif sys.platform == "darwin":
        args = [compiler, "-dynamiclib", "-g", "-Og", "-fvisibility=default",
                "-install_name", "@rpath/lib.dylib",
                "-o", str(out_so), str(src)]
    else:
        args = [compiler, "-shared", "-fPIC", "-g", "-Og", "-fvisibility=default",
                "-o", str(out_so), str(src)]
        if extra_link_opts:
            args.extend(extra_link_opts)

    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    [compile error] {src.name}: {r.stderr[:120]}")
    return r.returncode == 0


def _fallback_link_opts(case_dir: Path, src: Path) -> list[str]:
    """Best-effort linker options for direct compilation fallback.

    Preserves case-specific version-script semantics when CMake isn't used.
    """
    if sys.platform.startswith("linux"):
        # case65: explicit per-version scripts (v1.map / v2.map)
        if src.stem == "v1" and (case_dir / "v1.map").exists():
            return [f"-Wl,--version-script={case_dir / 'v1.map'}"]
        if src.stem == "v2" and (case_dir / "v2.map").exists():
            return [f"-Wl,--version-script={case_dir / 'v2.map'}"]
        # case13: v2/good side has symbol version script
        if src.stem == "good" and (case_dir / "libfoo.map").exists():
            return [f"-Wl,--version-script={case_dir / 'libfoo.map'}"]
    return []


def make_header(src: Path, out_h: Path) -> None:
    """Copy explicit .h/.hpp if present; generate minimal header for plain C."""
    for ext in (".h", ".hpp"):
        h = src.with_suffix(ext)
        if h.exists():
            shutil.copy(h, out_h)
            return
    if src.suffix == ".c":
        lines = []
        for line in src.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("/*", "*", "//")):
                lines.append(line)
                continue
            if "{" in stripped and not stripped.startswith("#"):
                decl = stripped.split("{")[0].strip().rstrip()
                if decl and not decl.endswith(";"):
                    lines.append(decl + ";")
            elif "}" not in stripped:
                lines.append(line)
        out_h.write_text("\n".join(lines))


def _best_h(name: str, bdir_h: Path, src_dir: Path) -> Path:
    """Prefer explicit header in src_dir, fall back to generated copy."""
    for ext in (".h", ".hpp"):
        p = src_dir / f"{name}{ext}"
        if p.exists():
            return p
    return bdir_h


def _resolve_headers_dir(case_dir: Path, v1_h: Path | None, v2_h: Path | None) -> str | None:
    """Return a headers directory for abidiff, or None if no header is available."""
    if v1_h and v1_h.exists():
        return str(v1_h.parent)
    if v2_h and v2_h.exists():
        return str(v2_h.parent)
    return None


# ── Case layout detection ─────────────────────────────────────────────────────
_SourceResult = tuple[Path | None, Path | None, Path | None, Path | None]
_NO_SOURCES: _SourceResult = (None, None, None, None)


def _header_ext(ext: str) -> str:
    """Map source extension to header extension."""
    return ".h" if ext == ".c" else ".hpp"


def _find_header(directory: Path, stem: str) -> Path | None:
    """Find a header file by stem, preferring .hpp over .h."""
    for hext in (".hpp", ".h"):
        p = directory / f"{stem}{hext}"
        if p.exists():
            return p
    return None


def _try_v1v2_layout(case_dir: Path) -> _SourceResult:
    """Try v1/v2 source layout."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"v1{ext}"
        v2 = case_dir / f"v2{ext}"
        if v1.exists() and v2.exists():
            # A .cpp source's header may still be named .h (a common C++
            # convention) — try the language-typical extension first, but
            # fall back to the other rather than silently resolving to no
            # header at all (which drops the case to ELF/DWARF-only mode
            # and hides any header-only-visible finding, e.g. case123's
            # default-argument removal, case125's `final` specifier).
            v1h = _find_header(case_dir, "v1")
            v2h = _find_header(case_dir, "v2")
            return v1, v2, v1h, v2h
    return _NO_SOURCES


def _try_old_new_layout(case_dir: Path) -> _SourceResult:
    """Try old/new directory layout (cases 19+)."""
    old_dir = case_dir / "old"
    new_dir = case_dir / "new"
    if not (old_dir.is_dir() and new_dir.is_dir()):
        return _NO_SOURCES
    for ext in (".c", ".cpp"):
        v1 = old_dir / f"lib{ext}"
        v2 = new_dir / f"lib{ext}"
        if v1.exists() and v2.exists():
            v1h = _find_header(old_dir, "lib")
            v2h = _find_header(new_dir, "lib")
            return v1, v2, v1h, v2h
    return _NO_SOURCES


def _try_libfoo_layout(case_dir: Path) -> _SourceResult:
    """Try libfoo_v1/v2 layout (case18)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"libfoo_v1{ext}"
        v2 = case_dir / f"libfoo_v2{ext}"
        if v1.exists() and v2.exists():
            hext = _header_ext(ext)
            v1h = case_dir / f"foo_v1{hext}"
            v2h = case_dir / f"foo_v2{hext}"
            return v1, v2, v1h if v1h.exists() else None, v2h if v2h.exists() else None
    return _NO_SOURCES


def _try_good_bad_layout(case_dir: Path) -> _SourceResult:
    """Try good/bad layout (cases 05/06/13). v1=bad (old), v2=good (new)."""
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"bad{ext}"
        v2 = case_dir / f"good{ext}"
        if v1.exists() and v2.exists():
            return v1, v2, None, None
    return _NO_SOURCES


def find_sources(case_dir: Path) -> _SourceResult:
    """Return (v1_src, v2_src, v1_h_hint, v2_h_hint) or (None, None, None, None) if unsupported."""
    for finder in (_try_v1v2_layout, _try_old_new_layout, _try_libfoo_layout, _try_good_bad_layout):
        result = finder(case_dir)
        if result != _NO_SOURCES:
            return result
    return _NO_SOURCES


# ── abicheck compare (dump + compare pipeline) ────────────────────────────────
def _find_or_build_abicheck_plugin(timeout: int) -> tuple[Path | None, str]:
    """Return the build-integrated Clang fact plugin, building it once if needed."""
    override = os.environ.get("ABICHECK_CLANG_PLUGIN")
    if override:
        plugin = Path(override)
        return (plugin, "") if plugin.is_file() else (None, f"plugin not found: {plugin}")

    plugin_build = BUILD_DIR / "_abicheck_clang_plugin"
    names = ("libabicheck-facts.so", "libabicheck-facts.dylib", "abicheck-facts.dll")
    for name in names:
        candidate = plugin_build / name
        if candidate.is_file():
            return candidate, ""

    source = REPO_DIR / "contrib" / "abicheck-clang-plugin"
    llvm_config = _first_available_tool("llvm-config-18", "llvm-config")
    clang = _first_available_tool("clang-18", "clang")
    clangxx = _first_available_tool("clang++-18", "clang++")
    if not (source.is_dir() and llvm_config and clang and clangxx and shutil.which("cmake")):
        return None, "Clang plugin prerequisites unavailable"
    try:
        cmakedir = subprocess.run(
            [llvm_config, "--cmakedir"], capture_output=True, text=True,
            timeout=15, check=True,
        ).stdout.strip()
        env = {**os.environ, "CC": clang, "CXX": clangxx}
        configure = subprocess.run(
            ["cmake", "-S", str(source), "-B", str(plugin_build),
             f"-DCMAKE_PREFIX_PATH={Path(cmakedir).parent}"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if configure.returncode != 0:
            return None, configure.stderr or configure.stdout
        build = subprocess.run(
            ["cmake", "--build", str(plugin_build), "--config", "Release"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if build.returncode != 0:
            return None, build.stderr or build.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    for name in names:
        matches = list(plugin_build.rglob(name))
        if matches:
            return matches[0], ""
    return None, "plugin build succeeded but library was not produced"


def _plugin_pack_is_target_specific(
    pack: Path, version: str, src: Path, opposite_src: Path,
) -> tuple[bool, str]:
    """Reject empty, wrong-version, or cross-release plugin evidence packs."""
    try:
        manifest = json.loads((pack / "manifest.json").read_text())
        fact_files = sorted((pack / "source_facts").glob("*.jsonl"))
        records = [json.loads(line) for path in fact_files for line in path.read_text().splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid plugin pack: {exc}"
    if manifest.get("version") != version or not fact_files or not records:
        return False, f"missing {version} target-specific source facts"
    sources = {Path(str(record.get("source", ""))).resolve() for record in records}
    if src.resolve() not in sources or opposite_src.resolve() in sources:
        return False, f"{version} pack contains wrong release translation units"
    evidence_keys = {
        "declarations", "types", "functions", "variables", "macros", "templates",
        "inline_bodies", "constexpr_values", "source_edges",
    }
    if not any(record.get(key) for record in records for key in evidence_keys):
        return False, f"{version} plugin pack contains no L3/L4/L5 facts"
    return True, ""


def _build_plugin_side(
    case_dir: Path, case: str, version: str, src: Path, opposite_src: Path,
    header: Path | None, plugin: Path, root: Path, timeout: int,
) -> tuple[Path | None, Path | None, str]:
    """Configure and build exactly one versioned library target with the plugin."""
    case_dir = case_dir.resolve()
    src = src.resolve()
    opposite_src = opposite_src.resolve()
    plugin = plugin.resolve()
    root = root.resolve()
    header = header.resolve() if header and header.exists() else header
    side_build = root / f"plugin_build_{version}"
    pack = root / f"abicheck_inputs_{version}"
    for path in (side_build, pack):
        if path.exists():
            shutil.rmtree(path)

    clang = _first_available_tool("clang-18", "clang")
    clangxx = _first_available_tool("clang++-18", "clang++")
    if not clang or not clangxx:
        return None, None, "Clang compiler unavailable"
    public_root = header.parent if header and header.exists() else src.parent
    flags = [
        f"-fplugin={plugin}",
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", f"out={pack}",
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", f"public-roots={public_root}",
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", f"library={case}",
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", f"version={version}",
    ]
    # Compact fixtures often define the API without including their public
    # header. Force it into the real target compile so the pack sees that API.
    if header and header.exists():
        flags += ["-include", str(header)]
    flag_string = shlex.join(flags)
    # Inject only after CMake has validated the compilers. Putting the plugin in
    # CMAKE_{C,CXX}_FLAGS would instrument CMake's compiler probes and contaminate
    # the target pack with CMakeCCompilerId/CMakeCXXCompilerABI translation units.
    injection = root / f"plugin_flags_{version}.cmake"
    cmake_flags = flag_string.replace("\\", "\\\\").replace('"', '\\"')
    injection.write_text(
        f'set(CMAKE_C_FLAGS "${{CMAKE_C_FLAGS}} {cmake_flags}")\n'
        f'set(CMAKE_CXX_FLAGS "${{CMAKE_CXX_FLAGS}} {cmake_flags}")\n'
    )
    env = {**os.environ, "CC": clang, "CXX": clangxx}
    try:
        configure = subprocess.run(
            ["cmake", "-S", str(case_dir.parent), "-B", str(side_build),
             "-DCMAKE_BUILD_TYPE=Debug", f"-DCMAKE_PROJECT_INCLUDE={injection}"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if configure.returncode != 0:
            return None, None, configure.stderr or configure.stdout
        build = subprocess.run(
            ["cmake", "--build", str(side_build), "--target", f"{case}_{version}",
             "--config", "Debug"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if build.returncode != 0:
            return None, None, build.stderr or build.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, str(exc)
    library = _find_cmake_lib(side_build / case, version)
    valid, error = _plugin_pack_is_target_specific(pack, version, src, opposite_src)
    if library is None:
        return None, None, f"target {case}_{version} produced no shared library"
    if not valid:
        return None, None, error
    return library, pack, ""

def run_abicheck(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                 case: str, rdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ToolResult:
    """Run the normal binary+headers abicheck benchmark lane.

    Keep this lane comparable with libabigail's binary/header modes.  The
    deeper source/build evidence path is exposed separately as
    ``abicheck_full``.
    """
    return _run_abicheck_dump_compare(v1_so, v2_so, v1_h, v2_h, case, rdir, timeout=timeout)


def run_abicheck_full(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                      case: str, rdir: Path, *, case_dir: Path | None = None,
                      v1_src: Path | None = None, v2_src: Path | None = None,
                      build_dir: Path | None = None,
                      timeout: int = DEFAULT_ABICHECK_FULL_TIMEOUT) -> ToolResult:
    """Build each release target with the Clang plugin, merge its pack, compare."""
    del v1_so, v2_so, build_dir  # full lane uses plugin-instrumented target artifacts
    started = time.monotonic()
    if not _HAS_ABICHECK or case_dir is None or v1_src is None or v2_src is None:
        return ToolResult(verdict="SKIP")
    root = BUILD_DIR / case / "full_plugin"
    root.mkdir(parents=True, exist_ok=True)
    plugin, error = _find_or_build_abicheck_plugin(timeout)
    if plugin is None:
        return ToolResult(verdict="ERROR", raw_output=error,
                          elapsed_ms=(time.monotonic() - started) * 1000)
    try:
        sides = [
            ("v1", v1_src, v2_src, v1_h),
            ("v2", v2_src, v1_src, v2_h),
        ]
        merged: list[Path] = []
        logs: list[str] = []
        for version, src, opposite, header in sides:
            library, pack, error = _build_plugin_side(
                case_dir, case, version, src, opposite, header, plugin, root, timeout,
            )
            if library is None or pack is None:
                return ToolResult(verdict="ERROR", raw_output=error,
                                  elapsed_ms=(time.monotonic() - started) * 1000)
            base = root / f"{version}.binary_headers.json"
            final = root / f"{version}.merged.json"
            dump = [_PYTHON, "-m", "abicheck.cli", "dump", str(library),
                    "-o", str(base), "--version", version]
            if header and header.exists():
                # -H alone only feeds castxml which headers to parse; it does
                # NOT mark them public for provenance classification (that's
                # the separate, opt-in --public-header flag per ADR-015 D4).
                # Without it every declaration's origin stays UNKNOWN, which
                # demotes surface-scope confidence to "reduced"/"no-provenance"
                # across the board. The benchmark's headers ARE the case's
                # real public headers, so tell the classifier that.
                dump += ["-H", str(header), "--public-header", str(header)]
            dr = subprocess.run(dump, capture_output=True, text=True,
                                timeout=timeout, env=_ABICHECK_ENV)
            if dr.returncode != 0 or not base.exists():
                return ToolResult(verdict="ERROR", raw_output=dr.stderr or dr.stdout,
                                  elapsed_ms=(time.monotonic() - started) * 1000)
            mr = subprocess.run(
                # ``abicheck.cli`` invokes Click before late subcommand modules
                # register ``merge``; the package entry point imports the full
                # module first and therefore exposes build-source commands.
                [_PYTHON, "-m", "abicheck", "merge", str(base), str(pack),
                 "-o", str(final), "--on-conflict", "error"],
                capture_output=True, text=True, timeout=timeout, env=_ABICHECK_ENV,
            )
            if mr.returncode != 0 or not final.exists():
                return ToolResult(verdict="ERROR", raw_output=mr.stderr or mr.stdout,
                                  elapsed_ms=(time.monotonic() - started) * 1000)
            merged.append(final)
            logs.extend([dr.stdout, dr.stderr, mr.stdout, mr.stderr])
        compare = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compare", str(merged[0]), str(merged[1]),
             "--format", "json"], capture_output=True, text=True,
            timeout=timeout, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(verdict="TIMEOUT", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    out = compare.stdout + compare.stderr
    (rdir / f"{case}_abicheck_full.txt").write_text(out)
    return ToolResult(
        verdict=_abicheck_verdict_from_compare(compare.stdout, compare.returncode),
        raw_output="".join(logs) + out,
        elapsed_ms=(time.monotonic() - started) * 1000,
    )


def _run_abicheck_dump_compare(
    v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
    case: str, rdir: Path, *, suffix: str = "", timeout: int = DEFAULT_TIMEOUT,
) -> ToolResult:
    """Run the baseline binary plus public-header dump/compare lane."""
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    bdir = BUILD_DIR / case
    bdir.mkdir(parents=True, exist_ok=True)
    snap1 = bdir / f"snap{suffix}_v1.json"
    snap2 = bdir / f"snap{suffix}_v2.json"
    started = time.monotonic()

    def dump(so: Path, header: Path | None, snap: Path, version: str) -> tuple[bool, str]:
        cmd = [_PYTHON, "-m", "abicheck.cli", "dump", str(so), "-o", str(snap),
               "--version", version]
        if header and header.exists():
            # See run_abicheck_full's dump() for why --public-header is
            # needed alongside -H: without it, origin stays UNKNOWN for
            # every declaration (ADR-015 D4 opt-in), demoting surface-scope
            # confidence to "reduced"/"no-provenance" across the board.
            cmd += ["-H", str(header), "--public-header", str(header)]
        run = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, env=_ABICHECK_ENV)
        return run.returncode == 0 and snap.exists(), run.stderr or run.stdout

    try:
        ok, error = dump(v1_so, v1_h, snap1, "v1")
        if not ok:
            return ToolResult(verdict="ERROR", raw_output=f"dump v1 failed: {error}",
                              elapsed_ms=(time.monotonic() - started) * 1000)
        ok, error = dump(v2_so, v2_h, snap2, "v2")
        if not ok:
            return ToolResult(verdict="ERROR", raw_output=f"dump v2 failed: {error}",
                              elapsed_ms=(time.monotonic() - started) * 1000)
        result = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compare", str(snap1), str(snap2),
             "--format", "json"], capture_output=True, text=True,
            timeout=timeout, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(verdict="TIMEOUT", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    out = result.stdout + result.stderr
    (rdir / f"{case}_abicheck{suffix}.txt").write_text(out)
    return ToolResult(
        verdict=_abicheck_verdict_from_compare(result.stdout, result.returncode),
        raw_output=out, elapsed_ms=(time.monotonic() - started) * 1000,
    )

def _abicheck_verdict_from_compare(stdout: str, returncode: int) -> str:
    """Derive normalized verdict from abicheck compare output or exit code."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, AttributeError):
        # Non-JSON fallback: preserve explicit textual verdicts when available.
        text = str(stdout).upper()
        if "COMPATIBLE_WITH_RISK" in text:
            return "COMPATIBLE_WITH_RISK"
        return _abicheck_verdict_from_exit_code(returncode)
    return {
        "BREAKING": "BREAKING",
        "API_BREAK": "API_BREAK",
        "COMPATIBLE_WITH_RISK": "COMPATIBLE_WITH_RISK",
        "COMPATIBLE": "COMPATIBLE",
        "NO_CHANGE": "NO_CHANGE",
    }.get(str(data.get("verdict", "")).upper(), "ERROR")


def _abicheck_verdict_from_exit_code(returncode: int) -> str:
    """Fallback verdict mapping from compare command exit code."""
    return {
        4: "BREAKING",
        2: "API_BREAK",
        # compare currently returns 0 for NO_CHANGE / COMPATIBLE / COMPATIBLE_WITH_RISK.
        # Keep fallback behavior for ambiguous code 0.
        1: "COMPATIBLE",
        0: "NO_CHANGE",
    }.get(returncode, "ERROR")


def _write_compat_descriptor(so: Path, h: Path | None, ver: str, out: Path) -> None:
    """Write an ABICC-format XML descriptor for abicheck compat."""
    # NOTE: abicheck compat currently expects header file paths in <headers>
    header = str(h) if h and h.exists() else ""
    out.write_text(
        f"<descriptor>\n"
        f"  <version>{ver}</version>\n"
        f"  <headers>{header}</headers>\n"
        f"  <libs>{so}</libs>\n"
        f"</descriptor>\n"
    )


# ── abicheck compat (ABICC XML drop-in) ──────────────────────────────────────
def run_abicheck_compat(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                        case: str, rdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ToolResult:
    """Run abicheck compat with ABICC-format XML descriptors."""
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")

    v1_xml = rdir / f"{case}_compat_v1.xml"
    v2_xml = rdir / f"{case}_compat_v2.xml"
    _write_compat_descriptor(v1_so, v1_h, "v1", v1_xml)
    _write_compat_descriptor(v2_so, v2_h, "v2", v2_xml)

    _t0 = time.monotonic()
    try:
        r = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compat", "check", "-lib", case,
             "-old", str(v1_xml), "-new", str(v2_xml)],
            capture_output=True, text=True, timeout=timeout, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=(time.monotonic() - _t0) * 1000)
    elapsed_ms = (time.monotonic() - _t0) * 1000

    out = r.stdout + r.stderr
    (rdir / f"{case}_abicheck_compat.txt").write_text(out)

    # compat exit codes (from abicheck/cli.py compat command):
    #   0 = NO_CHANGE or COMPATIBLE
    #   1 = BREAKING
    #   2 = API_BREAK (source-level break, binary compatible)
    if r.returncode == 1:
        verdict = "BREAKING"
    elif r.returncode == 2:
        verdict = "API_BREAK"
    elif r.returncode == 0:
        # distinguish NO_CHANGE from COMPATIBLE by output
        # abicheck compat prints "Verdict: NO_CHANGE" or "Verdict: COMPATIBLE"
        if "verdict: no_change" in out.lower() or "no changes" in out.lower() or "identical" in out.lower():
            verdict = "NO_CHANGE"
        else:
            verdict = "COMPATIBLE"
    else:
        verdict = "ERROR"

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      elapsed_ms=elapsed_ms)


# ── abicheck compat strict mode ───────────────────────────────────────────────
def run_abicheck_strict(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                        case: str, rdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ToolResult:
    """Run abicheck compat in strict mode (-s flag promotes API_BREAK→BREAKING)."""
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")

    # Reuse XML descriptors already created by run_abicheck_compat (same files)
    v1_xml = rdir / f"{case}_compat_v1.xml"
    v2_xml = rdir / f"{case}_compat_v2.xml"

    # If XMLs don't exist yet, create them (fallback)
    if not v1_xml.exists() or not v2_xml.exists():
        _write_compat_descriptor(v1_so, v1_h, "v1", v1_xml)
        _write_compat_descriptor(v2_so, v2_h, "v2", v2_xml)

    _t0 = time.monotonic()
    try:
        r = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compat", "check", "-lib", case,
             "-old", str(v1_xml), "-new", str(v2_xml),
             "-report-path", str(rdir / f"{case}_strict_report.html"),
             "-s"],
            capture_output=True, text=True, timeout=timeout, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=(time.monotonic() - _t0) * 1000)
    elapsed_ms = (time.monotonic() - _t0) * 1000

    out = r.stdout + r.stderr
    (rdir / f"{case}_abicheck_strict.txt").write_text(out)

    # strict mode exit codes: same as compat but API_BREAK is promoted to BREAKING (exit 1)
    #   0 = NO_CHANGE or COMPATIBLE
    #   1 = BREAKING (includes promoted API_BREAK)
    #   2 = API_BREAK (shouldn't occur with -s, but handle defensively)
    if r.returncode == 1:
        verdict = "BREAKING"
    elif r.returncode == 2:
        verdict = "API_BREAK"
    elif r.returncode == 0:
        if "verdict: no_change" in out.lower() or "no changes" in out.lower() or "identical" in out.lower():
            verdict = "NO_CHANGE"
        else:
            verdict = "COMPATIBLE"
    else:
        verdict = "ERROR"

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      elapsed_ms=elapsed_ms)


# ── abidiff ───────────────────────────────────────────────────────────────────
def run_abidiff(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                case: str, rdir: Path,
                headers_dir: str | None = None,
                suffix: str = "", timeout: int = DEFAULT_TIMEOUT, **_kw: Any) -> ToolResult:
    if not shutil.which("abidiff"):
        return ToolResult(verdict="SKIP")

    cmd = ["abidiff"]
    if headers_dir:
        if isinstance(headers_dir, (list, tuple)) and len(headers_dir) == 2:
            cmd += ["--headers-dir1", str(headers_dir[0]), "--headers-dir2", str(headers_dir[1])]
        else:
            cmd += ["--headers-dir1", str(headers_dir), "--headers-dir2", str(headers_dir)]
    cmd += [str(v1_so), str(v2_so)]

    _t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=(time.monotonic() - _t0) * 1000)
    elapsed_ms = (time.monotonic() - _t0) * 1000
    out = r.stdout + r.stderr
    (rdir / f"{case}_abidiff{suffix}.txt").write_text(out)

    # abidiff exit bitmask: bit0=tool-err, bit1=app-err, bit2=compat, bit3=breaking
    if r.returncode & 1 or r.returncode & 2:
        verdict = "ERROR"
    elif r.returncode & 8:
        verdict = "BREAKING"
    elif r.returncode & 4:
        verdict = "COMPATIBLE"
    elif r.returncode == 0:
        verdict = "NO_CHANGE"
    else:
        verdict = "ERROR"

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      elapsed_ms=elapsed_ms)


# ── ABICC (legacy XML descriptor) ─────────────────────────────────────────────
def run_abicc_xml(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                  case: str, rdir: Path, timeout: int = DEFAULT_ABICC_TIMEOUT) -> ToolResult:
    if not shutil.which("abi-compliance-checker"):
        return ToolResult(verdict="SKIP")

    def xml(so: Path, h: Path | None, ver: str, out: Path) -> bool:
        # Pass the specific header file path, not the whole directory.
        # Passing a directory causes abicc to include ALL .h files it finds
        # there (including duplicates from make_build subdirs), which leads to
        # redefinition errors and TIMEOUT/wrong verdicts.
        # If no public header is available, skip <headers> entirely so abicc
        # analyses exported symbols only (ELF-only mode).
        if h and h.exists():
            headers_line = f"  <headers>{h}</headers>\n"
        else:
            headers_line = ""
        out.write_text(
            f"<descriptor>\n"
            f"  <version>{ver}</version>\n"
            f"{headers_line}"
            f"  <libs>{so}</libs>\n"
            f"</descriptor>\n"
        )
        return True

    v1_xml = rdir / f"{case}_v1.xml"
    v2_xml = rdir / f"{case}_v2.xml"
    xml(v1_so, v1_h, "v1", v1_xml)
    xml(v2_so, v2_h, "v2", v2_xml)

    html_out = rdir / f"{case}_abicc_xml_report.html"
    _t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["abi-compliance-checker", "-l", case,
             "-old", str(v1_xml), "-new", str(v2_xml),
             "-report-path", str(html_out)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=(time.monotonic() - _t0) * 1000)
    elapsed_ms = (time.monotonic() - _t0) * 1000

    out = r.stdout + r.stderr
    (rdir / f"{case}_abicc_xml.txt").write_text(out)

    # Read verdict from output: ABICC may exit non-zero on GCC header warnings
    # (bug #78040) while still producing a correct compatibility report.
    m_pct = re.search(r"Binary compatibility: (\d+(?:\.\d+)?)%", out)
    if m_pct:
        # 100% = no breaking changes (may still have compatible additions)
        verdict = "COMPATIBLE" if float(m_pct.group(1)) == 100.0 else "BREAKING"
    elif r.returncode == 1:
        verdict = "BREAKING"
    elif r.returncode == 0:
        verdict = "COMPATIBLE"
    else:
        verdict = "ERROR"

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      report_path=str(html_out), elapsed_ms=elapsed_ms)


# ── ABICC (abi-dumper workflow) ────────────────────────────────────────────────
def run_abicc_dumper(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                     case: str, rdir: Path,
                     timeout: int = DEFAULT_ABICC_TIMEOUT) -> ToolResult:
    if not shutil.which("abi-compliance-checker"):
        return ToolResult(verdict="SKIP")
    if not shutil.which("abi-dumper"):
        return ToolResult(verdict="SKIP")

    dump_v1 = rdir / f"{case}_v1.abi"
    dump_v2 = rdir / f"{case}_v2.abi"
    _t_start = time.monotonic()

    for so, dump, ver, hdr in [
        (v1_so, dump_v1, "v1", v1_h),
        (v2_so, dump_v2, "v2", v2_h),
    ]:
        dump_cmd = ["abi-dumper", str(so), "-o", str(dump), "-lver", ver]
        if hdr and hdr.exists():
            dump_cmd += ["-public-headers", str(hdr.parent)]
        try:
            dr = subprocess.run(dump_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ToolResult(verdict="TIMEOUT",
                              elapsed_ms=(time.monotonic() - _t_start) * 1000)
        if dr.returncode != 0 or not dump.exists():
            return ToolResult(verdict="ERROR", raw_output=f"abi-dumper failed ({ver})",
                              elapsed_ms=(time.monotonic() - _t_start) * 1000)

    html_out = rdir / f"{case}_abicc_dumper_report.html"
    try:
        r = subprocess.run(
            ["abi-compliance-checker", "-l", case,
             "-old", str(dump_v1), "-new", str(dump_v2),
             "-report-path", str(html_out)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT",
                          elapsed_ms=(time.monotonic() - _t_start) * 1000)
    elapsed_ms = (time.monotonic() - _t_start) * 1000

    out = r.stdout + r.stderr
    (rdir / f"{case}_abicc_dumper.txt").write_text(out)

    m_pct = re.search(r"Binary compatibility: (\d+(?:\.\d+)?)%", out)
    if m_pct:
        verdict = "COMPATIBLE" if float(m_pct.group(1)) == 100.0 else "BREAKING"
    elif r.returncode == 1:
        verdict = "BREAKING"
    elif r.returncode == 0:
        verdict = "COMPATIBLE"
    else:
        verdict = "ERROR"

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      report_path=str(html_out), elapsed_ms=elapsed_ms)


def run_abidiff_headers(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                        case: str, rdir: Path, timeout: int = DEFAULT_TIMEOUT,
                        **kw: Any) -> ToolResult:
    """Wrapper: run abidiff with headers_dir resolved from v1_h/v2_h."""
    if v1_h and v1_h.exists() and v2_h and v2_h.exists() and v1_h.parent != v2_h.parent:
        headers_dir: str | tuple | None = (str(v1_h.parent), str(v2_h.parent))
    elif v1_h and v1_h.exists():
        headers_dir = str(v1_h.parent)
    elif v2_h and v2_h.exists():
        headers_dir = str(v2_h.parent)
    else:
        headers_dir = None
    return run_abidiff(v1_so, v2_so, v1_h, v2_h, case, rdir, headers_dir=headers_dir,
                       suffix="_headers", timeout=timeout)


TOOL_REGISTRY: list[Tool] = [
    Tool("abicheck", run_abicheck, "abicheck", 12, "expected"),
    Tool("abicheck_full", run_abicheck_full, "ac-full", 12, "expected"),
    Tool("abicheck_compat", run_abicheck_compat, "ac-compat", 12, "expected_compat"),
    Tool("abicheck_strict", run_abicheck_strict, "ac-strict", 14, "expected"),
    Tool("abidiff", run_abidiff, "abidiff", 12, "expected"),
    Tool("abidiff_headers", run_abidiff_headers, "abidiff+hdr", 12, "expected"),
    Tool("abicc_dumper", run_abicc_dumper, "ABICC(dump)", 12, "expected_abicc", show_slowest=True),
    Tool("abicc_xml", run_abicc_xml, "ABICC(xml)", 12, "expected_abicc", show_slowest=True),
]


# ── Helpers ───────────────────────────────────────────────────────────────────
_COLORS = {
    "BREAKING": "\033[91m",
    "API_BREAK": "\033[94m",  # blue — source-only, binary-safe
    "COMPATIBLE": "\033[93m",
    "NO_CHANGE": "\033[92m",
    "ERROR": "\033[95m",
    "SKIP": "\033[90m",
    "TIMEOUT": "\033[95m",
}
_RESET = "\033[0m"


def _col(v: str, width: int = 12) -> str:
    # Keep table alignment stable even for long labels like COMPATIBLE_WITH_RISK.
    clipped = v[:width]
    return f"{_COLORS.get(v, '')}{clipped:<{width}}{_RESET}"


def _correct(verdict: str, expected: str) -> str:
    """Return emoji indicator vs expected."""
    if verdict in ("SKIP", "ERROR", "TIMEOUT"):
        return "—"
    return "✅" if verdict == expected else "❌"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark abicheck vs abidiff vs ABICC")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help="Timeout per tool call for abicheck/abicheck_compat/"
                        f"abicheck_strict/abidiff(+headers) (default: {DEFAULT_TIMEOUT}s)")
    p.add_argument("--abicc-timeout", type=int, default=DEFAULT_ABICC_TIMEOUT,
                   help="Timeout per ABICC call — ABICC is the slowest tool and can "
                        f"hang, so keep this bounded (default: {DEFAULT_ABICC_TIMEOUT}s)")
    p.add_argument("--abicheck-full-timeout", type=int,
                   default=DEFAULT_ABICHECK_FULL_TIMEOUT,
                   help="Timeout per abicheck full-lane call "
                        f"(default: {DEFAULT_ABICHECK_FULL_TIMEOUT}s)")
    p.add_argument("--abicc-mode", choices=["xml", "dumper", "both"], default="both",
                   help="ABICC mode: xml (legacy XML descriptor), dumper (abi-dumper workflow), or both (default: both)")
    p.add_argument("--skip-abicc", action="store_true",
                   help="Skip ABICC entirely")
    p.add_argument("--skip-compat", action="store_true",
                   help="Skip abicheck compat column")
    p.add_argument("--cases", nargs="+", metavar="CASE",
                   help="Run only these case prefixes (e.g. case09 case16)")
    p.add_argument("--suite", choices=["all", "pinned74"], default="all",
                   help="Case suite to run: all catalog cases, or the historical 74-case release-pinned subset")
    p.add_argument("--tools", nargs="+", metavar="TOOL",
                   choices=["abicheck", "abicheck_full", "abicheck_compat", "abicheck_strict",
                            "abidiff", "abidiff_headers", "abicc_dumper", "abicc_xml"],
                   help="Run only selected tools")
    p.add_argument("--case64-toolchain", choices=["auto", "gcc", "clang"], default="auto",
                   help="Toolchain for case64_calling_convention_changed (default: auto; prefers clang when available)")
    p.add_argument("--evidence-tiers", action="store_true",
                   help="Run abicheck at each evidence tier (L0 binary / L1 +debug / "
                        "L2 +headers / L3 +build) and report which cases each data "
                        "source discovers, instead of the cross-tool comparison. "
                        "Slow path: builds each case once, then runs the full "
                        "dump+compare pipeline up to 4x per case.")
    return p.parse_args()


# ── Helpers (module-level) ──────────────────────────────────────────────────
def _remap_to_build(h: Path | None, src: Path, dst: Path) -> Path | None:
    """Remap a header path from the original case dir to the make_build copy."""
    if not h:
        return None
    try:
        return dst / h.relative_to(src)
    except ValueError:
        return dst / h.name


def _error_entry(case_name: str, expected: str) -> dict[str, Any]:
    """Standardized error row for tool outputs."""
    return {
        "case": case_name,
        "expected": expected,
        "expected_compat": EXPECTED_COMPAT.get(case_name, expected),
        "abicheck": "ERROR",
        "abicheck_full": "ERROR",
        "abicheck_compat": "ERROR",
        "abicheck_strict": "ERROR",
        "abidiff": "ERROR",
        "abidiff_headers": "ERROR",
        "abicc_dumper": "ERROR",
        "abicc_xml": "ERROR",
    }


def _case64_toolchain_policy(case_name: str, configured: str) -> tuple[str | None, bool]:
    """Return (preferred_family, force_case64_compile) for benchmark compilation."""
    case64 = case_name == "case64_calling_convention_changed"
    has_clang = bool(_first_available_tool("clang-18", "clang"))
    if configured == "clang":
        preferred_family = "clang"
    elif configured == "gcc":
        preferred_family = "gcc"
    else:  # auto
        preferred_family = "clang" if (case64 and has_clang) else None
    # For case64, if toolchain is explicitly/implicitly selected, compile directly
    # and bypass prebuilt artifacts to honor selected calling-convention compiler.
    return preferred_family, (case64 and preferred_family is not None)


def _try_reuse_prebuilt(
    *,
    force_case64_compile: bool,
    case_name: str,
) -> tuple[Path | None, Path | None, bool, bool]:
    """Try to reuse prebuilt example artifacts.

    Returns (v1_so, v2_so, used_prebuilt_artifacts, used_cmake_artifacts).
    """
    if force_case64_compile:
        return None, None, False, False

    prebuilt_dirs = [EXAMPLES_DIR / "build-all-local", EXAMPLES_DIR / "build-real"]
    for prebuilt_root in prebuilt_dirs:
        prebuilt_case_dir = prebuilt_root / case_name
        if not prebuilt_case_dir.is_dir():
            continue
        built_v1 = _find_cmake_lib(prebuilt_case_dir, "v1")
        built_v2 = _find_cmake_lib(prebuilt_case_dir, "v2")
        if built_v1 and built_v2:
            return built_v1, built_v2, True, True

    return None, None, False, False


# ── Module-level helpers extracted from main() ────────────────────────────────

def _accuracy(results: list[dict], key: str, expected_key: str = "expected") -> tuple[int, int]:
    scored = [r for r in results if r.get(expected_key, "?") != "?" and r[key] not in ("SKIP", "ERROR", "TIMEOUT", "NO_SOURCE")]
    correct = sum(1 for r in scored if r[key] == r[expected_key])
    return correct, len(scored)


def _coverage_accuracy(results: list[dict], key: str, expected_key: str = "expected") -> tuple[int, int]:
    """Accuracy over the full catalog denominator (every row in *results*).

    Unlike :func:`_accuracy`, a SKIP/ERROR/TIMEOUT is *not* excluded from the
    denominator — it counts as a miss, same as a wrong verdict: whether a tool
    got the wrong answer, hung, crashed, or simply cannot scan the case at all
    is immaterial to "did it produce the right answer for this catalog entry".
    """
    correct = sum(
        1 for r in results
        if r.get(expected_key, "?") != "?" and r[key] == r[expected_key]
    )
    return correct, len(results)


def _total_ms(results: list[dict], ms_key: str) -> float:
    return sum(r.get(ms_key, 0) for r in results)


def _tool_version(cmd: list[str]) -> str | None:
    """Best-effort one-line version string for an external tool, or None."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def _git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _ground_truth_digest() -> str | None:
    """SHA-256 of examples/ground_truth.json so a benchmark run is pinned to it."""
    gt = EXAMPLES_DIR / "ground_truth.json"
    if not gt.is_file():
        return None
    import hashlib

    return hashlib.sha256(gt.read_bytes()).hexdigest()


def _collect_metadata(results: list[dict], active_tools: list[Any], suite: str) -> dict[str, Any]:
    """Assemble reproducibility metadata + machine-readable accuracy.

    This is the release-pinnable artifact: it records the exact inputs
    (abicheck version, git commit, tool versions, ground-truth digest, case
    count) alongside per-tool accuracy, so a published number can be
    reproduced and audited against the tag it was generated from.
    """
    try:
        from abicheck import __version__ as abicheck_version
    except Exception:  # noqa: BLE001
        abicheck_version = "unknown"

    accuracy: dict[str, dict[str, Any]] = {}
    coverage_accuracy: dict[str, dict[str, Any]] = {}
    for t in active_tools:
        correct, total = _accuracy(results, t.name, t.expected_key)
        accuracy[t.name] = {
            "label": t.label,
            "correct": correct,
            "scored": total,
            "pct": round(100 * correct / total, 1) if total else None,
            "total_ms": round(_total_ms(results, t.ms_key)),
        }
        cov_correct, cov_total = _coverage_accuracy(results, t.name, t.expected_key)
        coverage_accuracy[t.name] = {
            "label": t.label,
            "correct": cov_correct,
            "total": cov_total,
            "pct": round(100 * cov_correct / cov_total, 1) if cov_total else None,
        }

    return {
        "schema": "abicheck-benchmark/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "abicheck_version": abicheck_version,
        "git_commit": _git_commit(),
        "suite": suite,
        "case_count": len(results),
        "ground_truth_sha256": _ground_truth_digest(),
        "tool_versions": {
            "abidiff": _tool_version(["abidiff", "--version"]),
            "abi-compliance-checker": _tool_version(["abi-compliance-checker", "-dumpversion"]),
            "gcc": _tool_version(["gcc", "--version"]),
            "castxml": _tool_version(["castxml", "--version"]),
        },
        "accuracy": accuracy,
        "coverage_accuracy": coverage_accuracy,
        "results": results,
    }


@dataclass
class _BuildResult:
    v1_so: Path
    v2_so: Path
    used_make_artifacts: bool
    used_cmake_artifacts: bool
    v1_h_hint: Path | None
    v2_h_hint: Path | None
    ok: bool


def _configure_cmake_env(force_case64_compile: bool, preferred_family: str | None) -> dict[str, str]:
    """Return a copy of os.environ with CC/CXX overridden for the requested toolchain."""
    cmake_env = os.environ.copy()
    if not force_case64_compile:
        return cmake_env
    if preferred_family == "clang":
        cc = _first_available_tool("clang-18", "clang")
        cxx = _first_available_tool("clang++-18", "clang++")
    elif preferred_family == "gcc":
        cc = _first_available_tool("gcc")
        cxx = _first_available_tool("g++")
    else:
        return cmake_env
    if cc:
        cmake_env["CC"] = cc
    if cxx:
        cmake_env["CXX"] = cxx
    return cmake_env


def _run_cmake_configure_and_build(
    case_dir: Path,
    cmake_build: Path,
    name: str,
    cmake_env: dict[str, str],
) -> Any:
    """Run cmake configure + build. Returns a result object with .returncode."""
    try:
        cr = subprocess.run(
            ["cmake", "-S", str(case_dir.parent), "-B", str(cmake_build),
             "-DCMAKE_BUILD_TYPE=Debug", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"],
            capture_output=True, text=True, timeout=60, env=cmake_env,
        )
        if cr.returncode == 0:
            cr = subprocess.run(
                ["cmake", "--build", str(cmake_build),
                 "--target", f"{name}_v1", f"{name}_v2",
                 "--config", "Debug"],
                capture_output=True, text=True, timeout=120, env=cmake_env,
            )
    except subprocess.TimeoutExpired:
        cr = type("R", (), {"returncode": -1})()
    return cr


def _resolve_cmake_libs(
    name: str,
    expected: str,
    cmake_build: Path,
    cr: Any,
    v1_so: Path,
    v2_so: Path,
    used_cmake_artifacts: bool,
    v1_h_hint: Path | None,
    v2_h_hint: Path | None,
    results: list[dict],
    used_make_artifacts: bool,
) -> _BuildResult:
    """Resolve built libraries from cmake output; append error entry and return ok=False on failure."""
    cmake_out = cmake_build / name
    if cr.returncode == 0 and cmake_out.exists():
        built_v1 = _find_cmake_lib(cmake_out, "v1")
        built_v2 = _find_cmake_lib(cmake_out, "v2")
        if built_v1 and built_v2:
            return _BuildResult(built_v1, built_v2, used_make_artifacts, True, v1_h_hint, v2_h_hint, ok=True)
        print(f"  {name:<35} CMAKE_NO_LIB")
        results.append(_error_entry(name, expected))
        return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=False)
    print(f"  {name:<35} CMAKE_BUILD_ERR")
    results.append(_error_entry(name, expected))
    return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=False)


def _build_case_artifacts(
    name: str,
    expected: str,
    case_dir: Path,
    bdir: Path,
    v1_src: Path,
    v2_src: Path,
    v1_h_hint: Path | None,
    v2_h_hint: Path | None,
    args: Any,
    results: list[dict],
) -> _BuildResult:
    """Build shared libraries for a test case. Returns _BuildResult with ok=False on error."""
    v1_so = bdir / f"lib_v1{SHARED_LIB_SUFFIX}"
    v2_so = bdir / f"lib_v2{SHARED_LIB_SUFFIX}"
    used_make_artifacts = False
    used_cmake_artifacts = False

    cmake_file = case_dir / "CMakeLists.txt"

    preferred_family, force_case64_compile = _case64_toolchain_policy(name, args.case64_toolchain)
    pb_v1, pb_v2, used_prebuilt_artifacts, pb_cmake = _try_reuse_prebuilt(
        force_case64_compile=force_case64_compile, case_name=name,
    )
    if pb_v1 and pb_v2:
        v1_so, v2_so = pb_v1, pb_v2
        used_cmake_artifacts = pb_cmake

    if not used_prebuilt_artifacts:
        if not (cmake_file.exists() and shutil.which("cmake")):
            # No CMakeLists.txt (optional per examples/CLAUDE.md) and no
            # prebuilt artifact — fall back to compiling v1_src/v2_src
            # directly, same as the CMake-less cases the direct-compile path
            # was written for.
            if compile_so(
                v1_src, v1_so, preferred_family=preferred_family,
                extra_link_opts=_fallback_link_opts(case_dir, v1_src),
            ) and compile_so(
                v2_src, v2_so, preferred_family=preferred_family,
                extra_link_opts=_fallback_link_opts(case_dir, v2_src),
            ):
                return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=True)
            print(f"  {name:<35} BUILD_PATH_UNAVAILABLE(prebuilt|cmake|direct)")
            results.append(_error_entry(name, expected))
            return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=False)

        cmake_build = bdir / "cmake_build"
        if cmake_build.exists():
            shutil.rmtree(str(cmake_build))
        cmake_build.mkdir(parents=True)
        cmake_env = _configure_cmake_env(force_case64_compile, preferred_family)
        if name == "case115_bit_int_width_changed":
            # Needs C23 _BitInt(N); the default system gcc may predate GCC 14
            # (which added _BitInt support). Prefer the newest gcc-1N on PATH
            # over the bare "gcc" alias rather than reporting a build error —
            # this is a toolchain-availability question, not a product gap.
            newer_gcc = _first_available_tool(
                "gcc-15", "gcc-14", "gcc-13", "gcc-12",
            )
            newer_gxx = _first_available_tool(
                "g++-15", "g++-14", "g++-13", "g++-12",
            )
            if newer_gcc:
                cmake_env = {**cmake_env, "CC": newer_gcc}
            if newer_gxx:
                cmake_env = {**cmake_env, "CXX": newer_gxx}
        cr = _run_cmake_configure_and_build(case_dir, cmake_build, name, cmake_env)
        return _resolve_cmake_libs(
            name, expected, cmake_build, cr, v1_so, v2_so,
            used_cmake_artifacts, v1_h_hint, v2_h_hint, results, used_make_artifacts,
        )

    return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=True)


def _print_tool_accuracy_bars(results: list[dict], active_tools: list[Any]) -> None:
    """Print per-tool accuracy bars with timing totals."""
    print("  Accuracy vs expected verdicts (scored cases only — SKIP/ERROR/TIMEOUT excluded):")
    for t in active_tools:
        c, total = _accuracy(results, t.name, t.expected_key)
        if total > 0:
            pct = 100 * c // total
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            tot_s = _total_ms(results, t.ms_key) / 1000
            print(f"    {t.label}: {c:>2}/{total} ({pct:3}%) {bar}  [{tot_s:6.1f}s total]")

    print(f"\n  Accuracy vs full catalog ({len(results)} cases — SKIP/ERROR/TIMEOUT/"
          "incapacity all count as misses, not exclusions):")
    for t in active_tools:
        c, total = _coverage_accuracy(results, t.name, t.expected_key)
        pct = 100 * c // total if total else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"    {t.label}: {c:>3}/{total} ({pct:3}%) {bar}")


def _print_abicheck_divergences(results: list[dict]) -> None:
    """Print cases where abicheck verdict differs from expected."""
    print("\n  Cases where abicheck differs from expected:")
    for r in results:
        if r.get("expected", "?") == "?":
            continue
        if r["abicheck"] not in ("SKIP", "ERROR", "TIMEOUT") and r["abicheck"] != r["expected"]:
            print(f"    {r['case']:<40} got={r['abicheck']} expected={r['expected']}")


def _print_strict_compat_divergences(results: list[dict]) -> None:
    """Print cases where abicheck_strict differs from abicheck_compat."""
    print("\n  Cases where abicheck_strict differs from abicheck_compat:")
    for r in results:
        ac_s = r.get("abicheck_strict", "SKIP")
        ac_c = r.get("abicheck_compat", "SKIP")
        if ac_s in ("SKIP", "ERROR", "TIMEOUT") or ac_c in ("SKIP", "ERROR", "TIMEOUT"):
            continue
        if ac_s != ac_c:
            exp = r.get("expected", "?")
            print(f"    {r['case']:<40} compat={ac_c} strict={ac_s} expected={exp}")


def _print_slowest_cases(results: list[dict], active_tools: list[Any]) -> None:
    """Print top-10 slowest cases for each tool flagged with show_slowest."""
    for tool_obj in active_tools:
        if not tool_obj.show_slowest:
            continue
        print(f"\n  Top {tool_obj.col_name} slowest cases:")
        slow = sorted(results, key=lambda r, k=tool_obj.ms_key: r.get(k, 0), reverse=True)
        for r in slow[:10]:
            ms = r.get(tool_obj.ms_key, 0)
            if ms > 0:
                verdict = r.get(tool_obj.name, "SKIP")
                print(f"    {r['case']:<40} {ms:>7}ms  [{verdict}]")


def _print_accuracy_summary(results: list[dict], active_tools: list[Any], selected_tools: set[str]) -> None:
    print("\n" + "─" * 80)
    _print_tool_accuracy_bars(results, active_tools)

    if "abicheck" in selected_tools:
        _print_abicheck_divergences(results)

    if "abicheck_strict" in selected_tools and "abicheck_compat" in selected_tools:
        _print_strict_compat_divergences(results)

    _print_slowest_cases(results, active_tools)


# ── Main helpers ──────────────────────────────────────────────────────────────

def _resolve_selected_tools(args: Any) -> set[str]:
    """Return the set of tool names to run, honoring high-level on/off switches."""
    use_dumper = not args.skip_abicc and args.abicc_mode in ("dumper", "both")
    use_xml = not args.skip_abicc and args.abicc_mode in ("xml", "both")
    use_compat = not args.skip_compat

    selected: set[str] = set(args.tools or [
        "abicheck", "abicheck_full", "abicheck_compat", "abicheck_strict",
        "abidiff", "abidiff_headers", "abicc_dumper", "abicc_xml",
    ])

    # honor high-level switches even if tool is listed explicitly
    if not use_compat:
        selected.discard("abicheck_compat")
        selected.discard("abicheck_strict")
    if not use_dumper:
        selected.discard("abicc_dumper")
    if not use_xml:
        selected.discard("abicc_xml")

    return selected


def _print_table_header(active_tools: list[Any]) -> None:
    """Print the column header row and separator."""
    cols = [("Case", 35), ("Expected", 12)] + [(t.col_name, t.col_width) for t in active_tools]
    hdr = " ".join(f"{name:<{w}}" for name, w in cols)
    print(f"\n{hdr}")
    print("─" * len(hdr))


def _skip_row_entry(name: str, expected: str) -> dict[str, Any]:
    """Return a result-row dict with all tool verdicts set to SKIP."""
    return {
        "case": name,
        "expected": expected,
        "abicheck": "SKIP",
        "abicheck_full": "SKIP",
        "abicheck_compat": "SKIP",
        "abicheck_strict": "SKIP",
        "abidiff": "SKIP",
        "abidiff_headers": "SKIP",
        "abicc_dumper": "SKIP",
        "abicc_xml": "SKIP",
    }


def _resolve_case_headers(
    v1_src: Path,
    v2_src: Path,
    bdir: Path,
    v1_h_hint: Path | None,
    v2_h_hint: Path | None,
    used_make_artifacts: bool,
    used_cmake_artifacts: bool,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Resolve v1_h, v2_h, v1_h_abicheck, v2_h_abicheck for a case.

    Header selection policy:
    - abicheck family: ELF-only (None) when Makefile/CMake artifacts are used
      and the case has no explicit headers, to avoid false BREAKING.
    - Header-aware tools: always synthesize/resolve headers for full context.
    """
    v1_h_gen = bdir / "v1.h"
    v2_h_gen = bdir / "v2.h"
    make_header(v1_src, v1_h_gen)
    make_header(v2_src, v2_h_gen)

    v1_h = v1_h_hint if v1_h_hint else (v1_h_gen if v1_h_gen.exists() else None)
    v2_h = v2_h_hint if v2_h_hint else (v2_h_gen if v2_h_gen.exists() else None)

    if (used_make_artifacts or used_cmake_artifacts) and not (v1_h_hint or v2_h_hint):
        v1_h_abicheck: Path | None = None
        v2_h_abicheck: Path | None = None
    else:
        v1_h_abicheck = v1_h
        v2_h_abicheck = v2_h

    return v1_h, v2_h, v1_h_abicheck, v2_h_abicheck


def _run_tools_for_case(
    active_tools: list[Any],
    v1_so: Path,
    v2_so: Path,
    v1_h: Path | None,
    v2_h: Path | None,
    v1_h_abicheck: Path | None,
    v2_h_abicheck: Path | None,
    name: str,
    rdir: Path,
    abicc_timeout: int,
    abicheck_full_timeout: int = DEFAULT_ABICHECK_FULL_TIMEOUT,
    timeout: int = DEFAULT_TIMEOUT,
    case_dir: Path | None = None,
    v1_src: Path | None = None,
    v2_src: Path | None = None,
    build_dir: Path | None = None,
) -> dict[str, ToolResult]:
    """Run all active tools for a case and return their results keyed by tool name."""
    tool_results: dict[str, ToolResult] = {}
    for t in active_tools:
        if t.name == "abicheck_full":
            tool_results[t.name] = t.run_fn(
                v1_so, v2_so, v1_h_abicheck, v2_h_abicheck, name, rdir,
                case_dir=case_dir, v1_src=v1_src, v2_src=v2_src,
                build_dir=build_dir, timeout=abicheck_full_timeout,
            )
        elif t.name in ("abicheck", "abicheck_compat", "abicheck_strict"):
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h_abicheck, v2_h_abicheck, name, rdir,
                                            timeout=timeout)
        elif t.name in ("abicc_dumper", "abicc_xml"):
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h, v2_h, name, rdir, timeout=abicc_timeout)
        else:
            # abidiff and abidiff_headers share the common signature
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h, v2_h, name, rdir, timeout=timeout)
    return tool_results


def _build_result_entry(
    name: str,
    expected: str,
    tool_results: dict[str, ToolResult],
) -> dict[str, Any]:
    """Build the full result dict for a case, merging per-tool verdicts and timing."""
    entry: dict[str, Any] = {
        "case": name,
        "expected": expected,
        "expected_compat": EXPECTED_COMPAT.get(name, expected),
        "expected_abicc": EXPECTED_ABICC.get(name, expected),
    }
    for t in TOOL_REGISTRY:
        tr = tool_results.get(t.name, ToolResult(verdict="SKIP"))
        entry[t.name] = tr.verdict
        entry[t.ms_key] = round(tr.elapsed_ms)
    return entry


# ── Special-shape example cases (bundle / snapshot-pair / L3-L5 / stub-pair) ──
# Several catalog cases do not fit the compilable v1/v2-.so shape every other
# tool in TOOL_REGISTRY assumes — ABICC/abidiff only understand a single ELF/
# PE/Mach-O pair. abicheck itself *can* compare most of these shapes through a
# different entry point, so they run abicheck-only here instead of being left
# out of the catalog scan entirely; every other tool is recorded SKIP with an
# explicit capability-gap note rather than silently absent from the scan.


def _case_gt_entry(name: str) -> dict[str, Any]:
    return _gt_data["verdicts"].get(name, {})


def _record_special_case_row(
    name: str, expected: str, results: list[dict], note: str,
    tool_results: dict[str, ToolResult],
) -> None:
    verdict = tool_results.get("abicheck", ToolResult(verdict="SKIP")).verdict
    print(f"  {name:<33} {expected:<12} {_col(verdict, 12)}  [{note}]")
    entry = _build_result_entry(name, expected, tool_results)
    entry["notes"] = note
    results.append(entry)


def _bundle_libs_for(name: str, case_dir: Path) -> list[str]:
    entry = _case_gt_entry(name)
    libs = list(entry.get("bundle_libraries") or [])
    if libs:
        return libs
    for side in ("old", "new"):
        found = sorted(p.stem for p in (case_dir / side).glob("lib*.c*") if p.is_file())
        if found:
            return found
    return []


def _build_case84_bundle(case_dir: Path, old_dir: Path, new_dir: Path, timeout: int) -> str:
    """Direct-gcc build for case84 (no CMakeLists.txt; SONAME-skewed bundle)."""
    gcc = _first_available_tool("gcc")
    if not gcc:
        return "gcc not found"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        (old_dir, "onedal_core.c", "libonedal_core.so.1"),
        (old_dir, "onedal_thread.c", "libonedal_thread.so.1"),
        (old_dir, "onedal_dpc.c", "libonedal_dpc.so.1"),
        (new_dir, "onedal_core.c", "libonedal_core.so.2"),
        (new_dir, "onedal_thread.c", "libonedal_thread.so.1"),
        (new_dir, "onedal_dpc.c", "libonedal_dpc.so.2"),
    ]
    for out_dir, src_name, soname in specs:
        r = subprocess.run(
            [gcc, "-shared", "-fPIC", f"-Wl,-soname,{soname}",
             str(case_dir / src_name), "-o", str(out_dir / soname)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return r.stderr or r.stdout
    return ""


def _build_cmake_bundle(
    case_dir: Path, name: str, old_dir: Path, new_dir: Path, timeout: int,
) -> str:
    """CMake build for the abicheck_add_bundle_case()-based bundle cases (90-93)."""
    del old_dir, new_dir  # populated by CMake itself at <build>/<case>/{old,new}
    if not shutil.which("cmake"):
        return "cmake not found"
    libs = _bundle_libs_for(name, case_dir)
    if not libs:
        return "no bundle libraries declared or discovered"
    cmake_build = BUILD_DIR / "_bundle_build"
    cmake_build.mkdir(parents=True, exist_ok=True)
    cr = subprocess.run(
        ["cmake", "-S", str(EXAMPLES_DIR), "-B", str(cmake_build), "-DCMAKE_BUILD_TYPE=Debug"],
        capture_output=True, text=True, timeout=timeout,
    )
    if cr.returncode != 0:
        return cr.stderr or cr.stdout
    targets = [f"{name}_{side}_{lib}" for side in ("old", "new") for lib in libs]
    br = subprocess.run(
        ["cmake", "--build", str(cmake_build), "--target", *targets],
        capture_output=True, text=True, timeout=max(timeout, 120),
    )
    if br.returncode != 0:
        return br.stderr or br.stdout
    return ""


def _run_bundle_case(
    case_dir: Path, name: str, entry: dict[str, Any], rdir: Path, timeout: int,
) -> ToolResult:
    """Run `abicheck compare old/ new/` on a multi-library bundle case (ADR-023)."""
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    started = time.monotonic()

    if name == "case84_bundle_soname_skew":
        bdir = BUILD_DIR / name
        old_dir, new_dir = bdir / "old", bdir / "new"
        error = _build_case84_bundle(case_dir, old_dir, new_dir, timeout)
    else:
        cmake_build = BUILD_DIR / "_bundle_build"
        old_dir = cmake_build / name / "old"
        new_dir = cmake_build / name / "new"
        error = _build_cmake_bundle(case_dir, name, old_dir, new_dir, timeout)
    if error:
        return ToolResult(verdict="ERROR", raw_output=error,
                          elapsed_ms=(time.monotonic() - started) * 1000)

    report_dir = BUILD_DIR / name / "bundle_reports"
    if report_dir.exists():
        shutil.rmtree(report_dir)
    report_dir.mkdir(parents=True)
    cmd = [_PYTHON, "-m", "abicheck.cli", "compare", str(old_dir), str(new_dir),
           "--format", "json", "--output-dir", str(report_dir)]
    manifest_file = entry.get("manifest_file")
    if manifest_file:
        cmd += ["--manifest", str(case_dir / str(manifest_file))]
    bundle_cohort = entry.get("bundle_cohort")
    if not bundle_cohort and "bundle_soname_skew" in (entry.get("expected_bundle_kinds") or []):
        bundle_cohort = "libonedal_"
    if bundle_cohort:
        cmd += ["--bundle-cohort", str(bundle_cohort)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_ABICHECK_ENV)
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=float(timeout) * 1000)
    elapsed_ms = (time.monotonic() - started) * 1000
    out = r.stdout + r.stderr
    (rdir / f"{name}_abicheck_bundle.txt").write_text(out)
    try:
        payload = json.loads(r.stdout)
    except (json.JSONDecodeError, AttributeError):
        return ToolResult(verdict="ERROR", raw_output=out, elapsed_ms=elapsed_ms)
    verdict = str(payload.get("bundle_verdict") or payload.get("verdict") or "ERROR").upper()
    return ToolResult(verdict=verdict, raw_output=out, elapsed_ms=elapsed_ms)


def _run_snapshot_pair_case(
    case_dir: Path, name: str, entry: dict[str, Any], rdir: Path, timeout: int,
    *, reconcile: bool,
) -> ToolResult:
    """Run `abicheck compare` directly on a committed AbiSnapshot pair.

    Covers ``mode: snapshot-pair`` (case170, plain old.abi.json/new.abi.json)
    and ``mode: reconcile`` (case164, v1.abi.json/v2.abi.json, needs
    --reconcile-build-context to clear the phantom finding, ADR-039).
    """
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    fixtures = entry.get("fixtures") or []
    if len(fixtures) != 2:
        return ToolResult(verdict="ERROR", raw_output=f"unexpected fixtures {fixtures!r}")
    old_file = case_dir / str(fixtures[0])
    new_file = case_dir / str(fixtures[1])
    started = time.monotonic()
    cmd = [_PYTHON, "-m", "abicheck.cli", "compare", str(old_file), str(new_file),
           "--format", "json"]
    if reconcile:
        cmd.append("--reconcile-build-context")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_ABICHECK_ENV)
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=float(timeout) * 1000)
    elapsed_ms = (time.monotonic() - started) * 1000
    out = r.stdout + r.stderr
    (rdir / f"{name}_abicheck_snapshot_pair.txt").write_text(out)
    return ToolResult(
        verdict=_abicheck_verdict_from_compare(r.stdout, r.returncode),
        raw_output=out, elapsed_ms=elapsed_ms,
    )


def _run_l3l5_case(name: str, entry: dict[str, Any]) -> ToolResult:
    """Run the L3/L4/L5 build-source-pack replay directly in-process.

    Cases 152-158/160-162 ship a hand-built evidence-model fixture pair
    (old.json/new.json) rather than compilable v1/v2 source — the same
    fixtures ``tests/test_l3l4l5_examples.py`` validates. No CLI path accepts
    this pack shape directly, so this calls the same diff_* functions that
    test file uses, then reuses the normal ChangeKind verdict policy so the
    result is comparable to every other row in this table.
    """
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    started = time.monotonic()
    old = json.loads((EXAMPLES_DIR / name / "old.json").read_text())
    new = json.loads((EXAMPLES_DIR / name / "new.json").read_text())
    tier = entry.get("min_evidence")
    try:
        from abicheck.checker_policy import compute_verdict  # noqa: PLC0415

        if tier == "L3":
            from abicheck.buildsource.build_diff import (
                diff_build_evidence,  # noqa: PLC0415
            )
            from abicheck.buildsource.build_evidence import (
                BuildEvidence,  # noqa: PLC0415
            )

            changes = diff_build_evidence(BuildEvidence.from_dict(old), BuildEvidence.from_dict(new))
        elif tier == "L4":
            from abicheck.buildsource.source_abi import (
                SourceAbiSurface,  # noqa: PLC0415
            )
            from abicheck.buildsource.source_diff import (
                diff_source_abi,  # noqa: PLC0415
            )

            changes = diff_source_abi(SourceAbiSurface.from_dict(old), SourceAbiSurface.from_dict(new))
        elif tier == "L5":
            from abicheck.buildsource.source_graph import (  # noqa: PLC0415
                SourceGraphSummary,
                diff_source_graph_findings,
            )

            changes = diff_source_graph_findings(
                SourceGraphSummary.from_dict(old), SourceGraphSummary.from_dict(new)
            )
        else:
            return ToolResult(verdict="ERROR", raw_output=f"unexpected min_evidence {tier!r}")
        verdict = compute_verdict(changes).value.upper()
    except Exception as exc:  # noqa: BLE001 - report as a benchmark row, not a crash
        return ToolResult(verdict="ERROR", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    return ToolResult(verdict=verdict, elapsed_ms=(time.monotonic() - started) * 1000)


def _run_stub_pair_case(case_dir: Path, entry: dict[str, Any]) -> ToolResult:
    """Compare a Python .pyi stub pair in-process (no compiled-binary change).

    Mirrors ``tests/test_python_api_examples.py``: no CLI path builds a
    python_api-only AbiSnapshot from a bare .pyi pair (there is no compiled
    binary at all for these cases — that's the point, see case163's README).
    """
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    del entry
    started = time.monotonic()
    try:
        from abicheck.checker import compare  # noqa: PLC0415
        from abicheck.model import AbiSnapshot  # noqa: PLC0415
        from abicheck.python_api import surface_from_stub_file  # noqa: PLC0415

        def snap(side: str) -> Any:
            s = AbiSnapshot(library="mymod.abi3.so", version=side)
            s.python_api = surface_from_stub_file(case_dir / f"{side}.pyi", module_name="mymod")
            return s

        result = compare(snap("v1"), snap("v2"))
        verdict = result.verdict.value.upper()
    except Exception as exc:  # noqa: BLE001 - report as a benchmark row, not a crash
        return ToolResult(verdict="ERROR", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    return ToolResult(verdict=verdict, elapsed_ms=(time.monotonic() - started) * 1000)


def _run_btf_case(case_dir: Path) -> ToolResult:
    """Compare committed v1.btf/v2.btf blobs in-process (no kernel toolchain).

    The CLI's `dump` command has no path for a raw BTF blob (it requires an
    ELF/PE/Mach-O binary), but abicheck's own BTF parser + compare() do handle
    it — mirrors tests/test_workflow_kernel_accel.py::
    test_committed_btf_example_matches_ground_truth exactly.
    """
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    started = time.monotonic()
    try:
        from abicheck.btf_metadata import parse_btf_from_bytes  # noqa: PLC0415
        from abicheck.checker import compare  # noqa: PLC0415
        from abicheck.model import AbiSnapshot  # noqa: PLC0415

        def snap(blob: str) -> Any:
            meta = parse_btf_from_bytes((case_dir / blob).read_bytes())
            return AbiSnapshot(library="vmlinux", version=blob, dwarf=meta.to_dwarf_metadata())

        result = compare(snap("v1.btf"), snap("v2.btf"))
        verdict = result.verdict.value.upper()
    except Exception as exc:  # noqa: BLE001 - report as a benchmark row, not a crash
        return ToolResult(verdict="ERROR", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    return ToolResult(verdict=verdict, elapsed_ms=(time.monotonic() - started) * 1000)


def _run_g20_audit_case(name: str, entry: dict[str, Any]) -> ToolResult:
    """Run the single-artifact audit/cross-source check and score it pass/fail.

    G20 cases (143-151) assert on a different surface than a BREAKING/
    COMPATIBLE verdict — the cross-check findings a single committed
    ``snapshot.abi.json`` produces (no old-vs-new pair at all). There is no
    verdict to compare, so this reduces to a boolean: did abicheck's
    run_crosschecks recover every kind ``expected_crosscheck_kinds``
    declares? Reported as the pseudo-verdict "MATCH"/"MISS" so it folds into
    the same accuracy accounting as every other row (paired with
    expected="MATCH" by the caller) — mirrors tests/test_g20_catalog.py.
    """
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")
    started = time.monotonic()
    try:
        from abicheck.buildsource.crosscheck import run_crosschecks  # noqa: PLC0415
        from abicheck.serialization import load_snapshot  # noqa: PLC0415

        snap_path = EXAMPLES_DIR / name / str((entry.get("fixtures") or ["snapshot.abi.json"])[0])
        snapshot = load_snapshot(snap_path)
        res = run_crosschecks(snapshot)
        emitted = {c.kind.value for c in res.findings}
        expected_kinds = set(entry.get("expected_crosscheck_kinds") or [])
        verdict = "MATCH" if expected_kinds <= emitted else "MISS"
    except Exception as exc:  # noqa: BLE001 - report as a benchmark row, not a crash
        return ToolResult(verdict="ERROR", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - started) * 1000)
    return ToolResult(verdict=verdict, elapsed_ms=(time.monotonic() - started) * 1000)


def _try_special_case(
    case_dir: Path, name: str, expected: str, entry: dict[str, Any],
    results: list[dict], args: Any,
) -> bool:
    """Route a catalog case that doesn't fit the compilable v1/v2-.so shape.

    Returns True when this function fully handled (printed + recorded) the
    case, so the caller should not fall through to the normal build/dump/
    compare flow.
    """
    rdir = REPORT_DIR / name
    rdir.mkdir(exist_ok=True)

    if entry.get("mode") == "audit":
        tr = _run_g20_audit_case(name, entry)
        note = ("single-artifact audit/cross-source check (no old-vs-new verdict "
                "concept) scored MATCH/MISS against expected_crosscheck_kinds, "
                "mirroring tests/test_g20_catalog.py — abidiff/ABICC have no mode "
                "for this at all")
        _record_special_case_row(name, "MATCH", results, note, {"abicheck": tr})
        return True

    if entry.get("skip"):
        # Currently only case121 (kernel BTF): ground_truth.json marks it
        # skip=true because the CLI's dump command has no path for a raw BTF
        # blob, but abicheck's own BTF parser + compare() handle it directly
        # (mirrors tests/test_workflow_kernel_accel.py) — abidiff/ABICC have no
        # BTF support at all.
        tr = _run_btf_case(case_dir)
        note = str(entry.get("reason", "excluded by ground_truth.json (skip=true)"))
        _record_special_case_row(name, expected, results, note, {"abicheck": tr})
        return True

    if entry.get("bundle") is True or entry.get("category") == "bundle":
        tr = _run_bundle_case(case_dir, name, entry, rdir, args.timeout)
        note = ("multi-library bundle (ADR-023) — no abidiff/ABICC equivalent for "
                "directory-of-libraries comparison")
        # Bundle cases carry their expected verdict under expected_bundle_verdict
        # (the top-level "expected" is None — there is no single-library verdict).
        bundle_expected = entry.get("expected_bundle_verdict") or expected
        _record_special_case_row(name, bundle_expected, results, note, {"abicheck": tr})
        return True

    if entry.get("mode") in ("snapshot-pair", "reconcile"):
        tr = _run_snapshot_pair_case(case_dir, name, entry, rdir, args.timeout,
                                     reconcile=entry.get("mode") == "reconcile")
        note = (f"committed {'/'.join(str(f) for f in entry.get('fixtures', []))} snapshot "
                "pair, no compilable v1/v2 source — no abidiff/ABICC equivalent for this "
                "evidence shape")
        _record_special_case_row(name, expected, results, note, {"abicheck": tr})
        return True

    if entry.get("fixtures") == ["old.json", "new.json"]:
        tr = _run_l3l5_case(name, entry)
        note = (f"L3/L4/L5 build-source-pack replay (min_evidence={entry.get('min_evidence')}), "
                "no compilable v1/v2 source — no abidiff/ABICC equivalent for this evidence shape")
        _record_special_case_row(name, expected, results, note, {"abicheck": tr})
        return True

    if entry.get("stub_pair"):
        tr = _run_stub_pair_case(case_dir, entry)
        note = ("Python .pyi stub pair, compiled binary is byte-identical — abidiff/ABICC "
                "have no Python-API comparison mode")
        _record_special_case_row(name, expected, results, note, {"abicheck": tr})
        return True

    return False


def _process_case(
    case_dir: Path,
    active_tools: list[Any],
    case_prefixes: list[str],
    results: list[dict],
    args: Any,
) -> None:
    """Process a single example case: build, run tools, print row, append result."""
    name = case_dir.name
    if case_prefixes and not any(name.startswith(pref) for pref in case_prefixes):
        return
    expected = EXPECTED.get(name, "?")

    # Platform filter
    case_platforms = PLATFORMS.get(name, ["linux", "macos", "windows"])
    if CURRENT_PLATFORM not in case_platforms:
        print(f"  {name:<33} {expected:<12} {'SKIP(platform)':<12}")
        results.append(_skip_row_entry(name, expected))
        return

    # Cases that don't fit the compilable v1/v2-.so shape (bundle directories,
    # single-artifact audits, build-source-pack replay, Python stub pairs) are
    # routed to abicheck's own capability instead of falling through to
    # find_sources()'s NO_SOURCE — see _try_special_case.
    if _try_special_case(case_dir, name, expected, _case_gt_entry(name), results, args):
        return

    v1_src, v2_src, v1_h_hint, v2_h_hint = find_sources(case_dir)
    if v1_src is None:
        print(f"  {name:<33} {expected:<12} {'NO_SOURCE':<12}")
        results.append(_skip_row_entry(name, expected))
        return

    rdir = REPORT_DIR / name
    rdir.mkdir(exist_ok=True)
    bdir = BUILD_DIR / name
    bdir.mkdir(exist_ok=True)

    # Build strategy: CMake > Makefile > direct compilation
    br = _build_case_artifacts(name, expected, case_dir, bdir, v1_src, v2_src,
                               v1_h_hint, v2_h_hint, args, results)
    if not br.ok:
        return
    v1_so = br.v1_so
    v2_so = br.v2_so
    v1_h_hint = br.v1_h_hint
    v2_h_hint = br.v2_h_hint

    v1_h, v2_h, v1_h_abicheck, v2_h_abicheck = _resolve_case_headers(
        v1_src, v2_src, bdir, v1_h_hint, v2_h_hint,
        br.used_make_artifacts, br.used_cmake_artifacts,
    )

    compile_db = _find_compile_db(bdir)
    build_info = compile_db if compile_db is not None else None

    tool_results = _run_tools_for_case(
        active_tools, v1_so, v2_so, v1_h, v2_h, v1_h_abicheck, v2_h_abicheck,
        name, rdir, args.abicc_timeout, args.abicheck_full_timeout, args.timeout,
        case_dir=case_dir, v1_src=v1_src, v2_src=v2_src, build_dir=build_info,
    )

    row_parts = [f"  {name:<33}", f"{expected:<12}"]
    row_parts += [_col(tool_results[t.name].verdict, t.col_width) for t in active_tools]
    print(" ".join(row_parts))

    results.append(_build_result_entry(name, expected, tool_results))


# ── Evidence-tier benchmark (five sources / L0–L4) ───────────────────────────
# Runs abicheck at progressively richer evidence levels so the catalog shows
# *which cases each data source can discover*:
#   L0 binary only      — stripped .so, no headers      (symbols-only mode)
#   L1 + debug info     — -g .so, no headers            (DWARF/PDB layout)
#   L2 + public headers — -g .so, -H include/           (castxml AST; default)
#   L3 + build context  — L2 plus -p build/ when a compile_commands.json exists
# L4 (source ABI replay via an BuildSourcePack) needs `collect`, which is
# not yet a CLI command, so it is reported as "n/a" here.
EVIDENCE_TIERS: list[str] = ["L0", "L1", "L2", "L3"]


def _strip_debug(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* and remove its debug info. False if strip is absent."""
    strip = _first_available_tool("strip", "llvm-strip")
    if not strip:
        return False
    shutil.copy2(src, dst)
    try:
        r = subprocess.run([strip, "--strip-debug", str(dst)],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and dst.exists()


def _abicheck_tier_result(
    v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
    case: str, tier: str, build_dir: Path | None,
) -> tuple[str, list[str]]:
    """Dump+compare both libs at one evidence tier.

    Returns ``(verdict, emitted_kinds)`` — the normalized verdict and the list of
    ChangeKind values abicheck actually emitted, so a tier is only credited with
    *discovering* a case when it produces the cataloged kind, not merely a
    verdict that happens to match (e.g. a broad COMPATIBLE).
    """
    if not _HAS_ABICHECK:
        return "SKIP", []
    bdir = BUILD_DIR / case
    bdir.mkdir(parents=True, exist_ok=True)

    def dump(so: Path, h: Path | None, snap: Path, ver: str) -> bool:
        cmd = [_PYTHON, "-m", "abicheck.cli", "dump", str(so), "-o", str(snap), "--version", ver]
        if h and h.exists():
            # See _run_abicheck_dump_compare's dump() for why --public-header
            # is needed alongside -H (ADR-015 D4 opt-in provenance).
            cmd += ["-H", str(h), "--public-header", str(h)]
        if build_dir is not None:
            cmd += ["-p", str(build_dir)]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=_ABICHECK_ENV)
        except subprocess.TimeoutExpired:
            return False
        return run.returncode == 0 and snap.exists()

    snap1 = bdir / f"tier_{tier}_v1.json"
    snap2 = bdir / f"tier_{tier}_v2.json"
    if not (dump(v1_so, v1_h, snap1, "v1") and dump(v2_so, v2_h, snap2, "v2")):
        return "ERROR", []
    try:
        r = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compare", str(snap1), str(snap2), "--format", "json"],
            capture_output=True, text=True, timeout=60, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", []
    verdict = _abicheck_verdict_from_compare(r.stdout, r.returncode)
    kinds: list[str] = []
    try:
        kinds = [c.get("kind", "") for c in json.loads(r.stdout).get("changes", [])]
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return verdict, kinds


def _find_compile_db(bdir: Path) -> Path | None:
    """Locate a compile_commands.json produced under the case build dir, if any."""
    for cand in (bdir / "cmake_build" / "compile_commands.json",
                 bdir / "compile_commands.json"):
        if cand.is_file():
            return cand
    return None


# Detection-crediting logic (kind-aware + kind-less-quiet floor) lives in the
# pure, unit-tested evidence_tiers module: evidence_tiers.detected_at(...).


def _run_case_evidence_tiers(case_dir: Path, args: Any) -> dict[str, Any] | None:
    """Build a case and run abicheck at every evidence tier. None if unbuildable."""
    name = case_dir.name
    expected = EXPECTED.get(name, "?")
    if CURRENT_PLATFORM not in PLATFORMS.get(name, ["linux", "macos", "windows"]):
        return None
    v1_src, v2_src, v1_h_hint, v2_h_hint = find_sources(case_dir)
    if v1_src is None:
        return None

    bdir = BUILD_DIR / name
    bdir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    br = _build_case_artifacts(name, expected, case_dir, bdir, v1_src, v2_src,
                               v1_h_hint, v2_h_hint, args, results)
    if not br.ok:
        return None

    v1_h, v2_h, _v1_ha, _v2_ha = _resolve_case_headers(
        v1_src, v2_src, bdir, br.v1_h_hint, br.v2_h_hint,
        br.used_make_artifacts, br.used_cmake_artifacts,
    )

    # L0 needs stripped copies; reuse the -g artifacts for the richer tiers.
    v1_strip = bdir / f"l0_v1{SHARED_LIB_SUFFIX}"
    v2_strip = bdir / f"l0_v2{SHARED_LIB_SUFFIX}"
    have_strip = _strip_debug(br.v1_so, v1_strip) and _strip_debug(br.v2_so, v2_strip)
    compile_db = _find_compile_db(bdir)

    verdicts: dict[str, str] = {}
    kinds: dict[str, list[str]] = {}

    def tier(t: str, v1: Path, v2: Path, h1: Path | None, h2: Path | None,
             bd: Path | None, enabled: bool = True) -> None:
        if not enabled:
            verdicts[t] = "n/a"
            kinds[t] = []
            return
        verdicts[t], kinds[t] = _abicheck_tier_result(v1, v2, h1, h2, name, t, bd)

    tier("L0", v1_strip, v2_strip, None, None, None, enabled=have_strip)
    tier("L1", br.v1_so, br.v2_so, None, None, None)
    tier("L2", br.v1_so, br.v2_so, v1_h, v2_h, None)
    tier("L3", br.v1_so, br.v2_so, v1_h, v2_h,
         compile_db.parent if compile_db else None, enabled=compile_db is not None)

    gt_entry = _gt_data["verdicts"].get(name, {})
    expected_kinds = list(gt_entry.get("expected_kinds", [])) + list(
        gt_entry.get("expected_bundle_kinds", [])
    )
    min_evidence = gt_entry.get("min_evidence", "?")
    return {
        "case": name,
        "expected": expected,
        "expected_kinds": expected_kinds,
        "min_evidence": min_evidence,
        "tier_verdicts": verdicts,
        "tier_kinds": kinds,
        "detected_at": evidence_tiers.detected_at(
            verdicts, kinds, expected, expected_kinds, min_evidence
        ),
    }


def _print_evidence_tier_table(rows: list[dict]) -> None:
    cols = [("Case", 38), ("Expected", 12), ("min_ev", 7)] + [(t, 10) for t in EVIDENCE_TIERS] + [("detect", 7)]
    hdr = " ".join(f"{n:<{w}}" for n, w in cols)
    print(f"\n{hdr}\n" + "─" * len(hdr))
    for r in rows:
        tv = r["tier_verdicts"]
        parts = [f"{r['case']:<38}", f"{r['expected']:<12}", f"{r['min_evidence']:<7}"]
        parts += [_col(tv.get(t, "—"), 10) for t in EVIDENCE_TIERS]
        det = r["detected_at"] or "MISS"
        parts.append(f"{det:<7}")
        print(" ".join(parts))


def _print_evidence_tier_summary(rows: list[dict]) -> None:
    print("\n" + "─" * 60)
    print("  Cumulative cases reaching the correct verdict, by evidence tier:")
    scored = [r for r in rows if r["expected"] != "?"]
    for tier in EVIDENCE_TIERS:
        rank = evidence_tiers.tier_rank(tier)
        # A case is "covered" at this tier if it is first detected at or below it.
        covered = sum(
            1 for r in scored
            if r["detected_at"] is not None
            and evidence_tiers.tier_rank(r["detected_at"]) <= rank
        )
        total = len(scored)
        pct = 100 * covered // total if total else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"    {tier} {evidence_tiers.TIER_LABELS[tier]:<48} {covered:>3}/{total} ({pct:3}%) {bar}")
    misses = [r["case"] for r in scored if r["detected_at"] is None]
    if misses:
        print(f"\n  Not reached by any tier ({len(misses)}): {', '.join(misses)}")
        print("  (a MISS means no tier emitted the cataloged change kind with the "
              "right verdict — usually the layer that would see it was unavailable "
              "(no castxml for L2, no compile DB for L3, no BuildSourcePack for L4), or "
              "the case's L3/L4 drift can't be reproduced by building v1/v2 with "
              "identical flags in this harness.)")
    # Honesty check: empirical first-detection vs ground_truth min_evidence.
    # (evidence_tiers.detected_at already floors kind-less quiet cases at their
    # designed tier, so an invisible-change NO_CHANGE like case122 reports a MISS
    # rather than a spurious L0 match.)
    drift = [
        (r["case"], r["min_evidence"], r["detected_at"])
        for r in scored
        if r["detected_at"] is not None
        and r["min_evidence"] not in ("?", r["detected_at"])
    ]
    if drift:
        print("\n  min_evidence vs empirical detect-tier differences "
              "(review scripts/evidence_tiers.py):")
        for case, declared, got in drift:
            print(f"    {case:<40} declared={declared} empirical={got}")


def _run_evidence_tiers(args: Any) -> None:
    """Driver for `--evidence-tiers`: run the catalog at L0/L1/L2/L3 and report."""
    REPORT_DIR.mkdir(exist_ok=True)
    BUILD_DIR.mkdir(exist_ok=True)
    all_cases = sorted(d for d in EXAMPLES_DIR.iterdir() if d.is_dir() and d.name.startswith("case"))
    if args.suite == "pinned74":
        all_cases = [d for d in all_cases if PINNED_74_CASE_RE.match(d.name)]
    case_prefixes = args.cases or []

    print("Evidence-tier benchmark — abicheck at five sources of information (L0–L4)")
    print("  L0 binary only · L1 +debug · L2 +headers · L3 +build · (L4 +source = n/a, needs BuildSourcePack)")

    rows: list[dict] = []
    for case_dir in all_cases:
        if case_prefixes and not any(case_dir.name.startswith(p) for p in case_prefixes):
            continue
        row = _run_case_evidence_tiers(case_dir, args)
        if row is not None:
            rows.append(row)

    _print_evidence_tier_table(rows)
    _print_evidence_tier_summary(rows)

    report = {
        "schema": "abicheck-evidence-tiers/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": _git_commit(),
        "ground_truth_sha256": _ground_truth_digest(),
        "tiers": EVIDENCE_TIERS,
        "results": rows,
    }
    out = REPORT_DIR / "evidence_tier_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\n  Report: {out}\n")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()

    if args.evidence_tiers:
        _run_evidence_tiers(args)
        return

    REPORT_DIR.mkdir(exist_ok=True)
    BUILD_DIR.mkdir(exist_ok=True)

    all_cases = sorted(d for d in EXAMPLES_DIR.iterdir() if d.is_dir() and d.name.startswith("case"))
    if args.suite == "pinned74":
        all_cases = [d for d in all_cases if PINNED_74_CASE_RE.match(d.name)]
    selected_tools = _resolve_selected_tools(args)
    active_tools = [t for t in TOOL_REGISTRY if t.name in selected_tools]

    _print_table_header(active_tools)

    results: list[dict] = []
    case_prefixes = args.cases or []

    for case_dir in all_cases:
        _process_case(case_dir, active_tools, case_prefixes, results, args)

    # ── Accuracy summary ──────────────────────────────────────────────────────
    _print_accuracy_summary(results, active_tools, selected_tools)

    summary = REPORT_DIR / "comparison_summary.json"
    summary.write_text(json.dumps(results, indent=2))

    # Release-pinnable artifact: metadata + accuracy + results in one file.
    report = _collect_metadata(results, active_tools, args.suite)
    report_path = REPORT_DIR / "benchmark_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"\n  Reports: {REPORT_DIR}/")
    print(f"  Summary: {summary}")
    print(f"  Report:  {report_path}  (pinned: commit={report['git_commit'] or 'unknown'}, "
          f"gt={(report['ground_truth_sha256'] or '')[:12]})\n")


if __name__ == "__main__":
    main()
