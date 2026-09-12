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
``action/run.sh``: an audit-only ``mode: compare`` request (old-library and
abi-baseline both omitted) routes to ``compare --no-baseline`` (the
replacement for legacy ``mode: scan`` with no baseline, per ADR-068's
Action-input-lifecycle amendment -- ``mode: scan`` itself is retired
outright). Per that same amendment's 2026-09-11 update, this Action never
injects a preset on the caller's behalf: the audit-gate exit axis
(``policy/audit_gate_exit.py``, exit code ``3``) only activates when the
caller explicitly passes ``severity-preset`` (any value but ``info-only``),
same as a native ``compare --no-baseline`` invocation would. A caller
migrating an audit-only ``mode: scan`` job that relied on the legacy,
unconditional default gating must add ``severity-preset`` itself -- this is
a *required* migration step, not something ``run.sh`` does for them.

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
from _workflow_exec import bash_executable  # noqa: E402

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
        "INPUT_MODE": "compare",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        "RUNNER_TEMP": str(runner_temp),
        **env_extra,
    }
    proc = subprocess.run(
        [bash_executable(), str(RUN_SH)],
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
    """(b) audit-only scan with a gating-*capable* (API_BREAK-classified)
    finding but NO explicit severity-preset -> the audit-gate axis never
    activates (it is opt-in, per ADR-068's 2026-09-11 amendment), so the
    step stays exit 0 even though the same finding gates once a preset is
    supplied (see TestAuditOnlyScanGatingFindingWithExplicitPreset below).
    This is the migration trap the amendment names: a job that relies on
    the default without also adding `severity-preset` silently stops
    gating."""

    @pytest.mark.parametrize("case_name", [_GATING_CASE, _GATING_CASE_2])
    def test_gating_capable_finding_stays_exit_zero_without_a_preset(
        self, tmp_path: Path, case_name: str
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(case_name))},
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "0", outputs


class TestAuditOnlyScanGatingFindingWithExplicitPreset:
    """The same (b) shape, now WITH the caller explicitly opting in via
    `severity-preset: default` -- this is the required migration step
    (ADR-068's 2026-09-11 amendment) that restores legacy `mode: scan`'s
    own default-gating behavior: exit computed via the AUDIT_GATE axis (3,
    or higher if another axis also fires via max-fold) -> Action verdict
    output = AUDIT_GATE (case148's own header_build_context_mismatch
    finding gates only under a non-info-only preset -- see
    tests/parity/test_no_baseline_audit_corpus_parity.py and
    policy/audit_gate_exit.py)."""

    @pytest.mark.parametrize("case_name", [_GATING_CASE, _GATING_CASE_2])
    def test_gating_finding_produces_audit_gate_verdict(
        self, tmp_path: Path, case_name: str
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(case_name)),
                "INPUT_SEVERITY_PRESET": "default",
            },
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
    docstring names this exact case as the regression it guards against).
    Exercised both without a preset (the axis is inactive at all) and with
    one explicitly supplied (the axis is active but the RISK classification
    itself is what keeps it from gating)."""

    def test_risk_finding_does_not_gate_without_a_preset(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs

    def test_risk_finding_does_not_gate_even_with_an_explicit_preset(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_SEVERITY_PRESET": "default",
            },
        )
        assert outputs["_returncode"] == 0, outputs
        assert outputs.get("verdict") != "AUDIT_GATE", outputs


class TestAuditOnlyScanExplicitInfoOnlyPresetSuppressesGating:
    """(d) audit-only scan where the caller explicitly set
    `severity-preset: info-only` -> the audit-gate axis stays inactive, so
    gating is suppressed (matches explicit user intent) -- legacy
    `mode: scan` gated unconditionally, but the replacement `mode: compare`
    shape must honor an explicit opt-out the same way a bare no-preset
    request already does."""

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
    `severity-preset: strict` -> the caller's own value reaches the CLI
    unchanged and opts the run into gating (`strict`, like `default`, is
    any-value-but-`info-only`), exactly as a two-sided compare's own
    `--severity-preset` forwarding already works -- there is no special
    audit-only injection path left to stomp it."""

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
        # The caller's own explicit preset must appear on the assembled
        # command line exactly once -- a plain pass-through bug could still
        # duplicate or drop it. Routed through the same dry-run preview
        # `run.sh` itself supports, so this doesn't need a second live CLI
        # invocation.
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
        # existing exit-3 AUDIT_GATE path (case148, with the required
        # explicit severity-preset opt-in -- the axis is no longer active
        # by default, see TestAuditOnlyScanGatingFindingNoExplicitPreset).
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_SEVERITY_PRESET": "default",
            },
        )
        assert outputs.get("verdict") == "AUDIT_GATE", outputs
        assert outputs.get("exit-code") == "3", outputs


class TestBaselineCompareBudgetOverflowMapsToExitFive:
    """Codex review, PR #1210, round 10: a baseline compare's own `--budget`
    forwarding reaches the CLI unchanged (`compare`'s own `--budget` guard
    exits 5, `cli_compare_fold.py`'s `sys.exit(5)`), and the shared
    `case $ABICHECK_EXIT` dispatch this Action routes every compare
    invocation through must map that to the documented `BUDGET_OVERFLOW`
    verdict, not the generic `*) VERDICT="ERROR"` catch-all -- along with
    the budget-specific job-summary/comment handling that verdict gets."""

    def test_budget_overflow_reports_budget_overflow_not_error(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_GATING_CASE)),
                "INPUT_OLD_LIBRARY": str(_snapshot_path(_GATING_CASE)),
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


class TestAuditOnlyJobSummaryHeadingIsNotACompatibilityReport:
    """Codex review, fresh evidence: with the default `add-job-summary:
    true`, an audit-only invocation has `MODE=compare` and previously
    received the unconditional "## abicheck ABI Compatibility Report"
    heading -- recreating exactly the unsupported compatibility claim the
    AUDIT_CLEAN/AUDIT_RISK verdict text below it was written to avoid
    (compare --no-baseline has no baseline and reports no compatibility
    verdict at all, ADR-068 D2)."""

    def test_audit_only_summary_uses_an_audit_heading_not_a_compatibility_one(
        self, tmp_path: Path
    ) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_ADD_JOB_SUMMARY": "true",
            },
        )
        assert outputs["_returncode"] == 0, outputs
        summary = (tmp_path / "github_step_summary").read_text(encoding="utf-8")
        assert "## abicheck ABI Audit Report" in summary, summary
        assert "## abicheck ABI Compatibility Report" not in summary, summary
