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

"""ADR-064 stage 1b, native-CLI half: `scan --format json` on an abort.

Split from `tests/test_cli_scan.py` rather than added there -- that module
sits at its own ADR-061 no-growth debt budget with zero line slack
(`architecture/debt.yaml`), the same reason `tests/test_scan_abort_result.py`
exists as its own file instead of extending an existing one. Reuses that
module's `runner`/`new_snap_compatible` fixtures and `_payload` helper via a
plain import rather than duplicating their snapshot-fixture setup.

The abort payload is shaped as a minimal ``ScanOutcome.to_dict()``-compatible
envelope (top-level ``verdict``/``exit_code``, the exit decision nested under
``diff.exit``) rather than `scan_abort_result_fields`'s own ``report`` shape
(the *typed API's* `ScanResult.report` nesting, a different envelope) --
`TestAbortPayloadIsAggregateCompatible` proves a saved abort report reads
back through `workflows.aggregate.gate.GateInfo.from_scan_report`/
`workflows.aggregate.load.parse_report_verdict` instead of raising
`_MalformedGate` (Codex review, fresh evidence: `GateInfo.from_scan_report`
requires a top-level `exit_code` and would have crashed on the old
`{"exit": ...}`-only shape).
"""

from __future__ import annotations

import json

from abicheck.cli import main
from abicheck.schemas import SCAN_SCHEMA_VERSION
from tests import test_cli_scan as _base

# Re-bound (not imported directly) so a test function's own same-named
# parameter doesn't read as redefining an unused import (ruff F811) --
# pytest resolves a fixture by this module-level name either way.
runner = _base.runner
new_snap_compatible = _base.new_snap_compatible


def _abort_payload(res) -> dict:  # type: ignore[no-untyped-def]
    """Parse the leading JSON report, tolerating trailing CLI error text.

    Unlike `test_cli_scan._payload` (a full-scan report with nothing after
    it), the `_EvidenceContractError` path prints this report *then* raises
    `click.ClickException`, which appends its own "Error: ..." text after
    it -- `json.loads` alone would reject that as trailing data.
    """
    out = res.output
    i = out.find("{")
    return json.JSONDecoder().raw_decode(out[i:])[0]


def _abort_payload_text(res) -> str:  # type: ignore[no-untyped-def]
    """Like `_abort_payload`, but returns the raw JSON substring (to write
    to a file) instead of the parsed object."""
    out = res.output
    i = out.find("{")
    _, end = json.JSONDecoder().raw_decode(out[i:])
    return out[i : i + end]


def test_budget_overflow_json_report_has_exit_block(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--budget", "0s", "--format", "json"],
    )
    assert res.exit_code == 5, res.output
    payload = _abort_payload(res)
    assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
    assert payload["verdict"] == "BUDGET_OVERFLOW"
    assert payload["exit_code"] == 5
    assert payload["diff"]["exit"]["code"] == 5
    assert payload["diff"]["exit"]["reasons"] == ["budget_overflow"]
    assert payload["diff"]["exit"]["budget_overflow_contribution"] == 5


def test_budget_overflow_text_format_has_no_json_report(runner, new_snap_compatible):
    # Unchanged pre-existing behaviour: `--format text` (the default) still
    # gets only the stderr message, no stdout report -- this fix is scoped to
    # `--format json`, per ADR-064's own framing of the open question.
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--budget", "0s"],
    )
    assert res.exit_code == 5, res.output
    assert "{" not in res.output


def test_evidence_contract_error_json_report_has_exit_block(
    runner, new_snap_compatible
):
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--depth",
            "source",
            "--format",
            "json",
        ],
    )
    assert res.exit_code != 0, res.output
    payload = _abort_payload(res)
    assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
    assert payload["verdict"] == "EVIDENCE_CONTRACT_ERROR"
    assert payload["exit_code"] == 1
    assert payload["diff"]["exit"]["code"] == 1
    assert payload["diff"]["exit"]["reasons"] == ["evidence_contract_error"]


def test_evidence_contract_error_text_format_has_no_json_report(
    runner, new_snap_compatible
):
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--depth", "source"],
    )
    assert res.exit_code != 0, res.output
    assert "{" not in res.output


def test_budget_overflow_writes_secondary_json_report(
    runner, new_snap_compatible, tmp_path
):
    # --format text (default) + --write json=... (the documented GitHub
    # Action combination, Codex review, fresh evidence): the secondary JSON
    # artifact must still be written on abort, not silently skipped just
    # because the primary renderer isn't JSON.
    secondary = tmp_path / "abort.json"
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--budget",
            "0s",
            "--write",
            f"json={secondary}",
        ],
    )
    assert res.exit_code == 5, res.output
    assert "{" not in res.output
    payload = json.loads(secondary.read_text())
    assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
    assert payload["verdict"] == "BUDGET_OVERFLOW"
    assert payload["exit_code"] == 5
    assert payload["diff"]["exit"]["reasons"] == ["budget_overflow"]


