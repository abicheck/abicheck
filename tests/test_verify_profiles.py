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
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

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


def test_importing_verify_does_not_reconfigure_stdout_or_stderr() -> None:
    """Finding 5 (CodeRabbit review, fresh evidence): reconfiguring
    stdout/stderr for line-buffering must be a `main()`-time action (via
    `_enable_line_buffered_output`), never a module-import-time side
    effect -- otherwise a test or another script merely importing
    `scripts.verify` (exactly what this test file's own module-loading
    block above does) would have ITS OWN stdout/stderr silently
    reconfigured as an import side effect, violating this repo's script
    import-side-effect rule. Run in a fresh subprocess, since the module
    under test is already cached in-process by the time this test runs."""
    script = (
        "import sys\n"
        "out_calls = []\n"
        "err_calls = []\n"
        "orig_out = sys.stdout.reconfigure\n"
        "orig_err = sys.stderr.reconfigure\n"
        "def out_spy(*a, **k):\n"
        "    out_calls.append((a, k))\n"
        "    return orig_out(*a, **k)\n"
        "def err_spy(*a, **k):\n"
        "    err_calls.append((a, k))\n"
        "    return orig_err(*a, **k)\n"
        "sys.stdout.reconfigure = out_spy\n"
        "sys.stderr.reconfigure = err_spy\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'abicheck_scripts_verify_import_check', 'scripts/verify.py'\n"
        ")\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "print(len(out_calls))\n"
        "print(len(err_calls))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # Round 30 Finding CR3 (CodeRabbit review, fresh evidence): the original
    # spy only covered stdout, so a stderr-only import-time side effect
    # would have slipped through undetected despite this test's own name.
    lines = result.stdout.strip().splitlines()
    assert lines[-2:] == ["0", "0"]


def _pytest_marker_expr(step: Any) -> str:
    """The `-m "<expr>"` pytest marker expression a Step's cmd carries.

    Steps are invoked as `sys.executable -m pytest ... -m "<expr>" ...`, so
    there are two `-m` flags in the tuple (Python's own module flag, then
    pytest's marker flag) — search for pytest's, not Python's.
    """
    pytest_idx = step.cmd.index("pytest")
    marker_idx = step.cmd.index("-m", pytest_idx) + 1
    return str(step.cmd[marker_idx])


# --- P0.3: isolated module lookup (no repository-root tool shadowing) ---


def test_isolated_module_runner_enables_user_site_for_system_site_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match normal user-site behavior of a --system-site-packages venv."""
    import importlib.util

    runner = ROOT / "scripts" / "run_isolated_module.py"
    spec = importlib.util.spec_from_file_location("isolated_runner", runner)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text(
        "include-system-site-packages = true\n", encoding="utf-8"
    )
    monkeypatch.setattr(module.sys, "prefix", str(venv))
    monkeypatch.setattr(module.sys, "base_prefix", str(tmp_path / "base"))
    assert module._normally_enables_user_site()


# --- profile shape -----------------------------------------------------


def test_python_tool_steps_use_isolated_module_lookup() -> None:
    """Tool steps must not let repository-root modules shadow installed tools."""
    runner = str(ROOT / "scripts" / "run_isolated_module.py")
    for step_name in {
        "lint",
        "fmt-check",
        "typecheck",
        "unit-fast",
        "unit-pr",
        "docs-build",
        "integration",
        "libabigail-parity",
        "abicc-parity",
        "slow",
    }:
        cmd = _step(step_name).cmd
        assert cmd[:3] == (sys.executable, "-I", runner), step_name


def test_isolated_module_runner_preserves_user_site_without_root_shadowing(
    tmp_path: Path,
) -> None:
    """A user-site tool is usable, but a same-named cwd module cannot win."""
    import os
    import subprocess

    user_base = tmp_path / "user-base"
    env = {**os.environ, "PYTHONUSERBASE": str(user_base)}
    user_site_proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import site; print(site.getusersitepackages())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert user_site_proc.returncode == 0, user_site_proc.stderr
    user_site = Path(user_site_proc.stdout.strip())
    user_site.mkdir(parents=True)

    # pytest is installed in the system site running this test. The user-site
    # module must retain normal precedence over it, while the checkout cannot.
    (user_site / "pytest.py").write_text(
        "print('trusted-user-site')\n", encoding="utf-8"
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pytest.py").write_text(
        "print('untrusted-checkout')\n", encoding="utf-8"
    )

    cmd = verify._py("pytest")
    proc = subprocess.run(
        cmd,
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "trusted-user-site"


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


def test_only_rejects_a_step_that_exists_but_is_not_in_this_profile() -> None:
    """`--profile pr --only <full-only-step>` must error, not silently drop
    the step and produce a smaller-than-requested "complete" run (Codex
    review, PR #604)."""
    full_only = {s.name for s in verify.STEPS if verify.FULL in s.profiles} - {
        s.name for s in verify.STEPS if verify.PR in s.profiles
    }
    assert full_only, "expected at least one full-only step to exist"
    step_name = sorted(full_only)[0]
    with pytest.raises(SystemExit, match=f"not in --profile {verify.PR}.*{step_name}"):
        verify.steps_for(verify.PR, {step_name}, set())


def test_only_rejects_a_completely_unknown_step_name() -> None:
    with pytest.raises(SystemExit, match="no such step: totally-bogus-step"):
        verify.steps_for(verify.PR, {"totally-bogus-step"}, set())


def test_only_accepts_a_step_that_is_in_this_profile() -> None:
    selected = verify.steps_for(verify.PR, {"lint"}, set())
    assert [s.name for s in selected] == ["lint"]


def test_pr_profile_run_with_a_skip_fails(monkeypatch, capsys) -> None:
    """A skipped step in the `pr` profile must exit 1, not 0 — a partial
    result must never be mistaken for a complete CI-equivalent pass.

    Uses a synthetic step with a precondition forced to fail, rather than
    relying on a real tool (e.g. mkdocs) being absent from *this*
    environment — a fully-provisioned dev setup (CLAUDE.md-recommended
    `pip install -e ".[dev,docs]"`, or `pixi run check`) must still exercise
    this invariant.
    """
    import dataclasses

    synthetic = dataclasses.replace(
        _step("docs-build"),
        precondition=lambda: "synthetic: forced skip for this test",
    )
    monkeypatch.setattr(
        verify,
        "STEPS",
        tuple(synthetic if s.name == "docs-build" else s for s in verify.STEPS),
    )
    rc = verify.main(["--profile", "pr", "--only", "docs-build"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out


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
    a `language: system` local hook routed through `scripts/verify.py` (or,
    if a mirror is reintroduced, its rev must match the pyproject.toml pin
    exactly)."""
    text = _read(".pre-commit-config.yaml")
    mirror_match = re.search(
        r"-\s*repo:\s*https://github\.com/pre-commit/mirrors-mypy\s*\n\s*rev:\s*v([0-9.]+)",
        text,
    )
    if mirror_match is None:
        # No active `- repo: .../mirrors-mypy` entry — a local/system hook
        # must exist instead, calling through verify.py (CLAUDE.md M0-3),
        # not a bare `mypy` invocation that could resolve a different
        # install than the pyproject.toml-pinned one (the exact PATH-
        # ambiguity bug this whole test file exists to catch instances of).
        assert re.search(
            r"-\s*id:\s*mypy\b.*?language:\s*system.*?entry:\s*python scripts/verify\.py",
            text,
            re.DOTALL,
        ), (
            ".pre-commit-config.yaml: no `mirrors-mypy` entry, but also no "
            "`language: system` mypy hook routed through scripts/verify.py — "
            "mypy must run through one or the other, not a bare `mypy` command."
        )
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
    pre-commit — keep that claim true, and keep it to exactly one hook so a
    future edit can't silently duplicate (or orphan) the entry."""
    text = _read(".pre-commit-config.yaml")
    # Count `entry:` lines specifically — the hook's `name:` field also
    # mentions the command descriptively, which would double-count a plain
    # substring search.
    canonical_entry = "entry: python scripts/verify.py --profile pr --only ai-readiness"
    occurrences = text.count(canonical_entry)
    assert occurrences == 1, (
        f"expected exactly one pre-commit hook with `{canonical_entry}`, "
        f"found {occurrences}"
    )


# --- .github/workflows/ci.yml ---------------------------------------------


def test_ci_ai_readiness_job_calls_verify_py() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert "scripts/verify.py --profile pr --only ai-readiness" in ci
    assert (
        "fp-rate" in ci
        and "tier-accuracy" in ci
        and "usecase-docs-sync" in ci
        and "docs-contract" in ci
    )
    assert (
        "scripts/verify.py --profile pr --only fp-rate,tier-accuracy,"
        "usecase-docs-sync,docs-contract,learning-ladder,agent-skills-generated,"
        "repo-facts" in ci
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
    assert "scripts/verify.py --profile pr --only distribution-build" in ci


def test_distribution_build_is_a_pr_profile_step() -> None:
    """ci.yml's `fair-metadata` job runs unconditionally on every PR (no path
    filter) — distribution-build must be a `pr`-profile step, not FULL-only,
    or `--profile pr` isn't actually CI-equivalent for this check."""
    step = _step("distribution-build")
    assert verify.PR in step.profiles


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
    """The Copilot adapter must reference AGENTS.md AND must not re-duplicate
    commands/invariants that belong solely in AGENTS.md — a bare "AGENTS.md"
    mention alone doesn't prove the file stayed thin (it could mention
    AGENTS.md once and then repeat a full command block anyway).

    The Cursor adapter (`.cursor/rules/abicheck.mdc`) was removed from this
    repository's repo-structure cleanup; there is no longer a second
    tool-specific adapter to check here."""
    unit_fast = _step("unit-fast")
    marker_expr = _pytest_marker_expr(unit_fast)
    for adapter_path in (".github/copilot-instructions.md",):
        text = _read(adapter_path)
        assert "AGENTS.md" in text, f"{adapter_path}: must reference AGENTS.md"
        assert "pip install" not in text, (
            f"{adapter_path}: setup instructions belong in AGENTS.md/"
            "CONTRIBUTING.md only — duplicating `pip install` here is exactly "
            "the drift this adapter pattern exists to prevent"
        )
        assert marker_expr not in text, (
            f"{adapter_path}: the fast-lane pytest marker expression is a "
            "volatile AGENTS.md-owned detail — it must not be duplicated here"
        )


# --- every pr-profile step is actually reachable from CI -------------------

#: `pr`-profile steps that CI deliberately does not invoke through a
#: `verify.py --only` list, each with the reason. This is an explicit,
#: reviewed list rather than a wildcard: a step added to the `pr` profile but
#: wired into no CI job is invisible in CI while `--profile pr` reports it
#: locally, which is exactly how `agent-skills-generated` shipped unreachable.
_PR_STEPS_NOT_IN_A_CI_ONLY_LIST = {
    # ci.yml runs the same pytest command directly as its own matrix job
    # (asserted by test_ci_unit_lane_matches_verify_unit_pr above), because
    # the lane spans several OS/Python combinations that one Step can't model.
    "unit-pr": "run directly as the unit-tests matrix job",
    # KNOWN GAP, pre-existing and not introduced here: `ruff format --check`
    # runs in `--profile pr`/`pixi run check` and in pre-commit, but no CI job
    # invokes it. Recorded so it stays visible rather than being mistaken for
    # a deliberate exemption.
    "fmt-check": "NOT RUN IN CI — pre-existing gap, tracked here",
}


def test_the_bugfix_contract_step_reports_a_partial_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one gate CI can run more of than a local shell can.

    Its declared half reads the PR body, so a local run that simply passed let
    `--profile pr` claim CI parity having checked half the gate. The first fix
    used a precondition, which skipped the step entirely and threw away the
    structural half's local coverage as well (Codex review). It now runs, and
    its distinct exit code maps to a skip.
    """
    step = next(s for s in verify.STEPS if s.name == "bugfix-test-contract")
    assert step.partial is not None
    assert step.precondition is None, (
        "a precondition would skip the step before its structural half ran"
    )
    assert "BUGFIX_CONTRACT_BODY_FILE" in step.partial[2]
    # A second code for a second reason: the two need different remediation
    # and checked different amounts (Codex review).
    assert "base ref" in step.partial[3]
    assert step.partial[2] != step.partial[3]


def test_a_partial_returncode_becomes_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mapping itself: a partial exit is not a pass, because the
    profile-level completeness contract only counts skips."""

    class _Proc:
        returncode = 2
        # `run_step` now prints the child's captured stdout/stderr BEFORE
        # the partial-result early return (Finding 6, CodeRabbit review,
        # fresh evidence -- see `test_partial_result_still_prints_captured_
        # output` below for the regression this reordering itself covers),
        # so a stub `subprocess.run` result needs these two attributes
        # regardless of which branch the test is targeting.
        stdout = ""
        stderr = ""

    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k: _Proc())
    step = next(s for s in verify.STEPS if s.name == "bugfix-test-contract")
    result = verify.run_step(step)
    assert result["status"] == "skipped"
    assert result["returncode"] == 2
    assert "BUGFIX_CONTRACT_BODY_FILE" in str(result["reason"])


def test_each_partial_code_reports_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receipt is the remediation, so it has to name the right one."""

    class _Proc:
        returncode = 3
        stdout = ""
        stderr = ""

    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k: _Proc())
    step = next(s for s in verify.STEPS if s.name == "bugfix-test-contract")
    result = verify.run_step(step)
    assert result["status"] == "skipped"
    assert "base ref" in str(result["reason"])
    assert "BUGFIX_CONTRACT_BODY_FILE" not in str(result["reason"])


def test_partial_result_still_prints_captured_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Finding 6 (CodeRabbit review, fresh evidence): a step that returns a
    PARTIAL result must still have its own captured stdout/stderr printed --
    `run_step` used to capture the child's output at `subprocess.run(...)`
    but only print it AFTER the `step.partial` early return, so a partial
    step never got its own diagnostic output printed at all, defeating half
    the purpose of the round-20 diagnostic-visibility fix for exactly the
    steps most likely to need it."""

    class _Proc:
        returncode = 2
        stdout = "some structural-check stdout\n"
        stderr = "some structural-check stderr\n"

    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k: _Proc())
    step = next(s for s in verify.STEPS if s.name == "bugfix-test-contract")
    result = verify.run_step(step)
    assert result["status"] == "skipped"
    captured = capsys.readouterr()
    assert "some structural-check stdout" in captured.out
    assert "some structural-check stderr" in captured.err


def test_an_ordinary_failure_is_still_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: only the one distinguished code maps to a skip, so a
    real finding cannot be laundered into an incomplete run."""

    class _Proc:
        returncode = 1
        # `run_step` (round 20 Part B / round 21) always reads `.stdout`/
        # `.stderr` off the child result to re-print it, mirroring the real
        # `subprocess.run(..., capture_output=True, text=True)` contract —
        # empty strings, not a missing attribute, when a step produced no
        # output. A bare `_Proc()` without these two would raise
        # `AttributeError` here before ever reaching the assertion this test
        # exists to make (P0.3 follow-up round 2 merge, fresh evidence).
        stdout = ""
        stderr = ""

    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k: _Proc())
    step = next(s for s in verify.STEPS if s.name == "bugfix-test-contract")
    assert verify.run_step(step)["status"] == "failed"


def _ci_only_step_names() -> set[str]:
    """Every step name CI passes to `verify.py --only`, across all workflows.

    Scans every workflow file, not just ci.yml: a gate can legitimately live in
    its own workflow (bugfix-test-contract.yml does), and reading only ci.yml
    would report such a step as unreachable and push it into the exemption
    list — which is meant for steps CI genuinely does not run.
    """
    import re

    names: set[str] = set()
    workflow_dir = ROOT / ".github" / "workflows"
    for path in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"verify\.py\s+--profile\s+\w+\s+--only\s+([\w,-]+)", text
        ):
            names.update(match.group(1).split(","))
    return names


def test_every_pr_profile_step_is_reachable_from_a_ci_job():
    pr_steps = {s.name for s in verify.STEPS if verify.PR in s.profiles}
    covered = _ci_only_step_names()
    unreachable = pr_steps - covered - set(_PR_STEPS_NOT_IN_A_CI_ONLY_LIST)
    assert unreachable == set(), (
        f"pr-profile step(s) {sorted(unreachable)} run in `verify.py --profile pr` "
        "but are invoked by no CI job — add them to a job's `--only` list, or "
        "record why not in _PR_STEPS_NOT_IN_A_CI_ONLY_LIST"
    )


def test_the_ci_reachability_exemptions_are_still_real_steps():
    """A stale exemption would silently widen the check above."""
    pr_steps = {s.name for s in verify.STEPS if verify.PR in s.profiles}
    assert set(_PR_STEPS_NOT_IN_A_CI_ONLY_LIST) <= pr_steps


def test_agent_skills_drift_gate_runs_in_ci():
    """ADR-058's generated trees are committed; without a CI-reachable drift
    gate, a stale publication tree merges silently."""
    assert "agent-skills-generated" in _ci_only_step_names()
