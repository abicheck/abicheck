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

"""Generate (or check) the test files mutmut's stats pass runs.

mutmut's stats pass runs its whole test selection serially under tracing --
52 of a measured 72 minutes per shard -- although only tests that call
``only_mutate`` code in-process can ever be associated with a mutant
(measured: 14,091 of 62,105 tests). This script runs that suite once under
``scripts/mutation_reach_trace.py`` in parallel (xdist), with the lane's own
markers and deselections, and writes every test file that has at least one
reaching test to ``tests/mutation_test_selection.txt``, which
``[tool.mutmut].pytest_add_cli_args_test_selection`` reads as a pytest
``@argfile`` (one path per line; argfiles cannot carry comments, so this
docstring is its provenance).

Selection is per file, and the trace is a superset of mutmut's own
association, so a narrowed stats pass associates exactly the tests the
unnarrowed one would. If the list goes stale (a test file starts reaching
mutated code), the effect is fail-closed: that file's kills are missing, so
mutants read as survivors rather than as killed. Two guards keep it fresh:

* ``--check`` (the weekly mutation run) fails if a reaching file is missing;
* a PR's own added or changed test files are appended to the selection for
  that run (``mutation_scope.py extend-selection``), so a new test is never
  left out of its own PR's measurement.

    python scripts/gen_mutation_test_selection.py            # rewrite the list
    python scripts/gen_mutation_test_selection.py --check    # fail on a missing file
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILE = REPO_ROOT / "tests" / "mutation_test_selection.txt"
#: What the stats pass ran before it was narrowed, and what the trace covers.
FULL_SELECTION = ["tests/"]
#: Lane-only flags that do not describe *which* tests exist or run.
_NOT_SELECTION = {"-x", "-q", "--tb=line"}


def lane_pytest_args(pyproject: Path = REPO_ROOT / "pyproject.toml") -> list[str]:
    """The mutmut lane's own marker/deselect/ignore arguments, minus ``-x``
    (one failing test must not hide the files after it) and its plugins."""
    args = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["mutmut"][
        "pytest_add_cli_args"
    ]
    out: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "-p":
            skip_next = True
            continue
        if arg not in _NOT_SELECTION:
            out.append(arg)
    return out


def files_of(nodeids: list[str]) -> list[str]:
    return sorted({n.split("::", 1)[0] for n in nodeids})


def trace(workers: str) -> list[str]:
    """Node ids of every test that reaches ``only_mutate`` code."""
    with tempfile.TemporaryDirectory() as out:
        env = {
            **os.environ,
            "MUTATION_REACH_OUT": out,
            "PYTHONPATH": os.pathsep.join(
                [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")]
            ),
        }
        cmd = [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "scripts.mutation_reach_trace", "-n", workers,
            *lane_pytest_args(), *FULL_SELECTION,
        ]  # fmt: skip
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False)  # noqa: S603
        # pytest exit 1 = some test failed: the trace is still complete (and
        # the lane's own clean run reports the failure). Anything else means
        # collection or the session itself broke, so the trace is not.
        if proc.returncode not in (0, 1):
            raise SystemExit(f"tracing run failed (pytest exit {proc.returncode})")
        hits: set[str] = set()
        for part in Path(out).glob("*.json"):
            hits.update(json.loads(part.read_text(encoding="utf-8")))
    if not hits:
        raise SystemExit(
            "tracing run recorded no reaching test: refusing an empty list"
        )
    return sorted(hits)


def selection_problems(lines: list[str], exists: Callable[[str], bool]) -> list[str]:
    """Why *lines* is not a valid stats-pass selection; empty when it is.

    The one rule for both shapes the file legitimately takes: the committed
    narrowed list (sorted, unique, every entry an existing
    ``tests/**/test_*.py``) and the whole-suite sentinel
    :data:`FULL_SELECTION` that ``mutation_scope.extend_selection`` writes
    when a PR changes a shared test module. The suite checks the file
    *after* CI has rewritten it -- mutmut's stats pass runs that check -- so
    a rule that knew only the committed shape aborted every shard of such a
    PR.
    """
    if not lines:
        return ["an empty selection would make the stats pass run nothing"]
    if lines == FULL_SELECTION:
        return []
    problems = []
    if lines != sorted(set(lines)):
        problems.append("entries are not sorted and unique")
    missing = [p for p in lines if not exists(p)]
    if missing:
        problems.append(f"selection names files that do not exist: {missing}")
    odd = [
        p
        for p in lines
        if not (p.startswith("tests/") and Path(p).name.startswith("test_"))
    ]
    if odd:
        problems.append(f"entries that are not tests/**/test_*.py files: {odd}")
    return problems


def read_selection(path: Path = SELECTION_FILE) -> list[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="Fail if a reaching file is missing."
    )
    parser.add_argument(
        "-n", "--workers", default="auto", help="xdist workers (default: auto)."
    )
    args = parser.parse_args(argv)

    reaching = files_of(trace(args.workers))
    if not args.check:
        SELECTION_FILE.write_text("\n".join(reaching) + "\n", encoding="utf-8")
        print(
            f"wrote {len(reaching)} test files to {SELECTION_FILE.relative_to(REPO_ROOT)}"
        )
        return 0
    committed = set(read_selection())
    missing = sorted(set(reaching) - committed)
    stale = sorted(committed - set(reaching))
    if stale:
        # Costs stats-pass time only; never hides a kill.
        print(
            f"note: {len(stale)} listed file(s) no longer reach mutated code: {stale}"
        )
    if missing:
        print(
            f"ERROR: {len(missing)} test file(s) reach only_mutate code but are not in "
            f"{SELECTION_FILE.relative_to(REPO_ROOT)}, so their kills are not counted: "
            f"{missing}\nRegenerate: python scripts/gen_mutation_test_selection.py"
        )
        return 1
    print(f"mutation test selection is complete ({len(committed)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
