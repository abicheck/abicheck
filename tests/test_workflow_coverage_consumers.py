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

"""Every coverage report a lane writes must have something that reads it.

Bug class `ci.instrumentation_without_a_consumer`. Coverage instrumentation
costs roughly 60% wall time on the lane carrying it, so a report nobody
uploads, archives or gates is pure loss — and it reads as diligence, which
is why it survives review.

PR #1240 fixed the two sites it found by inspection (the macOS *unit* lane
and the `slow` lane) and registered the class with an explicit known gap:
"nothing asserts the consumer half, so a future lane can reintroduce an
unread report and only a human reading the diff would notice." This module
closes that gap — and closing it immediately found a third site the
inspection had missed, in the *integration* lane, where the producer runs on
`runner.os != 'Windows'` (Linux **and** macOS) while the Codecov upload is
gated to `ubuntu-24.04`. That is the whole argument for making a rule
executable rather than writing it down.

The check is per *matrix combination*, not per file, because that is where
the defect lives: a report can have a perfectly real consumer and still be
orphaned on one platform of the same job. Each job's matrix is expanded, and
both the producing step's and the consuming step's `if:` are evaluated
against that combination through the shared `_gha_expressions` evaluator.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

import yaml
from _gha_expressions import condition_holds, runner_os_for
from _workflow_files import WORKFLOW_DIR, read_repo_text, workflow_paths

#: `--cov-report=xml` writes `coverage.xml`; `--cov-report=xml:NAME` writes NAME.
_COV_REPORT = re.compile(r"--cov-report=xml(?::([\w.\-]+))?")


def _matrix_combinations(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a job's matrix the way GitHub does.

    The cross product of the list-valued axes, then `exclude` applied as
    *partial* match, then `include` — which is not simply "append". GitHub
    merges an include entry into every existing combination it is compatible
    with (it adds keys, or restates values already there) and appends it as a
    new combination only when it would *overwrite* an original axis value.
    Appending unconditionally, as this did, produced a standalone
    `{'coverage': True}` leg for an include that should have merged, and
    ignored `exclude` entirely (CodeRabbit review).

    Neither shape exists in this repository today: `ci.yml`'s includes all
    set `os` to a value outside the base axis, which GitHub does append, and
    no workflow uses `exclude` — so the old code was accidentally right for
    the current tree and would have gone wrong on the first compatible
    include anyone added. A job with no matrix has one nameless combination,
    so jobs are handled uniformly.
    """
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        # A matrix built from an expression (`matrix: ${{ fromJson(...) }}`)
        # cannot be expanded statically. Treated as one nameless combination
        # so the job is still scanned, rather than skipped outright.
        matrix = {}
    keys = [
        k
        for k, v in matrix.items()
        if k not in ("include", "exclude") and isinstance(v, list)
    ]
    combos = [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(matrix[k] for k in keys))
    ] or [{}]

    for removed in matrix.get("exclude") or []:
        if isinstance(removed, dict):
            # A partial exclude removes every combination it matches on the
            # keys it names, leaving the rest alone.
            combos = [
                c for c in combos if any(c.get(k) != v for k, v in removed.items())
            ]

    for extra in matrix.get("include") or []:
        if not isinstance(extra, dict):
            continue
        # Compatible == does not overwrite any *original axis* value. An
        # include key absent from the axes always merges; one that restates
        # the axis value it already has merges too.
        merged_into_any = False
        for combo in combos:
            if all(k not in keys or combo.get(k) == v for k, v in extra.items()):
                combo.update(extra)
                merged_into_any = True
        if not merged_into_any:
            combos.append(dict(extra))
    return combos


