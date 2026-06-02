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

# mutmut summary lines use emoji status markers (the legend is the same in
# 2.x/3.x): 🎉 killed, 🙁 survived, ⏰ timeout, 🤔 suspicious, 🫥 no-tests
# (no covering test), 🔇 skipped (intentionally excluded). We also accept
# plain-text forms so the parser is resilient to version and locale differences.
#
# The gate only ever acts on an *explicit* parsed count — it never infers a
# result from an exit code or from progress text. That is deliberate: mutmut's
# run can exit 0 while still having unresolved (timeout/suspicious/no-tests)
# mutants, and a non-zero exit can mean either survivors or an abort, so neither
# the exit code nor a "309/464" progress token is trustworthy evidence of a
# clean zero-survivor measurement.
_EMOJI_SURVIVED = re.compile(r"🙁\s*(\d+)")
_WORD_SURVIVED_COUNT = re.compile(r"(\d+)\s+survived\b", re.IGNORECASE)
_LINE_SURVIVED = re.compile(r":\s*survived\b", re.IGNORECASE)

# Non-killed, non-survived statuses that mean the measurement is *incomplete*:
# the mutant was neither killed nor confirmed surviving. Accepting these as
# "zero survivors" would let an under-resolved run pass a zero baseline.
# Note: 🫥 is "no tests" (an uncovered mutant — a real gap, counted here); 🔇 is
# "skipped" (intentionally excluded — NOT counted as unresolved).
_EMOJI_TIMEOUT = re.compile(r"⏰\s*(\d+)")
_EMOJI_SUSPICIOUS = re.compile(r"🤔\s*(\d+)")
_EMOJI_NO_TESTS = re.compile(r"🫥\s*(\d+)")


def parse_survivors(text: str) -> int | None:
    """Extract the number of surviving mutants from ``mutmut`` output.

    Returns ``None`` when the text carries no recognizable survivor signal
    (e.g. mutmut errored or produced an unexpected format), so callers can tell
    "zero survivors" apart from "could not measure". A clean run still prints an
    explicit ``🙁 0`` in its summary, so zero is detected as zero — not inferred.
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


def count_unresolved(text: str) -> int:
    """Number of mutants that are neither killed nor survived (timeout /
    suspicious / no-tests). A run with these did not fully resolve, so it must
    not be accepted as a clean zero-survivor measurement."""
    total = 0
    for pat in (_EMOJI_TIMEOUT, _EMOJI_SUSPICIOUS, _EMOJI_NO_TESTS):
        m = pat.search(text)
        if m:
            total += int(m.group(1))
    return total


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=7200
    )
    return proc.stdout + proc.stderr


def _gather_results(args: argparse.Namespace) -> str | None:
    """Return mutmut's output text, or ``None`` if none could be obtained.

    Under ``--run`` we combine ``mutmut run``'s summary (which carries the
    🙁/⏰/🤔 counts, including an explicit ``🙁 0`` on a clean run) with
    ``mutmut results``. The run's exit code is intentionally not used as a
    success signal — see the parsing-comment above.
    """
    if args.results_file:
        if args.results_file == "-":
            return sys.stdin.read()
        try:
            with open(args.results_file, encoding="utf-8") as fh:
                return fh.read()
        except OSError as e:
            print(f"ERROR: cannot read --results-file: {e}")
            return None

    if shutil.which("mutmut") is None:
        print("mutation-score: mutmut not installed, skipping")
        return None

    combined = ""
    if args.run:
        print("mutation-score: running `mutmut run` (this is slow)…")
        combined += _run(["mutmut", "run"]) + "\n"
    combined += _run(["mutmut", "results"])
    return combined


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

    text = _gather_results(args)
    if text is None:
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

    survivors = parse_survivors(text)
    if survivors is None:
        print("mutation-score: could not parse survivor count from mutmut output")
        # No explicit count means we did not get a usable measurement — an
        # aborted/interrupted run, never an inferred zero. Fail under --run;
        # only skip when we were merely reading a file / reporting.
        return 1 if args.run else 0

    unresolved = count_unresolved(text)
    baseline = args.baseline if args.baseline is not None else SURVIVOR_BASELINE
    msg = f"mutation-score: {survivors} surviving mutant(s)"
    if unresolved:
        msg += f", {unresolved} unresolved (timeout/suspicious/no-tests)"
    print(msg)

    # Unresolved mutants mean the run did not fully resolve, so even zero
    # survivors is not a clean pass once we are gating against a baseline.
    if baseline is not None and unresolved > 0:
        print(
            f"ERROR: {unresolved} mutant(s) did not resolve (timeout/suspicious/"
            "no-tests) — the measurement is incomplete; fix or silence them so "
            "the survivor count is trustworthy."
        )
        return 1

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
