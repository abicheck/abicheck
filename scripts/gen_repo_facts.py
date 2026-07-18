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

"""Single source of truth for volatile repository facts (CLAUDE.md "M1-4").

Several facts drift silently because they're hand-typed in multiple places:
test counts, example-catalog size, and the GitHub Action version examples in
docs point to (`abicheck/abicheck@vX.Y.Z`). This script computes them and
writes `repo_facts.json`; `--check` fails if the committed file disagrees
with a freshly recomputed one (except `generated_utc`/`source_commit`, which
are provenance stamps, not facts to diff).

`fast_test_cases_collected` drifts for reasons that aren't fully understood
and aren't reliably reproducible: observed 20-test gaps between CI's
`ubuntu-latest` runner and two independent local `python3.13 -m venv`
reproductions of the identical `pip install -e ".[dev]"`, with the same
interpreter minor version and identical resolved dependency versions on both
sides. Pinning CI's `ai-readiness` job to the canonical Python
(`canonical_python` below) removes one plausible variable but did not, by
itself, eliminate the gap — so this field is in `_SOFT_DRIFT_FIELDS`:
`--check` prints its drift as a WARN, not an ERROR, and does not fail the
build over it. Every other field still hard-fails on drift.

`latest_release` is deliberately NOT auto-derived from `git describe`/tags:
a shallow CI checkout (the `actions/checkout` default) has no tags, so that
would silently compute a wrong answer instead of a missing one. It is a
maintained field — bump it in the same PR that cuts a release — and this
script only checks it's present, valid semver, and <= `project_version`
(the sanity bound `--check` *can* verify); doc references to it are checked
separately by `scripts/check_ai_readiness.py`'s `action-version-freshness`
check, which treats this file as ground truth.

Run locally:

    python scripts/gen_repo_facts.py            # regenerate repo_facts.json
    python scripts/gen_repo_facts.py --check     # CI mode, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

ROOT = Path(__file__).resolve().parent.parent
FACTS_PATH = ROOT / "repo_facts.json"

# Fields that are provenance stamps, not facts to diff in --check.
_PROVENANCE_FIELDS = frozenset({"generated_utc", "source_commit"})

# Fields whose drift is reported but does NOT fail `--check`. Observed in
# practice: `fast_test_cases_collected` differs between two environments that
# match on Python version AND every installed package version (a GitHub
# Actions ubuntu-latest runner vs. two independent local `python3.13 -m venv`
# reproductions of the exact same `pip install -e ".[dev]"` — same interpreter
# minor version, same dependency versions, still off by 20). Since the cause
# isn't reproducible or attributable to anything a contributor can fix (no
# missing tool, no code diff), hard-failing PRs on it produces pure noise;
# WARN keeps the number informative without blocking merges on an
# environment difference nobody caused.
_SOFT_DRIFT_FIELDS = frozenset({"fast_test_cases_collected"})

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Fast-lane marker expression — must match scripts/verify.py's "unit-fast"
# Step and AGENTS.md's documented command (tests/test_verify_profiles.py
# gates that agreement independently; this script doesn't re-derive it).
_FAST_MARKER = (
    "not integration and not libabigail and not abicc and not slow and not golden"
)

_COLLECTED_RE = re.compile(r"(?:(\d+)/)?(\d+) tests? collected")


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def _example_cases() -> int:
    gt = json.loads(
        (ROOT / "examples" / "ground_truth.json").read_text(encoding="utf-8")
    )
    return len(gt["verdicts"])


def _fast_test_cases_collected() -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-m",
            _FAST_MARKER,
            "--collect-only",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    text = proc.stdout + proc.stderr
    m = _COLLECTED_RE.search(text)
    if not m:
        raise SystemExit(
            f"gen_repo_facts: could not parse pytest --collect-only output:\n{text[-500:]}"
        )
    return int(m.group(1) or m.group(2))


def _git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def compute_facts(*, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute the current facts. `latest_release`/`canonical_python` carry
    forward from *existing* (the committed file) since neither is derivable
    from the checkout — see module docstring."""
    existing = existing or {}
    return {
        "project_version": _project_version(),
        "latest_release": existing.get("latest_release", _project_version()),
        "example_cases": _example_cases(),
        "fast_test_cases_collected": _fast_test_cases_collected(),
        "canonical_python": existing.get("canonical_python", "3.13"),
        "generated_utc": existing.get("generated_utc", "unset"),
        "source_commit": _git_commit(),
    }


def _load_existing() -> dict[str, Any] | None:
    if not FACTS_PATH.is_file():
        return None
    try:
        loaded: dict[str, Any] = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
        return loaded
    except json.JSONDecodeError:
        return None


def _validate(facts: dict[str, Any]) -> list[str]:
    problems = []
    if not _SEMVER_RE.match(str(facts.get("project_version", ""))):
        problems.append(
            f"project_version {facts.get('project_version')!r} is not X.Y.Z semver"
        )
    if not _SEMVER_RE.match(str(facts.get("latest_release", ""))):
        problems.append(
            f"latest_release {facts.get('latest_release')!r} is not X.Y.Z semver"
        )
    else:
        proj = tuple(int(p) for p in str(facts["project_version"]).split("."))
        rel = tuple(int(p) for p in str(facts["latest_release"]).split("."))
        if rel > proj:
            problems.append(
                f"latest_release {facts['latest_release']} is ahead of "
                f"project_version {facts['project_version']} — pyproject.toml "
                "wasn't bumped for a release that already shipped?"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if repo_facts.json is stale (CI mode).",
    )
    args = parser.parse_args(argv)

    existing = _load_existing()
    fresh = compute_facts(existing=existing)

    problems = _validate(fresh)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1

    if args.check:
        if existing is None:
            print(
                f"ERROR: {FACTS_PATH.name} does not exist — run without --check first."
            )
            return 1
        drift = {
            k: (existing.get(k), v)
            for k, v in fresh.items()
            if k not in _PROVENANCE_FIELDS and existing.get(k) != v
        }
        hard_drift = {k: v for k, v in drift.items() if k not in _SOFT_DRIFT_FIELDS}
        soft_drift = {k: v for k, v in drift.items() if k in _SOFT_DRIFT_FIELDS}
        if soft_drift:
            print(f"WARN: {FACTS_PATH.name} has non-blocking drift:")
            for k, (old, new) in sorted(soft_drift.items()):
                print(f"  {k}: committed={old!r} actual={new!r}")
        if hard_drift:
            print(f"ERROR: {FACTS_PATH.name} is stale:")
            for k, (old, new) in sorted(hard_drift.items()):
                print(f"  {k}: committed={old!r} actual={new!r}")
            print(f"Run `python scripts/{Path(__file__).name}` to refresh it.")
            return 1
        print(f"{FACTS_PATH.name} is up to date.")
        return 0

    fresh["generated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    FACTS_PATH.write_text(
        json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {FACTS_PATH.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