def test_evidence_contract_error_writes_secondary_json_report(
    runner, new_snap_compatible, tmp_path
):
    secondary = tmp_path / "abort.json"
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--depth",
            "source",
            "--write",
            f"json={secondary}",
        ],
    )
    assert res.exit_code != 0, res.output
    payload = json.loads(secondary.read_text())
    assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
    assert payload["verdict"] == "EVIDENCE_CONTRACT_ERROR"
    assert payload["exit_code"] == 1
    assert payload["diff"]["exit"]["reasons"] == ["evidence_contract_error"]


def test_budget_overflow_json_primary_and_secondary_both_written(
    runner, new_snap_compatible, tmp_path
):
    # Both renderers are "json" here (--format json --write json=...) -- the
    # same abort payload must reach stdout *and* the secondary file.
    secondary = tmp_path / "abort.json"
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--budget",
            "0s",
            "--format",
            "json",
            "--write",
            f"json={secondary}",
        ],
    )
    assert res.exit_code == 5, res.output
    stdout_payload = _abort_payload(res)
    secondary_payload = json.loads(secondary.read_text())
    assert stdout_payload == secondary_payload
    assert stdout_payload["diff"]["exit"]["reasons"] == ["budget_overflow"]


class TestAbortPayloadIsAggregateCompatible:
    """The abort JSON must read back as a real scan report, not just be
    *some* valid JSON -- `workflows.aggregate.gate.GateInfo.from_scan_report`
    raises `_MalformedGate` on a scan-shaped document with no top-level
    `exit_code` (Codex review, fresh evidence), which the earlier
    `{"exit": ...}`-only payload was.
    """

    def test_gate_info_reads_the_budget_overflow_payload(
        self, runner, new_snap_compatible
    ):
        from abicheck.workflows.aggregate.gate import GateInfo

        res = runner.invoke(
            main,
            ["scan", str(new_snap_compatible), "--budget", "0s", "--format", "json"],
        )
        payload = _abort_payload(res)
        gate = GateInfo.from_scan_report(payload)
        assert gate is not None
        assert gate.blocking is True

    def test_gate_info_reads_the_evidence_contract_error_payload(
        self, runner, new_snap_compatible
    ):
        from abicheck.workflows.aggregate.gate import GateInfo

        res = runner.invoke(
            main,
            [
                "scan",
                str(new_snap_compatible),
                "--depth",
                "source",
                "--format",
                "json",
            ],
        )
        payload = _abort_payload(res)
        gate = GateInfo.from_scan_report(payload)
        assert gate is not None
        assert gate.blocking is True

    def test_parse_report_verdict_does_not_raise_on_the_payload(
        self, runner, new_snap_compatible
    ):
        from abicheck.workflows.aggregate.load import parse_report_verdict

        res = runner.invoke(
            main,
            ["scan", str(new_snap_compatible), "--budget", "0s", "--format", "json"],
        )
        payload = _abort_payload(res)
        # "BUDGET_OVERFLOW" isn't a compatibility Verdict member (that enum has
        # no abort states) -- parse_report_verdict must fail closed (None),
        # not raise, same as it already does for any other unrecognized string.
        assert parse_report_verdict(payload) is None


class TestAbortPayloadThroughRealAggregate:
    """The exact scenario the review finding named: saving a real `scan
    --format json` abort report to disk and feeding it to `aggregate`, not
    just the two reader functions in isolation. `GateInfo.from_scan_report`
    accepting the payload was not sufficient on its own --
    `workflows.aggregate.load._load_report_file` only calls it after
    `parse_report_verdict` succeeds, and neither abort verdict is a
    `Verdict` member, so the full pipeline still read the abort as an
    unavailable/verdictless report until `_load_report_file` gained
    dedicated handling for both (Codex review, fresh evidence).
    """

    def test_budget_overflow_report_blocks_a_required_target(
        self, runner, new_snap_compatible, tmp_path
    ):
        from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

        res = runner.invoke(
            main,
            ["scan", str(new_snap_compatible), "--budget", "0s", "--format", "json"],
        )
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "abi-report-linux-x86_64.json").write_text(
            _abort_payload_text(res)
        )

        result = aggregate_reports_dir(
            reports_dir,
            expected=ExpectedTargets.from_lists(["linux-x86_64"], []),
        )
        # 1, not scan's own raw 5 -- the aggregate's published contract has
        # no exit 5; GateInfo.from_scan_report normalizes every scan exit
        # outside {0, 2, 4} to 1, and the forced abort gate must match.
        assert result.exit_code() == 1
        assert "linux-x86_64" in result.blocking_targets

    def test_evidence_contract_error_report_blocks_a_required_target(
        self, runner, new_snap_compatible, tmp_path
    ):
        from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

        res = runner.invoke(
            main,
            [
                "scan",
                str(new_snap_compatible),
                "--depth",
                "source",
                "--format",
                "json",
            ],
        )
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "abi-report-linux-x86_64.json").write_text(
            _abort_payload_text(res)
        )

        result = aggregate_reports_dir(
            reports_dir,
            expected=ExpectedTargets.from_lists(["linux-x86_64"], []),
        )
        assert result.exit_code() == 1
        assert "linux-x86_64" in result.blocking_targets
