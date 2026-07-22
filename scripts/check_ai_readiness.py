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
import importlib.util
import json
import re
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
EVAL = ROOT / "eval"
VALIDATION = ROOT / "validation"
ACTION = ROOT / "action"
CONTRIB_CLANG_PLUGIN = ROOT / "contrib" / "abicheck-clang-plugin"
GITHUB_DIR = ROOT / ".github"
AGENT_EVALS = ROOT / "agent-evals"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# First-party Python roots (CLAUDE.md "M1-2"): every tree of hand-written,
# agent-editable source the size/test-ratio checks below cover. `abicheck/`
# was previously the only root scanned — `scripts/`, `eval/`, `validation/`,
# `action/`, and the clang-plugin's `tests/` could grow unbounded (including
# the readiness script itself: `check_ai_readiness.py` was 1842 lines, over
# its own WARN threshold, before this list started covering `scripts/`).
FIRST_PARTY_PY_ROOTS: tuple[Path, ...] = (
    PKG,
    SCRIPTS,
    TESTS,
    EVAL,
    VALIDATION,
    ACTION,
    CONTRIB_CLANG_PLUGIN,
    AGENT_EVALS,
)

# Directory *names* excluded from first-party scanning wherever they appear
# under a first-party root — fixture data, golden snapshots, and generated
# CI-artifact output are not hand-written source an agent edits directly.
FIRST_PARTY_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {"__pycache__", "fixtures", "golden", "results", "build"}
)


def _iter_first_party_python_files() -> Iterable[Path]:
    """Yield every first-party .py file, skipping excluded subdirectories."""
    for root in FIRST_PARTY_PY_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel_parts = path.relative_to(ROOT).parts
            if any(part in FIRST_PARTY_EXCLUDE_DIR_NAMES for part in rel_parts):
                continue
            yield path


# File-size thresholds (lines).  Files over WARN_LINES surface a warning;
# files over ERROR_LINES are an error unless they appear in LARGE_FILE_ALLOWLIST.
WARN_LINES = 1500
ERROR_LINES = 2000

# Hard line limit is enforced for every first-party source file. If you find
# yourself wanting to add an entry, split the file instead — the AI-readiness
# check is meant to keep modules legible for agents. Every entry below
# predates first-party scanning covering `scripts/`/`tests/` (CLAUDE.md
# "M1-2") — these were already over the hard cap the moment those trees
# started being scanned, and each needs its own reviewed split pass, not one
# rushed through as a side effect of an unrelated readiness-gate change.
# Tracked here instead of silently exempted — every one still surfaces as a
# WARN on every run, so the debt stays visible rather than invisible.
#
# `check_ai_readiness.py` itself is the one entry that isn't pre-existing
# debt this change merely discovered — the checks added in this same commit
# pushed it over 2000 lines. It stays here rather than being split because
# its largest self-contained block (`check_cli_contract` and its ~15 private
# helpers, ADR-037 D10) is exactly what `tests/test_cli_contract.py`
# monkeypatches by module-level name (e.g. `gate._VERDICT_CMD_MODULES`)
# before calling `gate.check_cli_contract(...)` — moving that block to a
# sibling module would make `check_cli_contract` read a *different* module's
# globals than the ones the monkeypatch rebinds, silently breaking those
# tests' ability to patch the very state they're asserting against. That
# split needs its own pass that also updates the test file, not this one.
LARGE_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "scripts/benchmark_comparison.py",
        "scripts/check_ai_readiness.py",
        "tests/test_type_graph.py",
        "tests/test_l3l4l5_new_kinds.py",
        "tests/test_mcp_server_unit.py",
        "tests/test_cli_scan.py",
        "tests/test_appcompat.py",
        "tests/test_dumper_clang.py",
        "tests/test_source_abi.py",
        "tests/test_bundle.py",
        "tests/test_source_extractors_clang.py",
        "tests/test_build_source_cli.py",
        "tests/test_cov95_cli.py",
        "tests/test_service_unit.py",
        "tests/test_crosscheck.py",
        "tests/test_dwarf_coverage_gaps.py",
        "tests/test_package.py",
    }
)

# Directories that must contain a CLAUDE.md for per-area agent context.
REQUIRED_CLAUDE_MD_DIRS: tuple[Path, ...] = (
    PKG,
    PKG / "compat",
    TESTS,
    DOCS,
    EXAMPLES,
    SCRIPTS,
    EVAL,
    VALIDATION,
)

# Directories added later (CLAUDE.md "M1-1"/"M1-2") that use the canonical
# vendor-neutral AGENTS.md instead of CLAUDE.md — either file satisfies this
# check, unlike REQUIRED_CLAUDE_MD_DIRS above which stays CLAUDE.md-only for
# the original, already-established directories.
REQUIRED_AGENT_INSTRUCTION_DIRS: tuple[Path, ...] = (
    GITHUB_DIR,
    ACTION,
    CONTRIB_CLANG_PLUGIN,
    AGENT_EVALS,
)

# Minimum test-file ratio (test files / source files).
MIN_TEST_RATIO = 0.20
MIN_SOURCE_FILES_FOR_RATIO = 3

# Documented baseline mypy error count (see CLAUDE.md → "Known mypy issues").
# Fail if mypy reports MORE errors than this; emit a WARN when the count drops
# so the baseline is lowered deliberately rather than drifting silently.
MYPY_ERROR_BASELINE = 0


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
    """ERROR if a first-party source file exceeds ERROR_LINES (unless
    allow-listed); WARN at WARN_LINES regardless.

    Covers every FIRST_PARTY_PY_ROOTS tree (CLAUDE.md "M1-2"), not just
    `abicheck/` — `scripts/`, `eval/`, `validation/`, `action/`, and the
    clang-plugin's `tests/` can grow unbounded just as easily.
    """
    for path in _iter_first_party_python_files():
        rel = _rel(path)
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


def check_agent_instructions_coverage(f: Findings) -> None:
    """ERROR if a REQUIRED_AGENT_INSTRUCTION_DIRS tree has neither an
    AGENTS.md nor a CLAUDE.md (CLAUDE.md "M1-1"/"M1-2").

    Distinct from check_claude_md_coverage above: these are directories added
    after AGENTS.md became the canonical vendor-neutral instruction file, so
    either name satisfies the requirement — unlike REQUIRED_CLAUDE_MD_DIRS,
    which stays CLAUDE.md-only for the original, already-established dirs.
    """
    for d in REQUIRED_AGENT_INSTRUCTION_DIRS:
        if not d.exists():
            continue
        if (d / "AGENTS.md").is_file() or (d / "CLAUDE.md").is_file():
            continue
        f.err(
            "agent-instructions-coverage",
            f"{_rel(d)}/: missing AGENTS.md (or CLAUDE.md) — agents need per-area context",
        )


# ---------------------------------------------------------------------------
# Check: every scripts/*.py file is listed in scripts/CLAUDE.md's inventory
# ---------------------------------------------------------------------------


