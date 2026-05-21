#!/usr/bin/env python3
"""AI-readiness checks for the abicheck codebase.

Verifies invariants that keep the repository legible to AI agents and
prevent silent regressions in conventions documented in CLAUDE.md.

Run locally:

    python scripts/check_ai_readiness.py

Exit codes:
    0 = all errors clear (warnings may still be printed)
    1 = at least one ERROR finding

The script is pure-Python stdlib (no third-party deps) so it can run as
the first step in CI before `pip install`.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Make `abicheck` importable when the package is not pip-installed (e.g. when
# the script runs as the first CI step before `pip install -e .`).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PKG = ROOT / "abicheck"
TESTS = ROOT / "tests"
DOCS = ROOT / "docs"
EXAMPLES = ROOT / "examples"
SCRIPTS = ROOT / "scripts"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# File-size thresholds (lines).  Files over WARN_LINES surface a warning;
# files over ERROR_LINES are an error unless they appear in LARGE_FILE_ALLOWLIST.
WARN_LINES = 1500
ERROR_LINES = 2000

# Hard line limit is enforced for every source file. If you find yourself
# wanting to add an entry, split the file instead — the AI-readiness check is
# meant to keep modules legible for agents.
LARGE_FILE_ALLOWLIST: frozenset[str] = frozenset()

# Directories that must contain a CLAUDE.md for per-area agent context.
REQUIRED_CLAUDE_MD_DIRS: tuple[Path, ...] = (
    PKG,
    PKG / "compat",
    TESTS,
    DOCS,
    EXAMPLES,
    SCRIPTS,
)

# Minimum test-file ratio (test files / source files).
MIN_TEST_RATIO = 0.20
MIN_SOURCE_FILES_FOR_RATIO = 3

# Documented baseline mypy error count (see CLAUDE.md → "Known mypy issues").
# Fail if mypy reports MORE errors than this; emit a WARN when the count drops
# so the baseline is lowered deliberately rather than drifting silently.
MYPY_ERROR_BASELINE = 27


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Findings:
    """Collects errors and warnings, grouped by check name for readable output."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))

    def report(self) -> int:
        by_check: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"errors": [], "warnings": []}
        )
        for check, msg in self.errors:
            by_check[check]["errors"].append(msg)
        for check, msg in self.warnings:
            by_check[check]["warnings"].append(msg)

        for check, buckets in sorted(by_check.items()):
            print(f"\n=== {check} ===")
            for m in buckets["errors"]:
                print(f"  ERROR: {m}")
            for m in buckets["warnings"]:
                print(f"  WARN:  {m}")

        n_err, n_warn = len(self.errors), len(self.warnings)
        print(f"\nAI-readiness: {n_err} error(s), {n_warn} warning(s)")
        return 1 if n_err else 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_python_sources() -> Iterable[Path]:
    """Yield every .py file under the package (skip dunder-only files for some checks)."""
    yield from PKG.rglob("*.py")


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Check: file-size limits
# ---------------------------------------------------------------------------


def check_file_sizes(f: Findings) -> None:
    """ERROR if a source file exceeds ERROR_LINES (unless allow-listed);
    WARN at WARN_LINES regardless.
    """
    for path in _iter_python_sources():
        rel = _rel(path)
        # Skip __pycache__ and similar; rglob shouldn't return them but be safe.
        if "__pycache__" in rel:
            continue
        with path.open("r", encoding="utf-8") as fh:
            lines = sum(1 for _ in fh)
        if lines > ERROR_LINES:
            if rel in LARGE_FILE_ALLOWLIST:
                f.warn(
                    "file-size",
                    f"{rel}: {lines} lines (allowlisted; consider splitting per CLAUDE.md)",
                )
            else:
                f.err(
                    "file-size",
                    f"{rel}: {lines} lines exceeds hard limit ({ERROR_LINES}). Split via helpers or a _lib/ pattern.",
                )
        elif lines > WARN_LINES:
            f.warn(
                "file-size", f"{rel}: {lines} lines exceeds soft limit ({WARN_LINES})"
            )


