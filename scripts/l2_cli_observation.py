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

"""What the full-CLI L2 harness concludes from an *observed* native-invocation count.

The fifth 2000-line-cap split out of `check_l2_cli_perf.py`, and an inward seam
rather than another slice off the end: every function here takes a `CommandRun`
(or a list of them) plus a `Step` contract and returns a list of problem strings.
It reaches no fixture, no argv, no filesystem and no scenario, which is what lets
the parent import it rather than the reverse.

One rule governs the whole module and is the reason it is worth reading as a
unit: **an absent observation is unknown, never zero, and one favourable
observation never certifies a batch.** Both halves were real defects here -- a
`--no-spy` run read an absent counter as "extracted nothing" and failed
deterministically, and a `min()` over repetitions let one lucky warm run certify
a whole cold/warm batch. Every check below is written per observation and skips
rather than asserts when it has none.

Pure stdlib plus the receipt and model siblings.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("perf_receipt") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))

from l2_cli_model import Step  # noqa: E402
from perf_receipt import CommandRun  # noqa: E402


def _check_extraction(
    run: CommandRun, expectation: str, *, one_side: int | None
) -> list[str]:
    """Assert a step's observed header-extraction count against its contract.

    *one_side* is the **independently calibrated** cost of extracting a single
    operand -- taken from a setup step that really did extract exactly one side
    -- or ``None`` when no such calibration exists for this scenario.

    That distinction is load-bearing and was a real soundness bug in the first
    version of this harness: ``one_side`` was seeded from the measured step's
    *own* observed count, so the "not more than one side" comparison reduced to
    ``observed > observed`` and could never fail. A self-calibrating assertion
    is not an assertion. With no calibration available, the upper-bound half is
    now explicitly **not checked** (and reported as unchecked by the caller)
    rather than checked against a number derived from the thing under test.

    The lower-bound half needs no calibration and is always checked, because it
    is the "faster because it stopped working" direction: a stored/live
    comparison that extracts *nothing* never looked at its live side at all.
    """
    observed = (run.native_invocations or {}).get("header_extraction")
    if observed is None:
        return ["no native-invocation observation recorded (spy not installed?)"]
    if expectation == "forbidden":
        return _no_native_invocation_problems(run)
    if expectation in ("one_side", "both_sides"):
        return _live_extraction_problems(
            observed, expectation, one_side=one_side, run=run
        )
    return []


def _live_extraction_problems(
    observed: int, expectation: str, *, one_side: int | None, run: CommandRun
) -> list[str]:
    """The two live-operand contracts: a lower bound always, an upper bound when calibrated.

    The lower bound ("something was extracted") needs no calibration and is the
    "faster because it stopped working" direction. The count bound is checked only
    against an *independently* calibrated single-side cost; with none available it
    is deliberately left unchecked and reported as such, rather than checked
    against a number derived from the step under test.
    """
    sides = 1 if expectation == "one_side" else 2
    if observed == 0:
        subject = "the live side" if sides == 1 else "neither operand"
        verb = "was never extracted" if sides == 1 else "was extracted"
        return [f"zero header extractions: {subject} {verb}"]
    problems = _include_pass_problems(run, sides)
    if problems or one_side is None:
        return problems
    if sides == 1 and observed > one_side:
        return [
            f"{observed} header extraction(s) observed, but one side costs "
            f"{one_side} (calibrated from this scenario's own setup dump) -- "
            "the stored operand appears to have been re-extracted"
        ]
    if sides == 2 and observed < one_side * 2:
        return [
            f"{observed} header extraction(s) observed, expected at least "
            f"{one_side * 2} for two live operands (one side costs "
            f"{one_side}, calibrated from a setup dump)"
        ]
    return []


def _include_pass_problems(run: CommandRun, sides: int) -> list[str]:
    """A live L2 run must also have run its include-graph pass, once per side.

    Header-AST extraction is not the whole of the measured L2 work: the compare
    path also runs an always-on `clang -M` include/dependency pass, which the spy
    classifies as ``include_pass``. Checking only ``header_extraction`` let a
    regression that stops running that pass read as a *performance improvement* --
    the run is shorter, the evidence depth still resolves to ``headers``, and the
    deliberate break is still found, so nothing else notices (Codex review).

    The floor is one pass **per header the run was asked to parse**, not one per
    side. A flat per-side floor was the first version and it was too weak to
    catch the regression this check exists for (Codex review): the extended
    `simple-h8`/`simple-h32` arms really perform 16 and 64 passes, so a
    regression that processed only the first top-level header of each side would
    drop to 2 and still pass -- the evidence depth still resolves to ``headers``
    and the deliberate break still lives in ``part0.h``, so every other check
    agrees while most of the include-graph work has silently stopped happening.

    The count is derived from the measured argv's own ``--header``/``-H``
    arguments rather than from a fixture-shape table, for two reasons: it cannot
    drift away from what the run was actually told to do, and it needs no
    per-side bookkeeping -- a stored operand contributes no ``--header``, so a
    `one_side` step's floor falls out of the same count. Measured against the
    real fixtures to confirm the relationship is exactly one per header per side
    before encoding it: h1 -> 2, h8 -> 16, h32 -> 64.

    *sides* remains the floor when the argv names no header at all (which is
    every synthetic `CommandRun` in the tests, and would be a scenario shape
    that does not yet exist). Still deliberately not an equality: how many
    passes the product *should* run per header is its business, while running
    fewer than one each is the regression.
    """
    observed = (run.native_invocations or {}).get("include_pass")
    if observed is None:
        return []
    requested = _requested_header_count(run.argv)
    expected = max(sides, requested)
    if observed < expected:
        basis = (
            f"{requested} header(s) named on the command line"
            if requested > sides
            else f"{sides} live side(s)"
        )
        return [
            f"{observed} include/dependency pass(es) observed for {basis}, "
            f"expected at least {expected} (one per header per live side) -- the "
            "include-graph pass did not run over every header, so the run is "
            "shorter by skipping measured L2 work"
        ]
    return []


def _requested_header_count(argv: list[str]) -> int:
    """How many public headers *argv* asks the product to parse.

    Both spellings, because the harness measures both commands: ``compare`` takes
    a side-scoped ``--header SIDE=PATH`` per header, ``dump`` takes ``-H PATH``.
    Counting the flags rather than parsing their values is deliberate -- the
    question is how many parses were requested, and the same physical header
    named twice is two requests.
    """
    return sum(1 for token in argv if token in ("--header", "-H"))


def _no_native_invocation_problems(run: CommandRun) -> list[str]:
    """The ``forbidden`` contract: an absolute zero over EVERY invocation kind.

    Needs no calibration, and deliberately is not limited to the extraction
    bucket. A stored-operand path claims no compiler ran *at all*, so an
    ``include_pass``, a ``--version`` probe, or anything classified ``other``
    falsifies the claim exactly as an AST extraction does. Checking only
    ``header_extraction`` let a regression that starts spawning ``clang++ -M`` or
    ``g++ --version`` while loading two stored snapshots pass the one scenario
    whose entire point is that it spawns nothing.
    """
    nonzero = {
        kind: count for kind, count in (run.native_invocations or {}).items() if count
    }
    if not nonzero:
        return []
    detail = ", ".join(f"{kind}={count}" for kind, count in sorted(nonzero.items()))
    return [
        f"native compiler invocation(s) observed ({detail}) on a "
        "stored-operand path that must perform none -- the stored "
        "snapshot was re-extracted, or the path grew a new native call"
    ]


def uncalibrated_contracts(steps: list[Step], one_side: int | None) -> list[str]:
    """Which extraction contracts could only be partially checked.

    Reported so the receipt never implies a stronger claim than was made: a
    ``one_side``/``both_sides`` contract with no independent single-side
    calibration has had its lower bound checked (extraction happened at all) but
    not its upper bound (it was not more than one side's worth).
    """
    if one_side is not None:
        return []
    return [
        f"step {step.name}: contract {step.extraction!r} checked for >0 only -- no "
        "setup step in this scenario extracts exactly one side, so there is no "
        "independent calibration for the count bound"
        for step in steps
        if step.extraction in ("one_side", "both_sides")
    ]


def _extraction_counts(batch: list[CommandRun]) -> list[int | None]:
    """Observed header-extraction count per run, or ``None`` where unobserved.

    ``None``, never ``0``, when the spy is off: under ``--no-spy`` every
    ``native_invocations`` is empty, and reading the absent key as zero made the
    cache validators conclude "the cold run extracted nothing" and "the cache
    served stale evidence" -- so the supported instrumentation-overhead
    configuration failed deterministically rather than measuring anything (Codex
    review). Absent is not zero; it is unknown, which is the same rule
    `_check_extraction` and `_include_pass_problems` already follow.
    """
    return [(run.native_invocations or {}).get("header_extraction") for run in batch]


def _warm_cache_problems(runs: dict[str, list[CommandRun]]) -> list[str]:
    """Every cold/warm repetition must individually show the cache serving.

    Per repetition, deliberately, and paired by index -- repetition *i* runs
    cold then warm against one cache lifecycle, so `runs["cold"][i]` and
    `runs["warm"][i]` are the two halves of one observation.

    The first version reduced each batch with `min()` and compared the two
    numbers. That let ONE warm repetition hitting the cache certify the whole
    scenario while the others re-extracted in full -- so the gated median could
    describe an uncached run while the receipt reported a served cache, which is
    a mislabelled benchmark rather than a missed one (Codex review). A reduction
    across repetitions cannot express "each repetition was warm", so there is no
    reducer that fixes this: the comparison has to be per pair.
    """
    cold = runs.get("cold") or []
    warm = runs.get("warm") or []
    if not cold or not warm:
        return ["cold/warm runs missing"]
    if len(cold) != len(warm):
        return [
            f"{len(cold)} cold vs {len(warm)} warm repetition(s) -- cannot pair "
            "them, so no repetition's cache state is established"
        ]
    cold_counts = _extraction_counts(cold)
    warm_counts = _extraction_counts(warm)
    if any(n is None for n in (*cold_counts, *warm_counts)):
        # Unobserved, not zero. With the spy off this claim cannot be checked at
        # all, so it is left unchecked and reported as such by the caller rather
        # than failed -- `cache_claim_unverified` exists for exactly this.
        return []
    problems: list[str] = []
    for index, (cold_n, warm_n) in enumerate(zip(cold_counts, warm_counts)):
        if cold_n == 0:
            problems.append(
                f"repetition {index}: the cold run performed no header extraction "
                "at all -- the cache root was not actually fresh, so nothing here "
                "measures a cold state"
            )
        elif warm_n >= cold_n:
            problems.append(
                f"repetition {index}: warm extracted {warm_n} vs cold {cold_n} -- no "
                "cache served, so this repetition is not the warm state it is "
                "labelled as"
            )
    return problems