def _extract_markdown_section(text: str, heading: str) -> str:
    """Return the body of a `## <heading>` markdown section (up to the next
    `## `-level heading, or EOF). Empty string if the heading isn't found."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL
    )
    m = pattern.search(text)
    return m.group(1) if m else ""


def check_script_inventory_completeness(f: Findings) -> None:
    """WARN if a scripts/*.py file isn't mentioned by name in scripts/CLAUDE.md's
    "## Inventory" table specifically — not just mentioned anywhere in the file.

    scripts/CLAUDE.md's "Inventory" table is the discovery surface an agent
    reads before assuming a script does or doesn't exist — an unlisted script
    is invisible to that discovery path even though `ls scripts/` would find
    it (CLAUDE.md "M1-2": "script inventory completeness"). Scoping to the
    Inventory section specifically (not the whole file) matters: a script
    named only in prose elsewhere (e.g. "see gen_foo.py's docstring") would
    otherwise satisfy this check without actually having an inventory row.
    """
    claude_md = SCRIPTS / "CLAUDE.md"
    if not claude_md.is_file():
        return  # already reported by claude-md-coverage
    inventory = _extract_markdown_section(_read(claude_md), "Inventory")
    if not inventory:
        f.warn(
            "script-inventory",
            f"{_rel(claude_md)}: no '## Inventory' section found; "
            "script-inventory completeness can't be checked",
        )
        return
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name.startswith("_"):
            continue  # private/internal helper, not a discoverable entry point
        if f"`{path.name}`" not in inventory:
            f.warn(
                "script-inventory",
                f"{_rel(path)}: not mentioned in scripts/CLAUDE.md's '## Inventory' table",
            )


# ---------------------------------------------------------------------------
# Check: generated-file ownership (CLAUDE.md "M1-2")
# ---------------------------------------------------------------------------

# (path, required marker substring (case-insensitive), generator to re-run).
# Scoped to files where a textual marker is possible (Markdown/Python/JSON
# with a description field) — pure-data JSON snapshot fixtures (the G20/
# L3-L5/reachability example fixtures) have no room for a marker and are
# already gated by their own generator's `--check` drift flag instead.
GENERATED_FILE_MARKERS: tuple[tuple[Path, str, str], ...] = (
    (
        DOCS / "reference" / "detector-spec.md",
        "generated by scripts/gen_detector_spec.py",
        "gen_detector_spec.py",
    ),
    (
        DOCS / "reference" / "detector-spec.json",
        "(generated)",
        "gen_detector_spec.py",
    ),
    (
        PKG / "stable_abi_data.py",
        "generated data",
        "gen_stable_abi_data.py",
    ),
    (
        DOCS / "reference" / "github-action-inputs.md",
        "generated by scripts/gen_action_reference.py",
        "gen_action_reference.py",
    ),
)


def check_generated_file_ownership(f: Findings) -> None:
    """ERROR if a known-generated file lost its "this is generated" marker.

    Catches the case a hand-edit strips the banner comment entirely (which
    would otherwise defeat the whole point of marking a file generated — an
    agent reading it with no banner has no signal to check the generator
    instead of hand-editing). Drift *content* (a generated file whose content
    no longer matches its generator's output) is separately gated by each
    generator's own `--check` flag — this check only verifies the ownership
    signal itself is still present.
    """
    for path, marker, generator in GENERATED_FILE_MARKERS:
        if not path.is_file():
            continue
        if marker not in _read(path).lower():
            f.err(
                "generated-file-ownership",
                f"{_rel(path)}: missing its generated-file marker ({marker!r}) "
                f"— regenerate with `python scripts/{generator}` rather than "
                "hand-editing, and keep the marker comment.",
            )
    for path in sorted((DOCS / "examples").glob("case*.md")):
        if "generated by scripts/gen_examples_docs.py" not in _read(path).lower():
            f.err(
                "generated-file-ownership",
                f"{_rel(path)}: missing its generated-file marker — regenerate "
                "with `python scripts/gen_examples_docs.py` rather than hand-editing.",
            )


# ---------------------------------------------------------------------------
# Check: test-file ratio
# ---------------------------------------------------------------------------


def check_test_ratio(f: Findings) -> None:
    """Recursive test discovery (CLAUDE.md "M1-2"): a `test_*.py` nested in a
    subdirectory of `tests/` (e.g. a future `tests/subpkg/test_foo.py`) must
    count toward the ratio just as a top-level one does — `TESTS.glob(...)`
    silently wouldn't see it. Fixture/golden-data subtrees are excluded via
    FIRST_PARTY_EXCLUDE_DIR_NAMES since they aren't test modules even if a
    stray file there matched the `test_*.py` glob.
    """
    src_count = sum(1 for p in PKG.rglob("*.py") if not p.name.startswith("__"))
    if src_count < MIN_SOURCE_FILES_FOR_RATIO:
        return
    test_count = sum(
        1
        for p in TESTS.rglob("test_*.py")
        if not any(
            part in FIRST_PARTY_EXCLUDE_DIR_NAMES for part in p.relative_to(TESTS).parts
        )
    )
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
# Check: headline counts in docs stay in sync with source-of-truth
# ---------------------------------------------------------------------------


def check_doc_count_sync(f: Findings) -> None:
    """Keep hand-written headline counts in sync with their source of truth.

    Two numbers historically drifted across the docs: the number of `ChangeKind`
    values ("N change types") and the size of the example catalog
    (`examples/ground_truth.json`). Each anchor below pins a specific sentence to
    a computed value:

    - ERROR if the anchor sentence is present but the number is wrong (the real
      drift bug — forces docs to be updated when a ChangeKind or case is added).
    - WARN if the anchor sentence can no longer be found (wording changed, so the
      guard silently stopped covering that spot — update the regex here).
    """
    try:
        from abicheck.checker_policy import ChangeKind
    except Exception:
        # Package not importable (e.g. pre-install lane) — skip silently, like
        # the other ChangeKind checks.
        return

    n_kinds = len(list(ChangeKind))

    gt_path = EXAMPLES / "ground_truth.json"
    try:
        verdicts = json.loads(_read(gt_path))["verdicts"]
    except Exception:
        return
    n_catalog = len(verdicts)

    # (file, human label, expected value, regex capturing the documented number)
    anchors = [
        (
            ROOT / "README.md",
            "ChangeKind count",
            n_kinds,
            r"\*\*(\d+) ABI/API change types\*\*",
        ),
        (
            DOCS / "index.md",
            "ChangeKind count",
            n_kinds,
            r"\*\*(\d+) detection rules\*\*",
        ),
        (
            ROOT / "README.md",
            "ChangeKind count (feature bullet)",
            n_kinds,
            r"\*\*(\d+) change types\*\*",
        ),
        (
            ROOT / "README.md",
            "catalog size",
            n_catalog,
            r"contains \*\*(\d+) real-world ABI/API scenarios",
        ),
        (
            ROOT / "README.md",
            "catalog size (validation target)",
            n_catalog,
            r"the full \*\*(\d+)-case catalog\*\*",
        ),
        (
            DOCS / "reference" / "tool-comparison.md",
            "catalog size (full-catalog benchmark heading)",
            n_catalog,
            r"## Full-catalog benchmark \([^,]+, all (\d+) cases\)",
        ),
        (
            DOCS / "getting-started.md",
            "catalog size",
            n_catalog,
            r"repo includes (\d+) ABI scenario examples",
        ),
        (
            DOCS / "development/abicc-parity-status.md",
            "ChangeKind count (current total)",
            n_kinds,
            r"current ChangeKind total is \*\*(\d+)\*\*",
        ),
        (
            DOCS / "development/abicc-test-coverage-comparison.md",
            "ChangeKind count (current total)",
            n_kinds,
            r"current ChangeKind total is \*\*(\d+)\*\*",
        ),
        (
            DOCS / "user-guide/mcp-integration.md",
            "ChangeKind count (abi_list_changes JSON sample)",
            n_kinds,
            r"\"count\": (\d+)",
        ),
    ]

    for path, label, expected, pattern in anchors:
        text = _read(path)
        m = re.search(pattern, text)
        if m is None:
            f.warn(
                "doc-count-sync",
                f"{_rel(path)}: {label} anchor not found (pattern {pattern!r}); "
                "update the regex in check_doc_count_sync if the wording changed.",
            )
            continue
        found = int(m.group(1))
        if found != expected:
            f.err(
                "doc-count-sync",
                f"{_rel(path)}: {label} says {found}, but source of truth is {expected}. "
                "Update the doc (or the source) so they agree.",
            )

    # Generic sweep: any "<number> change kinds/types | ChangeKinds | detection
    # rules | -kind" phrase anywhere in the published docs must equal the real
    # enum size. The anchors above pin specific headline sentences; this catches
    # the long tail of casual mentions that historically drifted (190, 183,
    # 180+, 150+, 100+...). ADRs are dated decision records and keep the counts
    # that were true when they were written, so they are exempt; the archive of
    # retired/historical docs is exempt for the same reason.
    # `?...`? tolerates markdown code spans: "183 `ChangeKind` values",
    # "234 `ChangeKind`s".
    generic = re.compile(
        r"\b(\d{2,3})\+?"
        r"(?:-kind\b|\s+(?:ABI/API\s+)?(?:[Cc]hange\s+(?:kinds?|types?)|`?ChangeKinds?`?s?|detection\s+rules))"
    )
    adr_dir = DOCS / "development" / "adr"
    archive_dir = DOCS / "development" / "archive"
    exempt_dirs = (adr_dir, archive_dir)
    sweep_files = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "examples" / "README.md",
    ]
    sweep_files += [
        p
        for p in sorted(DOCS.rglob("*"))
        if p.suffix in {".md", ".yaml", ".yml"}
        and not any(p.is_relative_to(d) for d in exempt_dirs)
    ]
    for path in sweep_files:
        if not path.is_file():
            continue
        text = _read(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in generic.finditer(line):
                found = int(m.group(1))
                if found != n_kinds:
                    f.err(
                        "doc-count-sync",
                        f"{_rel(path)}:{lineno}: mentions {m.group(0)!r}, but the "
                        f"ChangeKind enum has {n_kinds} members. Update the doc "
                        "(or drop the count if it describes a subset).",
                    )


# ---------------------------------------------------------------------------
# Check: GitHub Action version-reference freshness (CLAUDE.md "M1-4")
# ---------------------------------------------------------------------------

# Historical/dated records are exempt: an ADR, an archived doc, a dated field
# report, or a retrospective "when this shipped" narrative bullet legitimately
# names the version that was current *then*, not "latest" — the same
# exemption principle check_doc_count_sync's generic sweep already uses for
# ADRs/archives.
_ACTION_VERSION_EXEMPT_DIRS: tuple[Path, ...] = (
    DOCS / "development" / "adr",
    DOCS / "development" / "archive",
    VALIDATION,
)
_ACTION_VERSION_EXEMPT_FILES: frozenset[Path] = frozenset(
    {
        DOCS / "development" / "goals.md",  # retrospective "Done:" bullets
        ROOT / "CHANGELOG.md",
    }
)

_ACTION_VERSION_RE = re.compile(r"abicheck/abicheck@v(\d+\.\d+\.\d+)")


def check_action_version_freshness(f: Findings) -> None:
    """ERROR if a non-exempt doc's `abicheck/abicheck@vX.Y.Z` GitHub Action
    usage example doesn't match repo_facts.json's `latest_release`.

    repo_facts.json is the single source of truth this checks against
    (generated by `scripts/gen_repo_facts.py`, itself gated by
    `verify.py`'s `repo-facts` step) — an agent should never be able to
    copy a `uses:` line from the docs and get a superseded release tag.
    """
    facts_path = ROOT / "repo_facts.json"
    if not facts_path.is_file():
        return  # repo-facts step / gen_repo_facts.py --check reports this
    try:
        latest = json.loads(_read(facts_path))["latest_release"]
    except (json.JSONDecodeError, KeyError):
        return

    candidates = [ROOT / "README.md"]
    if DOCS.exists():
        candidates += sorted(DOCS.rglob("*.md"))

    for path in candidates:
        if path in _ACTION_VERSION_EXEMPT_FILES:
            continue
        if any(path.is_relative_to(d) for d in _ACTION_VERSION_EXEMPT_DIRS):
            continue
        text = _read(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _ACTION_VERSION_RE.finditer(line):
                if m.group(1) != latest:
                    f.err(
                        "action-version-freshness",
                        f"{_rel(path)}:{lineno}: references "
                        f"abicheck/abicheck@v{m.group(1)}, but repo_facts.json's "
                        f"latest_release is {latest}. Update the doc (or add it "
                        "to the exemption list in check_action_version_freshness "
                        "if it's a deliberate historical reference).",
                    )


# ---------------------------------------------------------------------------
# Check: import-cycle detection
# ---------------------------------------------------------------------------


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("").as_posix()
    return rel.replace("/", ".")


def _module_imports(path: Path) -> set[str]:
    # Static-only: this walks `import` / `from … import` AST nodes. A *runtime*
    # `importlib.import_module("abicheck.X")` call is deliberately invisible
    # here — that is the escape hatch the `cli_buildsource` back-compat
    # `__getattr__` shim uses to re-export the graph helpers from `cli_graph`
    # without registering a `cli_buildsource → cli_graph` edge (which would form
    # a real cycle). If you switch a shim like that to a static import, expect
    # this gate to flag the cycle — that is very likely a real dependency-
    # direction problem, not something to unblock by extending
    # IMPORT_CYCLE_ALLOWLIST (see check_import_cycles' docstring / AGENTS.md
    # "What NOT to do"). Fix the direction (function-local import, or move
    # the shared logic to a leaf module) instead.
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
        # cli.py imports cli_compare_release / cli_baseline / cli_debian_symbols /
        # cli_appcompat / cli_stack / cli_suggest at module-load tail to register
        # their @main.command(...) decorators; those sub-modules import `main`
        # and shared helpers back from cli.
        frozenset({"cli", "cli_compare_release"}),
        frozenset({"cli", "cli_baseline"}),
        frozenset({"cli", "cli_debian_symbols"}),
        frozenset({"cli", "cli_appcompat"}),
        frozenset({"cli", "cli_plugin"}),
        frozenset({"cli", "cli_pr_comment"}),
        frozenset({"cli", "cli_probe"}),
        frozenset({"cli", "cli_stack"}),
        frozenset({"cli", "cli_suggest"}),
        frozenset({"cli", "cli_surface"}),
        # `scan` (cli_scan) reuses `embed_build_source` from cli_buildsource to
        # collect L3/L4/L5 inline; cli_buildsource imports `main`/helpers from cli;
        # cli imports cli_scan at its tail to register the command. All three edges
        # are the by-design sibling-registration / helper-reuse pattern, not a
        # true initialization cycle (the reuse import is function-local).
        frozenset({"cli", "cli_scan", "cli_buildsource"}),
        # ADR-035 D10 typed scan engine cluster: the typed engine
        # (`ScanRequest`/`run_scan`/`estimate_scan`) lives in the leaf module
        # `service_scan`, which `service` re-exports for the public Python API.
        # `service_scan.run_scan` drives the shared orchestration core in `cli_scan`
        # (function-local import) and `estimate_scan` reuses `service.expand_header_inputs`
        # (function-local); `cli_scan` reuses `service`/`cli_buildsource` collectors;
        # `cli`/`cli_surface` register and reuse those, and `cli` resolves inputs via
        # `cli_resolve` → `service`. `service_scan` imports nothing from `service` at
        # module-load time (it is a leaf), so the SCC closes only through function-local
        # imports (not an init cycle). One SCC, so this cluster covers its many
        # representative simple cycles by subset match. `cli_helpers_compare` and
        # `cli_buildsource_helpers` are extracted leaf helper modules for `cli` /
        # `cli_buildsource` (size-split per CLAUDE.md); they reuse `cli_resolve` /
        # `service` collectors and are re-exported back by their parent, so they join
        # the same by-design cluster (the package imports cleanly — no init deadlock).
        #
        # ADR-037 D1 (G22 Phase 1) widens this cluster: the verdict-emitting
        # front-ends now route through the Tier-2 service instead of calling
        # `checker.compare` directly. `cli_compare_release` and `appcompat` reach
        # `service` via *function-local* imports (`service.run_compare` /
        # `service.compare_snapshots`); `cli` registers every sibling command at its
        # module-load tail; and each sibling imports `main`/helpers back from `cli`.
        # That collapses the whole CLI-registration + service-routing graph into ONE
        # strongly-connected component. The members below are the *exact* SCC (it
        # closes only through function-local imports — the package imports cleanly,
        # no init deadlock), so listing the full set makes the subset match robust to
        # the DFS traversal order, which otherwise surfaces a different representative
        # simple cycle on each platform (e.g. via `cli_appcompat` vs `cli_plugin`).
        # A genuinely new bad cycle would pull in a module *outside* this SCC and so
        # would not be a subset — still flagged.
        #
        # ADR-037 D3 adds `cli_options`: the shared `@compile_context_options`
        # decorator's one resolver (`merge_compile_config`/`resolve_compile_context`,
        # shared by compare/dump/scan) reaches `CompileContext` in `service_scan` via
        # a *function-local* `from .service_scan import CompileContext`; `service_scan`
        # reaches `cli_scan` function-locally and `cli_scan` imports `cli_options` at
        # module load. `cli_options` itself imports only `cli_params` at module load
        # (it is a leaf), so this too closes only through function-local imports.
        #
        # The `compare`/`dump` command bodies are size-split out of `cli.py` into
        # `cli_compare_helpers.run_compare` / `cli_dump_helpers` (thin click wrappers
        # in `cli` delegate to them); those helpers reach the shared
        # `service`/`service_scan`/`cli_buildsource`/`cli_resolve` collectors
        # (function-local) and are imported back by `cli`, so they join the same SCC
        # — the package still imports cleanly (no init deadlock).
        #
        # `cli_scan_baseline` is the extracted `scan --baseline`/`--estimate`
        # sub-flow (size-split per CLAUDE.md): `cli_scan` imports it at module load
        # and it reaches `_safe_write_output` in `cli` plus the
        # `service`/`service_scan`/`cli_buildsource` collectors function-locally,
        # exactly as `cli_scan` did before the split — so it joins the same SCC and
        # introduces no new *runtime* edge (`service_scan` re-imports
        # `_public_provenance_set` from it function-locally).
        #
        # `cli_inputs` joins the same SCC (ADR-038 C.8): its `inputs validate`
        # command reuses the shared `-o/--format` pair via
        # `cli_options.output_options` (module-load import), and `cli_options`
        # is already a member of this cluster — so `cli -> cli_inputs ->
        # cli_options -> ... -> cli` closes through already-member modules,
        # not a new dependency direction. No init deadlock.
        #
        # `scan_engine` joins the same SCC (ADR-037 D1 dependency-direction fix):
        # the scan engine core (classify → always-on tier → level → compare,
        # `run_scan_core`) was split out of `cli_scan.py` into `scan_engine.py` so
        # the CLI (`cli_scan.py`) and the typed service API (`service_scan.py`)
        # both depend on one engine module instead of `service_scan.run_scan`
        # reaching into a front-end module — that inversion is exactly what this
        # split removes. What remains is a lateral engine-to-engine reference, not
        # a frontend dependency: `service_scan.run_scan` imports `run_scan_core`/
        # `_BudgetOverflow`/`_EvidenceContractError` from `scan_engine` (function-
        # local, avoiding an init-order issue); `scan_engine` type-annotates
        # `compile_context: CompileContext | None` with the type defined in
        # `service_scan` (under `if TYPE_CHECKING`, so it never executes) and
        # reaches `cli_buildsource.embed_build_source` / `cli_scan_baseline`
        # helpers function-locally, exactly as `cli_scan.py` did before the
        # split — so it closes the same cluster of cycles through already-member
        # modules rather than introducing a new one. No init deadlock — the
        # package still imports cleanly.
        #
        # `cli_config`, `cli_doctor`, and `cli_graph` also join this same SCC —
        # each already had its own standalone `{"cli", "cli_X"}` entry above,
        # which covers the trivial two-node cycle from `cli`'s tail-of-module
        # registration import. But `cli_config` reaches the shared machinery
        # via `cli_compare_helpers` (config `show-effective` reuses `_cli_flag`
        # from it) and `cli_doctor` via `cli_helpers_compare`, both of which are
        # already members of this cluster — so the *full* SCC computed by
        # Tarjan's algorithm over the real import graph includes all three,
        # regardless of which representative simple cycle the (traversal-order
        # dependent) DFS in `_find_cycles` happens to report. Without them
        # here, a cycle mixing one of these three with any other cluster
        # member (e.g. `cli -> cli_doctor -> cli_helpers_compare -> service ->
        # ... -> cli`) fails the subset match even though it is the identical
        # by-design cluster — which is exactly what made this check flaky
        # (non-deterministic `set` iteration order in `_find_cycles` picks a
        # different representative cycle each process run).
        frozenset(
            {
                "appcompat",
                "cli",
                # `cli_aggregate` joins this SCC exactly like `cli_inputs`: its
                # `aggregate` command reuses the shared `-o/--format` pair via
                # `cli_options.output_options` (module-load import), and
                # `cli_options` is already a member — so `cli -> cli_aggregate ->
                # cli_options -> ... -> cli` closes through already-member
                # modules, not a new dependency direction. No init deadlock.
                "cli_aggregate",
                "cli_appcompat",
                "cli_baseline",
                # `cli_build_output` (G30 P1.1) joins this SCC exactly like
                # `cli_aggregate`: its `build-output validate` command reuses
                # the shared `-o/--format` pair via `cli_options.output_options`
                # (module-load import), and `cli_options` is already a member —
                # so `cli -> cli_build_output -> cli_options -> ... -> cli`
                # closes through already-member modules, not a new dependency
                # direction. No init deadlock.
                "cli_build_output",
                "cli_buildsource",
                "cli_buildsource_helpers",
                "cli_compare_helpers",
                "cli_compare_release",
                "cli_config",
                "cli_debian_symbols",
                "cli_doctor",
                "cli_dump_helpers",
                "cli_graph",
                "cli_helpers_compare",
                "cli_inputs",
                "cli_options",
                "cli_plugin",
                "cli_pr_comment",
                "cli_probe",
                # `cli_project_targets` (G30 P1.5) joins this SCC exactly like
                # `cli_build_output`/`cli_aggregate`: its `project-targets
                # validate` command reuses the shared `-o/--format` pair via
                # `cli_options.output_options` (module-load import), and
                # `cli_options` is already a member — so `cli ->
                # cli_project_targets -> cli_options -> ... -> cli` closes
                # through already-member modules, not a new dependency
                # direction. No init deadlock.
                "cli_project_targets",
                "cli_resolve",
                "cli_scan",
                "cli_scan_baseline",
                "cli_stack",
                "cli_suggest",
                "cli_surface",
                "scan_engine",
                "service",
                "service_scan",
            }
        ),
        # TYPE_CHECKING-only typing cycle (no runtime import): AbiSnapshot
        # annotates an embedded BuildSourcePack; pack annotates SourceGraphSummary;
        # source_graph annotates Change from checker_types; checker_types annotates
        # model. Every edge in this loop is under `if TYPE_CHECKING`, so it never
        # executes — the single-artifact embed feature needs the snapshot to name
        # the pack type.
        frozenset(
            {"buildsource.pack", "buildsource.source_graph", "checker_types", "model"}
        ),
        # TYPE_CHECKING-only typing cycle (no runtime import): AbiSnapshot carries
        # an optional ``python_ext: PythonExtMetadata`` field (G14), while
        # ``python_ext`` annotates its functions with ``AbiSnapshot``. Both edges
        # are under ``if TYPE_CHECKING``, so neither runs at import time — the same
        # safe pattern as the sycl/buildsource metadata modules.
        frozenset({"model", "python_ext"}),
        # TYPE_CHECKING-only typing cycle (no runtime import): AbiSnapshot carries
        # an optional ``python_api: PythonApiSurface`` field (G23), while
        # ``python_api`` annotates ``detect_python_api`` with ``AbiSnapshot``.
        # Both edges are under ``if TYPE_CHECKING``, so neither runs at import
        # time — the same safe pattern as ``python_ext``.
        frozenset({"model", "python_api"}),
    }
)


def check_import_cycles(f: Findings) -> None:
    """ERROR on any strongly-connected component (SCC) not a subset of a
    baselined entry in IMPORT_CYCLE_ALLOWLIST (CLAUDE.md "M1-3").

    The honest name for what this enforces is "no *unapproved* SCC growth",
    not "no import cycles" — a large, deliberately-baselined CLI-registration
    SCC already exists and is allowed (see IMPORT_CYCLE_ALLOWLIST below). What
    this actually guards: no *new* module joins that baseline, and no *new*,
    separate SCC forms outside it. Extending IMPORT_CYCLE_ALLOWLIST to make a
    freshly-discovered cycle pass is an architectural decision needing an ADR
    or explicit review sign-off — it is not a routine unblock-CI step (see
    AGENTS.md "What NOT to do").
    """
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
        # Subset match: a detected cycle is allowed when its node-set is contained
        # in a declared by-design cluster. One SCC (the CLI registration / scan
        # engine cluster) yields many representative simple cycles whose exact
        # node-sets vary by traversal order, so matching a single cluster set is
        # robust — while a cycle that reaches any module *outside* the declared
        # clusters is not a subset of any and is still flagged.
        if any(short <= allowed for allowed in IMPORT_CYCLE_ALLOWLIST):
            continue
        f.err(
            "import-cycle-growth",
            " -> ".join(m.removeprefix("abicheck.") for m in cyc),
        )


# ---------------------------------------------------------------------------
# Check: mypy baseline drift
# ---------------------------------------------------------------------------


def check_mypy_baseline(f: Findings) -> None:
    """Run `mypy abicheck/` and ensure the error count hasn't drifted upward.

    Skipped (with a single info line) when mypy is unavailable. Invoked as
    ``sys.executable -m mypy`` rather than a bare ``mypy`` resolved via PATH
    (`shutil.which`) — a bare command name can resolve to a *different*
    install than the one pinned for this interpreter (`mypy==1.19.1` per
    pyproject.toml's `[dev]` extra), which silently ran the wrong mypy
    version here and reported a false baseline drift (CLAUDE.md "M0-3" —
    the same PATH-ambiguity class scripts/verify.py's `_py()` helper exists
    to close, just found again in this script's own bespoke invocation).
    """
    if importlib.util.find_spec("mypy") is None:
        print("mypy-baseline: mypy not installed, skipping")
        return
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", "abicheck"],
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
# Check: examples ground-truth integrity
# ---------------------------------------------------------------------------


def check_examples_ground_truth(f: Findings) -> None:
    """Each examples/case*/ must have a README.md AND an entry in
    examples/ground_truth.json["verdicts"]. Missing either side fails: the
    catalog is calibration data and the two sides have to stay in sync.
    """
    if not EXAMPLES.exists():
        return
    gt_path = EXAMPLES / "ground_truth.json"
    if not gt_path.is_file():
        f.err("examples-ground-truth", f"{_rel(gt_path)}: file not found")
        return
    try:
        gt = json.loads(_read(gt_path))
    except json.JSONDecodeError as e:
        f.err("examples-ground-truth", f"{_rel(gt_path)}: invalid JSON: {e}")
        return
    verdicts = gt.get("verdicts")
    if not isinstance(verdicts, dict):
        f.err("examples-ground-truth", f"{_rel(gt_path)}: missing 'verdicts' object")
        return
    case_dirs = {
        p.name for p in EXAMPLES.iterdir() if p.is_dir() and p.name.startswith("case")
    }

    for case_name in sorted(case_dirs):
        case_dir = EXAMPLES / case_name
        if not (case_dir / "README.md").is_file():
            f.err(
                "examples-ground-truth",
                f"examples/{case_name}/: missing README.md (per-case explainer required)",
            )
        if case_name not in verdicts:
            f.err(
                "examples-ground-truth",
                f"examples/{case_name}/: no entry in ground_truth.json['verdicts']",
            )

    for entry_name in sorted(verdicts):
        if entry_name not in case_dirs:
            f.warn(
                "examples-ground-truth",
                f"ground_truth.json references '{entry_name}' but no examples/{entry_name}/ directory",
            )


# ---------------------------------------------------------------------------
# Check: examples/README.md catalog stays in sync with ground_truth.json
# ---------------------------------------------------------------------------


def check_examples_readme_sync(f: Findings) -> None:
    """The hand-facing examples/README.md catalog must agree with ground_truth.

    Unlike the generated docs/examples/ tree (gated by gen_examples_docs.py
    --check), the top-level examples/README.md is GitHub-rendered and was
    historically hand-maintained, so its headline count, per-verdict
    distribution, and case-index rows drifted (missing newly-added cases and
    showing stale verdicts). This check pins all three to ground_truth.json so
    the drift can't recur silently.
    """
    gt_path = EXAMPLES / "ground_truth.json"
    readme = EXAMPLES / "README.md"
    if not gt_path.is_file() or not readme.is_file():
        return
    try:
        verdicts = json.loads(_read(gt_path))["verdicts"]
    except Exception:
        return
    text = _read(readme)

    single = {k: v for k, v in verdicts.items() if v.get("category") != "bundle"}
    n_bundle = len(verdicts) - len(single)
    n_total = len(verdicts)

    # Headline total.
    m = re.search(r"contains \*\*(\d+) cases\*\*", text)
    if m is None:
        f.warn(
            "examples-readme-sync",
            "examples/README.md: headline 'contains **N cases**' anchor not found; "
            "update the regex in check_examples_readme_sync if the wording changed.",
        )
    elif int(m.group(1)) != n_total:
        f.err(
            "examples-readme-sync",
            f"examples/README.md: headline says {int(m.group(1))} cases, "
            f"but ground_truth.json has {n_total}.",
        )

    # Per-verdict distribution rows (single-library cases only).
    expected_counts: dict[str, int] = {}
    for v in single.values():
        expected_counts[v["expected"]] = expected_counts.get(v["expected"], 0) + 1
    # Map the README's distribution rows to ground_truth expected verdicts.
    # COMPATIBLE is split into addition/quality rows in the README, so sum them.
    cat_counts: dict[str, int] = {}
    for v in single.values():
        cat_counts[v.get("category")] = cat_counts.get(v.get("category"), 0) + 1
    dist_anchors = [
        (r"\| BREAKING \| (\d+) \|", expected_counts.get("BREAKING", 0)),
        (r"\| API_BREAK \| (\d+) \|", expected_counts.get("API_BREAK", 0)),
        (
            r"\| COMPATIBLE_WITH_RISK \| (\d+) \|",
            expected_counts.get("COMPATIBLE_WITH_RISK", 0),
        ),
        (r"\| COMPATIBLE \(addition\) \| (\d+) \|", cat_counts.get("addition", 0)),
        (r"\| COMPATIBLE \(quality\) \| (\d+) \|", cat_counts.get("quality", 0)),
        (r"\| NO_CHANGE \| (\d+) \|", expected_counts.get("NO_CHANGE", 0)),
        (r"\| Bundle \(multi-binary\) \| (\d+) \|", n_bundle),
    ]
    for pattern, expected in dist_anchors:
        mm = re.search(pattern, text)
        if mm is None:
            f.warn(
                "examples-readme-sync",
                f"examples/README.md: distribution row {pattern!r} not found; "
                "update check_examples_readme_sync if the table changed.",
            )
        elif int(mm.group(1)) != expected:
            f.err(
                "examples-readme-sync",
                f"examples/README.md: distribution row {pattern!r} says "
                f"{int(mm.group(1))}, but ground_truth.json has {expected}.",
            )

    # Every case must appear as a case-index row, AND that row's category +
    # verdict must match ground_truth — not merely link to the README. Parsing
    # the row contents is what catches per-row drift the aggregate counts miss
    # (e.g. two cases swapping verdicts while the distribution totals stay put).
    category_label = {
        "breaking": "Breaking",
        "api_break": "API Break",
        "risk": "Risk",
        "addition": "Addition",
        "quality": "Quality",
        "no_change": "No Change",
        "bundle": "Bundle",
    }
    # | [NN](caseXXX/README.md) | Title | Category | <icon> VERDICT (notes) |
    row_re = re.compile(
        r"^\|\s*\[[^\]]*\]\((case[A-Za-z0-9_]+)/README\.md\)\s*"
        r"\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|\s*$",
        re.MULTILINE,
    )
    seen: set[str] = set()
    for match in row_re.finditer(text):
        name = match.group(1)
        cat_cell = match.group(3).strip()
        verdict_cell = match.group(4).strip()
        meta = verdicts.get(name)
        if meta is None:
            f.err(
                "examples-readme-sync",
                f"examples/README.md: index row for '{name}' has no "
                "ground_truth.json entry.",
            )
            continue
        seen.add(name)
        is_bundle = meta.get("category") == "bundle"
        want_verdict = "BUNDLE" if is_bundle else meta["expected"]
        want_cat = category_label.get(meta.get("category"), meta.get("category"))
        token = re.search(r"[A-Z_]{3,}", verdict_cell)
        got_verdict = token.group(0) if token else verdict_cell
        if got_verdict != want_verdict:
            f.err(
                "examples-readme-sync",
                f"examples/README.md: case '{name}' row shows verdict "
                f"{got_verdict!r}, but ground_truth.json says {want_verdict!r}.",
            )
        if cat_cell != want_cat:
            f.err(
                "examples-readme-sync",
                f"examples/README.md: case '{name}' row shows category "
                f"{cat_cell!r}, but ground_truth.json says {want_cat!r}.",
            )

    for name in sorted(set(verdicts) - seen):
        f.err(
            "examples-readme-sync",
            f"examples/README.md: case '{name}' has no parseable index row "
            f"(expected '| [..]({name}/README.md) | Title | Category | Verdict |').",
        )


# ---------------------------------------------------------------------------
# Check: mkdocs nav coverage
# ---------------------------------------------------------------------------


_MKDOCS_MD_REF_RE = re.compile(r"[:\s]\s*([A-Za-z0-9._/-]+\.md)\b")


def _collect_mkdocs_nav_refs() -> set[str]:
    """Extract every .md path referenced in mkdocs.yml.

    We deliberately don't depend on PyYAML — the script is stdlib-only and
    runs before pip install in CI. A regex over the nav block is good
    enough: mkdocs nav entries are always plain ``Title: path.md`` lines.
    """
    mkdocs = ROOT / "mkdocs.yml"
    if not mkdocs.is_file():
        return set()
    text = _read(mkdocs)
    return {m.group(1).strip() for m in _MKDOCS_MD_REF_RE.finditer(text)}


_MD_LINK_RE = re.compile(r"\]\(([^)#?]+\.md)(?:[#?][^)]*)?\)")


def _collect_doc_link_refs() -> set[str]:
    """Collect every relative .md link target inside docs/**/*.md.

    Pages reached transitively (e.g. examples/caseNN_*.md linked from a
    catalog page, ADRs linked from an index) shouldn't be flagged as
    orphans — they're reachable, just not enumerated in nav.
    """
    refs: set[str] = set()
    for md in DOCS.rglob("*.md"):
        try:
            base = md.parent.relative_to(DOCS).as_posix()
        except ValueError:
            base = ""
        for m in _MD_LINK_RE.finditer(_read(md)):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "/")):
                continue
            # Resolve relative to the containing doc.
            joined = (md.parent / target).resolve()
            try:
                rel = joined.relative_to(DOCS.resolve()).as_posix()
            except ValueError:
                continue
            refs.add(rel)
            if base:
                refs.add(f"{base}/{target}" if not target.startswith("../") else rel)
            else:
                refs.add(target)
    return refs


def check_mkdocs_nav_coverage(f: Findings) -> None:
    """Every docs/**/*.md file should be reachable from mkdocs.yml's nav
    OR from another doc page.

    Orphan docs make the site harder to navigate and often signal a
    stale page — `mkdocs build --strict` catches dangling refs but not
    orphans. WARN-only because some docs intentionally live outside nav
    (e.g. ADR archives reached via README links).
    """
    if not DOCS.exists():
        return
    nav_refs = _collect_mkdocs_nav_refs()
    if not nav_refs:
        return  # mkdocs.yml missing or unparseable — silent skip
    link_refs = _collect_doc_link_refs()
    reachable = nav_refs | link_refs
    for md in DOCS.rglob("*.md"):
        rel = md.relative_to(DOCS).as_posix()
        if rel in reachable:
            continue
        # CLAUDE.md/AGENTS.md are for AI agents, never published to the site
        # (both excluded via mkdocs.yml's exclude_docs).
        if md.name in ("CLAUDE.md", "AGENTS.md"):
            continue
        # index.md sits at a directory root and is implicitly served when
        # the parent section is opened, even if nothing links to it.
        if md.name == "index.md":
            continue
        f.warn(
            "mkdocs-nav-coverage",
            f"docs/{rel}: not referenced from mkdocs.yml nav or any other doc (orphan?)",
        )


# ---------------------------------------------------------------------------
# Check: every ADR is in both index.md and mkdocs.yml nav
# ---------------------------------------------------------------------------

#: Matches an ADR filename's leading number, e.g. "020" from
#: "020-build-context-capture.md". Two files may legitimately share a number
#: (020a/020b, 021a/021b are sibling sub-ADRs) — this check is per-file, not
#: per-number, so that's not flagged here.
_ADR_FILE_RE = re.compile(r"^\d{3}-.+\.md$")

#: An ADR's status, either as an inline bold label ("**Status:** Accepted —
#: ...") or as its own heading ("## Status\n\nAccepted — ..."). Both
#: conventions are in active use across the existing 51 ADRs.
_ADR_STATUS_INLINE_RE = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.MULTILINE)
_ADR_STATUS_HEADING_RE = re.compile(r"^## Status\s*\n+(.+)$", re.MULTILINE)

_ADR_REPLACEMENT_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _links_to_another_adr(status: str) -> bool:
    """True if `status` contains a Markdown link whose target looks like
    another ADR file (matches _ADR_FILE_RE), not just any link at all -- a
    "Superseded" status could otherwise link to unrelated context (e.g. a
    plan doc explaining why) and still satisfy a bare "has a link" check."""
    for href in _ADR_REPLACEMENT_LINK_RE.findall(status):
        basename = href.split("#", 1)[0].split("/")[-1]
        if _ADR_FILE_RE.match(basename):
            return True
    return False


def _adr_status_text(text: str) -> str | None:
    m = _ADR_STATUS_INLINE_RE.search(text)
    if m:
        return m.group(1)
    m = _ADR_STATUS_HEADING_RE.search(text)
    if m:
        return m.group(1)
    return None


def check_adr_index_and_nav_sync(f: Findings) -> None:
    """Every docs/development/adr/*.md file must be linked from index.md,
    and the ADR index itself must be listed in mkdocs.yml's nav.

    Individual ADRs are deliberately NOT required in nav (relaxed from the
    original rule): reachable via the index page is enough, and requiring
    51+ separate nav entries just for a flat historical-record tree was
    overloading top-level navigation for no reader benefit. The index page
    being in nav (checked below) is what makes every ADR actually reachable
    from published navigation, same as before — an ADR reachable only via
    index.md's link is fine now, whereas it wasn't under the old rule (ADR-041
    was accepted and linked from index.md but never added to mkdocs.yml
    individually, so under the *old* rule it was never published to nav
    despite being a real, current ADR; under this rule the index entry alone
    covers that).

    Two additional structural checks close a different gap: every ADR must
    carry a Status metadata line/heading (so a reader — or this check itself
    — can tell what state a given ADR is in without reading its full body),
    and an ADR whose status *leads with* "superseded" must link to its
    replacement (a bare "superseded" with no pointer to what replaced it
    leaves a reader stuck).
    """
    adr_dir = DOCS / "development" / "adr"
    if not adr_dir.is_dir():
        return
    index_text = _read(adr_dir / "index.md")
    nav_refs = _collect_mkdocs_nav_refs()
    index_nav_target = "development/adr/index.md"
    if nav_refs and index_nav_target not in nav_refs:
        f.err(
            "adr-index-nav-sync",
            "docs/development/adr/index.md: the ADR index itself is not "
            "listed in mkdocs.yml nav (every ADR is reachable only through "
            "this page, so it must be a real nav entry)",
        )
    for md in sorted(adr_dir.glob("*.md")):
        if md.name == "index.md" or not _ADR_FILE_RE.match(md.name):
            continue
        if md.name not in index_text:
            f.err(
                "adr-index-nav-sync",
                f"docs/development/adr/{md.name}: not linked from "
                f"docs/development/adr/index.md",
            )
        text = _read(md)
        status = _adr_status_text(text)
        if status is None:
            f.err(
                "adr-index-nav-sync",
                f"docs/development/adr/{md.name}: missing a Status "
                "metadata line ('**Status:** ...') or heading ('## Status')",
            )
            continue
        leading_word = re.split(r"[\s—.,;-]", status.strip(), maxsplit=1)[0]
        if leading_word.lower() == "superseded" and not _links_to_another_adr(status):
            f.err(
                "adr-index-nav-sync",
                f"docs/development/adr/{md.name}: status is 'Superseded' "
                "but doesn't link to its replacement ADR",
            )


# ---------------------------------------------------------------------------
# Check: banned imports / API misuse
# ---------------------------------------------------------------------------


# Files allowed to call ``print()`` (structured CLI output). Everything else
# should use the ``click.echo`` / ``_logger`` / ``reporter`` machinery so output
# can be redirected, suppressed, or annotated by callers.
_PRINT_ALLOWED: frozenset[str] = frozenset(
    {
        "abicheck/cli.py",
        "abicheck/cli_baseline.py",
        "abicheck/cli_compare_release.py",
        "abicheck/cli_debian_symbols.py",
        "abicheck/compat/cli.py",
        "abicheck/reporter.py",
    }
)


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        # subprocess.run(...), subprocess.Popen(...), etc.
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return func.attr in {"run", "Popen", "call", "check_call", "check_output"}
    return False


def check_banned_imports(f: Findings) -> None:
    """Catch a small set of real foot-guns:

    - ``print(...)`` outside the CLI / reporter layer — every other module
      should use structured output (click.echo, logger) so callers can
      capture or silence it.
    - ``subprocess.<call>(..., shell=True)`` — shell injection vector;
      callers can always pass a list of args instead.
    """
    for path in PKG.rglob("*.py"):
        rel = _rel(path)
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # print() outside the allowlist
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "print"
                and rel not in _PRINT_ALLOWED
            ):
                f.err(
                    "banned-imports",
                    f"{rel}:{node.lineno}: `print(...)` not allowed outside CLI/reporter modules; use click.echo or _logger",
                )
            # subprocess.<x>(..., shell=True)
            if _is_subprocess_call(node):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        f.err(
                            "banned-imports",
                            f"{rel}:{node.lineno}: `subprocess` with `shell=True` is a shell-injection vector; pass an args list instead",
                        )


# ---------------------------------------------------------------------------
# Check: CLI interface contract (ADR-037 D10.1)
# ---------------------------------------------------------------------------

# Tier-1 core entry points a front-end must never call directly — it must route
# through the Tier-2 service layer (``service.run_compare`` /
# ``service.compare_snapshots``). ADR-037 D1/D10.1.
_TIER1_CORE_FUNCS: frozenset[str] = frozenset({"compare"})

# ``"<rel-path>:<lineno>"`` call sites deliberately exempted, each needing a
# reason in review. Empty by design — a new exemption is a reviewed decision,
# not an accident (mirrors the INTENTIONAL_SUBSET philosophy of D10.2).
CLI_CONTRACT_ALLOWLIST: frozenset[str] = frozenset()


def _checker_compare_bindings(tree: ast.Module) -> set[str]:
    """Return the local names bound to ``checker.compare`` via import in *tree*.

    Handles ``from .checker import compare`` and ``... import compare as X`` at
    module or function scope, so a lazily-imported alias is caught too.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[-1] == "checker"
        ):
            for alias in node.names:
                if alias.name in _TIER1_CORE_FUNCS:
                    names.add(alias.asname or alias.name)
    return names


def _checker_module_bindings(tree: ast.Module) -> set[str]:
    """Return local names bound to the ``checker`` *module* itself.

    Catches ``from . import checker [as X]`` / ``from abicheck import checker
    [as X]`` and ``import abicheck.checker as X``, so an aliased
    ``core.compare(...)`` call is recognised, not just the literal
    ``checker.compare(...)``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # ``from . import checker`` (module is None) or ``from abicheck import checker``.
            if node.module is None or node.module.split(".")[-1] == "abicheck":
                for alias in node.names:
                    if alias.name == "checker":
                        names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "checker":
                    # ``import abicheck.checker`` binds ``abicheck``; only an
                    # explicit ``as X`` gives a usable ``X.compare`` call name.
                    if alias.asname:
                        names.add(alias.asname)
    return names


def _iter_cli_contract_sources() -> Iterable[Path]:
    """The front-end modules the contract covers: every ``cli*.py``, the
    consumer-side ``appcompat.py`` (a verdict-emitting front-end too), and the
    MCP server ``mcp_server.py`` — ADR-037 D1 names MCP a Tier-3 front-end, so it
    must route through the Tier-2 service just like the CLI commands."""
    yield from PKG.glob("cli*.py")
    for extra in ("appcompat.py", "mcp_server.py"):
        path = PKG / extra
        if path.is_file():
            yield path


# ── ADR-037 D10.2 / D10.4: shared-decorator coverage + one-default-per-flag ───
#
# These mirror the contract tables in ``abicheck/cli_options.py``. The gate is
# the first CI step and must stay pure-stdlib (no ``import abicheck``), so the
# small mapping is duplicated here and ``tests/test_cli_contract.py`` asserts it
# stays in lock-step with ``cli_options`` (the source of truth).

#: verdict-emitting command module basename → the command's registered name.
#: `appcompat` folded into `compare --used-by` (ADR-043) and no longer has its
#: own registered command.
_VERDICT_CMD_MODULES: dict[str, str] = {
    "cli.py": "compare",
}

#: decorator callables every verdict-emitting command must compose (ADR-037 D3).
_REQUIRED_FAMILY_DECORATORS: frozenset[str] = frozenset(
    {
        "two_sided_input_options",
        "policy_options",
        "severity_options",
        "scope_options",
        "output_options",
    }
)

#: (command, decorator) pairs allowed to be absent — a deliberate, reviewed
#: subset (mirrors ``cli_options.INTENTIONAL_SUBSET``).
_INTENTIONAL_SUBSET_DECORATORS: frozenset[tuple[str, str]] = frozenset()


def _decorator_callable_name(node: ast.expr) -> str | None:
    """The bare callable name of a decorator (``@foo`` or ``@foo(...)``).

    Returns ``None`` for attribute-style decorators (``@click.option(...)`` /
    ``@main.command(...)``) which are not shared-family decorators.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    return None


def _command_name_of(fn: ast.FunctionDef) -> str | None:
    """If *fn* is a ``@main.command("name")`` handler, return that name."""
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "command"
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
    return None


def _check_decorator_coverage(f: Findings) -> None:
    """ADR-037 D10.2: every verdict-emitting command composes the required shared
    option-family decorators (or is on the intentional-subset allowlist)."""
    for module, cmd_name in _VERDICT_CMD_MODULES.items():
        path = PKG / module
        if not path.is_file():
            continue
        rel = _rel(path)
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        found = False
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if _command_name_of(fn) != cmd_name:
                continue
            found = True
            applied = {
                name
                for dec in fn.decorator_list
                if (name := _decorator_callable_name(dec)) is not None
            }
            for required in sorted(_REQUIRED_FAMILY_DECORATORS):
                if required in applied:
                    continue
                if (cmd_name, required) in _INTENTIONAL_SUBSET_DECORATORS:
                    continue
                f.err(
                    "cli-contract",
                    f"{rel}: command `{cmd_name}` is missing shared option family "
                    f"`@{required}` (ADR-037 D3/D10.2). Compose it from "
                    "`cli_options.py` or add an `INTENTIONAL_SUBSET` entry with a reason.",
                )
        # A mapped command whose module exists but no longer declares it is a
        # D10.2 false-negative (coverage silently un-verifiable) — flag it.
        if not found:
            f.err(
                "cli-contract",
                f"{rel}: expected verdict-emitting command `{cmd_name}` was not "
                "found; its shared-decorator coverage (ADR-037 D10.2) could not be "
                "verified. Update `VERDICT_EMITTING_COMMANDS` if it moved or was renamed.",
            )


def _option_flag_and_default(call: ast.Call) -> tuple[str | None, str | None]:
    """For a ``click.option(...)`` call, return its canonical ``--flag`` name and
    the source text of its ``default=`` (or ``None`` if absent)."""
    flag: str | None = None
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            token = arg.value.split("/")[0]  # `--x/--no-x` → `--x`
            if token.startswith("--"):
                flag = token
                break
            if flag is None and token.startswith("-"):
                flag = token  # short-only fallback; a long form usually follows
    default_src: str | None = None
    for kw in call.keywords:
        if kw.arg == "default":
            default_src = ast.unparse(kw.value)
    return flag, default_src


def _check_one_default_per_flag(f: Findings) -> None:
    """ADR-037 D10.4: a flag declared in more than one shared decorator must not
    carry two different defaults (the historical ``--collect-mode`` trap)."""
    path = PKG / "cli_options.py"
    if not path.is_file():
        return
    rel = _rel(path)
    try:
        tree = ast.parse(_read(path), filename=rel)
    except SyntaxError:
        return
    defaults: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "option"
        ):
            flag, default_src = _option_flag_and_default(node)
            if flag is not None and default_src is not None:
                defaults[flag].add(default_src)
    for flag, seen in sorted(defaults.items()):
        if len(seen) > 1:
            f.err(
                "cli-contract",
                f"{rel}: flag `{flag}` is declared with conflicting defaults "
                f"{sorted(seen)} across shared decorators (ADR-037 D10.4). "
                "Give it one default.",
            )


# ── ADR-037 D10.3: MCP ⇄ CLI name-map completeness ───────────────────────────
#
# The single ``MCP_CLI_NAME_MAP`` table (``cli_options.py``) reconciles the MCP
# tool's JSON parameter names with the ``compare`` CLI flags so the two
# front-ends can't silently diverge. This gate keys on the *MCP* side: every
# ``abi_compare`` parameter must appear in the map (a CLI-flag-side completeness
# check that needs the live Click command runs in ``tests/test_cli_contract.py``).

#: ``abi_compare`` params that are framework plumbing, not part of the shared
#: request surface, so they need no CLI-flag row.
_MCP_NAME_MAP_EXEMPT_PARAMS: frozenset[str] = frozenset()


def _dict_literal_keys(tree: ast.Module, name: str) -> set[str] | None:
    """Return the string keys of a module-level ``name = {...}`` dict literal.

    ``None`` when no such assignment (or annotated assignment) with a dict
    literal value is found, so the caller can flag a missing table rather than
    silently passing. Iterates ``tree.body`` (not ``ast.walk``) so a same-named
    symbol nested in a function/class can never shadow the module-level table.
    """
    for node in tree.body:
        target_names: list[str] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = [node.target.id]
            value = node.value
        if name in target_names and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return None


def _function_param_names(tree: ast.Module, func_name: str) -> list[str] | None:
    """Return the positional/keyword parameter names of a module-level
    ``def func_name(...)``.

    Iterates ``tree.body`` (not ``ast.walk``) so the lookup is scoped to
    module-level definitions and a nested function of the same name cannot match.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            a = node.args
            return [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return None


def _check_mcp_cli_name_map(f: Findings) -> None:
    """ADR-037 D10.3: every ``abi_compare`` MCP param is in ``MCP_CLI_NAME_MAP``."""
    co_path = PKG / "cli_options.py"
    mcp_path = PKG / "mcp_server.py"
    if not co_path.is_file() or not mcp_path.is_file():
        return
    try:
        co_tree = ast.parse(_read(co_path), filename=_rel(co_path))
        mcp_tree = ast.parse(_read(mcp_path), filename=_rel(mcp_path))
    except SyntaxError:
        return
    keys = _dict_literal_keys(co_tree, "MCP_CLI_NAME_MAP")
    if keys is None:
        f.err(
            "cli-contract",
            f"{_rel(co_path)}: MCP_CLI_NAME_MAP table not found (ADR-037 D10.3); "
            "it is the single source of truth reconciling MCP params ⇄ CLI flags.",
        )
        return
    params = _function_param_names(mcp_tree, "abi_compare")
    if params is None:
        f.err(
            "cli-contract",
            f"{_rel(mcp_path)}: MCP tool `abi_compare` not found; its param ⇄ flag "
            "name-map coverage (ADR-037 D10.3) could not be verified.",
        )
        return
    for p in params:
        if p in _MCP_NAME_MAP_EXEMPT_PARAMS or p in keys:
            continue
        f.err(
            "cli-contract",
            f"{_rel(mcp_path)}: MCP param `abi_compare.{p}` is absent from "
            "`MCP_CLI_NAME_MAP` (ADR-037 D10.3) — add a row mapping it to its "
            "`compare` flag (or `None` if it has no CLI equivalent).",
        )


def check_cli_contract(f: Findings) -> None:
    """ERROR if a front-end module calls a Tier-1 core entry point
    (``checker.compare``) directly instead of routing through the Tier-2 service.

    Covers every ``abicheck/cli*.py``, ``abicheck/appcompat.py``, and
    ``abicheck/mcp_server.py``. ADR-037
    D1/D10.1: front-ends are thin adapters; one classification path is what keeps
    ``compare`` / ``compare-release`` / ``appcompat`` / MCP from drifting apart
    (the ``scope_public`` default divergence the ADR documents). Importing a
    ``checker`` *type* for annotations or result-rendering stays legal — the gate
    keys on the *call expression*, not the import statement. Both a direct
    ``compare`` import and an aliased ``checker``-module call are detected.
    """
    for path in sorted(_iter_cli_contract_sources()):
        rel = _rel(path)
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        bound = _checker_compare_bindings(tree)
        checker_modules = _checker_module_bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_tier1 = (isinstance(func, ast.Name) and func.id in bound) or (
                isinstance(func, ast.Attribute)
                and func.attr in _TIER1_CORE_FUNCS
                and isinstance(func.value, ast.Name)
                and func.value.id in checker_modules
            )
            if is_tier1 and f"{rel}:{node.lineno}" not in CLI_CONTRACT_ALLOWLIST:
                f.err(
                    "cli-contract",
                    f"{rel}:{node.lineno}: front-end calls Tier-1 `checker.compare` "
                    "directly; route through `service.run_compare` / "
                    "`service.compare_snapshots` (ADR-037 D1/D10.1)",
                )
    # D10.2 shared-decorator coverage + D10.4 one-default-per-flag (ADR-037 D3).
    _check_decorator_coverage(f)
    _check_one_default_per_flag(f)
    # D10.3 MCP ⇄ CLI name-map completeness (ADR-037 D8/D9 cross-front-end parity).
    _check_mcp_cli_name_map(f)


# ---------------------------------------------------------------------------
# Check: test assertion density (coverage-honesty guard)
# ---------------------------------------------------------------------------


# Substrings that mark a call as assertion-bearing: explicit asserts, the
# unittest-style ``self.assert*`` family, ``pytest.raises``/``warns``/``fail``,
# and common project helper-naming (``_check_*``, ``verify_*``, ``*_roundtrip``).
_ASSERTION_CALL_HINTS: tuple[str, ...] = (
    "assert",
    "check",
    "verify",
    "expect",
    "validate",
    "ensure",
    "roundtrip",
    "raises",
    "warns",
    "fail",
)


def _call_attr_or_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_direct_assertion(fn: ast.AST) -> bool:
    """True if *fn*'s body itself asserts (assert stmt, with-block, or a call
    whose name hints at an assertion)."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        # ``with pytest.raises(...)`` / ``with caplog ...`` express expectations.
        if isinstance(node, ast.With | ast.AsyncWith):
            return True
        if isinstance(node, ast.Call):
            name = _call_attr_or_name(node).lower()
            if any(h in name for h in _ASSERTION_CALL_HINTS):
                return True
    return False


def _called_function_names(fn: ast.AST) -> set[str]:
    return {_call_attr_or_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}


def check_test_assertion_density(f: Findings) -> None:
    """WARN on ``test_*`` functions that make no assertion, directly or via a
    same-file helper.

    This is the coverage-honesty guard the testing review asked for: a test
    that executes code without asserting anything still lifts line coverage but
    verifies nothing. The check resolves same-file helper calls to a fixed
    point, so tests that delegate their checks to a helper (e.g. golden-file
    comparisons) are not flagged. Remaining hits are genuine smoke tests —
    legitimate, but worth a deliberate confirmation rather than an accident.
    """
    if not TESTS.exists():
        return
    for path in sorted(TESTS.glob("test_*.py")):
        rel = _rel(path)
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue

        funcs: dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                funcs.setdefault(node.name, node)  # first definition wins

        asserting = {name for name, fn in funcs.items() if _has_direct_assertion(fn)}
        # Propagate: a function asserts if it calls a function that asserts.
        changed = True
        while changed:
            changed = False
            for name, fn in funcs.items():
                if name in asserting:
                    continue
                if _called_function_names(fn) & asserting:
                    asserting.add(name)
                    changed = True

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name.startswith("test")
                and node.name not in asserting
            ):
                f.warn(
                    "test-assertion-density",
                    f"{rel}:{node.lineno}: {node.name}() makes no assertion "
                    "(directly or via a helper) — confirm it's an intentional smoke test",
                )


# ---------------------------------------------------------------------------
# Check: Apache-2.0 license header
# ---------------------------------------------------------------------------


# Match either the SPDX identifier or the Apache-2.0 NOTICE prose used in
# the existing files. We don't care about exact format, just presence.
_LICENSE_RE = re.compile(
    r"(SPDX-License-Identifier:\s*Apache-2\.0|Apache License,\s*Version\s*2\.0)",
    re.IGNORECASE,
)


def check_license_header(f: Findings) -> None:
    """Every abicheck/**/*.py should carry the Apache-2.0 header.

    We look at the first 25 lines so the check tolerates an optional
    shebang or encoding cookie on top.
    """
    for path in PKG.rglob("*.py"):
        rel = _rel(path)
        # Empty files and package markers (__init__.py / __main__.py without
        # real code) are skipped — the project ships some intentionally
        # trivial files that don't need their own header.
        src = _read(path)
        if not src.strip():
            continue
        head = "\n".join(src.splitlines()[:25])
        if _LICENSE_RE.search(head):
            continue
        f.warn(
            "license-header",
            f"{rel}: missing Apache-2.0 license header (add `# SPDX-License-Identifier: Apache-2.0` or full notice)",
        )


# ---------------------------------------------------------------------------
# Registry & CLI
# ---------------------------------------------------------------------------


CHECKS: dict[str, Callable[[Findings], None]] = {
    "file-size": check_file_sizes,
    "claude-md-coverage": check_claude_md_coverage,
    "agent-instructions-coverage": check_agent_instructions_coverage,
    "script-inventory": check_script_inventory_completeness,
    "generated-file-ownership": check_generated_file_ownership,
    "test-ratio": check_test_ratio,
    "future-annotations": check_future_annotations,
    "changekind-partition": check_changekind_partition,
    "changekind-detector": check_changekind_detector_crossref,
    "changekind-docs": check_changekind_docs,
    "doc-count-sync": check_doc_count_sync,
    "action-version-freshness": check_action_version_freshness,
    "import-cycle-growth": check_import_cycles,
    "mypy-baseline": check_mypy_baseline,
    "examples-ground-truth": check_examples_ground_truth,
    "examples-readme-sync": check_examples_readme_sync,
    "mkdocs-nav-coverage": check_mkdocs_nav_coverage,
    "adr-index-nav-sync": check_adr_index_and_nav_sync,
    "banned-imports": check_banned_imports,
    "cli-contract": check_cli_contract,
    "license-header": check_license_header,
    "test-assertion-density": check_test_assertion_density,
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
