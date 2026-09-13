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

"""The two shapes the full-CLI L2 harness is built out of: `Step` and `Scenario`.

The innermost module of that harness. Argv construction, validation, gating and
the runner all depend on these; these depend on nothing but the fixture shape and
`perf_receipt.CommandRun`.

Extracted the fourth time `check_l2_cli_perf.py` crossed the AI-readiness
`file-size` gate's 2000-line hard cap. The three previous splits each took a
layer off the outside (`l2_cli_validation`, `l2_cli_gating`, `l2_cli_argv`) while
the scenario definitions -- the bulk of the file -- had to stay, because they
construct these two types and moving them would have meant importing the parent
back. Giving the types their own home removes that obstacle.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("l2_cli_fixture") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))

import l2_cli_fixture as fixtures  # noqa: E402
from perf_receipt import CommandRun  # noqa: E402

#: How many header extractions a correct run of each extraction shape performs,
#: expressed as a predicate over the observed count rather than an exact number:
#: the count scales with header and library count, and pinning it would make
#: this a test of the fixture's size.
EXTRACTION_EXPECTATIONS = {
    "forbidden": "zero header extractions (a stored-operand path)",
    "one_side": "extractions for exactly one operand (the live side only)",
    "both_sides": "extractions for both operands",
    "any": "not asserted",
}


@dataclass
class Step:
    """One measured CLI invocation inside a scenario."""

    name: str
    argv: list[str]
    #: Phase scope this step's wall time describes. ``"full_cli"`` is the real
    #: user-facing run; the others are nested inclusive windows measured for
    #: diagnosis and explicitly NOT additive with it.
    scope: str = "full_cli"
    extraction: str = "any"
    #: Exit codes that mean the run did its job. A compare finding a real break
    #: exits 4; treating that as a failure would make the break scenario
    #: unmeasurable.
    ok_exit_codes: tuple[int, ...] = (0,)
    output: Path | None = None
    #: Further files this one invocation is required to write. `output` alone was
    #: not enough for the two-formats scenario, whose single `compare` writes both
    #: a JSON and a Markdown export: only the JSON was cleared and required, so a
    #: repetition that wrote fresh JSON and silently omitted Markdown left the
    #: previous repetition's Markdown in place for the validator to accept, and an
    #: incomplete render's (faster) timing stayed in the median (Codex review).
    extra_outputs: tuple[Path, ...] = ()
    sample_rss: bool = False

    @property
    def declared_outputs(self) -> tuple[Path, ...]:
        """Every file this invocation must produce -- the one list to iterate.

        Exists so a caller cannot handle `output` and forget `extra_outputs`,
        which is the exact shape of the defect that introduced the second field.
        """
        return ((self.output,) if self.output is not None else ()) + self.extra_outputs


@dataclass
class Scenario:
    id: str
    description: str
    spec: fixtures.FixtureSpec
    #: Untimed setup run once per scenario (e.g. pre-dumping a stored operand).
    prepare: Callable[[fixtures.BuiltFixture, Path], list[Step]] | None
    steps: Callable[[fixtures.BuiltFixture, Path], list[Step]]
    validate: Callable[[Path, dict[str, list[CommandRun]]], list[str]]
    cache_mode: str = "cold"
    suites: tuple[str, ...] = ("pr", "extended")
