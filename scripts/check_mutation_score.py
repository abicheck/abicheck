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

"""Mutation-score gate — baseline-drift check for the core detector modules.

Mutation testing is the direct answer to "are these tests generalized, or do
they just execute lines without checking the result?". ``mutmut`` mutates the
detector logic (the modules listed under ``[tool.mutmut]`` in pyproject.toml)
and re-runs the suite; a *surviving* mutant is a line that is covered but not
actually verified by any assertion — exactly the coverage-filling failure mode.

This script runs (or reads) ``mutmut`` results, counts survivors, and compares
them to a documented baseline, the same way ``check_ai_readiness.py`` guards
the mypy error count:

* survivors **above** the baseline  -> ERROR (a test regressed / weakened);
* survivors **below** the baseline  -> note to lower the baseline deliberately;
* baseline **unset**                -> report-only (used to establish the first
  number on a scheduled run, since a full mutmut pass is too slow for every PR).

Because a full mutation run is minutes-to-hours, this is wired as a scheduled /
on-demand lane (``.github/workflows/mutation.yml``), not a per-PR gate.

Usage::

    # Run mutmut then check (CI, scheduled):
    python scripts/check_mutation_score.py --run --baseline 0

    # Check an existing run's output:
    mutmut results | python scripts/check_mutation_score.py --results-file -

The survivor-count *parser* is pure and unit-tested
(``tests/test_mutation_score_gate.py``) so the gate logic stays correct even on
machines without mutmut installed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

# Documented baseline. ``None`` means "not yet established" — the gate reports
# the survivor count but does not fail, so the first scheduled run can record a
# number here. Once set, raise it only deliberately (with justification), the
# same discipline as MYPY_ERROR_BASELINE.
SURVIVOR_BASELINE: int | None = None

# mutmut summary lines use emoji status markers; 🙁 is the "survived" bucket in
# mutmut 2.x/3.x. We also accept plain-text forms so the parser is resilient to
# version and locale differences.
_EMOJI_SURVIVED = re.compile(r"🙁\s*(\d+)")
_WORD_SURVIVED_COUNT = re.compile(r"(\d+)\s+survived\b", re.IGNORECASE)
_LINE_SURVIVED = re.compile(r":\s*survived\b", re.IGNORECASE)


def parse_survivors(text: str) -> int | None:
    """Extract the number of surviving mutants from ``mutmut`` output.

    Returns ``None`` when the text carries no recognizable survivor signal
    (e.g. mutmut errored or produced an unexpected format), so callers can tell
    "zero survivors" apart from "could not measure".
    """
    if not text or not text.strip():
        return None
    m = _EMOJI_SURVIVED.search(text)
    if m:
        return int(m.group(1))
    m = _WORD_SURVIVED_COUNT.search(text)
    if m:
        return int(m.group(1))
    # Fall back to counting per-mutant "<id>: survived" lines.
    line_hits = _LINE_SURVIVED.findall(text)
    if line_hits:
        return len(line_hits)
    return None


def _run(cmd: list[str]) -> tuple[str, int]:
    """Run *cmd*, returning ``(combined_output, returncode)``."""
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=7200
    )
    return proc.stdout + proc.stderr, proc.returncode


def _gather_results(args: argparse.Namespace) -> tuple[str, int | None] | None:
    """Return ``(output_text, run_exit_code)``.

    ``run_exit_code`` is the exit status of ``mutmut run`` when we invoked it,
    else ``None`` (results-file / no ``--run``). A ``None`` *return* means no
    output could be obtained at all.

    Using the run's exit code — rather than scraping progress text — is what
    lets the caller tell a clean, complete run (mutmut exits 0 only when every
    mutant was killed) apart from a run that was interrupted after printing
    progress like ``309/464`` but before finishing.
    """
    if args.results_file:
        if args.results_file == "-":
            return sys.stdin.read(), None
        try:
            with open(args.results_file, encoding="utf-8") as fh:
                return fh.read(), None
        except OSError as e:
            print(f"ERROR: cannot read --results-file: {e}")
            return None

    if shutil.which("mutmut") is None:
        print("mutation-score: mutmut not installed, skipping")
        return None

    combined = ""
    run_exit: int | None = None
    if args.run:
        print("mutation-score: running `mutmut run` (this is slow)…")
        run_text, run_exit = _run(["mutmut", "run"])
        combined += run_text + "\n"
    results_text, _ = _run(["mutmut", "results"])
    combined += results_text
    return combined, run_exit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Run `mutmut run` before reading results."
    )
    parser.add_argument(
        "--results-file",
        help="Read mutmut results from a file ('-' for stdin) instead of invoking mutmut.",
    )
    parser.add_argument(
        "--baseline",
        type=int,
        default=None,
        help="Override the documented survivor baseline (SURVIVOR_BASELINE).",
    )
    args = parser.parse_args(argv)

    gathered = _gather_results(args)
    if gathered is None:
        # No output at all (mutmut missing / file unreadable). When --run was
        # requested the job's whole purpose is to produce a measurement, so this
        # is a failure, not a silent no-op. Otherwise (report-only / file modes)
        # it is a graceful skip, matching the mypy-baseline behaviour.
        if args.run:
            print(
                "ERROR: --run requested but mutmut produced no output "
                "(not installed / could not start). Failing so the mutation "
                "gate is not a silent no-op."
            )
            return 1
        return 0

    text, run_exit = gathered
    survivors = parse_survivors(text)
    if survivors is None and args.run and run_exit == 0:
        # mutmut exits 0 only when a *complete* run killed every mutant. A
        # perfect run prints no survivor rows, so parse_survivors is None — but
        # the zero exit proves it finished cleanly: that is zero survivors, not
        # a failure to measure. (An interrupted run exits non-zero, so progress
        # text like "309/464" can never be mistaken for completion.)
        survivors = 0
    if survivors is None:
        print("mutation-score: could not parse survivor count from mutmut output")
        # Under --run, no parseable count and a non-zero/unknown run exit means
        # the run did not yield a usable measurement (aborted/interrupted) —
        # fail so the gate is not a silent no-op. Only skip without --run.
        return 1 if args.run else 0

    baseline = args.baseline if args.baseline is not None else SURVIVOR_BASELINE
    print(f"mutation-score: {survivors} surviving mutant(s)")

    if baseline is None:
        print(
            "mutation-score: baseline not yet established — report-only. "
            "Set SURVIVOR_BASELINE in scripts/check_mutation_score.py to this "
            "number to start gating on drift."
        )
        return 0

    if survivors > baseline:
        print(
            f"ERROR: surviving mutants {survivors} exceed baseline {baseline}. "
            "A test was weakened or new under-verified code landed — strengthen "
            "the assertions that should have killed the mutant(s)."
        )
        return 1
    if survivors < baseline:
        print(
            f"mutation-score: {survivors} < baseline {baseline} — please lower "
            "SURVIVOR_BASELINE to lock in the improvement."
        )
    else:
        print(f"mutation-score: OK ({survivors} == baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
