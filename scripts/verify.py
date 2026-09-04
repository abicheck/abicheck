#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Single verification orchestrator — the one place local/CI check commands live.

Pixi, pre-commit, CI, CLAUDE.md/AGENTS.md, and CONTRIBUTING.md all invoke this
script instead of maintaining their own copies of the check commands. Changing
a command here changes it everywhere; ``tests/test_verify_profiles.py``
compares the declared ``pr`` profile against ``.github/workflows/ci.yml`` so
the two can't silently drift apart again (see CLAUDE.md "M0-3").

Profiles:

    fast   Targeted unit tests (excludes golden), lint, format, types.
           The everyday inner-loop command.
    pr     The exact always-required CI-equivalent checks: everything `fast`
           runs, plus golden tests, coverage floor, and the ai-readiness /
           architecture / FP-rate / tier-accuracy / doc-sync /
           schema/FAIR-metadata gates that required workflows run on every PR.
    full   Everything in `pr`, plus external-tool, parity, performance,
           packaging, and changelog-fragment lanes — each skipped (not
           failed) when the environment lacks the tool it needs (or, for
           changelog-fragment, lacks a locally-resolvable `origin/main`), so
           `full` is meaningful on a partial toolchain. changelog-fragment is
           `full`-only, not `pr`: the real CI gate for it is the separate
           `changelog-check.yml` workflow (uses the actual PR base/head SHAs
           from the GitHub event), not this script.

Usage:

    python scripts/verify.py --profile fast
    python scripts/verify.py --profile pr
    python scripts/verify.py --profile full
    python scripts/verify.py --profile pr --only lint,typecheck
    python scripts/verify.py --profile pr --list
    python scripts/verify.py --profile pr --json receipt.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODULE_RUNNER = Path(__file__).resolve().with_name("run_isolated_module.py")


# Diagnostic instrumentation (round 20, Part B): a CI-only-reproducible
# failure mode showed `lint-and-types`' `ruff check`/`mypy` steps reporting
# "failed" with ZERO diagnostic output in the CI log -- no error lines at
# all, just an instant nonzero exit. GitHub Actions runners capture step
# output through a redirected, non-tty pipe rather than a pty; Python's
# default stdout buffering is fully-buffered (not line-buffered) for a
# pipe, so an unflushed buffer at process exit can genuinely lose real
# output. Reconfiguring this script's own stdout to line-buffer, on top of
# always echoing an already-captured subprocess's stdout/stderr in
# run_step() below (rather than relying on inherited-fd passthrough alone),
# closes that whole class of "silently lost diagnostic output" regardless
# of whether it was this specific mystery's root cause -- never silently
# losing a failing check's own error text is a durable improvement on its
# own.
def _enable_line_buffered_output() -> None:
    """Reconfigure this process's own stdout/stderr to line-buffer.

    Called from the ``if __name__ == "__main__":`` entry point (and
    :func:`main`, for a caller that invokes it directly), never at module
    import time (CodeRabbit review, "Move stream reconfiguration out of
    module import scope", fresh evidence): reconfiguring
    ``sys.stdout``/``sys.stderr`` is a process-wide side effect that leaks
    into whatever imports this module -- a test importing
    ``scripts.verify`` to exercise its step catalog, or another script
    importing it as a library, would otherwise have ITS OWN stdout/stderr
    silently reconfigured as a side effect of the import alone, violating
    this repo's own script import-side-effect rule. See the comment this
    function's body carries forward for why line-buffering matters at all.
    """
    # A round-20 diagnostic-visibility fix: a real CI incident's failure
    # mode showed `lint-and-types`' `ruff check`/`mypy` steps reporting
    # "failed" with ZERO diagnostic output in the CI log -- no error lines
    # at all, just an instant nonzero exit. GitHub Actions runners capture
    # step output through a redirected, non-tty pipe rather than a pty;
    # Python's default stdout buffering is fully-buffered (not
    # line-buffered) for a pipe, so an unflushed buffer at process exit can
    # genuinely lose real output. Reconfiguring this script's own stdout to
    # line-buffer, on top of always echoing an already-captured
    # subprocess's stdout/stderr in run_step() below (rather than relying
    # on inherited-fd passthrough alone), closes that whole class of
    # "silently lost diagnostic output" regardless of whether it was this
    # specific mystery's root cause -- never silently losing a failing
    # check's own error text is a durable improvement on its own.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)


