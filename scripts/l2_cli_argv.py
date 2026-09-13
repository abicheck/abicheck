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

"""The argv the full-CLI L2 harness measures -- the CLI's grammar, mirrored once.

Split out of `check_l2_cli_perf.py` once that file crossed the AI-readiness
`file-size` gate's 2000-line hard cap a third time; a mechanical extraction
(unchanged function bodies), and the narrowest seam left, since argv construction
depends on the fixture shape and nothing else.

Worth keeping in one place for a reason beyond line count: this is where the
product's CLI grammar is restated, so it is what goes stale when the CLI changes.
ADR-068 slices 7m/7n replaced `--format F -o PATH` with a repeatable
`-o FORMAT=DESTINATION` mid-review and every scenario had to be updated at once;
`tests/test_l2_cli_perf_gate.py`'s grammar guard exists so the next such change
is caught in milliseconds rather than by a real subprocess exiting 64 mid-lane.

Pure stdlib plus the fixture module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# The same sys.path guard the parent uses, for the same reason: this module must
# resolve its sibling whether it is loaded directly or as a `scripts.` submodule
# by a test that never imported the parent first.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("l2_cli_fixture") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))

import l2_cli_fixture as fixtures  # noqa: E402


def _cli(*args: str) -> list[str]:
    # `-m abicheck` rather than the `abicheck` console script: it is the entry
    # point that exists in every environment this may run in (including one
    # where the script directory is not on PATH), and it is the same code path.
    return [sys.executable, "-m", "abicheck", *args]


def _header_args(libs: list[fixtures.BuiltLibrary], side: str) -> list[str]:
    """``--header`` plus the ``--include`` roots those headers need to parse.

    The extra roots are not optional decoration: in a shared-context
    multi-library fixture the one physical ``detail/core.h`` lives at a common
    root, so without ``--include`` pointing there the headers do not resolve at
    all -- and a run that fell back would silently not be the shared arm it is
    labelled as.
    """
    out: list[str] = []
    for lib in libs:
        for header in lib.headers:
            out += ["--header", f"{side}={header}"]
        for extra in lib.extra_includes:
            out += ["--include", f"{side}={extra}"]
    return out


def _dump_argv(lib: fixtures.BuiltLibrary, out: Path) -> list[str]:
    argv = _cli("dump", str(lib.so), "--depth", "headers", "-o", str(out))
    for header in lib.headers:
        argv += ["-H", str(header)]
    # dump's --include is not side-scoped (there is only one operand).
    for extra in lib.extra_includes:
        argv += ["-I", str(extra)]
    return argv


def _compare_argv(
    old: str | None,
    new: str,
    *,
    exports: dict[str, Path],
    old_headers: list[str] | None = None,
    new_headers: list[str] | None = None,
    no_baseline: bool = False,
) -> list[str]:
    """A ``compare`` invocation exporting each ``{format: destination}`` pair.

    *exports* is a mapping rather than a single format because ``-o`` is
    repeatable (``-o FORMAT=DESTINATION``, ADR-068 slices 7m/7n): one invocation
    renders any number of artifacts from the one completed analysis. That is
    what lets the two-format scenario be a *single* comparison, which is what it
    is supposed to measure.
    """
    args = ["compare"]
    if no_baseline:
        args += [new, "--no-baseline"]
    else:
        if old is None:
            raise ValueError("a baseline comparison needs an old operand")
        args += [old, new]
    args += ["--depth", "headers"]
    for fmt, destination in exports.items():
        args += ["-o", f"{fmt}={destination}"]
    args += old_headers or []
    args += new_headers or []
    return _cli(*args)


def _dry_run_argv(argv: list[str]) -> list[str]:
    """*argv* rewritten as a resolution-only run.

    ``-o`` is stripped, not merely supplemented: ``compare`` rejects
    ``--dry-run -o PATH`` outright (exit 64 -- "a dry run performs no analysis
    and writes nothing"), which the first version of this harness tripped. The
    flag is dropped rather than the whole step skipped because the resolution
    window is the one phase boundary available here that does not require
    duplicating any product internals.
    """
    out: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in ("-o", "--output"):
            skip_next = True
            continue
        out.append(token)
    return out + ["--dry-run"]
