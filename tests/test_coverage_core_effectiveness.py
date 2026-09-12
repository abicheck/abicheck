# Copyright 2026 Nikolay Petrov
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

"""Requested coverage backend vs. the one coverage.py actually selects.

Same bug class as `tests/test_workflow_concurrency_grouping.py`: settings
that look active and do nothing. `COVERAGE_CORE=sysmon` is set on the CI
unit-test job and in `scripts/verify.py`'s canonical `unit-pr` step, and
the comment on it claimed it delivered "the same line+branch data" as the
default core. It does not: `[tool.coverage.run] branch = true` applies to
every lane, sys.monitoring cannot measure branches before Python 3.14, and
coverage.py therefore warns and falls back to CTracer. The request has
never taken effect in this repository.

Nothing failed, because a silently-ignored environment variable produces a
warning on stderr and correct coverage numbers. The invariant below is
what makes the gap observable: **what the repository requests and what
coverage.py selects must either agree, or the disagreement must be stated
where the setting is.** The oracle is coverage.py itself — the test starts
a real `Coverage` object under the repository's own branch setting and
reads back the selected tracer — never a version comparison this file
performs and could get wrong on its own.
"""

from __future__ import annotations

import os
import re
import sys
import warnings
from pathlib import Path

import coverage
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every place the repository requests a coverage core, with the text that
#: must carry the caveat while the request is inert.
_REQUEST_SITES = (
    Path(".github/workflows/ci.yml"),
    Path("scripts/verify.py"),
)


def _selected_core(*, branch: bool) -> str:
    """What coverage.py actually does with a sysmon request.

    The primary signal is coverage.py's own **documented** `no-sysmon`
    warning ("Can't use core=sysmon: ... using default core", listed on its
    messages page) — public, stable behaviour rather than an internal
    attribute. `Collector.tracer_name()` reached through `Coverage.
    _collector` names the core exactly, but `_collector` is private and
    coverage.py says so explicitly ("names starting with underscore are not
    part of the public API"); `pytest-cov` is unpinned here, so a future
    resolve could remove it and fail this test for a reason unrelated to
    the invariant (CodeRabbit review). It is therefore read defensively and
    used only to sharpen the answer, never as the thing the test depends
    on.

    Returns the tracer's own name when it can be read, otherwise
    `"fallback"`/`"sysmon"` derived from the warning alone.
    """
    previous = os.environ.get("COVERAGE_CORE")
    os.environ["COVERAGE_CORE"] = "sysmon"
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cov = coverage.Coverage(branch=branch)
            cov.start()
            cov.stop()
        fell_back = any("core=sysmon" in str(w.message) for w in caught)
        collector = getattr(cov, "_collector", None)
        namer = getattr(collector, "tracer_name", None)
        if callable(namer):
            return str(namer())
        return "fallback" if fell_back else "sysmon"
    finally:
        if previous is None:
            os.environ.pop("COVERAGE_CORE", None)
        else:
            os.environ["COVERAGE_CORE"] = previous


def _branch_coverage_is_enabled() -> bool:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    section = text.split("[tool.coverage.run]", 1)
    if len(section) < 2:
        return False
    body = section[1].split("\n[", 1)[0]
    return re.search(r"^\s*branch\s*=\s*true\s*$", body, re.MULTILINE) is not None


#: Lines of context around a COVERAGE_CORE line that count as "where the
#: setting is". Scoped deliberately: an earlier version of this test
#: searched the whole file, which a 1000-line module satisfies by accident
#: -- `scripts/verify.py` mentions "falls back" elsewhere for unrelated
#: reasons, so deleting the real caveat left the test green. The negative
#: control below is what caught that.
_CONTEXT_LINES = 15


def _requesting_sites() -> list[Path]:
    sites = []
    for candidate in _REQUEST_SITES:
        text = (REPO_ROOT / candidate).read_text()
        if "COVERAGE_CORE" in text and "sysmon" in text:
            sites.append(candidate)
    return sites


def _context_around_requests(path: Path) -> str:
    """The text immediately surrounding each COVERAGE_CORE line."""
    lines = (REPO_ROOT / path).read_text().splitlines()
    chunks = []
    for i, line in enumerate(lines):
        if "COVERAGE_CORE" in line:
            start = max(0, i - _CONTEXT_LINES)
            chunks.append("\n".join(lines[start : i + _CONTEXT_LINES]))
    return "\n".join(chunks).lower()


def test_branch_coverage_is_still_the_repository_setting() -> None:
    """Pins the premise. If branch coverage were turned off, sysmon would
    start working and every assertion below would change meaning rather
    than merely pass."""
    assert _branch_coverage_is_enabled(), (
        "pyproject.toml no longer enables branch coverage — re-derive this "
        "module's premise before trusting it"
    )


def test_sysmon_request_matches_what_coverage_selects_or_is_documented() -> None:
    """The invariant. Either the requested core is the one selected, or
    every place that requests it says so in the surrounding text."""
    selected = _selected_core(branch=True)
    sites = _requesting_sites()
    assert sites, "no site requests COVERAGE_CORE=sysmon; update _REQUEST_SITES"

    if selected.lower().startswith("sysmon"):
        return  # The request is live; nothing to document.

    undocumented = []
    for site in sites:
        context = _context_around_requests(site)
        if "inert" not in context and "falls back" not in context:
            undocumented.append(str(site))
    assert not undocumented, (
        f"COVERAGE_CORE=sysmon is requested but coverage.py selects "
        f"{selected!r} under this repository's branch setting, and these "
        f"sites do not say so: {undocumented}. Either state the fallback "
        f"where the setting is, or stop requesting a core that is ignored."
    )


def test_the_fallback_is_specifically_about_branch_coverage() -> None:
    """Names the actual cause rather than 'sysmon does not work here', so a
    future interpreter that fixes it is recognised. Skipped where the
    running interpreter selects sysmon for branches already (3.14+), since
    there is then no fallback to attribute."""
    with_branch = _selected_core(branch=True)
    without_branch = _selected_core(branch=False)
    if with_branch.lower().startswith("sysmon"):
        pytest.skip(
            f"sysmon measures branches on {sys.version_info.major}."
            f"{sys.version_info.minor}; the fallback this pins is gone"
        )
    assert without_branch != with_branch, (
        "sysmon was rejected for something other than branch coverage "
        f"(branch=False also selected {without_branch!r}) — this module's "
        "stated cause, and the comments citing it, are now wrong"
    )
    assert without_branch.lower().startswith("sysmon")
