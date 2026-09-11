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

"""End-to-end coverage for ADR-068's 2026-09-10 amendment as wired into
``action/run.sh``: an audit-only ``mode: scan`` request (no baseline) now
routes unconditionally to ``compare --no-baseline`` (there is no legacy
``scan`` CLI fallback left at all), and this Action injects
``--severity-preset default`` on that translated invocation whenever the
caller stated no preset of their own, so the audit-gate exit axis
(``policy/audit_gate_exit.py``, exit code ``3``) reproduces legacy
``scan``'s own default-gating behavior.

Driven through the *real* ``run.sh`` and the *real* ``abicheck`` binary
against the committed G20 corpus fixtures
(``tests/parity/test_no_baseline_audit_corpus_parity.py``'s own model for
"gating" vs. "non-gating" audit findings) -- not a stub, so the exit codes
this module asserts on are the genuine ones the CLI produces, folded
through the genuine ``run.sh`` dispatch. Each request shape gets its own
test, asserted individually rather than folded into one generic "it
works" case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

RUN_SH = _REPO / "action" / "run.sh"
_REAL_ABICHECK = shutil.which("abicheck")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or _REAL_ABICHECK is None,
    reason="needs a POSIX shell, a real abicheck on PATH, and action/run.sh",
)

# case148/case149 (cross-source header/build-context mismatch, ODR variant)
# are the G20 corpus's own gating shapes -- API_BREAK-classified findings.
# case143 (an accidentally-exported, non-public symbol) is RISK-classified
# and never gates. All three are exercised end to end by
# tests/parity/test_no_baseline_audit_corpus_parity.py already; this module
# reuses the identical committed fixtures rather than inventing new ones.
_GATING_CASE = "case148_xcheck_header_build_mismatch"
_GATING_CASE_2 = "case149_xcheck_odr_variant"
_NON_GATING_CASE = "case143_audit_accidental_export"


def _snapshot_path(case_name: str) -> Path:
    path = example_catalog.case_dir(case_name) / "snapshot.abi.json"
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _run_action(tmp_path: Path, env_extra: dict[str, str]) -> dict[str, object]:
    """Run the real ``action/run.sh`` (real ``abicheck`` on ``PATH``, no
    stub) and return its ``GITHUB_OUTPUT`` key/value pairs plus the raw
    process result."""
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "INPUT_MODE": "scan",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        "RUNNER_TEMP": str(runner_temp),
        **env_extra,
    }
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs: dict[str, object] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_returncode"] = proc.returncode
    outputs["_stdout"] = proc.stdout
    outputs["_stderr"] = proc.stderr
    return outputs


class TestAuditOnlyScanRoutesThroughCompareNoBaseline:
    """Sanity check shared by every scenario below: an audit-only
    ``mode: scan`` request really does reach ``compare --no-baseline``
    now, not the legacy ``scan`` CLI (there is none left to reach)."""

    def test_stderr_names_no_such_option_for_a_scan_only_flag(
        self, tmp_path: Path
    ) -> None:
        # `--no-pattern-verdicts` is a real `scan`-CLI-only flag with no
        # `compare` equivalent -- forwarding it through extra-args on an
        # audit-only request now reaches `compare`'s own real Click parser
        # (the accepted outcome per ADR-068's re-scoping), which is itself
        # proof the request reached `compare`, not `scan`.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_EXTRA_ARGS": "--no-pattern-verdicts",
            },
        )
        assert "No such option" in outputs["_stderr"], outputs


class TestAuditOnlyScanNoGatingFindings:
    """(a) audit-only scan with no gating findings -> exit 0, routed
    through compare."""

    def test_exit_zero_and_verdict_not_audit_gate(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "0", outputs


class TestAuditOnlyScanGatingFindingNoExplicitPreset:
    """(b) audit-only scan with a gating (API_BREAK-classified) finding and
    no explicit severity-preset -> exit computed via the AUDIT_GATE axis
    (3, or higher if another axis also fires via max-fold) -> Action
    verdict output = AUDIT_GATE. Also (2) the injected `--severity-preset
    default` reaches the underlying CLI at all (case148's own
    header_build_context_mismatch finding gates only under a non-info-only
    preset -- see tests/parity/test_no_baseline_audit_corpus_parity.py and
    policy/audit_gate_exit.py)."""

    @pytest.mark.parametrize("case_name", [_GATING_CASE, _GATING_CASE_2])
    def test_gating_finding_produces_audit_gate_verdict(
        self, tmp_path: Path, case_name: str
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(case_name))},
        )
        assert outputs["_returncode"] != 0, outputs
        assert outputs.get("verdict") == "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "3", outputs


class TestAuditOnlyScanRiskFindingDoesNotGate:
    """(c) audit-only scan with a RISK-classified finding only -> does NOT
    gate, exit 0, verdict is whatever it was before (not AUDIT_GATE) --
    case143's own accidental-export finding is RISK-classified
    (COMPATIBLE_WITH_RISK effective verdict), which the audit-gate axis
    explicitly must never gate on (policy/audit_gate_exit.py's own module
    docstring names this exact case as the regression it guards against)."""

    def test_risk_finding_does_not_gate_even_with_default_preset_injected(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs


class TestAuditOnlyScanExplicitInfoOnlyPresetSuppressesGating:
    """(d) audit-only scan where the caller explicitly set
    `severity-preset: info-only` -> injection must NOT override it, so
    gating is suppressed (matches explicit user intent) even though it's a
    `mode: scan` job -- legacy `scan` gated unconditionally, but the
    translated path must honor an explicit opt-out."""

    def test_info_only_preset_keeps_a_gating_finding_at_exit_zero(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_SEVERITY_PRESET": "info-only",
            },
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "0", outputs


class TestAuditOnlyScanExplicitNonDefaultPresetIsHonored:
    """(e) audit-only scan where the caller explicitly set
    `severity-preset: strict` -> injection must NOT override it, your
    injected default must not stomp the caller's explicit choice (in this
    case the observable behavior is identical to the injected default,
    since `strict` -- like `default` -- opts the run into gating; the
    point of this test is that the *caller's own value* reached the CLI,
    not a silently-substituted one, which the next test proves directly)."""

    def test_explicit_strict_preset_still_gates_a_gating_finding(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_SEVERITY_PRESET": "strict",
            },
        )
        assert outputs["_returncode"] != 0, outputs
        assert outputs.get("verdict") == "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "3", outputs

    def test_explicit_preset_reaches_the_cli_unduplicated(self, tmp_path: Path) -> None:
        # A stronger version of the test above: the caller's own explicit
        # preset must appear on the assembled command line exactly once --
        # never alongside a second, injected `default` occurrence (Click
        # keeps only the last repeated flag, so a silent duplicate would
        # either be harmless here or actively wrong depending on order;
        # either way it is not what "don't override an explicit choice"
        # means). Routed through the same dry-run preview `run.sh` itself
        # supports, so this doesn't need a second live CLI invocation.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_SEVERITY_PRESET": "strict",
                "INPUT_DRY_RUN": "true",
                "INPUT_VERBOSE": "true",
            },
        )
        assert outputs["_returncode"] == 0, outputs
        combined = f"{outputs['_stdout']}\n{outputs['_stderr']}"
        assert combined.count("--severity-preset") == 1, outputs
        assert "strict" in combined, outputs


class TestAuditOnlyScanExitZeroVerdictNeverClaimsCompatible:
    """Codex review, PR #1210, round 6: exit 0 on an audit-only scan was
    previously mapped to the generic VERDICT=COMPATIBLE -- "No binary ABI
    break detected" in the job summary/PR title -- even though no
    comparison ever ran. AUDIT_CLEAN (no finding at all) and AUDIT_RISK (a
    real finding present but not gated) replace it for this shape."""

    def test_no_findings_reports_audit_clean_not_compatible(
        self, tmp_path: Path
    ) -> None:
        # None of the committed G20 audit-corpus fixtures are genuinely
        # clean -- every one carries at least one candidate-side finding by
        # design (they exist specifically to exercise the audit detectors).
        # AUDIT_CLEAN needs a real zero-finding candidate, so compile one
        # on the fly: a single public function, no accidental exports, no
        # cross-source/ODR hazards for the audit detectors to flag.
        from tests._libabigail import compile_shared_lib

        lib = tmp_path / "libclean.so"
        compile_shared_lib(
            "int abicheck_clean_example(int x) { return x + 1; }",
            lib,
        )
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(lib)},
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") == "AUDIT_CLEAN", outputs
        assert outputs.get("exit-code") == "0", outputs

    def test_risk_only_finding_reports_audit_risk_not_compatible(
        self, tmp_path: Path
    ) -> None:
        # case143's own accidental-export finding: RISK-classified, never
        # gates (policy/audit_gate_exit.py) -- exit 0, but there IS a real
        # candidate-side finding, which AUDIT_CLEAN would misreport as
        # "nothing found".
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(
                    _snapshot_path("case143_audit_accidental_export")
                )
            },
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") == "AUDIT_RISK", outputs
        assert outputs.get("exit-code") == "0", outputs

    def test_gating_case_still_reports_audit_gate_not_audit_risk(
        self, tmp_path: Path
    ) -> None:
        # Sanity check that the new exit-0 verdicts don't leak into the
        # existing exit-3 AUDIT_GATE path (case148, default preset).
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE))},
        )
        assert outputs.get("verdict") == "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "3", outputs


class TestBaselineScanBudgetOverflowMapsToExitFive:
    """Codex review, PR #1210, round 10: a baseline `mode: scan` request's
    own `--budget` forwarding reaches `compare` unchanged (`compare`'s own
    `--budget` guard exits 5, `cli_compare_fold.py`'s `sys.exit(5)`), but
    the shared `case $ABICHECK_EXIT` dispatch this Action routes both
    `compare` and `mode: scan` through had no `5)` arm -- exit 5 fell into
    the generic `*) VERDICT="ERROR"` case, publishing `ERROR` instead of
    the documented `BUDGET_OVERFLOW` and skipping the budget-specific job-
    summary/comment handling `mode: scan` has always used."""

    def test_budget_overflow_reports_budget_overflow_not_error(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_AGAINST": str(_snapshot_path(_GATING_CASE)),
                "INPUT_BUDGET": "0s",
            },
        )
        assert outputs.get("verdict") == "BUDGET_OVERFLOW", outputs
        assert outputs.get("exit-code") == "5", outputs


class TestNativeCompareBudgetOverflowFailsTheStep:
    """Codex review, PR #1210, round 11, fresh evidence: the round-10 fix
    above only added a `FINAL_EXIT=1` check for `BUDGET_OVERFLOW` to the
    scan-mode final-exit-code branch -- a native `mode: compare` request
    passing `--budget` through the documented `extra-args` escape hatch
    (compare has no dedicated `budget` input of its own) now gets the more
    specific `BUDGET_OVERFLOW` verdict too, but nothing failed the step on
    it there, silently regressing a real budget overflow from a failing
    step to a passing one (`compare --help-all` documents `--budget` as
    making a CI job "fail clearly, exit 5")."""

    def test_budget_overflow_fails_the_step(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_EXTRA_ARGS": "--budget 0s",
            },
        )
        assert outputs.get("verdict") == "BUDGET_OVERFLOW", outputs
        assert outputs.get("exit-code") == "5", outputs
        assert outputs["_returncode"] != 0, outputs

    def test_fully_suppressed_finding_reports_audit_risk_not_audit_clean(
        self, tmp_path: Path
    ) -> None:
        # Codex review, PR #1210, round 7: a --suppress rule matching
        # case143's own finding empties `findings` but leaves
        # `suppressed_findings`/`suppressed_count` nonzero -- a suppressed
        # finding is a disposition, not an absence ("record before
        # disposing"), so this must still report AUDIT_RISK, not the
        # AUDIT_CLEAN "no candidate-side finding was detected" claim.
        suppress_file = tmp_path / "suppress.yml"
        suppress_file.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - symbol: "_Z11debug_dumpv"\n'
            '    change_kind: "exported_not_public"\n'
            '    reason: "test suppress"\n',
            encoding="utf-8",
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(
                    _snapshot_path("case143_audit_accidental_export")
                ),
                "INPUT_SUPPRESS": str(suppress_file),
            },
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") == "AUDIT_RISK", outputs
        assert outputs.get("exit-code") == "0", outputs
