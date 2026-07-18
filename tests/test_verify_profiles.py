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

"""Guard the CLAUDE.md "M0-3" invariant: scripts/verify.py is the ONE place
local/CI check commands live, and every consumer (pixi, pre-commit, CI,
CLAUDE.md) calls through it rather than keeping an independent copy.

These tests don't re-run the checks themselves (that's `scripts/verify.py`'s
job) — they assert that the *declared* command surfaces agree, so a future
edit that updates one consumer without the others fails fast instead of
silently drifting (the exact failure mode M0-3 was written to close).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_VERIFY_PATH = ROOT / "scripts" / "verify.py"
_spec = importlib.util.spec_from_file_location("abicheck_scripts_verify", _VERIFY_PATH)
assert _spec and _spec.loader
verify = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = verify  # dataclass() needs the module registered
_spec.loader.exec_module(verify)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _step(name: str) -> Any:
    for s in verify.STEPS:
        if s.name == name:
            return s
    raise AssertionError(f"no such verify.py step: {name!r}")


def _pytest_marker_expr(step: Any) -> str:
    """The `-m "<expr>"` pytest marker expression a Step's cmd carries.

    Steps are invoked as `sys.executable -m pytest ... -m "<expr>" ...`, so
    there are two `-m` flags in the tuple (Python's own module flag, then
    pytest's marker flag) — search for pytest's, not Python's.
    """
    pytest_idx = step.cmd.index("pytest")
    marker_idx = step.cmd.index("-m", pytest_idx) + 1
    return str(step.cmd[marker_idx])


# --- profile shape -----------------------------------------------------


def test_pr_profile_is_superset_of_fast_checks() -> None:
    """Every check-type step in `fast` (lint/fmt-check/typecheck) also runs
    under `pr` — `pr` must not be a weaker gate than the everyday inner loop."""
    fast_names = {s.name for s in verify.STEPS if verify.FAST in s.profiles}
    pr_names = {s.name for s in verify.STEPS if verify.PR in s.profiles}
    shared_gate_steps = {"lint", "fmt-check", "typecheck"}
    assert shared_gate_steps <= fast_names
    assert shared_gate_steps <= pr_names


def test_full_profile_is_superset_of_pr() -> None:
    pr_names = {s.name for s in verify.STEPS if verify.PR in s.profiles}
    full_names = {s.name for s in verify.STEPS if verify.FULL in s.profiles}
    assert pr_names <= full_names


def test_pr_profile_includes_golden_tests() -> None:
    """M0-3's second contradiction: the documented fast command and
    `pixi run check` excluded golden tests, but the canonical CI unit lane
    does not. The `pr` profile's unit step must include golden."""
    unit_pr = _step("unit-pr")
    marker_expr = _pytest_marker_expr(unit_pr)
    assert "golden" not in marker_expr


def test_fast_profile_excludes_golden_tests() -> None:
    unit_fast = _step("unit-fast")
    marker_expr = _pytest_marker_expr(unit_fast)
    assert "not golden" in marker_expr


# --- pyproject.toml / pixi ----------------------------------------------


def test_pixi_check_task_calls_verify_pr_profile() -> None:
    text = _read("pyproject.toml")
    m = re.search(r'^check\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml: [tool.pixi.feature.dev.tasks].check not found"
    assert m.group(1).strip() == "python scripts/verify.py --profile pr", (
        "`pixi run check` must be exactly `scripts/verify.py --profile pr` "
        "(CLAUDE.md M0-3) — a hand-picked depends-on list can silently drift "
        "from the real PR gate."
    )


# --- .pre-commit-config.yaml ---------------------------------------------


def test_pre_commit_does_not_pin_an_unpinned_mypy_mirror() -> None:
    """M0-3's first contradiction: a `mirrors-mypy` hook pins its OWN mypy
    version, independent of the `mypy==1.19.1` dev dependency pin — that let
    type errors through that only the CI-pinned mypy caught. mypy must run as
    a `language: system` local hook (or, if a mirror is reintroduced, its rev
    must match the pyproject.toml pin exactly)."""
    text = _read(".pre-commit-config.yaml")
    mirror_match = re.search(
        r"-\s*repo:\s*https://github\.com/pre-commit/mirrors-mypy\s*\n\s*rev:\s*v([0-9.]+)",
        text,
    )
    if mirror_match is None:
        # No active `- repo: .../mirrors-mypy` entry (e.g. a local/system
        # hook, which always uses whatever mypy is installed — i.e. the
        # pyproject.toml pin). Nothing further to check.
        return
    pyproject = _read("pyproject.toml")
    pin_match = re.search(r'"mypy==([0-9.]+)"', pyproject)
    assert pin_match, "pyproject.toml: mypy pin not found"
    assert mirror_match.group(1) == pin_match.group(1), (
        f"pre-commit mypy mirror pins v{mirror_match.group(1)} but "
        f"pyproject.toml pins mypy=={pin_match.group(1)} — these must match "
        "(CLAUDE.md M0-3)."
    )


def test_pre_commit_runs_ai_readiness() -> None:
    """scripts/CLAUDE.md documents that the AI-readiness gate runs via
    pre-commit — keep that claim true."""
    text = _read(".pre-commit-config.yaml")
    assert "ai-readiness" in text
    assert "verify.py" in text or "check_ai_readiness.py" in text


# --- .github/workflows/ci.yml ---------------------------------------------


def test_ci_ai_readiness_job_calls_verify_py() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert "scripts/verify.py --profile pr --only ai-readiness" in ci
    assert "fp-rate" in ci and "tier-accuracy" in ci and "usecase-docs-sync" in ci
    assert (
        "scripts/verify.py --profile pr --only fp-rate,tier-accuracy,usecase-docs-sync"
        in ci
    )


def test_ci_lint_and_types_job_calls_verify_py() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert "scripts/verify.py --profile pr --only lint,typecheck,docs-build" in ci


def test_ci_canonical_unit_lane_matches_verify_pr_profile() -> None:
    """The Linux/3.13 canonical unit-tests CI step keeps its own pytest
    invocation (matrix/coverage-artifact/xdist concerns don't fit the plain
    pass/fail Step model), but its marker expression and coverage floor must
    still agree with `verify.py`'s `unit-pr` step — the actual PR-gate
    contract, not just a step wrapper."""
    ci = _read(".github/workflows/ci.yml")
    unit_pr = _step("unit-pr")
    marker_expr = _pytest_marker_expr(unit_pr)
    assert marker_expr in ci, (
        f"ci.yml canonical unit lane must use the same -m marker expression "
        f"as verify.py's unit-pr step ({marker_expr!r})"
    )
    cov_fail_under = next(a for a in unit_pr.cmd if a.startswith("--cov-fail-under="))
    assert cov_fail_under in ci, (
        f"ci.yml canonical unit lane must use the same coverage floor as "
        f"verify.py's unit-pr step ({cov_fail_under!r})"
    )


def test_ci_fair_metadata_job_calls_verify_py() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert "scripts/verify.py --profile pr --only schema-sync,fair-metadata" in ci
    assert "scripts/verify.py --profile full --only distribution-build" in ci


# --- AGENTS.md / CLAUDE.md ---------------------------------------------


def test_agents_md_fast_command_matches_verify_fast_step() -> None:
    """AGENTS.md is the canonical instruction surface (CLAUDE.md "M1-1"); the
    fast test command it documents must agree with verify.py's unit-fast
    step."""
    agents_md = _read("AGENTS.md")
    unit_fast = _step("unit-fast")
    marker_expr = _pytest_marker_expr(unit_fast)
    assert marker_expr in agents_md, (
        "AGENTS.md's documented fast test command must use the same -m "
        f"marker expression as verify.py's unit-fast step ({marker_expr!r})"
    )
    assert "scripts/verify.py" in agents_md, (
        "AGENTS.md must document scripts/verify.py as the canonical "
        "verification entry point (CLAUDE.md M0-3)"
    )


def test_claude_md_is_a_thin_adapter_over_agents_md() -> None:
    """CLAUDE.md must import AGENTS.md rather than keep an independent copy
    of the canonical instructions (CLAUDE.md "M1-1")."""
    claude_md = _read("CLAUDE.md")
    assert "@AGENTS.md" in claude_md, (
        "CLAUDE.md must import the canonical AGENTS.md via `@AGENTS.md` "
        "instead of duplicating its content"
    )


def test_other_agent_adapters_point_at_agents_md() -> None:
    """The Copilot and Cursor adapters must reference AGENTS.md rather than
    keep their own copy of repository-wide commands/invariants."""
    copilot = _read(".github/copilot-instructions.md")
    assert "AGENTS.md" in copilot
    cursor = _read(".cursor/rules/abicheck.mdc")
    assert "AGENTS.md" in cursor
