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
import subprocess
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
    finite_positive_float_arg,
    is_gateable,
    positive_int_arg,
    summarize_samples,
)
from perf_receipt import (  # noqa: E402
    CommandRun,
    NativeInvocationSpy,
    RssSample,
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


# ── validation (always outside the timed window) ──────────────────────────────
def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
        # Needs no calibration: the contract is an absolute zero -- and it is a
        # zero over EVERY observed native invocation, not only the extraction
        # bucket. A stored-operand path claims no compiler ran at all, so an
        # `include_pass`, a `--version` probe, or anything classified `other`
        # falsifies the claim exactly as an AST extraction does. Checking only
        # `header_extraction` would let a regression that starts spawning
        # `clang++ -M` or `g++ --version` while loading two stored snapshots
        # pass a scenario whose entire point is that it spawns nothing.
        nonzero = {
            kind: count
            for kind, count in (run.native_invocations or {}).items()
            if count
        }
        if nonzero:
            detail = ", ".join(
                f"{kind}={count}" for kind, count in sorted(nonzero.items())
            )
            return [
                f"native compiler invocation(s) observed ({detail}) on a "
                "stored-operand path that must perform none -- the stored "
                "snapshot was re-extracted, or the path grew a new native call"
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
            exports={"json": out},
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
                    exports={"json": out},
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
        argv = _compare_argv(str(old_out), str(new_out), exports={"json": out})
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
                    exports={"json": out},
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
    """JSON plus a human report from **one** comparison, evidence extracted once.

    ``-o`` is repeatable (``-o FORMAT=DESTINATION``, ADR-068 slices 7m/7n) and
    every export renders the same completed analysis, so this is a single
    ``compare`` invocation producing both artifacts -- not two runs.

    The measurement that makes it worth a scenario is the one the CLI's own
    promise invites: *asking for a second artifact must not re-extract
    evidence*. That is checked directly rather than trusted, by requiring the
    invocation's header-extraction count to equal exactly one side's (the
    operands are a stored snapshot and a live artifact here, so one side is the
    correct figure) -- a re-run of the analysis for the second renderer would
    show up as a doubled count.

    Deliberately stored-old/live-new rather than stored/stored: with both
    operands stored the extraction count is zero either way, so it could not
    distinguish one analysis from two. An assertion that cannot fail is not one.
    """

    def prepare(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        return _prepare_stored(fixture, work, sides=("old",))

    def steps(fixture: fixtures.BuiltFixture, work: Path) -> list[Step]:
        old_out, _ = _stored_paths(work)
        json_out = work / "fmt.json"
        md_out = work / "fmt.md"
        return [
            Step(
                "compare_exporting_two_formats",
                _compare_argv(
                    str(old_out),
                    str(fixture.new[0].so),
                    exports={"json": json_out, "markdown": md_out},
                    new_headers=_header_args(fixture.new, "new"),
                ),
                # One side's worth, calibrated from prepare()'s own dump: two
                # exports must not mean two analyses.
                extraction="one_side",
                ok_exit_codes=(0, 2, 4),
                output=json_out,
                sample_rss=True,
            )
        ]

    def validate(work: Path, runs: dict[str, list[CommandRun]]) -> list[str]:
        problems = _validate_l2_reached(
            _load_report(work / "fmt.json"), sides=("old", "new")
        )
        markdown_path = work / "fmt.md"
        if not markdown_path.exists():
            return problems + ["the second export produced no file at all"]
        markdown = markdown_path.read_text(encoding="utf-8")
        if "ABI Report" not in markdown:
            problems.append("the markdown render is not a recognisable ABI report")
        # Both artifacts must describe the same analysis. A renderer that
        # dropped the finding would otherwise read as a pure speedup here.
        if spec.change == "break" and "BREAKING" not in markdown.upper():
            problems.append("the markdown render does not state the breaking verdict")
        return problems

    return Scenario(
        id=f"compare_two_formats[{spec.profile_id}]",
        description="one comparison exporting JSON and a human report together",
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
                        exports={"json": report},
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
    core.write_text(
        core.read_text(encoding="utf-8")
        + f"\n// invalidation probe {time.time_ns()}\n",
        encoding="utf-8",
    )


def _one_side_extractions(observed: dict[str, int] | None) -> int | None:
    """One operand's extraction cost, from a setup run that extracted exactly one.

    ``None`` when the observation shows no extraction at all: a setup step that
    extracted nothing calibrates nothing, and returning a fabricated ``1`` would
    be the self-calibration bug in a new place.
    """
    count = (observed or {}).get("header_extraction", 0)
    return count if count > 0 else None


#: Cache modes whose steps form one *sequence* that must begin cold, with the
#: steps inside it deliberately sharing whatever the earlier ones warmed.
_SEQUENCE_CACHE_MODES = frozenset({"cold_then_warm", "invalidation_control"})


def _reset_cache(cache_root: Path) -> None:
    shutil.rmtree(cache_root, ignore_errors=True)
    cache_root.mkdir(parents=True, exist_ok=True)


def _resolved_dependency_roots(fixture: fixtures.BuiltFixture) -> set[str]:
    """The distinct resolved ``detail/core.h`` paths the new side's libraries parse.

    One entry means a genuinely shared header context; N entries means N distinct
    ones. Derived from the filesystem rather than from
    ``FixtureSpec.distinct_contexts`` on purpose -- the flag states the intent,
    this states what was actually built, and the first version of this fixture
    had them disagree (every library got its own byte-identical copy, so the
    "shared" arm shared nothing while the receipt said it did).
    """
    roots: set[str] = set()
    for lib in fixture.new:
        for candidate in (lib.include_dir, *lib.extra_includes):
            core = candidate / "detail" / "core.h"
            if core.exists():
                roots.add(str(core.resolve()))
    return roots


def _build_fixture_for(
    scenario: Scenario, build_root: Path
) -> tuple[fixtures.BuiltFixture | None, dict[str, Any]]:
    """Build *scenario*'s fixture, returning it plus the receipt fields it yields.

    ``None`` with a ``status``/``skip_reason`` pair when the build fails: a
    scenario that could not build its own inputs measured nothing, so that is a
    hard failure and never a skip.

    The build's own duration is reported separately and is never inside any timed
    window -- compiling a fixture is setup, not something a user's command pays
    for.
    """
    start = time.perf_counter()
    try:
        fixture = fixtures.build(scenario.spec, build_root)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        # Those three are what a real compile can raise; catching more would
        # swallow a bug in this harness as if it were a toolchain problem.
        return None, {
            "status": "failed",
            "skip_reason": f"fixture build failed: {type(exc).__name__}: {exc}",
        }
    return fixture, {
        "fixture_build_seconds": time.perf_counter() - start,
        "input_digest": digest_paths(
            [h for lib in fixture.old + fixture.new for h in lib.headers]
        ),
        # Counted from the RESOLVED dependency header each library parses, not
        # from a label or an include-directory name: the shared arm is only
        # shared because every library reaches one physical file, and counting
        # anything else would report the number the flag claims rather than the
        # number the fixture has.
        "header_contexts": len(_resolved_dependency_roots(fixture)),
    }


def _run_setup_steps(
    setup_steps: list[Step], execute: Callable[..., CommandRun]
) -> tuple[list[dict[str, Any]], str | None]:
    """Run the untimed setup steps, returning their receipt rows and any failure.

    Setup rows are returned even when a later step fails, so a reader can see how
    far preparation got. Their scope is ``"setup"`` and they are never gated: a
    pre-dump of a stored operand is not something a user's command pays for.
    """
    rows: list[dict[str, Any]] = []
    for step in setup_steps:
        run = execute(step, timed=False)
        rows.append(
            {
                "name": step.name,
                "scope": "setup",
                "gated": False,
                "wall_seconds": run.wall_seconds,
                "exit_code": run.exit_code,
                "native_invocations": run.native_invocations,
            }
        )
        if run.exit_code not in step.ok_exit_codes:
            return rows, (
                f"setup step {step.name} exited {run.exit_code}: "
                f"{run.stderr.strip()[-600:]}"
            )
    return rows, None


def _needs_cold_cache(cache_mode: str, step: Step, index: int) -> bool:
    """Whether the cache must be emptied before this step of this repetition.

    Two different lifecycles, and conflating them was a real defect:

    * ``cold`` — **every** measured step must start cold, so the cache is reset
      before each one. Otherwise repeat 2 is served by repeat 1's cache and the
      median describes a warm run under a cold label.
    * ``cold_then_warm`` / ``invalidation_control`` — the steps form one
      *sequence* (cold, then warm, then optionally after-a-change) whose whole
      point is that later steps see what earlier ones warmed. So the cache is
      reset once per repetition, before the sequence's **first** step, and left
      alone inside it.

    The original code reset only for ``cache_mode == "cold"``, which meant the
    sequence modes never reset at all: at ``--repeat 2`` the second repetition's
    step *named* ``cold`` was served by the first repetition's cache, observed
    zero header extractions, and failed its own contract. The extended CI lane
    runs ``--repeat 3``, so it would have failed deterministically; every local
    run that missed it used ``--repeat 1``, where the bug cannot appear.
    """
    if cache_mode in _SEQUENCE_CACHE_MODES:
        return index == 0
    return cache_mode == "cold" and step.scope == "full_cli"


def _step_failure(
    step: Step,
    run: CommandRun,
    *,
    timeout: float,
    one_side: int | None,
    check_extraction: bool,
) -> list[str] | None:
    """Why *run* disqualifies its step, or ``None`` when it is a valid sample.

    Four independent ways a measured step is not a measurement: it timed out, it
    exited outside its allowed set, its observed extraction count broke its
    contract, or it wrote an output past the size cap. Each returns a message
    rather than raising, so the caller decides the scenario's fate in one place.
    """
    if run.timed_out:
        return [f"step {step.name} timed out after {timeout}s"]
    if run.exit_code not in step.ok_exit_codes:
        return [
            f"step {step.name} exited {run.exit_code} (allowed "
            f"{list(step.ok_exit_codes)}): {run.stderr.strip()[-600:]}"
        ]
    if check_extraction:
        problems = _check_extraction(run, step.extraction, one_side=one_side)
        if problems:
            return [f"step {step.name}: {p}" for p in problems]
    if step.output is not None and step.output.exists():
        size = step.output.stat().st_size
        if size > MAX_OUTPUT_BYTES:
            return [f"step {step.name} wrote {size} bytes, over the cap"]
    return None


def _run_measured_steps(
    measured: list[Step],
    *,
    execute: Callable[..., CommandRun],
    repeat: int,
    runs: dict[str, list[CommandRun]],
    scenario: Scenario,
    fixture: fixtures.BuiltFixture,
    cache_root: Path,
    timeout: float,
    one_side: int | None,
    check_extraction: bool,
) -> list[str] | None:
    """Run every measured step *repeat* times, collecting samples into *runs*.

    Returns ``None`` when every sample is valid, or the messages explaining the
    first invalid one -- at which point the scenario is abandoned rather than
    reported with a partial sample set, since a median over some-of-the-repeats
    is not the figure the receipt claims.
    """
    for _repetition in range(repeat):
        for index, step in enumerate(measured):
            if (
                scenario.cache_mode == "invalidation_control"
                and step.name == "after_dependency_change"
            ):
                _mutate_dependency_header(fixture)
            if _needs_cold_cache(scenario.cache_mode, step, index):
                _reset_cache(cache_root)
            run = execute(step, timed=True)
            runs.setdefault(step.name, []).append(run)
            failure = _step_failure(
                step,
                run,
                timeout=timeout,
                one_side=one_side,
                check_extraction=check_extraction,
            )
            if failure is not None:
                return failure
    return None


def _rss_receipt(rss: RssSample | None) -> dict[str, Any] | None:
    """The memory block of a step's receipt, or ``None`` when not sampled.

    Every field keeps the name that states what it is: ``sampled_`` for the
    interval-sampled tree figure and ``ru_maxrss_bytes`` for the kernel
    per-process high-water mark, with the interval and any unavailability reason
    alongside, so a reader can never mistake the first for an exact peak.
    ``ru_maxrss_bytes`` is present only when this run is what raised the kernel's
    cumulative children mark; otherwise it is ``None`` and ``ru_maxrss_scope``
    names the earlier, heavier child that holds it (see
    ``perf_receipt._children_high_water``) -- attaching a cumulative figure to a
    step that did not produce it is how a tiny step inherits a 2 GB reading.
    """
    if rss is None:
        return None
    return {
        "sampled_peak_tree_bytes": rss.sampled_peak_tree_bytes,
        "sample_count": rss.sample_count,
        "interval_seconds": rss.interval_seconds,
        "max_concurrent_processes": rss.max_concurrent_processes,
        "ru_maxrss_bytes": rss.ru_maxrss_bytes,
        "ru_maxrss_scope": rss.ru_maxrss_scope,
        "unavailable_reason": rss.unavailable_reason,
    }


def _step_receipt(step: Step, batch: list[CommandRun]) -> dict[str, Any]:
    """One measured step's receipt row, summarized over its repeats.

    Timing comes from the whole *batch* (median plus spread); the single-valued
    observations -- exit code, invocation counts, output size, memory -- come
    from the last repeat, since they do not vary meaningfully across repeats of
    the same command and a median of them would be meaningless.
    """
    stats = summarize_samples([r.wall_seconds for r in batch])
    last = batch[-1]
    return {
        "name": step.name,
        # Phase scope, stated per step. "full_cli" is the real user-facing
        # number; "startup_only" and "startup_and_resolution" are NESTED
        # INCLUSIVE windows that must never be added to it or to each other.
        "scope": step.scope,
        "additive": False,
        "gated": step.scope == "full_cli",
        "extraction_contract": step.extraction,
        "extraction_contract_meaning": EXTRACTION_EXPECTATIONS[step.extraction],
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
        "rss": _rss_receipt(last.rss),
    }


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

        fixture, build_rows = _build_fixture_for(scenario, build_root)
        result.update(build_rows)
        if fixture is None:
            return result

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
        setup_rows, setup_failure = _run_setup_steps(
            scenario.prepare(fixture, work) if scenario.prepare else [], execute
        )
        result["steps"].extend(setup_rows)
        if setup_failure is not None:
            result["status"] = "failed"
            result["validation_problems"].append(setup_failure)
            return result

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
        abort = _run_measured_steps(
            measured,
            execute=execute,
            repeat=repeat,
            runs=runs,
            scenario=scenario,
            fixture=fixture,
            cache_root=cache_root,
            timeout=timeout,
            one_side=one_side if keep_spy else None,
            check_extraction=keep_spy,
        )
        if abort is not None:
            result["status"] = "failed"
            result["validation_problems"] += abort
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

        result["steps"].extend(
            _step_receipt(step, runs[step.name]) for step in measured
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
        # A scenario that did not pass is not a measurement. Its timings exist in
        # the receipt (the receipt is written before the exit code is decided, on
        # purpose -- a failed run's numbers are diagnostic), but using them as a
        # baseline would gate a PR against a base run whose L2 correctness
        # validation failed: a base that fell back to binary-only evidence is
        # *faster*, so the head would be measured against a number no correct run
        # produces. Skipped on both sides for symmetry -- the same filter runs
        # over this run's own scenarios, so a failed head scenario never
        # contributes a point either.
        if scenario.get("status") != "ok":
            continue
        for step in scenario.get("steps", []):
            if step.get("gated") and is_gateable(step.get("wall_seconds")):
                out[(scenario["id"], step["name"])] = float(step["wall_seconds"])
    return out


def rejected_baseline_scenarios(scenarios: list[dict[str, Any]]) -> list[str]:
    """Scenario ids a baseline carries but that :func:`gated_points` refuses.

    Reported rather than silently dropped: "the baseline had three scenarios and
    two are usable" is information a reader needs to judge the gate's coverage,
    and the whole-run "shares no gated point" failure only fires when *every*
    one is unusable.
    """
    return [
        str(scenario.get("id"))
        for scenario in scenarios
        if scenario.get("status") != "ok"
    ]


def load_baseline(path: Path) -> tuple[dict[tuple[str, str], float], list[str]]:
    """A baseline report's gated points, plus the scenario ids it refused.

    Returns both halves so the caller can state what it is gating against: a
    baseline whose scenarios failed validation contributes no points, and saying
    so is the difference between "nothing to gate" and "gated against a broken
    base".
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", [])
    return gated_points(scenarios), rejected_baseline_scenarios(scenarios)


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


def host_unsuitable_reason() -> str | None:
    """Why this host cannot run the harness, or ``None`` when it can."""
    if not sys.platform.startswith("linux"):
        return "the full-CLI L2 harness is Linux/ELF-scoped"
    if not fixtures.compiler_available("g++"):
        return "no g++ on PATH: the fixture cannot be built"
    return None


def select_scenarios(suite: str, patterns: list[str] | None) -> list[Scenario]:
    """The suite's scenarios, narrowed to those whose id contains any *pattern*."""
    scenarios = pr_suite() if suite == "pr" else extended_suite()
    if not patterns:
        return scenarios
    return [s for s in scenarios if any(pattern in s.id for pattern in patterns)]


def _report_coverage_claim(
    results: list[dict[str, Any]], *, suite: str, narrowed: bool, no_spy: bool
) -> list[str]:
    """Enforce or disclaim required-shape coverage, returning any failures.

    A full ``pr``-suite run must measure every required shape -- a missing one is
    a failure, never a clean pass over the shapes that did run. A deliberately
    narrowed run instead *states* that it claims no coverage, which is the only
    honest form a local subset run can take. ``--no-spy`` disclaims separately,
    since it disables every extraction-count contract and so proves nothing about
    the compiler-free or single-side paths however many scenarios ran.
    """
    failures: list[str] = []
    if suite == "pr" and not narrowed:
        failures += required_coverage_failures(results, REQUIRED_PR_SHAPES)
    elif narrowed:
        missing = required_coverage_failures(results, REQUIRED_PR_SHAPES)
        print(
            f"\nCOVERAGE NOT CLAIMED: --scenario narrowed this run; "
            f"{len(missing)} required shape(s) were not measured. This run's "
            "verdict covers only the scenarios it actually ran."
        )
    if no_spy:
        print(
            "\nCOVERAGE NOT CLAIMED (native invocations): --no-spy disabled "
            "invocation observation, so no extraction-count contract was checked "
            "-- this run proves nothing about compiler-free or single-side paths."
        )
    return failures


def _gate_against_baseline(
    current: dict[tuple[str, str], float],
    baseline: dict[tuple[str, str], float] | None,
    threshold: GateThreshold,
    *,
    baseline_path: Path | None,
) -> list[str]:
    """Compare *current* against *baseline*, printing what was and was not gated.

    Returns the gate's failure messages. A baseline sharing **no** point with this
    run is itself a failure rather than a clean pass: an axis change or a
    mistargeted file otherwise produces an empty failure list, which reads
    identically to "everything was fine".
    """
    if baseline is None:
        print(
            "No --baseline given: report-only run. Pass a previously written "
            "--json-out report via --baseline to gate future runs against it."
        )
        return []
    matched = {k: v for k, v in current.items() if is_gateable(baseline.get(k))}
    if not matched:
        return [
            f"--baseline {baseline_path} shares no gated (scenario, step) point "
            f"with this run's {len(current)} — nothing was actually gated"
        ]
    failures = check_regressions(current, baseline, threshold)
    print(f"Gated {len(matched)} of {len(current)} measured point(s).")
    for key in sorted(set(current) - set(matched)):
        print(f"  NOTE: not gated (no baseline entry): {key[0]} / {key[1]}")
    return failures


def _read_baseline_or_fail(
    path: Path,
) -> tuple[dict[tuple[str, str], float] | None, str | None]:
    """Load *path*, or return the message explaining why it could not be read."""
    try:
        baseline, rejected = load_baseline(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"could not read --baseline {path}: {exc}"
    if rejected:
        print(
            f"\nNOTE: {len(rejected)} baseline scenario(s) did not pass their own "
            "validation and contribute no gated point (a failed base run is not a "
            "measurement, and is usually *faster* than a correct one):"
        )
        for scenario_id in rejected:
            print(f"  - {scenario_id}")
    return baseline, None


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
        # None, not the default value: `source` below must report whether the
        # caller stated a number, and an explicit 0.3 is otherwise
        # indistinguishable from the default 0.3.
        default=None,
    )
    p.add_argument(
        "--regress-min-delta-seconds",
        type=finite_nonnegative_float_arg,
        default=None,  # see --regress-tolerance
        help="Absolute floor combined with --regress-tolerance via max(). Nonzero "
        "by default here, unlike the in-process gates: a full-CLI run pays ~0.5s "
        "of interpreter startup whose jitter is a real part of every sample.",
    )
    p.add_argument(
        "--timeout-seconds",
        # Positive, not merely non-negative: zero instantly times out every step.
        type=finite_positive_float_arg,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    p.add_argument(
        # Positive: a zero interval busy-loops the sampler thread and perturbs
        # the timings it is supposed to only observe.
        "--rss-interval-seconds",
        type=finite_positive_float_arg,
        default=0.02,
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

    unsuitable = host_unsuitable_reason()
    if unsuitable:
        if args.require_toolchain:
            print(f"FAIL: --require-toolchain given but {unsuitable}.")
            return 1
        print(f"SKIP: {unsuitable} — nothing measured (exit 0, no coverage claimed).")
        return 0

    scenarios = select_scenarios(args.suite, args.scenario)
    if not scenarios:
        print(f"FAIL: --scenario {args.scenario} matched nothing.")
        return 1

    # "explicit" iff either flag was *supplied*. Deriving it from the tolerance
    # alone mislabelled the CI lane's own `--regress-min-delta-seconds 0.6` as
    # "default", and deriving it by value comparison mislabelled an explicitly
    # stated default-equal value the same way. The field exists so a reader can
    # check that a strict value was honored, so it has to track the statement,
    # not the number.
    threshold = GateThreshold(
        tolerance=(
            DEFAULT_REGRESS_TOLERANCE
            if args.regress_tolerance is None
            else args.regress_tolerance
        ),
        min_delta=(
            DEFAULT_REGRESS_MIN_DELTA_SECONDS
            if args.regress_min_delta_seconds is None
            else args.regress_min_delta_seconds
        ),
        source=(
            "explicit"
            if args.regress_tolerance is not None
            or args.regress_min_delta_seconds is not None
            else "default"
        ),
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
            "per-process kernel high-water mark, reported only for the step that "
            "raised it (it is cumulative over reaped children) -- null with a "
            "stated scope otherwise.",
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
    failures += _report_coverage_claim(
        results, suite=args.suite, narrowed=bool(args.scenario), no_spy=args.no_spy
    )

    baseline = None
    if args.baseline is not None:
        baseline, read_error = _read_baseline_or_fail(args.baseline)
        if read_error is not None:
            print(f"\nFAIL: {read_error}")
            return 1

    current = gated_points(results)
    print(f"\nEffective threshold: {threshold.as_dict()}")
    failures += _gate_against_baseline(
        current, baseline, threshold, baseline_path=args.baseline
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
