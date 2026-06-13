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


DEFAULT_ABICC_TIMEOUT = 120  # seconds

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
            hext = _header_ext(ext)
            v1h = case_dir / f"v1{hext}"
            v2h = case_dir / f"v2{hext}"
            return v1, v2, v1h if v1h.exists() else None, v2h if v2h.exists() else None
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
def run_abicheck(v1_so: Path, v2_so: Path, v1_h: Path | None, v2_h: Path | None,
                 case: str, rdir: Path) -> ToolResult:
    if not _HAS_ABICHECK:
        return ToolResult(verdict="SKIP")

    bdir = BUILD_DIR / case
    snap1 = bdir / "snap_v1.json"
    snap2 = bdir / "snap_v2.json"
    _t_start = time.monotonic()

    def dump(so: Path, h: Path | None, snap: Path, ver: str) -> tuple[bool, str]:
        cmd = [_PYTHON, "-m", "abicheck.cli", "dump", str(so), "-o", str(snap), "--version", ver]
        if h and h.exists():
            cmd += ["-H", str(h)]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=_ABICHECK_ENV)
        ok = run.returncode == 0 and snap.exists()
        return ok, (run.stderr or run.stdout)

    try:
        ok, err = dump(v1_so, v1_h, snap1, "v1")
        if not ok:
            return ToolResult(verdict="ERROR", raw_output=f"dump v1 failed: {err}",
                              elapsed_ms=(time.monotonic() - _t_start) * 1000)
        ok, err = dump(v2_so, v2_h, snap2, "v2")
        if not ok:
            return ToolResult(verdict="ERROR", raw_output=f"dump v2 failed: {err}",
                              elapsed_ms=(time.monotonic() - _t_start) * 1000)
    except subprocess.TimeoutExpired as exc:
        return ToolResult(verdict="TIMEOUT", raw_output=str(exc),
                          elapsed_ms=(time.monotonic() - _t_start) * 1000)

    try:
        r = subprocess.run(
            [_PYTHON, "-m", "abicheck.cli", "compare", str(snap1), str(snap2), "--format", "json"],
            capture_output=True, text=True, timeout=60, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT",
                          elapsed_ms=(time.monotonic() - _t_start) * 1000)
    elapsed_ms = (time.monotonic() - _t_start) * 1000

    out = r.stdout + r.stderr
    (rdir / f"{case}_abicheck.txt").write_text(out)

    verdict = _abicheck_verdict_from_compare(r.stdout, r.returncode)

    changes = [ln.strip() for ln in out.splitlines()
               if any(k in ln for k in ("removed", "added", "changed")) and ln.strip()]
    return ToolResult(verdict=verdict, changes=changes[:8], raw_output=out,
                      elapsed_ms=elapsed_ms)


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
                        case: str, rdir: Path) -> ToolResult:
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
            capture_output=True, text=True, timeout=60, env=_ABICHECK_ENV,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=60_000.0)
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
                        case: str, rdir: Path, timeout: int = 60) -> ToolResult:
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
        return ToolResult(verdict="TIMEOUT", elapsed_ms=float(timeout) * 1000)
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
                suffix: str = "", **_kw: Any) -> ToolResult:
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return ToolResult(verdict="TIMEOUT", elapsed_ms=60_000.0)
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
        return ToolResult(verdict="TIMEOUT", elapsed_ms=float(timeout) * 1000)
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
                        case: str, rdir: Path, **kw: Any) -> ToolResult:
    """Wrapper: run abidiff with headers_dir resolved from v1_h/v2_h."""
    if v1_h and v1_h.exists() and v2_h and v2_h.exists() and v1_h.parent != v2_h.parent:
        headers_dir: str | tuple | None = (str(v1_h.parent), str(v2_h.parent))
    elif v1_h and v1_h.exists():
        headers_dir = str(v1_h.parent)
    elif v2_h and v2_h.exists():
        headers_dir = str(v2_h.parent)
    else:
        headers_dir = None
    return run_abidiff(v1_so, v2_so, v1_h, v2_h, case, rdir, headers_dir=headers_dir, suffix="_headers")


TOOL_REGISTRY: list[Tool] = [
    Tool("abicheck", run_abicheck, "abicheck", 12, "expected"),
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
    p.add_argument("--abicc-timeout", type=int, default=DEFAULT_ABICC_TIMEOUT,
                   help=f"Timeout per ABICC call (default: {DEFAULT_ABICC_TIMEOUT}s)")
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
                   choices=["abicheck", "abicheck_compat", "abicheck_strict",
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
    for t in active_tools:
        correct, total = _accuracy(results, t.name, t.expected_key)
        accuracy[t.name] = {
            "label": t.label,
            "correct": correct,
            "scored": total,
            "pct": round(100 * correct / total, 1) if total else None,
            "total_ms": round(_total_ms(results, t.ms_key)),
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
            print(f"  {name:<35} BUILD_PATH_UNAVAILABLE(prebuilt|cmake)")
            results.append(_error_entry(name, expected))
            return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=False)

        cmake_build = bdir / "cmake_build"
        if cmake_build.exists():
            shutil.rmtree(str(cmake_build))
        cmake_build.mkdir(parents=True)
        cmake_env = _configure_cmake_env(force_case64_compile, preferred_family)
        cr = _run_cmake_configure_and_build(case_dir, cmake_build, name, cmake_env)
        return _resolve_cmake_libs(
            name, expected, cmake_build, cr, v1_so, v2_so,
            used_cmake_artifacts, v1_h_hint, v2_h_hint, results, used_make_artifacts,
        )

    return _BuildResult(v1_so, v2_so, used_make_artifacts, used_cmake_artifacts, v1_h_hint, v2_h_hint, ok=True)


def _print_tool_accuracy_bars(results: list[dict], active_tools: list[Any]) -> None:
    """Print per-tool accuracy bars with timing totals."""
    print("  Accuracy vs expected verdicts:")
    for t in active_tools:
        c, total = _accuracy(results, t.name, t.expected_key)
        if total > 0:
            pct = 100 * c // total
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            tot_s = _total_ms(results, t.ms_key) / 1000
            print(f"    {t.label}: {c:>2}/{total} ({pct:3}%) {bar}  [{tot_s:6.1f}s total]")


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
        "abicheck", "abicheck_compat", "abicheck_strict",
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
) -> dict[str, ToolResult]:
    """Run all active tools for a case and return their results keyed by tool name."""
    tool_results: dict[str, ToolResult] = {}
    for t in active_tools:
        if t.name in ("abicheck", "abicheck_compat", "abicheck_strict"):
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h_abicheck, v2_h_abicheck, name, rdir)
        elif t.name in ("abicc_dumper", "abicc_xml"):
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h, v2_h, name, rdir, timeout=abicc_timeout)
        else:
            # abidiff and abidiff_headers share the common signature
            tool_results[t.name] = t.run_fn(v1_so, v2_so, v1_h, v2_h, name, rdir)
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

    tool_results = _run_tools_for_case(
        active_tools, v1_so, v2_so, v1_h, v2_h, v1_h_abicheck, v2_h_abicheck,
        name, rdir, args.abicc_timeout,
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
            cmd += ["-H", str(h)]
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
