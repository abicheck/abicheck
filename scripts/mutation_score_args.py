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

"""The command-line parser of ``check_mutation_score.py``.

Split out of that gate (at its file-size record) so ``main()`` there could be
broken into phase helpers without growing the file. Pure argparse; no logic.
"""

from __future__ import annotations

import argparse


def build_parser(
    description: str | None, default_baseline_file: str
) -> argparse.ArgumentParser:
    """Return the parser for ``check_mutation_score.py``'s flags."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--run", action="store_true", help="Run `mutmut run` first.")
    parser.add_argument(
        "--results-file", help="Read results from a file ('-' for stdin)."
    )
    parser.add_argument(
        "--mutants-dir",
        default="mutants",
        help="Directory holding mutmut's artifacts (default: mutants).",
    )
    parser.add_argument(
        "--baseline", type=int, default=None, help="Global-total baseline override."
    )
    parser.add_argument(
        "--baseline-file",
        default=default_baseline_file,
        help="Per-module baseline JSON (default: mutation-baseline.json).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the measurement to --baseline-file instead of gating on it.",
    )
    parser.add_argument(
        "--diff-scoped",
        action="store_true",
        help="Fail on any survivor in a function changed vs --base-ref.",
    )
    parser.add_argument(
        "--base-ref", default="origin/main", help="Base ref for --diff-scoped."
    )
    parser.add_argument("--diff-file", help="Read the diff from a file instead of git.")
    parser.add_argument(
        "--scope-run-to-diff",
        action="store_true",
        help=(
            "With --run --diff-scoped (and not --require-baseline): pass "
            "mutmut run the MUTANT_NAMES of only the only_mutate module(s) "
            "this diff touches, so the expensive test-execution phase runs "
            "against a fraction of the mutant population instead of all of "
            "[tool.mutmut].only_mutate every time. Falls back to an "
            "unscoped (full) run whenever the scope cannot be established "
            "as safe — never widens the gate, only ever the amount of work "
            "it costs to satisfy it."
        ),
    )
    parser.add_argument(
        "--scope-run-to-functions",
        action="store_true",
        help=(
            "Like --scope-run-to-diff, but when no baseline of either kind "
            "exists, execute only the mutants of the only_mutate functions "
            "this diff changed: without a baseline the diff-scoped gate is "
            "the only reader of any mutant's outcome, so nothing it reads is "
            "dropped. Falls back to --scope-run-to-diff otherwise."
        ),
    )
    parser.add_argument(
        "--shard",
        help=(
            "K/N: when this run would measure the whole population, measure "
            "only shard K's modules (a deterministic partition of only_mutate). "
            "A diff-scoped run is not split: shard 1 runs it, the rest skip."
        ),
    )
    parser.add_argument(
        "--plan-shards",
        type=int,
        metavar="N",
        help=(
            "Print, as a JSON list, the shard indices (of N) that would have "
            "work under the other arguments, and exit 0 without running "
            "mutmut. The workflow's resolve job uses it so a run that is not "
            "split starts one runner instead of N, N-1 of which only skip."
        ),
    )
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help=(
            "Fail if no baseline is available. The scheduled drift lane uses "
            "this so it cannot silently degrade to report-only."
        ),
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help=(
            "With --run --diff-scoped: gate only the functions this diff changed, "
            "and never widen to the whole population (the PR lane). A diff that "
            "changes no mutable function runs nothing; drift is the weekly run's."
        ),
    )
    parser.add_argument("--json", help="Write a machine-readable receipt here.")
    return parser
