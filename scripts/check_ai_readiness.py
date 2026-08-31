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
_MODULE_RUNNER = Path(__file__).resolve().with_name("run_isolated_module.py")


def _isolated_module_command(*mod_args: str) -> tuple[str, ...]:
    return (sys.executable, "-I", str(_MODULE_RUNNER), *mod_args)


# Make `abicheck` importable when the package is not pip-installed (e.g. when
# the script runs as the first CI step before `pip install -e .`).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ...and this script's own directory, so the sibling gate modules below import
# whether this file is run directly (Python adds it automatically) or loaded
# from its path by `tests/test_ai_readiness.py` (Python doesn't).
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
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
SKILLS_SRC = ROOT / "skills-src"

# ADR-058's generated agent-skill publication trees. Every Markdown file under
# each of these is `scripts/gen_agent_skills.py` output, at arbitrary depth and
# growing count (per skill: its SKILL.md, its own references/*.md, and every
# copied shared/*.md fragment) -- which is why the ownership check below walks
# them instead of enumerating individual paths in GENERATED_FILE_MARKERS.
GENERATED_SKILL_ROOTS: tuple[Path, ...] = (
    ROOT / ".agents" / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".gemini" / "skills",
)

# Sibling gate module (imported after the sys.path setup above). It owns the
# ADR Status *parsing* helpers as well as its own check, so `adr-index-nav-sync`
# below reads them from there rather than keeping a second copy.
from adr_status_sync import (  # noqa: E402
    _ADR_FILE_RE,
    _adr_status_text,
    check_adr_status_sync,
)
from engine_cli_boundary import check_engine_cli_boundary  # noqa: E402
from fact_detector_misuse import check_fact_detector_misuse  # noqa: E402
from fact_field_readers import check_fact_field_readers  # noqa: E402
from findings_report import Findings as _SharedFindings  # noqa: E402

# The generated-skill publication trees' own generator (ADR-058 / G36 P0.3).
# Imported for `discover_skills` alone, so the ownership check below asks the
# generator which directories it owns rather than keeping a second, drifting
# copy of that rule -- an output root may also hold hand-authored skills this
# generator never produced. Pure-stdlib at import time, same constraint as
# this script.
from gen_agent_skills import (  # noqa: E402
    GENERATED_MARKER_SUBSTRING as _SKILL_MARKER,
    discover_skills as _discover_generated_skills,
)

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
# debt this change merely discovered — the checks added in the commit that
# introduced `engine-cli-boundary` pushed it over 2000 lines, and PR #813's
# `cli-contract` extension (`dumper.dump`/`service.resolve_input`, plus the
# `_dotted_path`/`_importfrom_names_module`/`_relative_import_level_for_source`
# helpers their false-positive/false-negative fixes needed) grew it further
# still. It stays here rather than being split because its largest
# self-contained block (`check_cli_contract` and its now ~20 private
# helpers, ADR-037 D10) is exactly what `tests/test_cli_contract.py`
# monkeypatches by module-level name (e.g. `gate._VERDICT_CMD_MODULES`)
# before calling `gate.check_cli_contract(...)` — moving that block to a
# sibling module would make `check_cli_contract` read a *different* module's
# globals than the ones the monkeypatch rebinds, silently breaking those
# tests' ability to patch the very state they're asserting against. That
# split needs its own pass that also updates the test file, not one folded
# reactively into an unrelated correctness fix under continued review.
LARGE_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "scripts/benchmark_comparison.py",
        "scripts/check_ai_readiness.py",
        "tests/test_type_graph.py",
        "tests/test_l3l4l5_new_kinds.py",
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
    SKILLS_SRC,
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