# ---------------------------------------------------------------------------
# Check: CLAUDE.md coverage per major directory
# ---------------------------------------------------------------------------


def check_claude_md_coverage(f: Findings) -> None:
    for d in REQUIRED_CLAUDE_MD_DIRS:
        if not d.exists():
            continue
        candidate = d / "CLAUDE.md"
        if not candidate.is_file():
            f.err(
                "claude-md-coverage",
                f"{_rel(d)}/: missing CLAUDE.md (agents need per-area context)",
            )


# ---------------------------------------------------------------------------
# Check: test-file ratio
# ---------------------------------------------------------------------------


def check_test_ratio(f: Findings) -> None:
    src_count = sum(1 for p in PKG.rglob("*.py") if not p.name.startswith("__"))
    if src_count < MIN_SOURCE_FILES_FOR_RATIO:
        return
    test_count = sum(1 for p in TESTS.glob("test_*.py"))
    ratio = test_count / src_count if src_count else 0.0
    if ratio < MIN_TEST_RATIO:
        f.warn(
            "test-ratio",
            f"abicheck/: {test_count} test files / {src_count} source files = {ratio:.0%} (< {MIN_TEST_RATIO:.0%})",
        )


# ---------------------------------------------------------------------------
# Check: `from __future__ import annotations`
# ---------------------------------------------------------------------------


_FUTURE_RE = re.compile(r"^\s*from\s+__future__\s+import\s+annotations\b", re.MULTILINE)