def _isolated_module_command(*mod_args: str) -> tuple[str, ...]:
    return (sys.executable, "-I", str(_MODULE_RUNNER), *mod_args)


FAST = "fast"
PR = "pr"
FULL = "full"
PROFILES = (FAST, PR, FULL)


def _which_any(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _origin_main_available() -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return (
            "no origin/main ref available locally (shallow clone or detached checkout)"
        )
    return None


def _need_bins(*names: str) -> Callable[[], str | None]:
    def check() -> str | None:
        if _which_any(*names) is None:
            return f"none of {', '.join(names)} found on PATH"
        return None

    return check


def _need_all_bins(*names: str) -> Callable[[], str | None]:
    """Like :func:`_need_bins`, but every name must be present, not just one.

    ``_need_bins``'s "any of" semantics fit a single-tool-with-aliases check
    (``abi-compliance-checker``/``.pl``); a step needing several genuinely
    different tools together (e.g. ``clang``/``clang++``/``g++``) needs
    "all of", not "at least one of".
    """

    def check() -> str | None:
        missing = [n for n in names if shutil.which(n) is None]
        if missing:
            return f"not found on PATH: {', '.join(missing)}"
        return None

    return check


def _need_linux_and_all_bins(*names: str) -> Callable[[], str | None]:
    """Like :func:`_need_all_bins`, but also requires a Linux host.

    A step whose *script* self-restricts to Linux (its own module docstring,
    not something this precondition duplicates) would otherwise still report
    `passed` on a macOS/Windows host that happens to have every named tool on
    PATH — the precondition would pass, the step would run, and the script's
    own internal platform check would exit 0 immediately having measured
    nothing, which `run_step()` cannot tell apart from genuine work (Codex
    review: this is exactly the gap `--json-out`'s `complete` receipt field
    exists to catch, and a step-level false pass defeats it at the source).
    """
    bins_check = _need_all_bins(*names)

    def check() -> str | None:
        if not sys.platform.startswith("linux"):
            return f"Linux only (host platform is {sys.platform!r})"
        return bins_check()

    return check


def _need_modules(*names: str) -> Callable[[], str | None]:
    def check() -> str | None:
        missing = [n for n in names if not _module_available(n)]
        if missing:
            return f"module(s) not installed: {', '.join(missing)}"
        return None

    return check


def _py(*mod_args: str) -> tuple[str, ...]:
    """Invoke an installed Python tool without repository-root shadowing.

    For ``pytest``/``mypy``/``ruff``/``mkdocs`` — packages that expose a
    `python -m <name>` entry point. Never a bare command name: PATH can
    resolve those to a *different* install than the one `pip install -e
    ".[dev]"` put on this interpreter (e.g. a stray user-level `pytest`
    missing the `xdist`/`cov` plugins) — the exact reproducibility gap M0-3
    exists to close, just one layer lower than the mypy-version drift it was
    written for.

    The interpreter starts under ``-I`` (isolated mode), so a PR-planted
    repository-root ``pytest.py``/``mypy.py``/``ruff.py`` cannot be imported
    in place of the real, installed tool during ``-m`` module lookup (P0.3,
    security hardening) — CI running these checks with ``cwd=ROOT`` was
    otherwise exactly the shadowing vector. ``run_isolated_module.py`` then
    restores the base interpreter's/venv's normal user-site visibility so a
    ``pip install --user`` tool remains resolvable. For a local
    ``scripts/*.py`` file (not an importable module), use `_pyscript`
    instead — `-m` takes a dotted module name, not a file path.
    """
    return _isolated_module_command(*mod_args)


def _pyscript(path: str, *args: str) -> tuple[str, ...]:
    """Invoke a local script file with the same interpreter running verify.py."""
    return (sys.executable, path, *args)


#: `check_bugfix_test_contract.py` exits 2 when its structural half passed but
#: no PR body was available, so the declared half never ran. Mapping that to a
#: skip is what keeps a local `--profile pr` from claiming CI parity over half
#: a gate, while still running the half that does not need a body.
_BUGFIX_CONTRACT_PARTIAL = {
    2: (
        "BUGFIX_CONTRACT_BODY_FILE is unset, so the declared half could not "
        "run (the structural half did) — set it to a file holding the PR "
        "description"
    ),
    # A second code, because the two situations need different remediation
    # and checked different amounts. Reporting the body-file reason for a
    # missing base ref sent the reader to the wrong fix and claimed the
    # structural half had run when nothing had (Codex review).
    3: (
        "the base ref could not be resolved, so nothing was checked — fetch "
        "the base branch (`git fetch origin main`)"
    ),
}


@dataclass(frozen=True)
class Step:
    name: str
    cmd: tuple[str, ...]
    profiles: frozenset[str]
    env: dict[str, str] = field(default_factory=dict)
    precondition: Callable[[], str | None] | None = None
    #: ``{returncode: reason}``: a step that ran but could only do part of its
    #: job reports one of these codes, and this maps it to a skip so the
    #: profile-level completeness contract sees it. A mapping rather than one
    #: pair because a step can be partial for more than one reason, and the
    #: receipt has to name the right one. Distinct from `precondition`, which
    #: decides *before* running and therefore runs nothing at all.
    partial: dict[int, str] | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Step catalog — the single source of truth for local/CI check commands.
# ---------------------------------------------------------------------------

STEPS: tuple[Step, ...] = (
    Step(
        "lint",
        _py("ruff", "check", "abicheck/", "tests/"),
        frozenset({FAST, PR, FULL}),
        description="Ruff lint",
    ),
    Step(
        "fmt-check",
        _py("ruff", "format", "--check", "abicheck/", "tests/"),
        frozenset({FAST, PR, FULL}),
        description="Ruff format check",
    ),
    Step(
        "typecheck",
        _py("mypy", "abicheck/"),
        frozenset({FAST, PR, FULL}),
        description="mypy (pinned per pyproject.toml [dev])",
    ),
    Step(
        "unit-fast",
        _py(
            "pytest",
            "tests/",
            "-m",
            "not integration and not libabigail and not abicc and not slow and not golden",
            "-q",
        ),
        frozenset({FAST}),
        description="Fast unit lane — matches the documented go-to command",
    ),
    Step(
        "ai-readiness",
        _pyscript("scripts/check_ai_readiness.py"),
        frozenset({PR, FULL}),
        description="Structural readiness gate (file size, ChangeKind partition, import cycles, mypy drift, ...)",
    ),
    Step(
        "architecture",
        _pyscript("scripts/check_architecture.py"),
        frozenset({PR, FULL}),
        description="ADR-061 responsibility packages, dependency direction, and debt no-growth gate",
    ),
    Step(
        "unit-pr",
        _py(
            "pytest",
            "tests/",
            "--tb=short",
            "-m",
            "not integration and not libabigail and not abicc and not slow",
            "-n",
            "auto",
            "--dist",
            "worksteal",
            "--cov=abicheck",
            "--cov-report=term-missing",
            "--cov-fail-under=95",
        ),
        frozenset({PR, FULL}),
        env={"COVERAGE_CORE": "sysmon"},
        description="Canonical Linux/3.13 unit-tests CI lane, incl. golden + 95% coverage floor",
    ),
    Step(
        "bugfix-test-contract",
        # Fixed argv on purpose: CI passes the real PR refs and body through
        # BUGFIX_CONTRACT_{BASE,HEAD,BODY_FILE}, and a local run falls back to
        # origin/main..HEAD with no body, exercising the structural half. Both
        # go through this one step rather than the workflow calling the script
        # directly, so `--profile pr` locally cannot pass while the CI-only
        # contract fails later (AGENTS.md "M0-3", Codex review).
        _pyscript("scripts/check_bugfix_test_contract.py"),
        frozenset({PR, FULL}),
        partial=_BUGFIX_CONTRACT_PARTIAL,
        description="Bug-fix test contract (structural half locally, declared half in CI)",
    ),
    Step(
        "fp-rate",
        _pyscript("scripts/check_fp_rate.py"),
        frozenset({PR, FULL}),
        description="Scoping FP-rate gate (ADR-024 §7)",
    ),
    Step(
        "tier-accuracy",
        _pyscript("scripts/check_tier_accuracy.py"),
        frozenset({PR, FULL}),
        description="Per-tier accuracy gate",
    ),
    Step(
        "usecase-docs-sync",
        _pyscript("scripts/check_usecase_docs_sync.py"),
        frozenset({PR, FULL}),
        description="Use-case registry vs. human docs drift gate",
    ),
    Step(
        # The learning-series hub's step list and role-path table are rendered
        # from docs/_meta/learning-ladder.yaml; this is the drift half only
        # (the ladder's rules run inside docs-contract).
        "learning-ladder",
        _pyscript("scripts/gen_learning_ladder.py", "--check"),
        frozenset({PR, FULL}),
        description="Learning-series hub ladder/paths blocks in sync with docs/_meta/learning-ladder.yaml",
    ),
    Step(
        "docs-contract",
        _pyscript("scripts/check_docs_contract.py"),
        frozenset({PR, FULL}),
        description="docs/AGENTS.md ownership contract: topics.yaml integrity, front-matter schema, duplicate-block scan",
    ),
    Step(
        # The generated-doc drift contract (docs/AGENTS.md, "regenerating
        # generated docs") applied to ADR-058's new artifact family: the three
        # committed publication trees must reproduce from skills-src/. Checks
        # all three, not just the authoritative .agents/skills/ one — a
        # rewrite bug reachable only on one emission path would otherwise pass
        # indefinitely.
        "agent-skills-generated",
        _pyscript("scripts/gen_agent_skills.py", "--check"),
        frozenset({PR, FULL}),
        description="Generated agent-skill trees match skills-src/ (ADR-058)",
    ),
    Step(
        "repo-facts",
        _pyscript("scripts/gen_repo_facts.py", "--check"),
        frozenset({PR, FULL}),
        description="repo_facts.json freshness (test/example counts, version) — CLAUDE.md M1-4",
    ),
    Step(
        # G37 Phase 0. Two steps rather than one, because they fail for
        # different reasons and want different fixes: this one means "the pack
        # no longer describes the repository" (re-run the generator), while
        # skill-eval-freshness below means "the committed evidence no longer
        # describes the pack" (re-run the evaluation). Collapsing them would
        # report the cheap fix and the expensive one under one name.
        "skill-eval-pack",
        _pyscript("scripts/gen_skill_eval_pack.py", "--check"),
        frozenset({PR, FULL}),
        description="skill-eval-pack.json matches its generator (G37 D6)",
    ),
    Step(
        # No model runs here (G37 D2) — this reads committed hashes and
        # committed bundles, so it is bit-for-bit reproducible and costs
        # nothing. That is the whole reason it can be a required check.
        "skill-eval-freshness",
        _pyscript("scripts/check_skill_eval_freshness.py"),
        frozenset({PR, FULL}),
        description="Skill-eval evidence is fresh, every hash maps to a skill, every observed input is hashed (G37 D6)",
    ),
    Step(
        # Same generated-artifact contract as skill-eval-pack above, for the
        # Harbor task battery it derives from: a scenario/fixture change that
        # doesn't also regenerate agent-evals/skills/harbor/tasks/ leaves the
        # committed tasks describing a stale corpus.
        "harbor-tasks",
        _pyscript("scripts/gen_harbor_tasks.py", "--check"),
        frozenset({PR, FULL}),
        description="agent-evals/skills/harbor/tasks/ matches its generator",
    ),
    Step(
        # harbor-tasks above only re-derives the tree structurally -- it
        # never asks whether the result is actually a *valid* Harbor task.
        # `harbor` needs Python >=3.12 and is not a repository dependency
        # (it exists to validate output *for* Harbor, not to be one), so
        # this is FULL-only, gated on the module being importable, same
        # shape as docs-build's `_need_modules("mkdocs")` above.
        "harbor-schema",
        _py(
            "pytest",
            "tests/test_gen_harbor_tasks.py",
            "-k",
            "TestHarborSchemaValidation",
            "--tb=short",
        ),
        frozenset({FULL}),
        env={"ABICHECK_MIN_EXECUTED": "1"},
        precondition=_need_modules("harbor"),
        description="Generated Harbor tasks validate against the real harbor package's Task/TaskConfig schema",
    ),
    Step(
        # FULL only, NOT PR: this step's precondition depends on origin/main
        # being locally resolvable, which is a checkout-topology fact (shallow
        # clone, detached HEAD, a fresh CI checkout without an explicit
        # `git fetch origin main`) rather than a "missing tool" a contributor
        # can just install — so it doesn't fit the pr-profile's
        # skip-means-incomplete contract (Codex review, PR #604). It also
        # isn't redundant to drop from `pr`: the actual required CI gate for
        # this check is the separate `changelog-check.yml` workflow, which
        # always passes real PR base/head SHAs from the GitHub event rather
        # than relying on a local `origin/main` ref — `verify.py --profile pr`
        # was never how this check runs in CI. Kept in `full` as a
        # best-effort local convenience for contributors who do have
        # origin/main fetched.
        "changelog-fragment",
        _pyscript("scripts/check_changelog_fragment.py"),
        frozenset({FULL}),
        precondition=_origin_main_available,
        description="changelog.d/ fragment gate for abicheck/**/*.py diffs (local convenience; changelog-check.yml is the real CI gate)",
    ),
    Step(
        "schema-sync",
        _pyscript("scripts/publish_schemas.py", "--check"),
        frozenset({PR, FULL}),
        description="Published JSON-schema copies match the generators",
    ),
    Step(
        "fair-metadata",
        _pyscript("scripts/check_fair_metadata.py"),
        frozenset({PR, FULL}),
        description="FAIR/codemeta/CITATION metadata gate",
    ),
    Step(
        "docs-build",
        _py("mkdocs", "build", "--strict"),
        frozenset({PR, FULL}),
        precondition=_need_modules("mkdocs"),
        description="mkdocs strict build (dangling refs, nav coverage)",
    ),
    Step(
        # ABICHECK_MIN_EXECUTED (tests/conftest.py's silent-skip guard, also
        # used by every marker lane in ci.yml): `castxml` being on PATH
        # doesn't guarantee gcc/g++ is too — without this, a partial
        # toolchain could let pytest collect the `integration` marker, skip
        # every single test, and still exit 0, which `run_step` would then
        # report as "passed" having verified nothing. '1' (not CI's Linux
        # '20') because this step runs on whatever OS/toolchain combination
        # the caller has — the guard's job here is "did anything run at
        # all", not asserting a platform-specific count.
        "integration",
        _py("pytest", "tests/", "-m", "integration", "--tb=short"),
        frozenset({FULL}),
        env={"ABICHECK_MIN_EXECUTED": "1"},
        precondition=_need_bins("castxml"),
        description="DWARF/header parsing against real castxml + a C/C++ compiler",
    ),
    Step(
        # Marker-scoped over all of tests/ (not a hardcoded file list): matches
        # `pixi run -e parity test-libabigail` — a hardcoded list silently misses
        # any file added later that also carries @pytest.mark.libabigail (this
        # already happened: tests/test_abidiff_parity_extended.py and
        # tests/test_surface_scope_parity.py both carry the marker but were never
        # in the old 3-file list). ABICHECK_MIN_EXECUTED='5' matches the floor
        # ci.yml's libabigail-parity job uses for the same marker-scoped run.
        "libabigail-parity",
        _py("pytest", "tests/", "-m", "libabigail", "--tb=short"),
        frozenset({FULL}),
        env={"ABICHECK_MIN_EXECUTED": "5"},
        precondition=_need_bins("abidiff"),
        description="libabigail parity lane (marker-scoped)",
    ),
    Step(
        # Marker-scoped for the same reason as libabigail-parity above —
        # tests/test_abicc_parity_extended.py also carries @pytest.mark.abicc.
        # ABICHECK_MIN_EXECUTED='10' matches ci.yml's abicc-parity job floor.
        "abicc-parity",
        _py("pytest", "tests/", "-m", "abicc", "--tb=short"),
        frozenset({FULL}),
        env={"ABICHECK_MIN_EXECUTED": "10"},
        precondition=_need_bins("abi-compliance-checker", "abi-compliance-checker.pl"),
        description="ABICC parity lane (marker-scoped)",
    ),
    Step(
        "slow",
        # -n auto --dist worksteal, matching ci.yml's "Run slow tests" step:
        # most `slow`-marked tests here are independent (Hypothesis/property-
        # based suites plus the production-scale snapshot-compression round
        # trips), so a serial run was pure wasted wall time on a multi-core
        # runner/machine and left this local step unable to reproduce CI's
        # actual timing behavior (Codex review, PR #1036).
        # tests/test_performance.py, tests/test_perf_binary_scan.py, AND
        # tests/test_header_scan_deadline_integration.py are excluded here
        # and covered by the separate "slow-perf" step below, run serially --
        # see that step's own comment for why (Codex review, same PR, three
        # rounds: the second and third files each caught in their own
        # follow-up round).
        _py(
            "pytest",
            "tests/",
            "-m",
            "slow",
            "--ignore=tests/test_performance.py",
            "--ignore=tests/test_perf_binary_scan.py",
            "--ignore=tests/test_header_scan_deadline_integration.py",
            "--tb=short",
            "-n",
            "auto",
            "--dist",
            "worksteal",
        ),
        frozenset({FULL}),
        description="Hypothesis / perf-benchmark tests (parallel; excludes wall-clock-timed tests)",
    ),
    Step(
        "slow-perf",
        # Deliberately NOT parallelized, unlike "slow" above: every test in
        # tests/test_performance.py, tests/test_perf_binary_scan.py, and
        # tests/test_header_scan_deadline_integration.py (whole-file
        # `pytestmark = pytest.mark.slow` in the first two; the third has one
        # `@pytest.mark.slow` test,
        # test_pathological_header_natural_cost_is_tracked, with its own 60s
        # ceiling) measures real wall-clock time against a fixed budget
        # (2s/5s/30s, 3s/5s and 30s/45s, and 60s respectively) or fits a
        # scaling exponent -- running them concurrently with other CPU-heavy
        # tests makes scheduler contention part of the measurement, which can
        # fail (or distort) the gate with no actual product regression.
        # ci.yml's "Run slow tests" step keeps all three files serial,
        # together, for the identical reason (Codex review, PR #1036);
        # performance.yml's own dedicated job only covers the first two --
        # an acknowledged, separate gap in that workflow, not fixed here.
        _py(
            "pytest",
            "tests/test_performance.py",
            "tests/test_perf_binary_scan.py",
            "tests/test_header_scan_deadline_integration.py",
            "-m",
            "slow",
            "--tb=short",
        ),
        frozenset({FULL}),
        description='Wall-clock-timed perf-benchmark tests (serial, unlike "slow")',
    ),
    Step(
        # Report-only (no --baseline): a single-checkout, single-Step run has
        # no PR-vs-base comparison to make (that half lives in
        # performance.yml's own header-graph-regression job, which spans two
        # checkouts/venvs in one workflow run — structurally not expressible
        # as one verify.py Step, the same reason benchmark_scaling.py's own
        # sibling `regression` job was never routed through here either).
        # This step's job is closing the *other* gap: without it, `verify.py
        # --profile full` could never even run check_header_graph_perf.py at
        # all (Codex review, fresh evidence).
        "header-graph-perf",
        _pyscript(
            "scripts/check_header_graph_perf.py",
            "--sizes",
            "25",
            "100",
            "--require-castxml",
        ),
        frozenset({FULL}),
        # castxml must actually be present here: without it in the
        # precondition, a clang-only host would silently "pass" having
        # measured only a subset of the real always-on attach cost, rather
        # than genuinely skipping this step -- and --require-castxml above
        # then turns a *present-but-out-of-policy* castxml into a hard
        # failure instead of a silent skip, the same distinction the CI
        # jobs draw (Codex review, fresh evidence).
        precondition=_need_linux_and_all_bins("clang", "clang++", "g++", "castxml"),
        description="Header-graph attach-cost trend measurement (G31 Phase D, report-only)",
    ),
    Step(
        # The build/source workflow produces the fixed artifact immediately
        # before invoking this catalog step. Keeping the checker here makes
        # the local full-profile command and the required CI proof use the
        # same named verification contract.
        "build-source-release-proof",
        _pyscript("scripts/check_build_source_release_proof.py"),
        frozenset({FULL}),
        description="Fixed 10-case build/source release-proof artifact gate",
    ),
    Step(
        "mutation",
        _pyscript("scripts/check_mutation_score.py"),
        frozenset({FULL}),
        precondition=_need_bins("mutmut"),
        description="Mutation-score survivor-baseline gate",
    ),
    Step(
        # Builds dist/ itself (`python -I -m build`), then `twine check`s the
        # result, then validates its metadata — self-contained, so this step
        # doesn't require a caller to have already populated dist/.
        #
        # In PR, not just FULL: ci.yml's `fair-metadata` job runs this
        # unconditionally on every PR (no path filter) — it's a required
        # check, not an optional parity/external-tool lane, so `pr` must
        # include it to actually be CI-equivalent. Its precondition still
        # lets it skip gracefully (flagged via the pr-profile incomplete-run
        # warning above) rather than forcing every contributor to have
        # `build`/`twine` installed just to run `--profile pr`.
        "distribution-build",
        _pyscript("scripts/build_and_check_distribution.py"),
        frozenset({PR, FULL}),
        precondition=_need_modules("build", "twine"),
        description="Build sdist/wheel, twine check, validate metadata",
    ),
)


def steps_for(profile: str, only: set[str] | None, skip: set[str]) -> list[Step]:
    selected = [s for s in STEPS if profile in s.profiles]
    if only:
        # Validated against `selected` (this profile's steps), NOT the global
        # catalog: a name that exists globally but not in `--profile
        # <profile>` (e.g. `--profile pr --only libabigail-parity`, a
        # full-only step) would otherwise silently vanish from the run
        # instead of erroring — `--only` is an explicit request, so a step
        # that can't be honored must fail loudly, not produce a quietly
        # smaller "complete" run (Codex review, PR #604).
        in_profile_names = {s.name for s in selected}
        unknown = only - in_profile_names
        if unknown:
            out_of_profile = unknown & {s.name for s in STEPS}
            unknown_entirely = unknown - out_of_profile
            parts = []
            if out_of_profile:
                parts.append(
                    f"not in --profile {profile}: {', '.join(sorted(out_of_profile))}"
                )
            if unknown_entirely:
                parts.append(f"no such step: {', '.join(sorted(unknown_entirely))}")
            raise SystemExit(f"--only: {'; '.join(parts)}")
        selected = [s for s in selected if s.name in only]
    if skip:
        unknown = skip - {s.name for s in STEPS}
        if unknown:
            raise SystemExit(
                f"--skip: unknown step name(s): {', '.join(sorted(unknown))}"
            )
        selected = [s for s in selected if s.name not in skip]
    return selected


def _git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def run_step(step: Step) -> dict[str, object]:
    if step.precondition is not None:
        reason = step.precondition()
        if reason is not None:
            print(f"\n=== {step.name} === SKIPPED ({reason})")
            return {
                "name": step.name,
                "status": "skipped",
                "reason": reason,
                "duration_s": 0.0,
            }

    print(f"\n=== {step.name} === {' '.join(step.cmd)}", flush=True)
    start = time.time()
    env = {**os.environ, **step.env}
    # Diagnostic instrumentation (round 20, Part B) -- capture_output=True
    # instead of the previous bare subprocess.run(...) (which relied on the
    # child inheriting this process's stdout/stderr fds directly). Explicitly
    # capturing and then unconditionally re-printing the child's own
    # stdout/stderr here, regardless of exit status, means a failing step's
    # diagnostic output can never be silently lost to an intermediate
    # buffering/redirection layer between the child and whatever ultimately
    # renders this script's output (e.g. a CI runner's non-tty log pipe) --
    # see this module's own top-of-file comment for the full reasoning. This
    # also means a caller with `--json` gets the raw text available even when
    # the terminal itself scrolled it out of view.
    proc = subprocess.run(step.cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    duration = time.time() - start
    # Echo the child's captured stdout/stderr BEFORE the partial-result
    # early return below (CodeRabbit review, fresh evidence): this used to
    # run only after the `step.partial` check, so a step that returns a
    # PARTIAL result (see `_BUGFIX_CONTRACT_PARTIAL`) never got its own
    # diagnostic output printed at all -- defeating half the point of the
    # round-20 diagnostic-visibility fix (this function's own top comment)
    # for exactly the steps most likely to need it (a step whose exit code
    # is ambiguous enough to be classified PARTIAL is also the step whose
    # own stdout/stderr is most useful for a human to actually see).
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n", flush=True)
    if proc.stderr:
        print(
            proc.stderr,
            end="" if proc.stderr.endswith("\n") else "\n",
            file=sys.stderr,
            flush=True,
        )
    if step.partial is not None and proc.returncode in step.partial:
        reason = step.partial[proc.returncode]
        print(f"=== {step.name}: PARTIAL ({duration:.1f}s) — {reason} ===")
        return {
            "name": step.name,
            "status": "skipped",
            "reason": reason,
            "duration_s": round(duration, 1),
            "returncode": proc.returncode,
        }
    status = "passed" if proc.returncode == 0 else "failed"
    print(f"=== {step.name}: {status} ({duration:.1f}s) ===", flush=True)
    return {
        "name": step.name,
        "status": status,
        "duration_s": round(duration, 1),
        "returncode": proc.returncode,
    }


def main(argv: Sequence[str] | None = None) -> int:
    _enable_line_buffered_output()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--profile", choices=PROFILES, default=FAST, help="Which check bundle to run"
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated step names to run (subset of the profile)",
    )
    parser.add_argument("--skip", default="", help="Comma-separated step names to skip")
    parser.add_argument(
        "--list", action="store_true", help="List the steps for --profile and exit"
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        default=None,
        help="Write a JSON verification receipt to PATH",
    )
    args = parser.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    selected = steps_for(args.profile, only or None, skip)

    if not selected:
        # --only/--skip narrowed the profile down to nothing — almost always a
        # typo or a step name that doesn't belong to --profile. Fail loudly
        # instead of silently reporting an empty "passed" run.
        raise SystemExit(
            f"--profile {args.profile}: no steps selected after applying "
            f"--only/--skip (did you name a step that belongs to a different "
            f"profile? run --list to see this profile's steps)"
        )

    if args.list:
        for s in selected:
            print(f"{s.name}\t{' '.join(s.cmd)}\t{s.description}")
        return 0

    results = []
    for step in selected:
        results.append(run_step(step))

    n_passed = sum(1 for r in results if r["status"] == "passed")
    n_failed = sum(1 for r in results if r["status"] == "failed")
    n_skipped = sum(1 for r in results if r["status"] == "skipped")
    skipped_names = [str(r["name"]) for r in results if r["status"] == "skipped"]

    # `pr` steps are documented as "always-required CI-equivalent checks" — a
    # skip here (missing tool/module, e.g. mkdocs not installed) means this
    # run did NOT reproduce everything the real PR gate checks. Unlike `full`
    # (where skip-on-missing-tool is the deliberate, expected design), that
    # makes a `pr`-profile run genuinely incomplete, not just imperfect — so
    # it fails, the same as `n_failed`, rather than merely warning. A partial
    # result must never exit 0 and be mistaken for a complete one.
    incomplete = args.profile == PR and n_skipped > 0
    overall = "failed" if n_failed else "incomplete" if incomplete else "passed"

    print(
        f"\nverify.py --profile {args.profile}: {n_passed} passed, {n_failed} failed, {n_skipped} skipped"
    )
    if incomplete:
        print(
            f"WARNING: this `pr`-profile run is INCOMPLETE — skipped "
            f"{', '.join(skipped_names)}. It is not a full substitute for CI "
            f"until the missing tool(s)/module(s) are installed. Treating this "
            f"as a failure (exit 1), not a pass."
        )

    if args.json:
        receipt = {
            "profile": args.profile,
            "commit": _git_commit(),
            "complete": n_skipped == 0,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "checks": results,
            "skipped_capabilities": skipped_names,
            "overall": overall,
        }
        Path(args.json).write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        print(f"receipt written to {args.json}")

    return 1 if n_failed or incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