class Findings(_SharedFindings):
    """This gate's error/warning collector — the shared one, labelled for it.

    The collection/grouping/printing itself lives in ``findings_report.py`` so
    ``check_docs_contract.py`` reports identically without a second copy.
    """

    SUMMARY_LABEL = "AI-readiness"


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
        PKG / "model" / "change_catalog" / "kinds.pyi",
        "this file is generated",
        "gen_changekind_stub.py",
    ),
    (
        DOCS / "reference" / "github-action-inputs.md",
        "generated by scripts/gen_action_reference.py",
        "gen_action_reference.py",
    ),
    (
        DOCS / "reference" / "cli-reference.md",
        "generated by scripts/gen_cli_reference.py",
        "gen_cli_reference.py",
    ),
    (
        DOCS / "reference" / "python-api-reference.md",
        "generated by scripts/gen_python_api_reference.py",
        "gen_python_api_reference.py",
    ),
    (
        DOCS / "reference" / "config-keys-reference.md",
        "generated by scripts/gen_config_reference.py",
        "gen_config_reference.py",
    ),
    (
        DOCS / "reference" / "platforms.md",
        "generated by scripts/gen_platform_matrix.py",
        "gen_platform_matrix.py",
    ),
    (
        DOCS / "reference" / "header-backend-capabilities.md",
        "generated by scripts/gen_backend_capability_matrix.py",
        "gen_backend_capability_matrix.py",
    ),
    (
        # Carries its marker as a JSON *field*, not a comment banner: JSON has
        # no comments, and `agent-benchmark` consumes this file, so a pack a
        # reader cannot json.load() would defeat the point of publishing one.
        AGENT_EVALS / "skills" / "skill-eval-pack.json",
        "generated by scripts/gen_skill_eval_pack.py",
        "gen_skill_eval_pack.py",
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
    for path in sorted((DOCS / "reference" / "examples").glob("case*.md")):
        if "generated by scripts/gen_examples_docs.py" not in _read(path).lower():
            f.err(
                "generated-file-ownership",
                f"{_rel(path)}: missing its generated-file marker — regenerate "
                "with `python scripts/gen_examples_docs.py` rather than hand-editing.",
            )
    # ADR-058's agent-skill trees: an arbitrary-depth, growing set of generated
    # Markdown across three roots, so this walks them (like the case*.md loop
    # above) rather than registering one GENERATED_FILE_MARKERS entry per file.
    # Scoped to the skill directories `gen_agent_skills.py` actually owns --
    # `.claude/skills/` legitimately also holds hand-authored skills, which are
    # not generator output and must not be flagged.
    owned = [d.name for d in _discover_generated_skills(SKILLS_SRC)]
    for root in GENERATED_SKILL_ROOTS:
        for name in owned:
            for path in sorted((root / name).rglob("*.md")):
                if _SKILL_MARKER not in _read(path).lower():
                    f.err(
                        "generated-file-ownership",
                        f"{_rel(path)}: missing its generated-file marker — "
                        "regenerate with `python scripts/gen_agent_skills.py` "
                        "rather than hand-editing (edit skills-src/ instead).",
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
    for i, (n1, s1) in enumerate(pairs):
        for n2, s2 in pairs[i + 1 :]:
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

    Four values historically drifted across the docs: the number of `ChangeKind`
    values ("N change types"), the size of the example catalog
    (`examples/ground_truth.json`), the snapshot `schema_version`, and the
    compare report's `report_schema_version` (each a doc page hand-copying a
    number that already has a fact owner, per AGENTS.md's "don't hand-copy a
    count/version that has a fact owner elsewhere" rule, with nothing catching
    the next bump forgetting it) — plus the CastXML policy's supported-version
    range. Each anchor below pins a specific sentence to a computed value:

    Pinning is the *second* line of defence, not the first: where a version is
    incidental to what a sentence is saying, the page should link to the fact
    owner and hold no copy at all (ADR-055 D3). An anchor here is for a value
    that must appear literally — a JSON output sample, or the owner page's own
    statement of the current value. It only guards the sites someone thought
    to anchor, which is exactly how the fourth copy on `snapshot-format.md`
    (its snapshot-vs-report comparison table) sat unchecked until review
    caught it.

    - ERROR if the anchor sentence is present but the number is wrong (the real
      drift bug — forces docs to be updated when a ChangeKind or case is added).
    - WARN if the anchor sentence can no longer be found (wording changed, so the
      guard silently stopped covering that spot — update the regex here).
    """
    try:
        from abicheck import schemas
        from abicheck.castxml_policy import (
            MAX_CASTXML,
            MIN_CASTXML,
            MIN_CASTXML_CLANG_MAJOR,
        )
        from abicheck.checker_policy import ChangeKind
    except ModuleNotFoundError:
        # Package not importable (e.g. pre-install lane) — skip silently, like
        # the other ChangeKind checks. Deliberately *only* this: a broken
        # `abicheck.schemas` (or any other import-time failure in an installed
        # package) is a real defect, and swallowing it here would silently stop
        # checking every version number below while still reporting success
        # (CodeRabbit review).
        return

    # ADR-055 D3: read every persisted-artifact version through the registry
    # rather than importing each artifact's own constant here. That is what
    # gives `schemas.current()` a consumer instead of leaving it a lookup
    # nothing calls — the registry exists precisely because a version number
    # with no single queryable owner is how `docs/use/python-api.md` came to
    # claim schema_version 8 against a real 17.
    SCHEMA_VERSION = schemas.current("snapshot")
    REPORT_SCHEMA_VERSION = schemas.current("compare")

    n_kinds = len(list(ChangeKind))

    gt_path = EXAMPLES / "ground_truth.json"
    try:
        verdicts = json.loads(_read(gt_path))["verdicts"]
    except Exception:
        return
    n_catalog = len(verdicts)

    # (file, human label, expected value, regex capturing the documented number)
    #
    # Deliberately NOT tracked here: `docs/reference/tool-comparison.md`'s
    # "## Full-catalog benchmark (<date>, all N cases)" heading. Unlike every
    # anchor below (a live "how big is the catalog right now" claim), that
    # heading pins the denominator of one specific, dated, already-measured
    # benchmark run (its own "Reproducibility envelope" note records the exact
    # commit; its results table reads `Correct / <that same N>`) — mechanically
    # bumping N there whenever the catalog grows would silently overstate what
    # was actually measured, without a real rerun to back the new number
    # (caught by review on the PR that added case196: this file's own N was
    # briefly bumped 195->196 despite the run underneath it staying pinned at
    # 193, which the results table and reproducibility envelope still said).
    # Bump that heading's own number only alongside an actual rerun that
    # produces new results to match.
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
            DOCS / "start" / "first-check.md",
            "catalog size",
            n_catalog,
            r"repo includes (\d+) ABI scenario examples",
        ),
        (
            DOCS / "contribute/abicc-parity-status.md",
            "ChangeKind count (current total)",
            n_kinds,
            r"current ChangeKind total is \*\*(\d+)\*\*",
        ),
        (
            DOCS / "contribute/abicc-test-coverage-comparison.md",
            "ChangeKind count (current total)",
            n_kinds,
            r"current ChangeKind total is \*\*(\d+)\*\*",
        ),
        (
            DOCS / "reference/snapshot-format.md",
            "snapshot schema_version (headline sentence)",
            SCHEMA_VERSION,
            r"The current value is \*\*`(\d+)`\*\*",
        ),
        (
            DOCS / "reference/snapshot-format.md",
            "snapshot schema_version (JSON example)",
            SCHEMA_VERSION,
            r'"schema_version":\s*(\d+),',
        ),
        (
            DOCS / "reference/snapshot-format.md",
            "snapshot schema_version (field table)",
            SCHEMA_VERSION,
            r"Snapshot format version \(currently `(\d+)`\)",
        ),
        (
            DOCS / "reference/snapshot-format.md",
            "snapshot schema_version (snapshot-vs-report comparison table)",
            SCHEMA_VERSION,
            r"\*\*Type\*\* \| integer \(currently `(\d+)`\)",
        ),
        (
            DOCS / "use/output-formats.md",
            "compare report_schema_version (compare report JSON example)",
            REPORT_SCHEMA_VERSION,
            r'"report_schema_version":\s*"([0-9]+\.[0-9]+)"',
        ),
        (
            DOCS / "reference/environment.md",
            "CastXML policy minimum version (ABICHECK_ALLOW_UNSUPPORTED_CASTXML row)",
            MIN_CASTXML,
            r">=(\d+\.\d+\.\d+),<\d+\.\d+\.\d+",
        ),
        (
            DOCS / "reference/environment.md",
            "CastXML policy exclusive-upper-bound version"
            " (ABICHECK_ALLOW_UNSUPPORTED_CASTXML row)",
            MAX_CASTXML,
            r">=\d+\.\d+\.\d+,<(\d+\.\d+\.\d+)",
        ),
        (
            DOCS / "reference/environment.md",
            "CastXML policy minimum bundled Clang major version"
            " (ABICHECK_ALLOW_UNSUPPORTED_CASTXML row)",
            MIN_CASTXML_CLANG_MAJOR,
            r"bundled/linked Clang `>=(\d+)`",
        ),
        (
            DOCS / "use/troubleshooting.md",
            "CastXML policy minimum version (version-gate section)",
            MIN_CASTXML,
            r"supported range \(currently `>=(\d+\.\d+\.\d+),<\d+\.\d+\.\d+`",
        ),
        (
            DOCS / "use/troubleshooting.md",
            "CastXML policy exclusive-upper-bound version (version-gate section)",
            MAX_CASTXML,
            r"supported range \(currently `>=\d+\.\d+\.\d+,<(\d+\.\d+\.\d+)`",
        ),
        (
            DOCS / "use/troubleshooting.md",
            "CastXML policy minimum bundled Clang major version (version-gate section)",
            MIN_CASTXML_CLANG_MAJOR,
            r"bundled/linked Clang `>=(\d+)`",
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
        # Most anchors pin an integer count; the CastXML version anchors pin a
        # dotted version string instead (MIN_CASTXML/MAX_CASTXML aren't plain
        # ints) — compare in whichever type the source of truth actually is.
        found: int | str = int(m.group(1)) if isinstance(expected, int) else m.group(1)
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
    adr_dir = DOCS / "contribute" / "adr"
    archive_dir = DOCS / "contribute" / "archive"
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
    DOCS / "contribute" / "adr",
    DOCS / "contribute" / "archive",
    VALIDATION,
)
_ACTION_VERSION_EXEMPT_FILES: frozenset[Path] = frozenset(
    {
        DOCS / "contribute" / "goals.md",  # retrospective "Done:" bullets
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
        # cli_appcompat / cli_plugin / cli_pr_comment / cli_probe / cli_stack /
        # cli_suggest / cli_surface / cli_scan / cli_buildsource at module-load
        # tail to register their @main.command(...) decorators; those
        # sub-modules import `main` and shared helpers back from cli. Each of
        # these once had its own standalone `{"cli", "cli_X"}` (or, for
        # cli_scan/cli_buildsource, three-item) entry here; all twelve were
        # removed (2026-08-31 IMPORT_CYCLE_ALLOWLIST audit) once confirmed
        # redundant — every module they name is already a member of the one
        # big cluster below, so `short <= allowed` already matches any
        # detected cycle naming a subset of them via that cluster entry alone,
        # exactly the reasoning the cluster's own `cli_config`/`cli_doctor`/
        # `cli_graph` comment already relied on for never having standalone
        # entries of their own. Confirmed empirically: removing all twelve
        # produces zero new `check_import_cycles` findings.
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
        # `l0_export_delta` joins the same SCC (ADR-049 Phase 5 §6.3): the one
        # L0 hard-removal extraction shared by direct `compare`
        # (`cli_helpers_compare.fold_l0_hard_removals`) and `scan --against`
        # (`cli_scan_baseline._run_baseline_compare`) was split out of both
        # call sites into this leaf module so neither hand-copies the same
        # "resolve symbols-only and diff unscoped" logic. It reaches
        # `service.compare_snapshots`/`resolve_input` function-locally
        # (exactly like `cli_helpers_compare`/`cli_scan_baseline` already do),
        # and both of those already-member modules import it back
        # function-locally — so this closes the same cluster of cycles
        # through already-member modules, not a new dependency direction.
        # No init deadlock.
        #
        # `service_compare_pipeline` joins the same SCC (ADR-055 D1), for the
        # same reason `l0_export_delta` and `scan_engine` above do: it is a
        # *split* of an existing member, not a new dependency direction.
        # `run_compare_request`'s body was one function that both resolved and
        # classified, which left the native `compare` CLI no seam to run its
        # Click-dependent ADR-049 `resolve_and_apply` step in — so the CLI kept
        # a second resolution implementation of its own. Splitting that body
        # into `resolve_compare_request` / `classify_compare_pair` removed the
        # reason for the copy. The extracted module reaches
        # `cli_buildsource`/`cli_dump_helpers`/`cli_buildsource_helpers` and
        # `service` itself function-locally — the *identical* set of edges
        # `service.run_compare_request` already had before the split, moved
        # rather than added — and `service` imports it back at its module-load
        # tail. It imports only `api_types`/`errors` (both leaves) at module
        # load, so the package still imports cleanly: no init deadlock. Net
        # effect on the graph is a *reduction*, since `cli_resolve` no longer
        # carries its own copy of the resolution this module now owns.
        #
        # `service_dump_pipeline` and `service_input_resolution` join the same
        # SCC (G33 Phase 5), on exactly the terms `service_compare_pipeline`
        # above was signed off under — a *split* of an existing member, not a
        # new dependency direction:
        #   * `service_input_resolution` holds the per-input primitives that
        #     were `service_compare_pipeline`'s private helpers (`_resolve_side`,
        #     `_embed_side_build_source`, `_enforce_requested_depth`). Every
        #     edge it has — `service`, `cli_buildsource`, `cli_dump_helpers`,
        #     `cli_buildsource_helpers` — is an edge that code already had one
        #     module over, moved rather than added.
        #   * `service_dump_pipeline` is `run_dump_request`: `dump`'s
        #     counterpart to `resolve_compare_request`, so a typed Python
        #     caller reaches the same evidence resolution
        #     instead of a five-argument subset of `resolve_input`. It reaches
        #     `service` function-locally and `service` imports it back at its
        #     module-load tail — the identical shape as its compare sibling.
        # Both import only leaf modules (`api_types`/`errors`/
        # `service_input_resolution`) at module load, so the package still
        # imports cleanly: no init deadlock.
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
                # `cli_project` (G30 P1.1/P1.4/P1.5, consolidated by ADR-054's
                # CLI-organization review) joins this SCC exactly like the
                # three former standalone groups it replaces
                # (`cli_build_output`/`cli_project_targets`/`cli_run_plan`)
                # did: its `project validate`/`validate-build`/`plan`
                # commands reuse the shared `-o/--format` pair via
                # `cli_options.output_options` (module-load import), and
                # `cli_options` is already a member — so `cli -> cli_project
                # -> cli_options -> ... -> cli` closes through already-member
                # modules, not a new dependency direction. No init deadlock.
                "cli_project",
                "cli_pr_comment",
                "cli_probe",
                "cli_resolve",
                "cli_scan",
                "cli_scan_baseline",
                "cli_stack",
                "cli_suggest",
                "cli_surface",
                "l0_export_delta",
                "scan_engine",
                "service",
                "service_compare_pipeline",
                "service_dump_pipeline",
                # `service_header_graph_attach` joins the same SCC on exactly
                # the terms `service_compare_pipeline`/`service_dump_pipeline`
                # above were signed off under -- a *split* of an existing
                # member, not a new dependency direction. `_attach_header_
                # graph` was previously defined directly in `service.py`
                # (purely for the file-size cap, mirroring the earlier splits
                # of `service_render`/`service_scan` out of the same file); it
                # imports `service_scan.expand_header_inputs` at module load
                # (the identical edge `service.py` itself already carried via
                # its own tail-of-file `from .service_scan import (...)`
                # re-export block), and `service` imports it back eagerly at
                # its own top. `service_scan` itself imports nothing from
                # `service` at module load (it is a leaf) -- the SCC closes
                # only through function-local imports elsewhere in this same
                # cluster, so this adds no new *runtime* edge and no init
                # deadlock; the package still imports cleanly.
                "service_header_graph_attach",
                "service_input_resolution",
                "service_scan",
                # `service_dump_native` joins the same SCC on exactly the terms
                # `service_header_graph_attach` above was signed off under -- a
                # *split* of an existing member (ADR-061 "make service.py a
                # thin facade" pass), not a new dependency direction.
                # `run_dump`/`_run_dump_uncached`/`_dump_elf` and siblings were
                # previously defined directly in `service.py` (the same
                # file-size-cap reason the earlier splits give); this module
                # imports `service_scan.expand_header_inputs` at its own tail
                # (the identical edge `service.py` itself already carried via
                # its own tail-of-file `from .service_scan import (...)`
                # re-export block, now moved rather than added) and
                # `service_header_graph_attach._attach_header_graph` eagerly
                # at its top (an edge `service.py` already carried too). Both
                # edges land on already-member modules, and `service` imports
                # this module back at its own tail -- so this closes the same
                # cluster of cycles through already-member modules, not a new
                # one. `service_dump_native_pe` (the PE/Mach-O half of the
                # same original block, split out purely to keep this new
                # module under the 800-line production cap a genuinely new
                # file gets no debt-ledger baseline to grow into) reaches
                # `service_header_scoped` only via the same lazy
                # `importlib.import_module` indirection `service.py`'s own
                # docstring already documents as invisible to this check's
                # static AST walk -- it carries no static edge into this
                # cluster and is deliberately not listed here.
                "service_dump_native",
                # ADR-061 Phase 3 split `service_input_resolution` (already a
                # member, two entries up) into `workflows.artifact.resolve` and
                # `workflows.artifact.execute`, leaving the old path as a
                # delegating facade. `execute` inherits the member's edges
                # verbatim -- `service`/`service_scan` reached function-locally,
                # imported back at those modules' tails -- so this is the same
                # rename-follows-member case the sign-off above already covers,
                # not a new dependency direction. `resolve` is deliberately
                # absent: it holds only the decide-half, whose edges are all
                # outward, so it never closes the loop.
                "workflows.artifact.execute",
                # ADR-061 Phase 4 split `cli` (already a member of this SCC,
                # and the reason it exists -- sibling command modules import
                # `main` back from it) into a registration facade plus the
                # three modules below. Same rename-follows-member case as
                # `workflows.artifact.execute` above: every edge they have is
                # an edge `cli` already had, moved rather than added, and the
                # loop still closes only through the tail-of-module
                # registration imports it always closed through. `cli` itself
                # stays a member via its own `{"cli", "cli_X"}` entries above.
                "frontends.cli.commands.compare",
                "frontends.cli.commands.dump",
                "frontends.cli.runtime",
                # CLI cleanup phase two, PR C: `frontends.cli.dump_execute` is
                # a same-session size-split sibling of `frontends.cli.commands
                # .dump` (already a member, immediately above) -- it holds
                # the real ELF run's call into `service_dump_pipeline.
                # execute_dump_request` (already a member) that used to live
                # directly in `dump.py`. Every edge it has --
                # `service_dump_pipeline`, function-local, exactly the shape
                # `cli_dump_helpers`/`cli_buildsource` already reach it
                # through -- is an edge this cluster already carried, moved
                # one file over rather than added; `dump.py` itself calls it
                # (a new intra-cluster edge, not an edge leaving the
                # cluster). No init deadlock: it imports only `click`/
                # `errors` (a leaf) at module load.
                "frontends.cli.dump_execute",
                # G38 Phase 15 file-split prerequisite: `cli_compare_release`
                # (already a member, above) split its per-pair/per-library
                # comparison engine and its matrix-result/output/gating
                # engine into two new siblings, `cli_compare_release_
                # pairwise`/`cli_compare_release_matrix`, purely to stay
                # under the AI-readiness 2000-line hard cap. Same rename-
                # follows-member case as `service_header_graph_attach`/
                # `workflows.artifact.execute` above: both new modules'
                # `from .cli import (...)` edge is the identical edge
                # `cli_compare_release` already carried before the split,
                # moved rather than added, and `cli_compare_release` itself
                # imports each back at its own top -- so this closes the
                # same cluster of cycles through already-member modules,
                # not a new dependency direction.
                "cli_compare_release_matrix",
                "cli_compare_release_pairwise",
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

    Skipped (with a single info line) when mypy is unavailable. Invoked via
    an isolated ``sys.executable`` module runner rather than a bare ``mypy``
    resolved via PATH (`shutil.which`) — a bare command name can resolve to
    a *different* install than the one pinned for this interpreter
    (`mypy==1.19.1` per pyproject.toml's `[dev]` extra), which silently ran
    the wrong mypy version here and reported a false baseline drift
    (CLAUDE.md "M0-3" — the same PATH-ambiguity class scripts/verify.py's
    `_py()` helper exists to close). The runner starts Python with ``-I``
    and restores the base interpreter's user site, additionally preventing
    a repository-root ``mypy.py`` from shadowing the installed tool while
    this check runs with ``cwd=ROOT`` (P0.3, security hardening).
    """
    if importlib.util.find_spec("mypy") is None:
        print("mypy-baseline: mypy not installed, skipping")
        return
    try:
        proc = subprocess.run(
            _isolated_module_command("mypy", "abicheck"),
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
        # Round 20, Part B: this check previously parsed and printed only
        # the summary COUNT, never the individual error lines -- so a real
        # CI failure here ("mypy reports 4 errors; baseline is 0") gave a
        # reader no way to see what those 4 errors actually were without
        # separately reproducing the run. Always echo the raw mypy output
        # when it drove a hard failure; this is a durable usability fix
        # independent of any one investigation.
        print("mypy-baseline: raw mypy output follows —\n" + text, flush=True)
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

    Unlike the generated docs/reference/examples/ tree (gated by gen_examples_docs.py
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


def _strip_yaml_line_comment(line: str) -> str:
    """Drop a YAML comment from one line: a `#` that starts a comment (at
    line start, or preceded by whitespace) and isn't inside a quoted
    string. Without this, a nav entry commented out to remove it from
    publication (`# - ADR Index: contribute/adr/index.md`) would still be
    picked up by a raw whole-file regex scan, making a page look reachable
    from nav when it no longer is (PR #619 review)."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                return line[:i]
    return line


def _collect_mkdocs_nav_refs() -> set[str]:
    """Extract every .md path referenced in mkdocs.yml.

    We deliberately don't depend on PyYAML — the script is stdlib-only and
    runs before pip install in CI. A regex over the nav block is good
    enough: mkdocs nav entries are always plain ``Title: path.md`` lines.
    Comments are stripped first (see _strip_yaml_line_comment) so a
    commented-out entry doesn't count as a real one.
    """
    mkdocs = ROOT / "mkdocs.yml"
    if not mkdocs.is_file():
        return set()
    text = "\n".join(
        _strip_yaml_line_comment(line) for line in _read(mkdocs).split("\n")
    )
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

#: (?<!!) excludes image syntax (`![alt](src)` / `![alt][label]`) -- an
#: image embed is not a navigable link, even though its bracket/paren shape
#: otherwise matches the same pattern as a real link.
_ADR_REPLACEMENT_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_ADR_REF_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\[([^\]]*)\]")
#: CommonMark allows a link reference definition to be indented 0-3 spaces
#: -- anchoring straight to column 0 would miss a validly-indented
#: definition (PR #619 review).
_ADR_REF_DEF_RE = re.compile(r"^[ \t]{0,3}\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)

#: A fenced-code opening delimiter (``` or ~~~, 3+ repeats, optional leading
#: indent up to 3 spaces per CommonMark, optional trailing info string).
_ADR_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$")
_ADR_BACKTICK_RUN_RE = re.compile(r"`+")


def _strip_adr_fenced_code(text: str) -> str:
    """Remove fenced code blocks before scanning `text` for links -- a code
    sample demonstrating Markdown link syntax (e.g. `[001](001-example.md)`
    inside a ``` block explaining the convention) would otherwise be
    misread as a real, navigable link (PR #619 review). Mirrors
    check_docs_contract.py's _strip_fenced_code: a closing fence must be
    alone on its own line, using the same delimiter character as the opener
    with at least as many repeats."""
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _ADR_FENCE_OPEN_RE.match(lines[i])
        if m is None:
            out.append(lines[i])
            i += 1
            continue
        fence = m.group(1)
        i += 1
        closer = re.compile(rf"^[ \t]{{0,3}}{fence[0]}{{{len(fence)},}}[ \t]*$")
        while i < n and closer.match(lines[i]) is None:
            i += 1
        i += 1  # skip the closing fence line itself (or EOF, harmlessly)
    return "\n".join(out)


def _strip_adr_inline_code(text: str) -> str:
    """Remove CommonMark inline code spans (a backtick run, matched by
    length, not just a single backtick) before scanning for links -- same
    rationale as _strip_adr_fenced_code, for a link-shaped example wrapped
    in `single or ``double`` backticks instead of a fenced block."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        m = _ADR_BACKTICK_RUN_RE.match(text, i)
        if m is None:
            out.append(text[i])
            i += 1
            continue
        run_len = m.end() - i
        j = m.end()
        closer = None
        while j < n:
            m2 = _ADR_BACKTICK_RUN_RE.match(text, j)
            if m2 is None:
                j += 1
                continue
            if m2.end() - j == run_len:
                closer = m2
                break
            j = m2.end()
        if closer is None:
            out.append(text[i : m.end()])
            i = m.end()
        else:
            i = closer.end()
    return "".join(out)


#: HTML comments are invisible when MkDocs renders the page -- a link
#: hidden inside one (`<!-- [ADR-050](050-new.md) -->`) must not satisfy a
#: "links to X" check any more than one buried in code would (PR #619
#: review).
_ADR_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_adr_link_noise(text: str) -> str:
    """Strip fenced code, inline code, and HTML comments before scanning
    `text` for Markdown links -- all three render as non-navigable or
    invisible content in MkDocs, so a link-shaped string inside any of them
    must not count as a real link."""
    text = _strip_adr_fenced_code(text)
    text = _strip_adr_inline_code(text)
    text = _ADR_HTML_COMMENT_RE.sub("", text)
    return text


def _resolve_adr_href_target(
    href: str, adr_dir: Path, resolved_adr_dir: Path
) -> Path | None:
    """Resolve a Markdown link target found somewhere under `adr_dir` to the
    real ADR file it points at, or None if it doesn't resolve to one.
    Checking only the link target's basename against _ADR_FILE_RE isn't
    enough, since a link like `[plan](../notes/002-plan.md)` has a basename
    that matches the ADR filename pattern while actually pointing outside
    the ADR directory entirely."""
    href_path = href.strip()
    if href_path.startswith("<"):
        # CommonMark's angle-bracket link destination form ([text](<url>))
        # -- MkDocs renders it as a normal link, so the `<`/`>` must not end
        # up as part of the resolved path.
        end = href_path.find(">")
        href_path = href_path[1:end] if end != -1 else href_path[1:]
    else:
        # Non-bracketed destinations may carry an optional title after a
        # space ([text](url "title")) -- without splitting it off, the
        # trailing `"title"` text stays glued to the basename and never
        # matches _ADR_FILE_RE.
        href_path = href_path.split(" ", 1)[0]
    href_path = href_path.split("#", 1)[0]
    if not href_path or "://" in href_path or href_path.startswith(("mailto:", "/")):
        return None
    basename = href_path.split("/")[-1]
    if not _ADR_FILE_RE.match(basename):
        return None
    resolved = (adr_dir / href_path).resolve()
    if resolved.parent == resolved_adr_dir and resolved.is_file():
        return resolved
    return None


def _adr_href_resolves_elsewhere(
    href: str, adr_dir: Path, resolved_adr_dir: Path, resolved_own_path: Path
) -> bool:
    """True if `href` resolves to another real ADR file inside `adr_dir` --
    *other than* the ADR making the claim."""
    resolved = _resolve_adr_href_target(href, adr_dir, resolved_adr_dir)
    return resolved is not None and resolved != resolved_own_path


def _links_to_another_adr(
    status: str, adr_dir: Path, own_path: Path, full_text: str
) -> bool:
    """True if `status` contains a Markdown link (inline `[text](url)` or
    reference-style `[text][label]` with a `[label]: url` definition
    elsewhere in the file) that resolves to another real ADR file inside
    `adr_dir` -- *other than* `own_path` itself. Must be more than "any
    link at all" -- a "Superseded" status could otherwise link to unrelated
    context (e.g. a plan doc explaining why) and still satisfy a bare "has
    a link" check, or even link to itself ("Superseded by [this
    ADR](001-x.md)" inside 001-x.md) and still satisfy a bare "resolves to
    a real ADR file" check."""
    resolved_adr_dir = adr_dir.resolve()
    resolved_own_path = own_path.resolve()
    for href in _ADR_REPLACEMENT_LINK_RE.findall(status):
        if _adr_href_resolves_elsewhere(
            href, adr_dir, resolved_adr_dir, resolved_own_path
        ):
            return True
    # Reference-style links: [text][label] / [text][] -- resolve `label`
    # (or `text` for the collapsed form) against a `[label]: url` definition
    # anywhere in the file (reference definitions are conventionally placed
    # at the bottom of the document, not necessarily near the status
    # paragraph itself).
    definitions = {
        label.strip().casefold(): url
        for label, url in _ADR_REF_DEF_RE.findall(full_text)
    }
    for link_text, label in _ADR_REF_LINK_RE.findall(status):
        key = (label or link_text).strip().casefold()
        url = definitions.get(key)
        if url and _adr_href_resolves_elsewhere(
            url, adr_dir, resolved_adr_dir, resolved_own_path
        ):
            return True
    return False


def _index_links_to_adr(
    index_text: str, adr_dir: Path, resolved_adr_dir: Path, target: Path
) -> bool:
    """True if `index_text` (docs/contribute/adr/index.md's full content)
    contains an actual Markdown link (inline or reference-style) resolving
    to `target`. A bare mention of the filename in prose or a code sample
    isn't enough -- MkDocs doesn't turn plain text into a navigable link, so
    that wouldn't make the ADR reachable from the published index page."""
    for href in _ADR_REPLACEMENT_LINK_RE.findall(index_text):
        if _resolve_adr_href_target(href, adr_dir, resolved_adr_dir) == target:
            return True
    definitions = {
        label.strip().casefold(): url
        for label, url in _ADR_REF_DEF_RE.findall(index_text)
    }
    for link_text, label in _ADR_REF_LINK_RE.findall(index_text):
        key = (label or link_text).strip().casefold()
        url = definitions.get(key)
        if url and _resolve_adr_href_target(url, adr_dir, resolved_adr_dir) == target:
            return True
    return False


def check_adr_index_and_nav_sync(f: Findings) -> None:
    """Every docs/contribute/adr/*.md file must be linked from index.md,
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
    adr_dir = DOCS / "contribute" / "adr"
    if not adr_dir.is_dir():
        return
    # Stripped before link scanning (_index_links_to_adr) so a fenced/inline
    # code example or an HTML comment can't be misread as a real, navigable
    # link to the index page (PR #619 review).
    index_text = _strip_adr_link_noise(_read(adr_dir / "index.md"))
    resolved_adr_dir = adr_dir.resolve()
    nav_refs = _collect_mkdocs_nav_refs()
    index_nav_target = "contribute/adr/index.md"
    if nav_refs and index_nav_target not in nav_refs:
        f.err(
            "adr-index-nav-sync",
            "docs/contribute/adr/index.md: the ADR index itself is not "
            "listed in mkdocs.yml nav (every ADR is reachable only through "
            "this page, so it must be a real nav entry)",
        )
    for md in sorted(adr_dir.glob("*.md")):
        if md.name == "index.md" or not _ADR_FILE_RE.match(md.name):
            continue
        if not _index_links_to_adr(index_text, adr_dir, resolved_adr_dir, md.resolve()):
            f.err(
                "adr-index-nav-sync",
                f"docs/contribute/adr/{md.name}: not linked from "
                f"docs/contribute/adr/index.md",
            )
        # Same stripping as index_text above -- a Superseded status hiding
        # its replacement link in inline code or an HTML comment must not
        # satisfy _links_to_another_adr() below (PR #619 review).
        text = _strip_adr_link_noise(_read(md))
        status = _adr_status_text(text)
        if status is None:
            f.err(
                "adr-index-nav-sync",
                f"docs/contribute/adr/{md.name}: missing a Status "
                "metadata line ('**Status:** ...') or heading ('## Status')",
            )
            continue
        # Strip Markdown emphasis (`**Superseded**`) and split on ':' too
        # (`Superseded: see ADR-002`) -- either phrasing otherwise leaves
        # the leading token as "**Superseded**" or "Superseded:", neither of
        # which matches the plain "superseded" comparison below, silently
        # skipping the replacement-link requirement (PR #619 review).
        leading_word = re.split(r"[\s—:.,;-]", status.strip(), maxsplit=1)[0].strip("*")
        if leading_word.lower() == "superseded" and not _links_to_another_adr(
            status, adr_dir, md, text
        ):
            f.err(
                "adr-index-nav-sync",
                f"docs/contribute/adr/{md.name}: status is 'Superseded' "
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

# Tier-1 core entry points a front-end must never call directly — each maps
# the module defining it to the function name(s) and the guidance a
# violation should carry. ADR-037 D1/D10.1 covers ``checker.compare``;
# ``dumper.dump``/``service.resolve_input`` extend the identical rule per
# Phase 0 item 2 of
# docs/contribute/plans/duplication-and-convergence-assessment.md — a
# front-end reaching any of the three bypasses the one shared
# resolve/execute path the rest of that plan is converging on.
#
# Known, pre-existing limitation (not introduced by the extension to three
# targets — the original single-target ``checker.compare`` check already had
# this exact shape): binding detection is file-wide and lexically
# scope-blind. A local parameter or nested function that happens to *shadow*
# an imported Tier-1 name (e.g. a function parameter literally named
# ``resolve_input``) and is then called would be misread as the imported
# Tier-1 function. No real call site in this codebase does this today (the
# `test_no_tier_skip` real-repo run stays at 0 findings), and doing so would
# itself already draw a `ruff` shadowing/redefinition warning — a full
# lexical-scope-aware rewrite of this AST walk is a disproportionate
# response to a theoretical, not-observed risk and is left as a documented
# residual gap rather than attempted reactively here.
_TIER1_TARGETS: tuple[tuple[str, frozenset[str], str], ...] = (
    (
        "checker",
        frozenset({"compare"}),
        "route through `service.run_compare` / `service.compare_snapshots`",
    ),
    (
        "dumper",
        frozenset({"dump"}),
        "route through `service.run_dump` / `service_dump_pipeline.run_dump_request`",
    ),
    (
        "service",
        frozenset({"resolve_input"}),
        "route through `service_input_resolution.resolve_side_snapshot` "
        "(or `cli_resolve._resolve_input`, its CLI-side wrapper)",
    ),
)

#: Modules whose ``_resolve_input()`` function is exempted from the
#: ``service.resolve_input`` entry above: ``cli_resolve.py``'s own
#: ``_resolve_input()`` *is* the CLI's sanctioned, framework-aware wrapper
#: over ``service.resolve_input`` (see its module docstring) — the same role
#: ``service.py`` itself plays for ``checker.compare``. The exemption is
#: scoped to calls inside that one function
#: (``_resolve_input_wrapper_call_sites``), not the whole module — a
#: *different* function added later to the same file must still route
#: through it rather than bypassing the rule directly.
_RESOLVE_INPUT_WRAPPER_MODULES: frozenset[str] = frozenset({"cli_resolve"})

# ``"<rel-path>:<lineno>:<col_offset>:<module>.<func>"`` call sites
# deliberately exempted, each needing a reason in review. The target
# identity and the call's own column are both part of the key — not just
# `path:lineno` — so replacing an allowlisted call with a *different*
# Tier-1 violation on the same line, or adding a second call to the *same*
# target on that line (e.g. `(dump(a), dump(b))`), cannot silently inherit
# the exemption; each direct Tier-1 call needs its own reviewed entry. The
# freshness test below depends on this to actually verify the reviewed
# call is still there, not just that *some* finding exists at that site.
# Pre-populated with the pre-existing, already-documented
# `dumper.dump`/`service.resolve_input` direct-call sites Phase 1 of the
# same plan names as duplication to converge
# (`cli_dump_helpers.perform_elf_dump`, `appcompat.check_appcompat`,
# `cli_scan_baseline`'s baseline resolution) — a new entry beyond these
# needs the same reviewed sign-off (mirrors the INTENTIONAL_SUBSET
# philosophy of D10.2).
CLI_CONTRACT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Native ELF CLI dump (P0 item 2): calls `dumper.dump()` directly
        # rather than through `service_dump_pipeline.run_dump_request` —
        # tracked as Phase 1 item 1 of the convergence plan.
        "abicheck/cli_dump_helpers.py:1299:19:dumper.dump",
        # Standalone application-compatibility (P0 item 6): dumps both
        # sides directly rather than through any of the other paths.
        "abicheck/appcompat.py:1604:19:dumper.dump",
        "abicheck/appcompat.py:1620:19:dumper.dump",
        # Scan baseline resolution (P0 item 4's baseline half): calls
        # `service.resolve_input()` directly rather than through
        # `service_input_resolution.resolve_side_snapshot`.
        "abicheck/cli_scan_baseline.py:1087:19:service.resolve_input",
        # ABICC compatibility wrapper (P1 "ABICC compatibility is a parallel
        # frontend and engine path"): its own parallel engine path calls
        # both `dumper.dump()` and `checker.compare()` directly.
        "abicheck/compat/cli.py:317:19:dumper.dump",
        "abicheck/compat/cli.py:974:17:checker.compare",
        "abicheck/compat/cli.py:1167:15:dumper.dump",
    }
)


def _relative_import_level_for_source(path: Path) -> int:
    """The ``ImportFrom.level`` a module at *path* must use to reach
    abicheck's own top-level package via a relative import.

    Python's relative-import level counts dots from the *importing*
    module's own containing package, not from a fixed depth: a top-level
    module (``abicheck/cli.py``) reaches ``abicheck`` with a single dot
    (``from . import checker``, level 1), while a module one package
    deeper (``abicheck/compat/cli.py``, whose own package is
    ``abicheck.compat``) needs two (``from .. import checker``, level 2) —
    a single dot there resolves to ``abicheck.compat.checker`` instead, a
    different module entirely.
    """
    depth = len(path.resolve().relative_to(PKG.resolve()).parent.parts)
    return depth + 1


def _importfrom_names_module(
    dotted: str, level: int, module: str, required_level: int
) -> bool:
    """True if an ``ImportFrom(module=dotted, level=level)`` genuinely names
    abicheck's own top-level ``<module>`` — a relative ``.{module}`` at
    exactly *required_level* dots (see ``_relative_import_level_for_source``
    — a *different* level names a different, sibling module even when the
    trailing spelling matches) or an absolute ``abicheck.{module}`` — not an
    unrelated module that merely *ends* in or *is spelled* the same bare
    name (``from vendor.service import resolve_input`` and a bare ``from
    service import resolve_input`` must not be mistaken for abicheck's own
    ``service`` module; a real front-end module here always spells its own
    sibling either relatively or through the ``abicheck.`` prefix, never as
    a bare top-level name).
    """
    if level >= 1:
        return level == required_level and dotted == module
    return dotted == f"abicheck.{module}"


def _import_names_module(dotted: str, module: str) -> bool:
    """True if a plain ``import <dotted>`` genuinely names abicheck's own
    top-level ``<module>`` (``abicheck.<module>`` only — never a bare
    ``<module>``), for the identical reason as ``_importfrom_names_module``
    above."""
    return dotted == f"abicheck.{module}"


def _tier1_func_bindings(
    tree: ast.Module, module: str, funcs: frozenset[str], required_level: int
) -> set[str]:
    """Return the local names bound to one of *funcs* via a direct import of
    *module*'s function(s) in *tree*.

    Handles ``from .<module> import <func>`` and ``... import <func> as X`` at
    module or function scope, so a lazily-imported alias is caught too.
    *required_level* is the source file's own relative-import depth — see
    ``_relative_import_level_for_source``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and _importfrom_names_module(
                node.module, node.level, module, required_level
            )
        ):
            for alias in node.names:
                if alias.name in funcs:
                    names.add(alias.asname or alias.name)
    return names


def _dotted_path(node: ast.expr) -> str | None:
    """The full dotted-attribute spelling of a ``Name``/``Attribute`` chain
    (``abicheck.service`` for ``abicheck.service``'s AST), or ``None`` if
    *node* is not a plain dotted chain (a call result, a subscript, ...)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_path(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _tier1_package_aliases(tree: ast.Module) -> set[str]:
    """Every local name bound to the ``abicheck`` top-level package object
    itself — ``import abicheck`` binds ``abicheck``, ``import abicheck as
    abi`` binds ``abi``. Used to recognize a qualified Tier-1 call reached
    through a package-level alias (``abi.service.resolve_input(...)``), not
    just the literal ``abicheck`` spelling (Codex review: only the bare name
    was tracked, so an aliased-package spelling silently bypassed the gate).
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "abicheck":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _tier1_module_bindings(
    tree: ast.Module, module: str, required_level: int
) -> set[str]:
    """Return every spelling *tree* could call ``<module>.<func>(...)``
    through: local names bound to *module* itself, plus the full dotted path
    the submodule is reachable through off every name bound to the
    ``abicheck`` package object itself (the bare ``abicheck`` name, and any
    ``import abicheck as X`` alias — see ``_tier1_package_aliases``).

    Catches ``from . import <module> [as X]``, ``from abicheck import
    <module> [as X]``, and ``import abicheck.<module> [as X]`` alike, so an
    aliased ``core.<func>(...)`` call is recognised — and so is
    ``abicheck.<module>.<func>(...)`` *and* ``<pkg_alias>.<module>.<func>(...)``
    for any package alias, regardless of *which* of the three import forms
    actually loaded the submodule (Codex review: an earlier version only
    granted this for the unaliased ``import abicheck.<module>`` spelling,
    so ``import abicheck as abi`` combined with a separately-aliased
    ``from abicheck import service`` still bypassed the gate — but Python
    binds the submodule onto the package object as a side effect of *any*
    of the three import forms, whether or not that statement's own local
    binding is aliased, so every one of them must grant the same package-
    alias reachability) — not just a call through a name bound directly to
    *module*. *required_level* is the source file's own relative-import
    depth (see ``_relative_import_level_for_source``): a bare ``from .
    import <module>`` only names the top-level abicheck module at exactly
    that level, since a nested front end's single dot names a *sibling*
    within its own subpackage instead.
    """
    names: set[str] = set()
    submodule_imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # ``from . import <module>`` (module is None, level must match
            # this source's own depth) or an *absolute* ``from abicheck
            # import <module>`` (level 0 — a relative ``from .abicheck
            # import <module>`` is a different module, `abicheck.abicheck`,
            # not the package root) — not an unrelated ``from vendor import
            # <module>``-shaped import, and not a nested front end's own
            # same-subpackage sibling.
            if (node.module is None and node.level == required_level) or (
                node.level == 0 and node.module == "abicheck"
            ):
                for alias in node.names:
                    if alias.name == module:
                        # This form loads `abicheck.<module>` and binds it
                        # onto the package object regardless of whether the
                        # *local* name is aliased (`as X`) -- so it grants
                        # package-alias reachability the same as the
                        # unaliased `import abicheck.<module>` form below.
                        names.add(alias.asname or alias.name)
                        submodule_imported = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _import_names_module(alias.name, module):
                    submodule_imported = True
                    if alias.asname:
                        names.add(alias.asname)
                    else:
                        # ``import abicheck.<module>`` binds only ``abicheck``;
                        # the module itself stays reachable as the full
                        # dotted path off it (``abicheck.<module>.<func>``).
                        names.add(alias.name)
    if submodule_imported:
        # The submodule attribute lives on the package *object* -- every
        # other name bound to that same object (a package-level alias)
        # reaches it too, not just the literal ``abicheck`` spelling.
        names.update(
            f"{pkg_alias}.{module}" for pkg_alias in _tier1_package_aliases(tree)
        )
    return names


#: Every AST node shape that introduces its own implicit scope, distinct
#: from its lexically-enclosing one — the ``def``/``class``/``lambda``
#: trio, plus a comprehension/generator expression (``[x for x in ...]``),
#: which CPython also compiles as a hidden nested scope (Codex review: the
#: comprehension case was missing, so ``next(service.resolve_input(x) for x
#: in paths)`` nested inside the wrapper was wrongly exempted).
_SCOPE_DEFINING_TYPES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _definition_head_parts(node: ast.AST) -> list[ast.AST]:
    """The parts of a scope-defining node that execute in its *enclosing*
    scope, right when the node itself is evaluated — decorators,
    default-argument expressions, the return annotation, base-class/keyword
    expressions (class), and — for a comprehension — only the *outermost*
    ``for`` clause's iterable (the one Python expression a comprehension
    evaluates eagerly in the enclosing scope; every other clause, condition,
    and the result expression itself run inside the comprehension's own
    scope). Never the node's own body/return-expression, which is the *new*
    scope it introduces.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # `node.returns` (the `-> T` annotation) is, like a default-argument
        # expression, evaluated in the enclosing scope at def-time, not the
        # scope the definition introduces (Codex review: only `node.args`
        # was included, so a nested definition's return annotation calling a
        # Tier-1 target was wrongly flagged instead of recognized as running
        # in the enclosing wrapper's own scope). `None` when unannotated.
        parts: list[ast.AST] = [*node.decorator_list, node.args]
        if node.returns is not None:
            parts.append(node.returns)
        return parts
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    if isinstance(node, ast.Lambda):
        return [node.args]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return [node.generators[0].iter] if node.generators else []
    return []


def _iter_calls_in_own_scope(node: ast.AST) -> Iterable[ast.Call]:
    """Yield every ``Call`` lexically inside *node*'s own scope, without
    descending into a *nested* function/class/lambda definition's own body
    — a call there belongs to that nested scope, not to *node*'s, and must
    not be silently swept in as if it were (Codex review: a bypass placed
    inside a nested ``def`` within the sanctioned wrapper previously
    inherited the wrapper's own exemption via a full ``ast.walk``). A
    nested definition's *head* (:func:`_definition_head_parts`) is walked
    though, not skipped wholesale — those expressions run immediately in
    *this* scope (Codex review: ``def inner(x=service.resolve_input(a))``
    nested inside the wrapper was wrongly flagged).

    For a ``FunctionDef``/``AsyncFunctionDef`` passed directly (the only
    shape a caller passes at the top level), only ``node.body`` is this
    scope — its own head is the *enclosing* (module) scope, not this one,
    and is deliberately excluded the same way a nested definition's own
    head is *included* for the opposite reason (Codex review: a call in
    the top-level wrapper's own default expression was swept in as
    exempt). Applying that special case *inside* this function, not by
    having a caller pass ``node.body`` statements one at a time, matters:
    the pruning check below only fires when a scope-defining node is
    discovered as a *child* during recursion — calling this function
    directly on a *nested* ``def`` would bypass it entirely. The actual
    per-child walk is :func:`_iter_calls_in_children`, reused for a head
    part's own recursion too — a head part can itself be scope-defining
    (e.g. a comprehension nested inside another comprehension's outermost
    iterable), and it must get the identical pruning treatment an ordinary
    body statement gets, not the naive top-level entry (Codex review: that
    naive entry skipped the pruning check for exactly this shape).
    """
    children: Iterable[ast.AST] = (
        node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else ast.iter_child_nodes(node)
    )
    yield from _iter_calls_in_children(children)


def _iter_calls_in_children(children: Iterable[ast.AST]) -> Iterable[ast.Call]:
    """The shared per-child walk behind :func:`_iter_calls_in_own_scope`:
    yield a ``Call`` child directly, and for a scope-defining child recurse
    only into its own head parts (never its body); everything else
    descends normally. Used both for a node's ordinary children and for a
    scope-defining child's own head parts, so a head part that is itself
    scope-defining is pruned the same way either shape is reached.
    """
    for child in children:
        if isinstance(child, ast.Call):
            yield child
        if isinstance(child, _SCOPE_DEFINING_TYPES):
            yield from _iter_calls_in_children(_definition_head_parts(child))
            continue
        yield from _iter_calls_in_children(ast.iter_child_nodes(child))


def _resolve_input_wrapper_call_sites(tree: ast.Module) -> frozenset[tuple[int, int]]:
    """``(lineno, col_offset)`` of every ``Call`` inside the *module-level*
    ``_resolve_input()``'s own scope — the only call sites the
    ``service.resolve_input`` rule may exempt in a module listed in
    ``_RESOLVE_INPUT_WRAPPER_MODULES``.

    Looks only at ``tree.body`` (top-level statements) to find the wrapper
    itself — a same-named method on a class, or a same-named function
    nested inside a different one, is not the reviewed, documented wrapper
    — and, once found, walks only its own lexical scope
    (:func:`_iter_calls_in_own_scope`), not a full ``ast.walk``, so a call
    inside a *nested* function or class defined within the wrapper does not
    silently inherit its exemption either.

    Keyed by the full ``(lineno, col_offset)`` position, not bare ``lineno``
    (Codex review: a nested-scope bypass on the *same line* as an
    exempt outer call — e.g. ``return (lambda: service.resolve_input(a))()``
    — was itself correctly pruned by :func:`_iter_calls_in_own_scope`, but
    the outer call's own line number was still recorded, and a line-only
    membership check let the inner call inherit that exemption purely from
    sharing a line, not from being the reviewed site).

    :func:`_iter_calls_in_own_scope` itself excludes a default-argument
    expression (``def _resolve_input(a=service.resolve_input(path)):``),
    which executes once in the *enclosing* scope at def-time, not inside
    the wrapper's own runtime scope (Codex review) — see its docstring.
    """
    sites: set[tuple[int, int]] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_input":
            sites.update(
                (call.lineno, call.col_offset)
                for call in _iter_calls_in_own_scope(node)
            )
    return frozenset(sites)


def _iter_cli_contract_sources() -> Iterable[Path]:
    """The front-end modules the contract covers: every ``cli*.py``, the
    consumer-side ``appcompat.py`` (a verdict-emitting front-end too), and
    ``compat/cli.py`` (the ABICC-compatible CLI wrapper — a *nested* front
    end `PKG.glob("cli*.py")` alone would miss, per Phase 0 item 2 of
    docs/contribute/plans/duplication-and-convergence-assessment.md). The MCP
    server was removed; agent integrations route through these same
    front ends (CLI or the typed Python API) rather than a separate tier."""
    yield from PKG.glob("cli*.py")
    for extra in ("appcompat.py", "compat/cli.py"):
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
    # ADR-061 Phase 4 moved `compare`'s body out of `cli.py`, which is now a
    # registration facade. The check follows the command, not the old home.
    "frontends/cli/commands/compare.py": "compare",
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


def check_cli_contract(f: Findings) -> None:
    """ERROR if a front-end module calls a Tier-1 core entry point
    (``checker.compare``, ``dumper.dump``, ``service.resolve_input``) directly
    instead of routing through the Tier-2 service.

    Covers every ``abicheck/cli*.py`` and ``abicheck/appcompat.py``. ADR-037
    D1/D10.1: front-ends are thin adapters; one classification/resolution path
    is what keeps ``compare`` / ``compare-release`` / ``appcompat`` from
    drifting apart (the ``scope_public`` default divergence the ADR documents)
    — the same reasoning Phase 0 item 2 of
    docs/contribute/plans/duplication-and-convergence-assessment.md extends to
    ``dumper.dump``/``service.resolve_input``. Importing a target module's
    *type* for annotations or result-rendering stays legal — the gate keys on
    the *call expression*, not the import statement. Both a direct function
    import and an aliased module-attribute call are detected for each target.
    """
    for path in sorted(_iter_cli_contract_sources()):
        rel = _rel(path)
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        is_wrapper_module = path.stem in _RESOLVE_INPUT_WRAPPER_MODULES
        # Only the reviewed wrapper *call*, inside `_resolve_input()` itself,
        # is exempt — not every `service.resolve_input` call anywhere else in
        # the same module, which would let a future bypass elsewhere in this
        # file accumulate unnoticed.
        wrapper_call_sites = (
            _resolve_input_wrapper_call_sites(tree)
            if is_wrapper_module
            else frozenset()
        )
        required_level = _relative_import_level_for_source(path)
        for module, funcs, guidance in _TIER1_TARGETS:
            bound = _tier1_func_bindings(tree, module, funcs, required_level)
            target_modules = _tier1_module_bindings(tree, module, required_level)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_tier1 = (isinstance(func, ast.Name) and func.id in bound) or (
                    isinstance(func, ast.Attribute)
                    and func.attr in funcs
                    and _dotted_path(func.value) in target_modules
                )
                if not is_tier1:
                    continue
                if (
                    module == "service"
                    and (
                        node.lineno,
                        node.col_offset,
                    )
                    in wrapper_call_sites
                ):
                    continue
                called = func.attr if isinstance(func, ast.Attribute) else func.id
                site_key = f"{rel}:{node.lineno}:{node.col_offset}:{module}.{called}"
                if site_key not in CLI_CONTRACT_ALLOWLIST:
                    f.err(
                        "cli-contract",
                        f"{rel}:{node.lineno}:{node.col_offset}: front-end calls "
                        f"Tier-1 `{module}.{called}` directly; {guidance} "
                        "(ADR-037 D1/D10.1)",
                    )
    # D10.2 shared-decorator coverage + D10.4 one-default-per-flag (ADR-037 D3).
    _check_decorator_coverage(f)
    _check_one_default_per_flag(f)


# ---------------------------------------------------------------------------
# Check: engine/CLI dependency direction (Phase 0 of
# docs/contribute/plans/duplication-and-convergence-assessment.md)
# ---------------------------------------------------------------------------
#
# Implementation lives in the sibling leaf module `engine_cli_boundary.py`
# (mirroring `adr_status_sync.py`'s own extraction) -- this file is already
# past the 2000-line hard cap and only stays green through
# `LARGE_FILE_ALLOWLIST`, which is not a license to keep growing it.


# ---------------------------------------------------------------------------
# Check: Fact[T] equality misuse (ADR-063 Phase 0,
# docs/contribute/plans/one-semantic-pipeline.md)
# ---------------------------------------------------------------------------
#
# Implementation lives in the sibling leaf module `fact_detector_misuse.py`,
# same reason as `engine_cli_boundary.py` above -- this is the static check
# `abicheck/model/fact.py`'s own `Fact` docstring already claims exists
# ("see scripts/check_ai_readiness.py's fact-detector-misuse check"), which
# this module is what makes true: no baseline, since it has zero existing
# hits under `abicheck/` today -- any `==`/`!=` comparison of a `Fact[T]`
# value is a hard error, not an allowlisted one.


# ---------------------------------------------------------------------------
# Check: unmigrated Fact[T]-bridged legacy field readers (ADR-063 Phase 0,
# docs/contribute/plans/one-semantic-pipeline.md)
# ---------------------------------------------------------------------------
#
# Implementation lives in the sibling leaf module `fact_field_readers.py`,
# same reason as `engine_cli_boundary.py` above -- this is the "widened,
# non-glob AI-readiness check" that phase's own Design section named as
# not yet written: a real repo-wide scan, not a `diff_*.py` glob, since a
# glob is exactly what let several real readers go unnoticed across
# multiple review rounds.


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
    "adr-status-sync": check_adr_status_sync,
    "banned-imports": check_banned_imports,
    "cli-contract": check_cli_contract,
    "engine-cli-boundary": check_engine_cli_boundary,
    "fact-detector-misuse": check_fact_detector_misuse,
    "fact-field-readers": check_fact_field_readers,
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
