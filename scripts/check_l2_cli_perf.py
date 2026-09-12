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

"""Full-CLI L2 performance harness: real binaries, real headers, real process.

The third and outermost of this repository's three perf measurement levels:

1. ``benchmark_scaling.py`` -- synthetic, in-process, compiler-free scaling of
   the comparison/suppression/severity/serialization/reporting stages.
2. ``check_header_graph_perf.py`` -- the real L2 ``dump`` and header-graph
   attach, in-process, on a synthetic header sweep.
3. **this script** -- the whole ``abicheck`` CLI as a subprocess against a real
   compiled fixture.

Level 3 exists because nothing at levels 1 and 2 observes what a user actually
waits for. The measured window here is the CLI subprocess's entire lifetime:
Python interpreter startup, ``.abicheck.yml`` discovery, input resolution,
evidence extraction *or* snapshot load, comparison, and report writing. Fixture
compilation, snapshot pre-dumping for a stored-operand scenario, and every
correctness check happen strictly outside it -- they are setup and validation,
not things a user's ``abicheck compare`` pays for.

**Correctness is gated alongside time, and that is the point.** A perf harness
that only measures duration rewards the worst possible regression: a change
that gets faster by quietly doing less -- falling back to binary-only evidence,
losing a header, skipping the include pass, emitting an empty snapshot. So every
scenario asserts, outside its timed window, the things a fast-but-wrong run
would fail: the resolved evidence depth really reached ``headers``, the public
scoping resolved without falling back, the expected declarations are present,
the header call/include/type graph passes all ran undegraded, and the fixture's
deliberate break produced both a removal finding and a layout finding. A
scenario whose validation fails is a **failure**, never a fast data point.

**Native invocations are observed, not assumed.** Each run executes with a
``PATH`` of ``exec``-ing shims (``perf_receipt.NativeInvocationSpy``) that log
every ``castxml``/``clang++``/``g++`` invocation with its argv, classified into
header extraction, include pass, and version probe. That is what turns three
otherwise-unfalsifiable claims into measurements:

* a stored-snapshot/stored-snapshot comparison performs **zero** header
  extractions;
* a stored/live comparison performs exactly **one side's** worth, i.e. it does
  not re-extract the operand it was handed as a snapshot;
* a live run labelled "warm cache" really was served by a cache, proven by its
  extraction count dropping, rather than by the mere fact that it ran second.

Phase accounting uses real, separately-measurable boundaries rather than a
second copy of the product's internals: ``abicheck --version`` measures
interpreter+import startup, and ``compare --dry-run`` measures startup plus
config and input resolution with the diff never run. These are **nested
inclusive** windows, recorded with their scope and never summed -- a point this
script's own receipt states per phase, because adding two inclusive windows
together is the arithmetic error the whole receipt design is meant to prevent.

Usage::

    # Measure and print (report-only without --baseline):
    python scripts/check_l2_cli_perf.py --json-out reports/perf/l2_cli.json

    # Gate a PR run against a base-branch report:
    python scripts/check_l2_cli_perf.py --baseline base_l2_cli.json \\
        --regress-tolerance 0.3 --regress-min-delta-seconds 0.5

    # The extended axis sweep (periodic/manual lane, not a PR):
    python scripts/check_l2_cli_perf.py --suite extended

Self-skips (exit 0) without a C++ compiler, or off Linux/ELF -- matching
``check_header_graph_perf.py``'s own scope. ``--require-toolchain`` turns that
skip into a failure for a CI job that installed one and therefore must not
silently report a pass over nothing measured.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("perf_receipt") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import l2_cli_fixture as fixtures  # noqa: E402
from perf_measurement import (  # noqa: E402
    GateThreshold,
    finite_nonnegative_float_arg,
    is_gateable,
    positive_int_arg,
    summarize_samples,
)
from perf_receipt import (  # noqa: E402
    CommandRun,
    NativeInvocationSpy,
    build_receipt,
    digest_paths,
    run_measured,
    write_receipt,
)

DEFAULT_REPEAT = 3
DEFAULT_REGRESS_TOLERANCE = 0.3
DEFAULT_REGRESS_MIN_DELTA_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 900.0
#: Refuse to keep a report larger than this -- a runaway renderer filling the
#: runner's disk is a failure mode a perf lane can actually hit.
MAX_OUTPUT_BYTES = 64 * 1024 * 1024

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
    sample_rss: bool = False


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


# ── CLI invocation builders ───────────────────────────────────────────────────
def _cli(*args: str) -> list[str]:
    # `-m abicheck` rather than the `abicheck` console script: it is the entry
    # point that exists in every environment this may run in (including one
    # where the script directory is not on PATH), and it is the same code path.
    return [sys.executable, "-m", "abicheck", *args]


def _header_args(libs: list[fixtures.BuiltLibrary], side: str) -> list[str]:
    out: list[str] = []
    for lib in libs:
        for header in lib.headers:
            out += ["--header", f"{side}={header}"]
    return out


def _dump_argv(lib: fixtures.BuiltLibrary, out: Path) -> list[str]:
    argv = _cli("dump", str(lib.so), "--depth", "headers", "-o", str(out))
    for header in lib.headers:
        argv += ["-H", str(header)]
    return argv


def _compare_argv(
    old: str | None,
    new: str,
    *,
    out: Path,
    fmt: str = "json",
    old_headers: list[str] | None = None,
    new_headers: list[str] | None = None,
    no_baseline: bool = False,
) -> list[str]:
    args = ["compare"]
    if no_baseline:
        args += [new, "--no-baseline"]
    else:
        assert old is not None
        args += [old, new]
    args += ["--depth", "headers", "--format", fmt, "-o", str(out)]
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


# ── validation (always outside the timed window) ──────────────────────────────
def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _validate_l2_reached(
    report: dict[str, Any], *, sides: tuple[str, ...]
) -> list[str]:
    """The run really reached L2 on every named side, and did not degrade.

    Checks the *resolved* depth rather than trusting that ``--depth headers``
    was passed: a binary-only fallback still accepts the flag, and that fallback
    is faster, which is exactly why a timing harness must reject it.
    """
    problems: list[str] = []
    assurance = report.get("analysis_assurance") or {}
    if assurance.get("effective_depth") != "headers":
        problems.append(
            f"effective_depth={assurance.get('effective_depth')!r}, expected 'headers' "
            "-- the run did not actually perform L2 analysis"
        )
    if assurance.get("depth_satisfied") is not True:
        problems.append(f"depth_satisfied={assurance.get('depth_satisfied')!r}")
    for side in sides:
        depth = report.get(f"{side}_evidence_depth")
        if depth != "headers":
            problems.append(f"{side}_evidence_depth={depth!r}, expected 'headers'")
    scope = report.get("scope") or {}
    if not scope.get("public_headers_applied"):
        problems.append("scope.public_headers_applied is false -- no public scoping")
    if scope.get("fell_back"):
        problems.append("scope.fell_back is true -- public scoping degraded")
    return problems


def _validate_break_findings(report: dict[str, Any]) -> list[str]:
    kinds = {c.get("kind") for c in report.get("changes") or []}
    problems = []
    if report.get("verdict") != "BREAKING":
        problems.append(f"verdict={report.get('verdict')!r}, expected BREAKING")
    for family, alternatives in fixtures.EXPECTED_BREAK_KIND_FAMILIES.items():
        if not kinds & set(alternatives):
            problems.append(
                f"no {family}-family finding (expected one of {list(alternatives)}); "
                f"got {sorted(k for k in kinds if k)}"
            )
    return problems


def _validate_unchanged(report: dict[str, Any]) -> list[str]:
    kinds = {c.get("kind") for c in report.get("changes") or []}
    problems = []
    for family, alternatives in fixtures.EXPECTED_BREAK_KIND_FAMILIES.items():
        if kinds & set(alternatives):
            problems.append(
                f"unchanged comparison reported a {family}-family finding "
                f"{sorted(kinds & set(alternatives))} -- a false positive"
            )
    return problems


def _validate_snapshot(path: Path) -> list[str]:
    """A dumped snapshot carries real L2 content, not an empty shell.

    Loaded through the product's own ``load_snapshot`` rather than read as raw
    JSON: the on-disk form is a sectioned envelope, and checking the envelope's
    keys would pass for a snapshot whose sections are empty -- "a file was
    written" is precisely the wrong thing for this harness to accept as
    success.
    """
    from abicheck.serialization import load_snapshot

    problems: list[str] = []
    if not path.exists():
        return [f"no snapshot written at {path}"]
    size = path.stat().st_size
    if size > MAX_OUTPUT_BYTES:
        problems.append(f"snapshot is {size} bytes, over the {MAX_OUTPUT_BYTES} cap")
    snapshot = load_snapshot(str(path))
    names = {f.name for f in snapshot.functions} | {t.name for t in snapshot.types}
    for declaration in fixtures.EXPECTED_DECLARATIONS:
        if not any(declaration in name for name in names):
            problems.append(
                f"declaration {declaration!r} missing from the snapshot "
                "-- headers were lost or never parsed"
            )
    pack = snapshot.build_source
    graph = getattr(pack, "source_graph", None) if pack else None
    if graph is None:
        problems.append("no source graph: the header-graph attach never ran")
        return problems
    passes = graph.extractor_passes or {}
    for required in ("header_call_graph", "header_include_graph", "header_type_graph"):
        if not passes.get(required):
            problems.append(
                f"extractor pass {required!r} did not run or did not complete"
            )
    if graph.degraded_passes:
        problems.append(f"degraded extractor passes: {sorted(graph.degraded_passes)}")
    include = (graph.coverage or {}).get("include_edges") or {}
    if not include.get("collected"):
        problems.append("include graph was not collected")
    elif not include.get("count"):
        problems.append(
            "include graph collected zero edges -- the fixture's shared detail/ "
            "header should always produce at least one"
        )
    return problems


def _validate_audit(report: dict[str, Any]) -> list[str]:
    """``--no-baseline`` must read as an audit, not as a compatibility verdict."""
    problems = []
    if report.get("no_baseline") is not True:
        problems.append(f"no_baseline={report.get('no_baseline')!r}, expected True")
    if report.get("verdict") is not None:
        problems.append(
            f"verdict={report.get('verdict')!r} -- a no-baseline audit must not "
            "manufacture a compatibility verdict (there is nothing to compare to)"
        )
    outcome = report.get("run_outcome") or {}
    if outcome.get("compatibility") is not None:
        problems.append(
            f"run_outcome.compatibility={outcome.get('compatibility')!r}, expected null"
        )
    if not report.get("audit_report_schema_version"):
        problems.append("no audit_report_schema_version -- this is not an audit report")
    return problems


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
        # Needs no calibration: the contract is an absolute zero.
        if observed:
            return [
                f"{observed} header extraction(s) observed on a stored-operand path "
                "that must perform none -- the stored snapshot was re-extracted"
            ]
    elif expectation == "one_side":
        if observed == 0:
            return ["zero header extractions: the live side was never extracted"]
        if one_side is not None and observed > one_side:
            return [
                f"{observed} header extraction(s) observed, but one side costs "
                f"{one_side} (calibrated from this scenario's own setup dump) -- "
                "the stored operand appears to have been re-extracted"
            ]
    elif expectation == "both_sides":
        if observed == 0:
            return ["zero header extractions: neither operand was extracted"]
        if one_side is not None and observed < one_side * 2:
            return [
                f"{observed} header extraction(s) observed, expected at least "
                f"{one_side * 2} for two live operands (one side costs "
                f"{one_side}, calibrated from a setup dump)"
            ]
    return []


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


# ── scenarios ─────────────────────────────────────────────────────────────────
def _stored_paths(work: Path) -> tuple[Path, Path]:
    return work / "old.abi.json", work / "new.abi.json"


def _prepare_stored(
    fixture: fixtures.BuiltFixture, work: Path, *, sides: tuple[str, ...]
) -> list[Step]:
    """Pre-dump the named sides. Returns *untimed* setup steps."""
    old_out, new_out = _stored_paths(work)
    steps = []
    if "old" in sides:
        steps.append(
            Step("prep_dump_old", _dump_argv(fixture.old[0], old_out), scope="setup")
        )
    if "new" in sides:
        steps.append(
            Step("prep_dump_new", _dump_argv(fixture.new[0], new_out), scope="setup")
        )
    return steps


def scenario_dump(spec: fixtures.FixtureSpec, suites=("pr", "extended")) -> Scenario:
    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        out = work / "dumped.abi.json"
        return [
            Step("startup", _cli("--version"), scope="startup_only"),
            Step(
                "dump",
                _dump_argv(fixture.new[0], out),
                extraction="one_side",
                output=out,
                sample_rss=True,
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        return _validate_snapshot(work / "dumped.abi.json")

    return Scenario(
        id=f"dump_l2[{spec.profile_id}]",
        description="dump a binary + public headers to a stored L2 snapshot",
        spec=spec,
        prepare=None,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_compare_live_live(
    spec: fixtures.FixtureSpec, suites=("pr", "extended")
) -> Scenario:
    def prepare(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        # An untimed calibration dump of ONE side, purely so the measured
        # comparison's "both operands were extracted" assertion has an
        # independent single-side number to check against. Without it that
        # assertion could only check ">0" (see _check_extraction's docstring on
        # why it must not calibrate from the step under test). Costs about a
        # second of setup, outside every timed window, and buys a real bound
        # instead of an unchecked one.
        return _prepare_stored(fixture, work, sides=("old",))

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        out = work / "live_live.json"
        # Library 0 only, even for a multi-library spec. A single `compare`
        # takes one old artifact and one new artifact; handing it every
        # library's headers at once is not a supported shape, and the first
        # version of this harness did exactly that and produced a compiler
        # redefinition error rather than a measurement. The cost of a *set* of
        # libraries is scenario_multi_library's job, as N real per-library
        # comparisons.
        old_h = _header_args(fixture.old[:1], "old")
        new_h = _header_args(fixture.new[:1], "new")
        argv = _compare_argv(
            str(fixture.old[0].so),
            str(fixture.new[0].so),
            out=out,
            old_headers=old_h,
            new_headers=new_h,
        )
        return [
            # Nested inclusive window: startup + config + input resolution, with
            # the diff never run. Recorded to attribute the full run's time, and
            # explicitly not subtracted from it in any gated number.
            Step("resolution", _dry_run_argv(argv), scope="startup_and_resolution"),
            Step(
                "compare",
                argv,
                extraction="both_sides",
                ok_exit_codes=(0, 2, 4),
                output=out,
                sample_rss=True,
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        report = _load_report(work / "live_live.json")
        problems = _validate_l2_reached(report, sides=("old", "new"))
        problems += (
            _validate_break_findings(report)
            if spec.change == "break"
            else _validate_unchanged(report)
        )
        return problems

    return Scenario(
        id=f"compare_live_live[{spec.profile_id}]",
        description="compare two live binaries, each with its own historical headers",
        spec=spec,
        prepare=prepare,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_compare_stored_live(
    spec: fixtures.FixtureSpec, suites=("pr", "extended")
) -> Scenario:
    def prepare(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        return _prepare_stored(fixture, work, sides=("old",))

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        old_out, _ = _stored_paths(work)
        out = work / "stored_live.json"
        return [
            Step(
                "compare",
                _compare_argv(
                    str(old_out),
                    str(fixture.new[0].so),
                    out=out,
                    new_headers=_header_args(fixture.new, "new"),
                ),
                # The load-bearing assertion: exactly one side's extraction
                # cost. More than that means the stored operand was re-parsed,
                # which is a product defect, not something to work around.
                extraction="one_side",
                ok_exit_codes=(0, 2, 4),
                output=out,
                sample_rss=True,
            )
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        report = _load_report(work / "stored_live.json")
        problems = _validate_l2_reached(report, sides=("old", "new"))
        problems += (
            _validate_break_findings(report)
            if spec.change == "break"
            else _validate_unchanged(report)
        )
        return problems

    return Scenario(
        id=f"compare_stored_live[{spec.profile_id}]",
        description="compare a stored L2 snapshot against a live binary + headers",
        spec=spec,
        prepare=prepare,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_compare_stored_stored(
    spec: fixtures.FixtureSpec, suites=("pr", "extended")
) -> Scenario:
    def prepare(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        return _prepare_stored(fixture, work, sides=("old", "new"))

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        old_out, new_out = _stored_paths(work)
        out = work / "stored_stored.json"
        argv = _compare_argv(str(old_out), str(new_out), out=out)
        return [
            Step("resolution", _dry_run_argv(argv), scope="startup_and_resolution"),
            Step(
                "compare",
                argv,
                extraction="forbidden",
                ok_exit_codes=(0, 2, 4),
                output=out,
                sample_rss=True,
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        report = _load_report(work / "stored_stored.json")
        problems = _validate_l2_reached(report, sides=("old", "new"))
        problems += (
            _validate_break_findings(report)
            if spec.change == "break"
            else _validate_unchanged(report)
        )
        return problems

    return Scenario(
        id=f"compare_stored_stored[{spec.profile_id}]",
        description="compare two stored L2 snapshots (no compiler must run)",
        spec=spec,
        prepare=prepare,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_no_baseline(
    spec: fixtures.FixtureSpec, suites=("pr", "extended")
) -> Scenario:
    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        out = work / "audit.json"
        return [
            Step(
                "audit",
                _compare_argv(
                    None,
                    str(fixture.new[0].so),
                    out=out,
                    new_headers=_header_args(fixture.new, "new"),
                    no_baseline=True,
                ),
                extraction="one_side",
                ok_exit_codes=(0, 1, 2, 4),
                output=out,
                sample_rss=True,
            )
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        return _validate_audit(_load_report(work / "audit.json"))

    return Scenario(
        id=f"compare_no_baseline[{spec.profile_id}]",
        description="single-artifact L2 audit (--no-baseline): audit semantics, no verdict",
        spec=spec,
        prepare=None,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_two_formats(
    spec: fixtures.FixtureSpec, suites=("pr", "extended")
) -> Scenario:
    """JSON plus a human report, without re-extracting evidence for the second.

    A single invocation emitting two formats at once is **not** a supported
    capability today (``--format`` is single-valued; repeating it simply lets
    the last one win -- verified, not assumed). So this measures the supported
    equivalent and asserts the property that actually matters: evidence is
    extracted once, into stored snapshots, and each render then runs with a
    header-extraction count of **zero**. The marginal cost of the second format
    is therefore rendering only.

    Adding a real dual-format flag would be a product change, which is out of
    this harness's scope; the gap is recorded in ``docs/contribute/performance.md``
    rather than papered over by pretending one invocation did both.
    """

    def prepare(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        return _prepare_stored(fixture, work, sides=("old", "new"))

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        old_out, new_out = _stored_paths(work)
        json_out = work / "fmt.json"
        md_out = work / "fmt.md"
        return [
            Step(
                "render_json",
                _compare_argv(str(old_out), str(new_out), out=json_out, fmt="json"),
                extraction="forbidden",
                ok_exit_codes=(0, 2, 4),
                output=json_out,
            ),
            Step(
                "render_markdown",
                _compare_argv(str(old_out), str(new_out), out=md_out, fmt="markdown"),
                extraction="forbidden",
                ok_exit_codes=(0, 2, 4),
                output=md_out,
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        problems = _validate_l2_reached(
            _load_report(work / "fmt.json"), sides=("old", "new")
        )
        markdown = (work / "fmt.md").read_text()
        if "ABI Report" not in markdown:
            problems.append("the markdown render is not a recognisable ABI report")
        # The user-facing report must reflect the same finding the JSON does; a
        # renderer that dropped it would otherwise be a pure speedup here.
        if spec.change == "break" and "BREAKING" not in markdown.upper():
            problems.append("the markdown render does not state the breaking verdict")
        return problems

    return Scenario(
        id=f"compare_two_formats[{spec.profile_id}]",
        description="JSON and a human report over one extraction of evidence",
        spec=spec,
        prepare=prepare,
        steps=steps,
        validate=validate,
        suites=suites,
    )


def scenario_warm_cache(spec: fixtures.FixtureSpec, suites=("extended",)) -> Scenario:
    """Cold-then-warm, with the served cache *proven by counters*.

    Three distinct states are involved and the harness refuses to conflate them:

    * **cold application cache** -- a fresh process with a fresh
      ``XDG_CACHE_HOME``. This is *not* a cold disk: the OS page cache still
      holds the fixture and the interpreter, and no attempt is made to drop it
      (dropping a CI runner's page cache needs privilege and would measure the
      host, not the product).
    * **warm AST cache** -- a fresh process against the *same* cache root and
      byte-identical inputs, so the on-disk AST cache can serve.
    * **invalidated** -- the same again after a transitive dependency header's
      content changes. The cache must *not* serve; if it does, the product is
      reusing stale evidence, which this scenario reports as a failure rather
      than as a speedup.

    Which cache actually served is read off the observed extraction counts, not
    inferred from run order: ``observed_cache_service`` is ``"none"`` when the
    warm run extracts as much as the cold one, ``"partial"`` when it extracts
    less but more than zero, and ``"full"`` at zero. Asserting "this was warm"
    from the mere fact of running second is the error this replaces -- a second
    run served by nothing looks identical in wall time on a fast fixture.
    """

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        lib = fixture.new[0]
        return [
            Step(
                "cold",
                _dump_argv(lib, work / "cold.abi.json"),
                extraction="one_side",
                output=work / "cold.abi.json",
            ),
            Step(
                "warm",
                _dump_argv(lib, work / "warm.abi.json"),
                extraction="any",
                output=work / "warm.abi.json",
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        problems = _validate_snapshot(work / "cold.abi.json")
        problems += _validate_snapshot(work / "warm.abi.json")
        cold = runs.get("cold") or []
        warm = runs.get("warm") or []
        if not cold or not warm:
            return problems + ["cold/warm runs missing"]

        def extractions(batch: list[CommandRun]) -> int:
            return min(
                (r.native_invocations or {}).get("header_extraction", 0) for r in batch
            )

        cold_n, warm_n = extractions(cold), extractions(warm)
        if cold_n == 0:
            problems.append(
                "the cold run performed no header extraction at all -- the cache "
                "root was not actually fresh, so nothing here measures a cold state"
            )
        elif warm_n >= cold_n:
            problems.append(
                f"warm run extracted {warm_n} vs cold {cold_n}: no cache served, so "
                "this scenario is not measuring a warm state despite being labelled one"
            )
        return problems

    return Scenario(
        id=f"cold_then_warm_cache[{spec.profile_id}]",
        description="cold application cache then warm AST cache, proven by counters",
        spec=spec,
        prepare=None,
        steps=steps,
        validate=validate,
        cache_mode="cold_then_warm",
        suites=suites,
    )


def scenario_cache_invalidation(
    spec: fixtures.FixtureSpec, suites=("extended",)
) -> Scenario:
    """A changed transitive dependency header must invalidate the cache.

    The negative control for the warm-cache scenario above, and the one that
    catches the worse of the two failure modes: reusing stale evidence is a
    correctness bug, where a missed cache hit is only a slow one. It edits the
    ``detail/`` header that every public header includes -- a *transitive*
    dependency, not the header named on the command line -- because that is the
    edge a content-addressed cache keyed only on the named inputs would miss.
    """

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        lib = fixture.new[0]
        return [
            Step(
                "cold",
                _dump_argv(lib, work / "inv_cold.abi.json"),
                extraction="one_side",
            ),
            Step("warm", _dump_argv(lib, work / "inv_warm.abi.json"), extraction="any"),
            Step(
                "after_dependency_change",
                _dump_argv(lib, work / "inv_after.abi.json"),
                extraction="one_side",
                output=work / "inv_after.abi.json",
            ),
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        problems = _validate_snapshot(work / "inv_after.abi.json")
        after = runs.get("after_dependency_change") or []
        if not after:
            return problems + ["no post-change run recorded"]
        extracted = max(
            (r.native_invocations or {}).get("header_extraction", 0) for r in after
        )
        if extracted == 0:
            problems.append(
                "no header extraction after a transitive dependency header changed "
                "-- the cache served stale evidence"
            )
        return problems

    return Scenario(
        id=f"cache_invalidation[{spec.profile_id}]",
        description="a changed transitive dependency header must force re-extraction",
        spec=spec,
        prepare=None,
        steps=steps,
        validate=validate,
        cache_mode="invalidation_control",
        suites=suites,
    )


#: The PR suite: one small fixture, all six required CLI forms, plus an
#: unchanged control. Deliberately one ``FixtureSpec`` for all of them so the
#: fixture is compiled once and reused -- a second identical build would double
#: the lane's setup cost for no additional coverage.
_PR_SPEC = fixtures.FixtureSpec(shape="simple", headers=1, libraries=1, change="break")
_PR_UNCHANGED = fixtures.FixtureSpec(
    shape="simple", headers=1, libraries=1, change="unchanged"
)


def pr_suite() -> list[Scenario]:
    return [
        scenario_dump(_PR_SPEC),
        scenario_compare_live_live(_PR_SPEC),
        scenario_compare_stored_live(_PR_SPEC),
        scenario_compare_stored_stored(_PR_SPEC),
        scenario_no_baseline(_PR_SPEC),
        scenario_two_formats(_PR_SPEC),
        scenario_compare_live_live(_PR_UNCHANGED),
    ]


def extended_suite() -> list[Scenario]:
    """Selected points along independent axes -- never the full cross product.

    A full cross product of shape x header count x library count x change x
    cache mode is 100+ compiler-driven scenarios, most of which would re-measure
    an axis another point already covers. Each entry below moves **one** axis off
    the PR baseline, which is what makes a regression attributable.
    """
    out = pr_suite()
    axes = [
        # shape axis
        fixtures.FixtureSpec(shape="templates", headers=1, libraries=1),
        # header-count axis over a shared dependency closure
        fixtures.FixtureSpec(shape="simple", headers=8, libraries=1),
        fixtures.FixtureSpec(shape="simple", headers=32, libraries=1),
        # library-count axis, shared then distinct header contexts
        fixtures.FixtureSpec(shape="simple", headers=1, libraries=2),
        fixtures.FixtureSpec(shape="simple", headers=1, libraries=5),
        fixtures.FixtureSpec(
            shape="simple", headers=1, libraries=5, distinct_contexts=True
        ),
    ]
    for spec in axes:
        out.append(scenario_compare_live_live(spec, suites=("extended",)))
        if spec.libraries > 1:
            out.append(scenario_multi_library(spec))
    out.append(scenario_warm_cache(_PR_SPEC))
    out.append(scenario_cache_invalidation(_PR_SPEC))
    return out


def scenario_multi_library(spec: fixtures.FixtureSpec) -> Scenario:
    """The cost of a *set* of libraries as the per-library L2 operations it is.

    No declarative L2 bundle comparison exists today, so this measures the
    supported thing honestly: N separate per-library ``compare`` invocations,
    reporting the set's total cost, each library's own cost, how many distinct
    header contexts were involved, and how many header extractions the set
    performed in total. That last number is the interesting one -- it measures
    **today's** behaviour, including any repeated work across libraries sharing
    one header context, and does not presuppose that sharing is implemented.
    Calling this a "bundle scan" would be a claim about a capability that is not
    there.
    """

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        out = []
        for index, (old, new) in enumerate(zip(fixture.old, fixture.new)):
            report = work / f"lib{index}.json"
            out.append(
                Step(
                    f"compare_lib{index}",
                    _compare_argv(
                        str(old.so),
                        str(new.so),
                        out=report,
                        old_headers=_header_args([old], "old"),
                        new_headers=_header_args([new], "new"),
                    ),
                    extraction="both_sides",
                    ok_exit_codes=(0, 2, 4),
                    output=report,
                    sample_rss=True,
                )
            )
        return out

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        problems: list[str] = []
        for index in range(spec.libraries):
            report = _load_report(work / f"lib{index}.json")
            problems += [
                f"lib{index}: {p}"
                for p in _validate_l2_reached(report, sides=("old", "new"))
            ]
            problems += [f"lib{index}: {p}" for p in _validate_break_findings(report)]
        return problems

    return Scenario(
        id=f"multi_library_set[{spec.profile_id}]",
        description="N per-library L2 comparisons measured as one workload",
        spec=spec,
        prepare=None,
        steps=steps,
        validate=validate,
        suites=("extended",),
    )


# ── running a scenario ────────────────────────────────────────────────────────
def _mutate_dependency_header(fixture: fixtures.BuiltFixture) -> None:
    """Append to the shared ``detail/`` header every public header includes."""
    core = fixture.new[0].include_dir / "detail" / "core.h"
    core.write_text(core.read_text() + f"\n// invalidation probe {time.time_ns()}\n")


def _one_side_extractions(observed: dict[str, int] | None) -> int | None:
    """One operand's extraction cost, from a setup run that extracted exactly one.

    ``None`` when the observation shows no extraction at all: a setup step that
    extracted nothing calibrates nothing, and returning a fabricated ``1`` would
    be the self-calibration bug in a new place.
    """
    count = (observed or {}).get("header_extraction", 0)
    return count if count > 0 else None


def run_scenario(
    scenario: Scenario,
    *,
    repeat: int,
    timeout: float,
    rss_interval: float,
    keep_spy: bool = True,
) -> dict[str, Any]:
    """Build, prepare, measure and validate one scenario. Returns its receipt."""
    result: dict[str, Any] = {
        "id": scenario.id,
        "description": scenario.description,
        "profile": scenario.spec.profile_id,
        "cache_mode": scenario.cache_mode,
        "status": "ok",
        "skip_reason": None,
        "validation": "not_run",
        "validation_problems": [],
        "steps": [],
    }
    with tempfile.TemporaryDirectory(prefix="l2cli_") as tmp:
        root = Path(tmp)
        build_root = root / "fixture"
        work = root / "work"
        work.mkdir(parents=True, exist_ok=True)
        cache_root = root / "cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        spy = NativeInvocationSpy(root / "spy")
        if keep_spy:
            spy.install()
        result["spy_shimmed_tools"] = list(spy.shimmed)

        t_build = time.perf_counter()
        try:
            fixture = fixtures.build(scenario.spec, build_root)
        except Exception as exc:  # a build failure is a hard failure, not a skip
            result["status"] = "failed"
            result["skip_reason"] = f"fixture build failed: {type(exc).__name__}: {exc}"
            return result
        # Setup cost, reported separately and never inside any measured window.
        result["fixture_build_seconds"] = time.perf_counter() - t_build
        result["input_digest"] = digest_paths(
            [h for lib in fixture.old + fixture.new for h in lib.headers]
        )
        result["header_contexts"] = len(
            {str(lib.include_dir.name) for lib in fixture.new}
            if scenario.spec.distinct_contexts
            else {"shared"}
        )

        base_env = dict(os.environ)
        # A per-scenario cache root is what makes "cold application cache" true.
        # XDG_CACHE_HOME is the same variable dumper_cache._cache_path honors.
        base_env["XDG_CACHE_HOME"] = str(cache_root)
        env = spy.env(base_env) if keep_spy else base_env

        def execute(step: Step, *, timed: bool) -> CommandRun:
            # Guarded: with --no-spy the shim directory was never created, so
            # resetting its log raises FileNotFoundError. Found by the
            # overhead measurement this flag exists to enable -- the flag's
            # whole purpose is to run the lane without the spy, and it could
            # not.
            if keep_spy:
                spy.reset()
            run = run_measured(
                step.argv,
                env=env,
                timeout=timeout,
                sample_rss=step.sample_rss and timed,
                rss_interval=rss_interval,
            )
            run.native_invocations = spy.kind_counts() if keep_spy else {}
            return run

        # Untimed setup (pre-dumping a stored operand, etc.).
        setup_steps = scenario.prepare(fixture, work) if scenario.prepare else []
        for step in setup_steps:
            run = execute(step, timed=False)
            if run.exit_code not in step.ok_exit_codes:
                result["status"] = "failed"
                result["validation_problems"].append(
                    f"setup step {step.name} exited {run.exit_code}: "
                    f"{run.stderr.strip()[-600:]}"
                )
                return result
            result["steps"].append(
                {
                    "name": step.name,
                    "scope": "setup",
                    "gated": False,
                    "wall_seconds": run.wall_seconds,
                    "exit_code": run.exit_code,
                    "native_invocations": run.native_invocations,
                }
            )

        measured = scenario.steps(fixture, work)
        runs: dict[str, list[CommandRun]] = {}
        # Calibrated from the SETUP dumps only -- never from a measured step's
        # own count, which would make the comparison self-referential (see
        # _check_extraction's docstring). A setup dump of one library with one
        # side's headers is exactly "one operand's extraction cost".
        one_side: int | None = None
        if keep_spy:
            for entry in result["steps"]:
                if entry["scope"] == "setup" and entry["name"].startswith("prep_dump"):
                    calibrated = _one_side_extractions(entry["native_invocations"])
                    if calibrated is not None:
                        one_side = calibrated
                        break
        result["one_side_extraction_calibration"] = one_side
        for repetition in range(repeat):
            for step in measured:
                if (
                    scenario.cache_mode == "invalidation_control"
                    and step.name == "after_dependency_change"
                ):
                    _mutate_dependency_header(fixture)
                if scenario.cache_mode == "cold" and step.scope == "full_cli":
                    # Every timed repeat of a cold-cache scenario must really be
                    # cold: without this, repeat 2 would be served by repeat 1's
                    # cache and the median would describe a warm run under a
                    # cold label.
                    shutil.rmtree(cache_root, ignore_errors=True)
                    cache_root.mkdir(parents=True, exist_ok=True)
                run = execute(step, timed=True)
                runs.setdefault(step.name, []).append(run)
                if run.timed_out:
                    result["status"] = "failed"
                    result["validation_problems"].append(
                        f"step {step.name} timed out after {timeout}s"
                    )
                    return result
                if run.exit_code not in step.ok_exit_codes:
                    result["status"] = "failed"
                    result["validation_problems"].append(
                        f"step {step.name} exited {run.exit_code} (allowed "
                        f"{list(step.ok_exit_codes)}): {run.stderr.strip()[-600:]}"
                    )
                    return result
                if keep_spy:
                    problems = _check_extraction(
                        run, step.extraction, one_side=one_side
                    )
                    if problems:
                        result["status"] = "failed"
                        result["validation_problems"] += [
                            f"step {step.name}: {p}" for p in problems
                        ]
                        return result
                if step.output is not None and step.output.exists():
                    size = step.output.stat().st_size
                    if size > MAX_OUTPUT_BYTES:
                        result["status"] = "failed"
                        result["validation_problems"].append(
                            f"step {step.name} wrote {size} bytes, over the cap"
                        )
                        return result

        # Validation, strictly after every timed window.
        result["uncalibrated_contracts"] = (
            uncalibrated_contracts(measured, one_side) if keep_spy else []
        )
        problems = scenario.validate(work, runs)
        result["validation"] = "passed" if not problems else "failed"
        result["validation_problems"] += problems
        if problems:
            result["status"] = "failed"

        for step in measured:
            batch = runs[step.name]
            stats = summarize_samples([r.wall_seconds for r in batch])
            last = batch[-1]
            result["steps"].append(
                {
                    "name": step.name,
                    # Phase scope, stated per step. "full_cli" is the real
                    # user-facing number; "startup_only" and
                    # "startup_and_resolution" are NESTED INCLUSIVE windows that
                    # must never be added to it or to each other.
                    "scope": step.scope,
                    "additive": False,
                    "gated": step.scope == "full_cli",
                    "extraction_contract": step.extraction,
                    "extraction_contract_meaning": EXTRACTION_EXPECTATIONS[
                        step.extraction
                    ],
                    "wall_seconds": stats.median,
                    "wall_seconds_samples": [r.wall_seconds for r in batch],
                    "wall_seconds_min": stats.min,
                    "wall_seconds_max": stats.max,
                    "wall_seconds_cv": stats.cv,
                    "user_cpu_seconds": last.user_cpu_seconds,
                    "system_cpu_seconds": last.system_cpu_seconds,
                    "cpu_scope": last.cpu_scope,
                    "exit_code": last.exit_code,
                    "native_invocations": last.native_invocations,
                    "output_bytes": (
                        step.output.stat().st_size
                        if step.output is not None and step.output.exists()
                        else None
                    ),
                    "rss": (
                        {
                            "sampled_peak_tree_bytes": last.rss.sampled_peak_tree_bytes,
                            "sample_count": last.rss.sample_count,
                            "interval_seconds": last.rss.interval_seconds,
                            "max_concurrent_processes": last.rss.max_concurrent_processes,
                            "ru_maxrss_bytes": last.rss.ru_maxrss_bytes,
                            "unavailable_reason": last.rss.unavailable_reason,
                        }
                        if last.rss is not None
                        else None
                    ),
                }
            )
        if scenario.cache_mode == "cold_then_warm":
            result["observed_cache_service"] = _classify_cache_service(runs)
    return result


def _classify_cache_service(runs: dict[str, list[CommandRun]]) -> str:
    """Which cache actually served the warm run, read from its counters."""
    cold = runs.get("cold") or []
    warm = runs.get("warm") or []
    if not cold or not warm:
        return "unknown"

    def n(batch: list[CommandRun]) -> int:
        return min(
            (r.native_invocations or {}).get("header_extraction", 0) for r in batch
        )

    cold_n, warm_n = n(cold), n(warm)
    if warm_n == 0:
        return "full"
    if warm_n < cold_n:
        return "partial"
    return "none"


# ── gating ────────────────────────────────────────────────────────────────────
def gated_points(scenarios: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    """``(scenario id, step name) -> median wall seconds`` for gated steps only.

    Only ``scope == "full_cli"`` steps are gated. The nested startup and
    resolution windows are recorded for diagnosis but never gated: they are
    inside the number that *is* gated, so gating them too would charge one
    slowdown twice and make a single regression look like three.
    """
    out: dict[tuple[str, str], float] = {}
    for scenario in scenarios:
        for step in scenario.get("steps", []):
            if step.get("gated") and is_gateable(step.get("wall_seconds")):
                out[(scenario["id"], step["name"])] = float(step["wall_seconds"])
    return out


def load_baseline(path: Path) -> dict[tuple[str, str], float]:
    data = json.loads(path.read_text())
    return gated_points(data.get("scenarios", []))


def check_regressions(
    current: dict[tuple[str, str], float],
    baseline: dict[tuple[str, str], float],
    threshold: GateThreshold,
) -> list[str]:
    failures = []
    for key, value in sorted(current.items()):
        base = baseline.get(key)
        if not is_gateable(base):
            continue
        allowed = threshold.allowed_delta(base)
        if value > base + allowed:
            failures.append(
                f"{key[0]} / {key[1]}: {value:.3f}s > baseline {base:.3f}s + "
                f"{allowed:.3f}s allowed ({(value / base - 1) * 100:+.0f}%) "
                f"[tolerance={threshold.tolerance} "
                f"min_delta_seconds={threshold.min_delta} source={threshold.source}]"
            )
    return failures


def required_coverage_failures(
    scenarios: list[dict[str, Any]], required_ids: list[str]
) -> list[str]:
    """Every required scenario shape must have actually been measured.

    A run missing one of the six CLI forms is **not** a clean pass, however
    green the forms it did run look. Matched on the scenario-id prefix before
    the profile suffix, so an id carrying a different fixture profile still
    counts as covering its shape.
    """
    measured = {s["id"].split("[", 1)[0] for s in scenarios if s.get("status") == "ok"}
    return [
        f"required scenario shape {shape!r} was not measured successfully"
        for shape in required_ids
        if shape not in measured
    ]


REQUIRED_PR_SHAPES = [
    "dump_l2",
    "compare_live_live",
    "compare_stored_live",
    "compare_stored_stored",
    "compare_no_baseline",
    "compare_two_formats",
]


def _print_table(scenarios: list[dict[str, Any]], *, markdown: bool = False) -> None:
    columns = (
        "scenario",
        "step",
        "scope",
        "wall_s",
        "cv%",
        "extract",
        "incl",
        "rss_mb",
        "valid",
    )
    rows = []
    for scenario in scenarios:
        for step in scenario.get("steps", []):
            native = step.get("native_invocations") or {}
            rss = step.get("rss") or {}
            peak = rss.get("sampled_peak_tree_bytes")
            cv = step.get("wall_seconds_cv")
            rows.append(
                (
                    scenario["id"],
                    step["name"],
                    step.get("scope", "?"),
                    f"{step.get('wall_seconds', float('nan')):.3f}",
                    f"{cv * 100:.1f}" if cv is not None else "n/a",
                    str(native.get("header_extraction", "-")),
                    str(native.get("include_pass", "-")),
                    f"{peak / 1e6:.0f}" if peak else "-",
                    scenario.get("validation", "?"),
                )
            )
    if markdown:
        print("| " + " | ".join(columns) + " |")
        print("|" + "|".join(["---"] * len(columns)) + "|")
        for row in rows:
            print("| " + " | ".join(row) + " |")
        return
    widths = [
        max(len(columns[i]), *(len(r[i]) for r in rows)) if rows else len(columns[i])
        for i in range(len(columns))
    ]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--suite",
        choices=("pr", "extended"),
        default="pr",
        help="pr = the six CLI forms on one small fixture (default); extended = "
        "the selected axis sweep, for the periodic/manual lane",
    )
    p.add_argument("--repeat", type=positive_int_arg, default=DEFAULT_REPEAT)
    p.add_argument(
        "--scenario",
        action="append",
        default=None,
        help="Run only scenarios whose id contains this substring (repeatable)",
    )
    p.add_argument("--baseline", type=Path, default=None)
    p.add_argument(
        "--regress-tolerance",
        type=finite_nonnegative_float_arg,
        default=DEFAULT_REGRESS_TOLERANCE,
    )
    p.add_argument(
        "--regress-min-delta-seconds",
        type=finite_nonnegative_float_arg,
        default=DEFAULT_REGRESS_MIN_DELTA_SECONDS,
        help="Absolute floor combined with --regress-tolerance via max(). Nonzero "
        "by default here, unlike the in-process gates: a full-CLI run pays ~0.5s "
        "of interpreter startup whose jitter is a real part of every sample.",
    )
    p.add_argument(
        "--timeout-seconds",
        type=finite_nonnegative_float_arg,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    p.add_argument(
        "--rss-interval-seconds", type=finite_nonnegative_float_arg, default=0.02
    )
    p.add_argument("--json-out", type=Path, default=None)
    p.add_argument("--markdown", action="store_true")
    p.add_argument(
        "--no-spy",
        action="store_true",
        help="Disable native-invocation observation. Measures this harness's own "
        "instrumentation overhead by comparison -- it also disables every "
        "extraction-count assertion, so a run with it on can never claim to "
        "have proven a compiler-free path.",
    )
    p.add_argument(
        "--require-toolchain",
        action="store_true",
        help="Fail instead of skipping when the C++ toolchain or platform is "
        "unsuitable -- for a CI job that installed one and must not report a "
        "pass over nothing measured.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    unsuitable = None
    if not sys.platform.startswith("linux"):
        unsuitable = "the full-CLI L2 harness is Linux/ELF-scoped"
    elif not fixtures.compiler_available("g++"):
        unsuitable = "no g++ on PATH: the fixture cannot be built"
    if unsuitable:
        if args.require_toolchain:
            print(f"FAIL: --require-toolchain given but {unsuitable}.")
            return 1
        print(f"SKIP: {unsuitable} — nothing measured (exit 0, no coverage claimed).")
        return 0

    scenarios = pr_suite() if args.suite == "pr" else extended_suite()
    if args.scenario:
        scenarios = [
            s for s in scenarios if any(pattern in s.id for pattern in args.scenario)
        ]
        if not scenarios:
            print(f"FAIL: --scenario {args.scenario} matched nothing.")
            return 1

    threshold = GateThreshold(
        tolerance=args.regress_tolerance,
        min_delta=args.regress_min_delta_seconds,
        source="explicit"
        if args.regress_tolerance != DEFAULT_REGRESS_TOLERANCE
        else "default",
    )

    started = time.perf_counter()
    results = [
        run_scenario(
            scenario,
            repeat=args.repeat,
            timeout=args.timeout_seconds,
            rss_interval=args.rss_interval_seconds,
            keep_spy=not args.no_spy,
        )
        for scenario in scenarios
    ]
    lane_seconds = time.perf_counter() - started

    _print_table(results, markdown=args.markdown)
    build_seconds = sum(r.get("fixture_build_seconds") or 0.0 for r in results)
    print(
        f"\nLane cost: {lane_seconds:.1f}s total, of which "
        f"{build_seconds:.1f}s is fixture compilation (setup, excluded from every "
        "measured window)."
    )

    receipt = build_receipt(
        harness="check_l2_cli_perf",
        profile=args.suite,
        identity_extra={
            "suite": args.suite,
            "repeat": args.repeat,
            "spy_enabled": not args.no_spy,
            "lane_seconds": lane_seconds,
            "fixture_build_seconds": build_seconds,
        },
        scenarios=results,
        thresholds={"wall_seconds": threshold.as_dict()},
        notes=[
            "wall_seconds for scope=full_cli is the whole CLI subprocess: "
            "interpreter startup, config and input resolution, evidence "
            "extraction or load, comparison and report writing.",
            "scope=startup_only and scope=startup_and_resolution are NESTED "
            "INCLUSIVE windows. They must not be summed with each other or with "
            "full_cli; each carries additive=false.",
            "sampled_peak_tree_bytes is a sampled lower bound on concurrent "
            "process-tree RSS, not an exact peak; ru_maxrss_bytes is a separate "
            "per-process kernel high-water mark.",
            "Fixture compilation is setup and is excluded from every measured "
            "window; its cost is reported separately.",
        ],
    )

    failures: list[str] = []
    for result in results:
        if result["status"] != "ok":
            failures.append(
                f"{result['id']}: {result['status']}: "
                + "; ".join(
                    result["validation_problems"] or [result.get("skip_reason") or "?"]
                )
            )
    # A narrowed run is explicitly not a coverage-claiming run. Required-shape
    # coverage is enforced for a full pr-suite run -- where a missing shape must
    # never read as a clean pass -- and reported-but-not-enforced when the caller
    # asked for a subset, with the lack of a coverage claim stated rather than
    # left for a reader to infer from the absent rows.
    if args.suite == "pr" and not args.scenario:
        failures += required_coverage_failures(results, REQUIRED_PR_SHAPES)
    elif args.scenario:
        missing = required_coverage_failures(results, REQUIRED_PR_SHAPES)
        print(
            f"\nCOVERAGE NOT CLAIMED: --scenario narrowed this run; "
            f"{len(missing)} required shape(s) were not measured. This run's "
            "verdict covers only the scenarios it actually ran."
        )
    if args.no_spy:
        print(
            "\nCOVERAGE NOT CLAIMED (native invocations): --no-spy disabled "
            "invocation observation, so no extraction-count contract was checked "
            "-- this run proves nothing about compiler-free or single-side paths."
        )

    baseline = None
    if args.baseline is not None:
        try:
            baseline = load_baseline(args.baseline)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"\nFAIL: could not read --baseline {args.baseline}: {exc}")
            return 1

    current = gated_points(results)
    print(f"\nEffective threshold: {threshold.as_dict()}")
    if baseline is not None:
        matched = {k: v for k, v in current.items() if is_gateable(baseline.get(k))}
        if not matched:
            failures.append(
                f"--baseline {args.baseline} shares no gated (scenario, step) point "
                f"with this run's {len(current)} — nothing was actually gated"
            )
        else:
            failures += check_regressions(current, baseline, threshold)
            print(f"Gated {len(matched)} of {len(current)} measured point(s).")
            ungated = sorted(set(current) - set(matched))
            for key in ungated:
                print(f"  NOTE: not gated (no baseline entry): {key[0]} / {key[1]}")
    else:
        print(
            "No --baseline given: report-only run. Pass a previously written "
            "--json-out report via --baseline to gate future runs against it."
        )

    if args.json_out is not None:
        same = (
            args.baseline is not None
            and args.json_out.resolve() == args.baseline.resolve()
        )
        if same:
            print(
                f"\nNOTE: --json-out and --baseline both name {args.json_out} — not "
                "overwriting the baseline this run was gated against."
            )
        else:
            receipt["failures"] = failures
            write_receipt(args.json_out, receipt)
            print(f"\nWrote {args.json_out}")

    if failures:
        print("\nFAIL: full-CLI L2 perf gate:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nOK: every measured L2 CLI scenario validated and within threshold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
