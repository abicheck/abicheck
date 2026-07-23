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
           FP-rate / tier-accuracy / doc-sync / schema/FAIR-metadata gates
           the `ai-readiness` and `fair-metadata` CI jobs run on every PR.
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


def _need_modules(*names: str) -> Callable[[], str | None]:
    def check() -> str | None:
        missing = [n for n in names if not _module_available(n)]
        if missing:
            return f"module(s) not installed: {', '.join(missing)}"
        return None

    return check


def _py(*mod_args: str) -> tuple[str, ...]:
    """Invoke an installed Python console tool as `sys.executable -m <mod_args>`.

    For ``pytest``/``mypy``/``ruff``/``mkdocs`` — packages that expose a
    `python -m <name>` entry point. Never a bare command name: PATH can
    resolve those to a *different* install than the one `pip install -e
    ".[dev]"` put on this interpreter (e.g. a stray user-level `pytest`
    missing the `xdist`/`cov` plugins) — the exact reproducibility gap M0-3
    exists to close, just one layer lower than the mypy-version drift it was
    written for. For a local ``scripts/*.py`` file (not an importable
    module), use `_pyscript` instead — `-m` takes a dotted module name, not a
    file path.
    """
    return (sys.executable, "-m", *mod_args)


def _pyscript(path: str, *args: str) -> tuple[str, ...]:
    """Invoke a local script file with the same interpreter running verify.py."""
    return (sys.executable, path, *args)


@dataclass(frozen=True)
class Step:
    name: str
    cmd: tuple[str, ...]
    profiles: frozenset[str]
    env: dict[str, str] = field(default_factory=dict)
    precondition: Callable[[], str | None] | None = None
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
        "docs-contract",
        _pyscript("scripts/check_docs_contract.py"),
        frozenset({PR, FULL}),
        description="docs/AGENTS.md ownership contract: topics.yaml integrity, front-matter schema, duplicate-block scan",
    ),
    Step(
        "repo-facts",
        _pyscript("scripts/gen_repo_facts.py", "--check"),
        frozenset({PR, FULL}),
        description="repo_facts.json freshness (test/example counts, version) — CLAUDE.md M1-4",
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
        _py("pytest", "tests/", "-m", "slow", "--tb=short"),
        frozenset({FULL}),
        description="Hypothesis / perf-benchmark tests",
    ),
    Step(
        "mutation",
        _pyscript("scripts/check_mutation_score.py"),
        frozenset({FULL}),
        precondition=_need_bins("mutmut"),
        description="Mutation-score survivor-baseline gate",
    ),
    Step(
        # Builds dist/ itself (`python -m build`), then `twine check`s the
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

    print(f"\n=== {step.name} === {' '.join(step.cmd)}")
    start = time.time()
    env = {**os.environ, **step.env}
    proc = subprocess.run(step.cmd, cwd=ROOT, env=env)
    duration = time.time() - start
    status = "passed" if proc.returncode == 0 else "failed"
    print(f"=== {step.name}: {status} ({duration:.1f}s) ===")
    return {
        "name": step.name,
        "status": status,
        "duration_s": round(duration, 1),
        "returncode": proc.returncode,
    }


def main(argv: Sequence[str] | None = None) -> int:
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