def check_future_annotations(f: Findings) -> None:
    """WARN when a source file lacks the documented future-annotations import.

    Empty files, package markers, and modules whose only statements are
    `__all__`/docstrings can be skipped.  We keep the check simple: any
    file with executable AST nodes beyond a docstring or `__future__` line
    is expected to carry the import per CLAUDE.md conventions.
    """
    for path in _iter_python_sources():
        # Package markers rarely use annotations themselves; skip.
        if path.name in {"__init__.py", "__main__.py"}:
            continue
        rel = _rel(path)
        src = _read(path)
        if not src.strip():
            continue
        try:
            tree = ast.parse(src, filename=rel)
        except SyntaxError:
            continue
        # Skip near-empty files.
        meaningful = [
            n
            for n in tree.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        if not meaningful:
            continue
        if _FUTURE_RE.search(src):
            continue
        f.warn(
            "future-annotations",
            f"{rel}: missing `from __future__ import annotations` (CLAUDE.md convention)",
        )


# ---------------------------------------------------------------------------
# Check: ChangeKind partition completeness
# ---------------------------------------------------------------------------


def check_changekind_partition(f: Findings) -> None:
    try:
        from abicheck.checker_policy import (
            API_BREAK_KINDS,
            BREAKING_KINDS,
            COMPATIBLE_KINDS,
            RISK_KINDS,
            ChangeKind,
        )
    except Exception as e:  # noqa: BLE001 — surface ANY import failure
        f.err("changekind-partition", f"failed to import ChangeKind: {e}")
        return

    all_kinds = set(ChangeKind)
    buckets = {
        "BREAKING_KINDS": set(BREAKING_KINDS),
        "API_BREAK_KINDS": set(API_BREAK_KINDS),
        "COMPATIBLE_KINDS": set(COMPATIBLE_KINDS),
        "RISK_KINDS": set(RISK_KINDS),
    }
    covered: set[ChangeKind] = set().union(*buckets.values())
    missing = all_kinds - covered
    if missing:
        names = ", ".join(sorted(k.name for k in missing))
        f.err("changekind-partition", f"ChangeKinds not in any category: {names}")

    # Detect overlap between buckets (each kind belongs to exactly one).
    pairs = list(buckets.items())
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            (n1, s1), (n2, s2) = pairs[i], pairs[j]
            both = s1 & s2
            if both:
                names = ", ".join(sorted(k.name for k in both))
                f.err(
                    "changekind-partition",
                    f"ChangeKinds appear in both {n1} and {n2}: {names}",
                )


# ---------------------------------------------------------------------------
# Check: every ChangeKind is produced by some diff/detector module
# ---------------------------------------------------------------------------


def check_changekind_detector_crossref(f: Findings) -> None:
    """WARN if a ChangeKind is never produced (no `ChangeKind.NAME` reference
    anywhere in the package outside the definition file itself).
    """
    try:
        from abicheck.checker_policy import ChangeKind
    except Exception:
        return  # already reported by partition check

    detector_text = ""
    for path in PKG.rglob("*.py"):
        if path.name == "checker_policy.py":
            continue  # the definition file: every kind appears here trivially
        detector_text += "\n" + _read(path)

    for kind in ChangeKind:
        token = f"ChangeKind.{kind.name}"
        if token not in detector_text:
            f.warn(
                "changekind-detector",
                f"{kind.name}: not referenced anywhere in abicheck/ outside checker_policy.py (orphan kind?)",
            )


# ---------------------------------------------------------------------------
# Check: every ChangeKind is documented in docs/
# ---------------------------------------------------------------------------


def check_changekind_docs(f: Findings) -> None:
    try:
        from abicheck.checker_policy import ChangeKind
    except Exception:
        return

    if not DOCS.exists():
        return
    doc_text = ""
    for path in DOCS.rglob("*.md"):
        doc_text += "\n" + _read(path)

    for kind in ChangeKind:
        # Accept either the enum value (often the canonical key) or the name.
        # Many change kinds appear in docs as their string value (e.g. "symbol_removed").
        try:
            value = str(kind.value)
        except Exception:
            value = ""
        if kind.name in doc_text or (value and value in doc_text):
            continue
        f.warn(
            "changekind-docs",
            f"{kind.name}: not documented in docs/ (value={value!r})",
        )


# ---------------------------------------------------------------------------
# Check: import-cycle detection
# ---------------------------------------------------------------------------


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("").as_posix()
    return rel.replace("/", ".")


def _module_imports(path: Path) -> set[str]:
    src = _read(path)
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return set()
    out: set[str] = set()
    pkg_name = _module_name(path).rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                # Relative import: `from . import X` / `from .. import X`
                if node.level:
                    base_parts = pkg_name.split(".")
                    base = ".".join(base_parts[: len(base_parts) - (node.level - 1)])
                    for alias in node.names:
                        out.add(f"{base}.{alias.name}" if base else alias.name)
                continue
            if node.level:  # relative
                base_parts = pkg_name.split(".")
                base = ".".join(base_parts[: len(base_parts) - (node.level - 1)])
                full = f"{base}.{node.module}" if base else node.module
                out.add(full)
            else:
                out.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return {m for m in out if m.startswith("abicheck")}


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        if node in visiting:
            if visiting[node] == 1:
                idx = stack.index(node)
                cycles.append(stack[idx:] + [node])
            return
        visiting[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):
            dfs(nxt)
        stack.pop()
        visiting[node] = 2

    for n in list(graph):
        if n not in visiting:
            dfs(n)

    # Deduplicate cycles by their normalized rotation.
    seen: set[tuple[str, ...]] = set()
    unique: list[list[str]] = []
    for c in cycles:
        nodes = tuple(c[:-1])  # last == first
        if not nodes:
            continue
        k = min(nodes.index(m) for m in nodes if m == min(nodes))
        rotated = tuple(nodes[k:] + nodes[:k])
        if rotated in seen:
            continue
        seen.add(rotated)
        unique.append(list(rotated) + [rotated[0]])
    return unique


# Intentional import cycles to ignore. Each entry is a frozenset of module
# short names (no `abicheck.` prefix) that participate in a known, by-design
# cycle — e.g. Click sub-command modules that register on a parent's group.
IMPORT_CYCLE_ALLOWLIST: frozenset[frozenset[str]] = frozenset(
    {
        # cli.py imports cli_compare_release / cli_baseline / cli_debian_symbols
        # at module-load tail to register their @main.command(...) decorators;
        # those sub-modules import `main` and shared helpers back from cli.
        frozenset({"cli", "cli_compare_release"}),
        frozenset({"cli", "cli_baseline"}),
        frozenset({"cli", "cli_debian_symbols"}),
    }
)


def check_import_cycles(f: Findings) -> None:
    # Build module -> direct abicheck imports.
    all_modules = {_module_name(p) for p in PKG.rglob("*.py")}
    graph: dict[str, set[str]] = {}
    for p in PKG.rglob("*.py"):
        mod = _module_name(p)
        deps = _module_imports(p)
        # Resolve "abicheck.foo" → keep only nodes that exist as modules
        # (drop sub-symbols imported `from abicheck.foo import Bar`).
        resolved: set[str] = set()
        for d in deps:
            if d in all_modules:
                resolved.add(d)
            else:
                parent = d.rsplit(".", 1)[0]
                if parent in all_modules:
                    resolved.add(parent)
        graph[mod] = resolved

    cycles = _find_cycles(graph)
    for cyc in cycles:
        short = frozenset(m.removeprefix("abicheck.") for m in cyc[:-1])
        if short in IMPORT_CYCLE_ALLOWLIST:
            continue
        f.err(
            "import-cycles",
            " -> ".join(m.removeprefix("abicheck.") for m in cyc),
        )


# ---------------------------------------------------------------------------
# Check: mypy baseline drift
# ---------------------------------------------------------------------------


def check_mypy_baseline(f: Findings) -> None:
    """Run `mypy abicheck/` and ensure the error count hasn't drifted upward.

    Skipped (with a single info line) when mypy is unavailable on PATH.
    """
    mypy_bin = shutil.which("mypy")
    if mypy_bin is None:
        print("mypy-baseline: mypy not installed, skipping")
        return
    try:
        proc = subprocess.run(  # noqa: S603 — explicit binary path from PATH
            [mypy_bin, "abicheck"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        f.warn("mypy-baseline", f"mypy run failed: {e}")
        return

    # mypy summary line looks like:  "Found 17 errors in 5 files (checked 80 source files)"
    text = proc.stdout + proc.stderr
    m = re.search(r"Found (\d+) errors? in \d+ files?", text)
    if not m:
        if "Success" in text:
            count = 0
        else:
            f.warn("mypy-baseline", "could not parse mypy output; skipping drift check")
            return
    else:
        count = int(m.group(1))

    if count > MYPY_ERROR_BASELINE:
        f.err(
            "mypy-baseline",
            f"mypy reports {count} errors; baseline is {MYPY_ERROR_BASELINE} (CLAUDE.md). "
            f"Fix the new errors or update the baseline deliberately.",
        )
    elif count < MYPY_ERROR_BASELINE:
        f.warn(
            "mypy-baseline",
            f"mypy reports {count} errors; baseline is {MYPY_ERROR_BASELINE} — please lower the baseline.",
        )


# ---------------------------------------------------------------------------
# Registry & CLI
# ---------------------------------------------------------------------------


CHECKS: dict[str, Callable[[Findings], None]] = {
    "file-size": check_file_sizes,
    "claude-md-coverage": check_claude_md_coverage,
    "test-ratio": check_test_ratio,
    "future-annotations": check_future_annotations,
    "changekind-partition": check_changekind_partition,
    "changekind-detector": check_changekind_detector_crossref,
    "changekind-docs": check_changekind_docs,
    "import-cycles": check_import_cycles,
    "mypy-baseline": check_mypy_baseline,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=sorted(CHECKS),
        help="Skip a check by name (repeatable).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        choices=sorted(CHECKS),
        help="Run only the named check(s).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable summary on stdout (in addition to the report).",
    )
    args = parser.parse_args(argv)

    findings = Findings()
    selected = args.only or list(CHECKS)
    for name in selected:
        if name in args.skip:
            continue
        CHECKS[name](findings)

    rc = findings.report()

    if args.json:
        print(
            json.dumps(
                {
                    "errors": [{"check": c, "message": m} for c, m in findings.errors],
                    "warnings": [
                        {"check": c, "message": m} for c, m in findings.warnings
                    ],
                    "exit_code": rc,
                }
            )
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