def _context(combo: dict[str, Any]) -> dict[str, Any]:
    """The expression context one matrix combination sees, including the
    `runner.os` GitHub derives from the runner label rather than storing."""
    ctx: dict[str, Any] = {f"matrix.{k}": str(v) for k, v in combo.items()}
    if "os" in combo:
        ctx["runner.os"] = runner_os_for(str(combo["os"]))
    return ctx


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """A job's steps, skipping any malformed non-mapping entry."""
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _orphaned_reports() -> list[str]:
    """Every (workflow, job, matrix combination) that writes a coverage
    report no step in the same combination reads."""
    findings = []
    for path in workflow_paths():
        doc = yaml.safe_load(read_repo_text(path))
        for job_name, job in ((doc or {}).get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = _steps(job)
            for combo in _matrix_combinations(job):
                ctx = _context(combo)
                for step in steps:
                    run = step.get("run")
                    if not isinstance(run, str):
                        continue
                    if not condition_holds(step.get("if"), ctx):
                        continue
                    for match in _COV_REPORT.findall(run):
                        report = match or "coverage.xml"
                        if not _has_consumer(steps, step, report, ctx):
                            findings.append(
                                f"{path.name}::{job_name} {combo or '(no matrix)'} "
                                f"writes {report} but nothing reads it here"
                            )
    return findings


def _has_consumer(
    steps: list[dict[str, Any]],
    producer: dict[str, Any],
    report: str,
    ctx: dict[str, Any],
) -> bool:
    """Whether some *other* step that runs under *ctx* names *report*.

    An upload action, an artifact upload, or any step referencing the file
    all count — the rule is that the measurement is read, not how."""
    for step in steps:
        if step is producer:
            continue
        if not condition_holds(step.get("if"), ctx):
            continue
        if report in yaml.safe_dump(step):
            return True
    return False


def test_every_written_coverage_report_has_a_consumer() -> None:
    """The invariant itself: instrumentation must have a reader."""
    orphaned = _orphaned_reports()
    assert not orphaned, (
        "coverage instrumentation costs ~60% wall time on the lane carrying "
        "it, so a report with no reader is pure loss. Either give it a "
        "consumer (an upload, an artifact, a gate) or stop collecting it — "
        f"keeping the tests either way: {orphaned}"
    )


def test_the_survey_actually_finds_coverage_producers() -> None:
    """Guards the scan: a parsing change that matched no producer would make
    the assertion above vacuously true."""
    producers = 0
    for path in workflow_paths():
        producers += len(_COV_REPORT.findall(read_repo_text(path)))
    assert producers >= 2, (
        f"found {producers} coverage producers; expected the known lanes"
    )


def test_a_platform_scoped_consumer_does_not_cover_every_platform() -> None:
    """The property that makes this check per-combination rather than per
    file, pinned directly.

    A file-level check — "some step somewhere names this report" — passes on
    exactly the defect this module was written to catch: a real consumer,
    gated to one platform, while the producer runs on two.
    """
    steps = [
        {"if": "runner.os != 'Windows'", "run": "pytest --cov-report=xml:c.xml"},
        {
            "if": "matrix.os == 'ubuntu-24.04'",
            "uses": "codecov/codecov-action",
            "with": {"files": "c.xml"},
        },
    ]
    linux = _context({"os": "ubuntu-24.04"})
    macos = _context({"os": "macos-latest"})
    assert _has_consumer(steps, steps[0], "c.xml", linux)
    assert not _has_consumer(steps, steps[0], "c.xml", macos)


class TestMatrixExpansionFollowsGitHub:
    """`_matrix_combinations` decides which legs the guard above inspects, so
    a leg it invents or loses is a finding invented or lost.

    Stated as its own contract rather than only through the workflows this
    repository happens to have today: neither `exclude` nor a *compatible*
    `include` appears in the current tree, so every assertion here would be
    unreachable from the real workflows -- which is precisely why the
    behaviour was wrong and nothing noticed (CodeRabbit review).
    """

    def test_the_cross_product_is_every_axis_combination(self) -> None:
        combos = _matrix_combinations(
            {"strategy": {"matrix": {"os": ["a", "b"], "py": ["1", "2"]}}}
        )
        assert sorted(map(str, combos)) == sorted(
            str({"os": o, "py": p}) for o in ("a", "b") for p in ("1", "2")
        )

    def test_a_job_without_a_matrix_is_one_nameless_combination(self) -> None:
        assert _matrix_combinations({}) == [{}]

    def test_an_include_adding_a_new_key_merges_into_every_combination(self) -> None:
        """GitHub merges rather than appends here. Appending produced a
        standalone `{'cov': True}` leg with no `os` at all, which the guard
        would then evaluate every `if:` against."""
        combos = _matrix_combinations(
            {"strategy": {"matrix": {"os": ["a", "b"], "include": [{"cov": True}]}}}
        )
        assert combos == [{"os": "a", "cov": True}, {"os": "b", "cov": True}]

    def test_an_include_restating_an_axis_value_merges_only_there(self) -> None:
        combos = _matrix_combinations(
            {
                "strategy": {
                    "matrix": {"os": ["a", "b"], "include": [{"os": "a", "cov": True}]}
                }
            }
        )
        assert combos == [{"os": "a", "cov": True}, {"os": "b"}]

    def test_an_include_overwriting_an_axis_value_is_appended(self) -> None:
        """The one shape this repository does use: `ci.yml` adds windows and
        macOS legs to an `os: [ubuntu-latest]` axis."""
        combos = _matrix_combinations(
            {
                "strategy": {
                    "matrix": {"os": ["a"], "include": [{"os": "z", "cov": True}]}
                }
            }
        )
        assert combos == [{"os": "a"}, {"os": "z", "cov": True}]

    def test_a_partial_exclude_removes_every_combination_it_matches(self) -> None:
        combos = _matrix_combinations(
            {
                "strategy": {
                    "matrix": {
                        "os": ["a", "b"],
                        "py": ["1", "2"],
                        "exclude": [{"os": "a"}],
                    }
                }
            }
        )
        assert combos == [{"os": "b", "py": "1"}, {"os": "b", "py": "2"}]

    def test_exclude_is_applied_before_include(self) -> None:
        """GitHub's documented order. Reversing it would let an include
        resurrect a leg the author excluded."""
        combos = _matrix_combinations(
            {
                "strategy": {
                    "matrix": {
                        "os": ["a", "b"],
                        "exclude": [{"os": "a"}],
                        "include": [{"cov": True}],
                    }
                }
            }
        )
        assert combos == [{"os": "b", "cov": True}]

    def test_the_real_unit_tests_matrix_still_expands_to_its_five_legs(self) -> None:
        """The regression this must not cause: the live matrix is exactly the
        shape the old append-everything code got right by luck."""
        doc = yaml.safe_load(read_repo_text(WORKFLOW_DIR / "ci.yml"))
        combos = _matrix_combinations(doc["jobs"]["unit-tests"])
        assert [(c["os"], c["python-version"]) for c in combos] == [
            ("ubuntu-latest", "3.12"),
            ("ubuntu-latest", "3.13"),
            ("ubuntu-latest", "3.14"),
            ("windows-latest", "3.13"),
            ("macos-latest", "3.13"),
        ]
