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

"""ADR-063 Phase 7's ``run_outcome`` block on the ``compare-release`` fan-out
JSON (``_format_release_json``/``_write_release_summary_file``).

Split out of ``tests/test_run_outcome.py`` once that file crossed the
architecture gate's 1200-line test-file cap (Codex review follow-up round) --
this class was its newest, most self-contained addition, so moving it here
(rather than adding a debt.yaml growth entry) keeps the parent file under
its cap without accepting new debt, mirroring
``tests/test_check_report_run_outcome_backfill.py``'s own earlier split for
the identical reason.
"""

from __future__ import annotations


class TestReleaseJsonRunOutcome:
    def test_format_release_json_carries_run_outcome(self):
        """Codex review (P2): docs/use/output-formats.md documents that
        every JSON report carries run_outcome, including 'the release
        fan-out' -- _format_release_json never actually built one."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "BREAKING",
            Path("/o"),
            Path("/n"),
            [{"library": "libfoo.so", "verdict": "BREAKING"}],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(out)
        assert "run_outcome" in data
        assert data["run_outcome"]["gate"] == "abi_breaking"
        assert data["run_outcome"]["compatibility"] == "BREAKING"

    def test_format_release_json_run_outcome_surfaces_operational_error(self):
        """A library that failed to dump/extract/compare (verdict ERROR) is
        an operational failure, not a compatibility-gate finding --
        run_outcome must say so via .operational, matching OperationalStatus.
        EXTRACTION_ERROR's own documented grounding."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "ERROR",
            Path("/o"),
            Path("/n"),
            [{"library": "libfoo.so", "verdict": "ERROR"}],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(out)
        assert data["run_outcome"]["operational"] == "extraction_error"

    def test_removed_library_escalation_agrees_with_severity_block(self):
        """Codex review (P2), fresh evidence: a severity-scheme release
        using --fail-on-removed-library escalates run_outcome.gate to
        abi_breaking even when ordinary findings contribute 0, but the
        severity block emitted alongside it must escalate too (mirroring
        buildsource/check_report.py's _escalate_removed_library_severity) --
        otherwise GateInfo.from_report_data's own severity/run_outcome
        contradiction check (this same PR) rejects this exact, legitimate
        report as corrupt (verified failing before this fix: severity.
        exit_code stayed 0 while run_outcome.gate read abi_breaking)."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json
        from abicheck.severity import resolve_severity_config
        from abicheck.workflows.aggregate.gate import GateInfo

        cfg = resolve_severity_config("default")
        out = _format_release_json(
            "COMPATIBLE",
            Path("/o"),
            Path("/n"),
            [{"library": "libfoo.so", "verdict": "COMPATIBLE"}],
            ["libfoo.so"],
            [],
            {"libfoo.so": Path("/o/libfoo.so")},
            {},
            [],
            None,
            None,
            severity_config=cfg,
            severity_exit_code=0,
            fail_on_removed=True,
        )
        data = json.loads(out)
        assert data["severity"]["exit_code"] == 4
        assert data["run_outcome"]["gate"] == "abi_breaking"
        gate = GateInfo.from_report_data(data)  # must not raise _MalformedGate
        assert gate is not None
        assert gate.exit_code == 4
        assert gate.blocking is True

    def test_operational_sentinel_library_does_not_mask_a_real_break(self):
        """Codex review (P2), fresh evidence: one BREAKING library alongside
        one ERROR library previously produced run_outcome.compatibility=None
        (the raw worst_verdict, "ERROR", isn't a real Verdict at all) --
        masking the genuine breaking library entirely. _release_completed_
        compatibility_verdict must exclude the ERROR/not_comparable
        sentinels and report the worst REAL verdict underneath them, while
        the top-level `verdict`/`operational` axis is unaffected."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "ERROR",
            Path("/o"),
            Path("/n"),
            [
                {"library": "libfoo.so", "verdict": "BREAKING"},
                {"library": "libbar.so", "verdict": "ERROR"},
            ],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(out)
        assert data["verdict"] == "ERROR"
        assert data["run_outcome"]["compatibility"] == "BREAKING"
        assert data["run_outcome"]["operational"] == "extraction_error"

    def test_all_sentinel_libraries_with_no_global_comparison_yield_null_compatibility(
        self,
    ):
        """Codex review (P2), fresh evidence beyond the sentinel-masking
        fix above: when EVERY library result is ERROR/not_comparable and no
        bundle/matrix comparison ran either, _release_completed_
        compatibility_verdict's own "NO_CHANGE" floor default previously
        leaked through as a false claim that a clean comparison completed.
        compatibility must be null -- unknown, not falsely "no change" --
        when nothing real was actually compared."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "ERROR",
            Path("/o"),
            Path("/n"),
            [
                {"library": "libfoo.so", "verdict": "ERROR"},
                {"library": "libbar.so", "verdict": "not_comparable"},
            ],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(out)
        assert data["verdict"] == "ERROR"
        assert data["run_outcome"]["compatibility"] is None
        assert data["run_outcome"]["operational"] == "extraction_error"

    def test_write_release_summary_file_carries_run_outcome(self, tmp_path):
        """Codex review (P2): the --output-dir sibling of
        _format_release_json never built a run_outcome either -- the
        identical gap PR #803 already fixed for effective_config_digest on
        this same sibling document."""
        import json

        from abicheck.cli_compare_release import _write_release_summary_file

        _write_release_summary_file(
            tmp_path,
            "BREAKING",
            [{"library": "libfoo.so", "verdict": "BREAKING"}],
            [],
            [],
            {},
            {},
        )
        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["run_outcome"]["gate"] == "abi_breaking"


class TestAggregateLoaderPreservesReleaseGateCategory:
    def test_operational_error_preserves_the_recovered_compatibility_gate_category(
        self, tmp_path
    ):
        """Codex review, fresh evidence: when one `compare-release` member
        errors after a sibling produces a real `BREAKING` result, the
        recovered `run_outcome.compatibility` was loaded correctly, but the
        returned `GateInfo.blocking_categories` hard-coded only
        `("operational_error",)` and discarded the recorded `run_outcome.
        gate: "abi_breaking"` -- hiding the real compatibility blocker even
        though the numeric exit code was already correct at 4."""
        import json

        from abicheck.workflows.aggregate.load import _load_report_file

        report = tmp_path / "abi-report-linux.json"
        report.write_text(
            json.dumps(
                {
                    "verdict": "ERROR",
                    "old_dir": "/old",
                    "new_dir": "/new",
                    "libraries": [
                        {"name": "a", "verdict": "ERROR"},
                        {"name": "b", "verdict": "BREAKING"},
                    ],
                    "run_outcome": {
                        "schema_version": "1",
                        "compatibility": "BREAKING",
                        "assurance": None,
                        "gate": "abi_breaking",
                        "operational": "extraction_error",
                        "lifecycle": "existing",
                    },
                }
            ),
            encoding="utf-8",
        )

        loaded = _load_report_file(report, prefix="abi-report-")

        assert loaded.gate is not None
        assert loaded.gate.exit_code == 4
        assert "abi_breaking" in loaded.gate.blocking_categories
        assert "operational_error" in loaded.gate.blocking_categories

    def test_release_not_comparable_with_contradicting_run_outcome_fails_closed(
        self, tmp_path
    ):
        """CodeRabbit review, fresh evidence: a schema-valid `run_outcome`
        block whose `operational` contradicts the report's own root
        `"not_comparable"` sentinel (here `gate: none`/`operational: none`,
        as if the comparison were clean) previously produced a nonblocking
        `GateInfo` via `GateInfo.from_report_data` -- trusting a
        self-inconsistent block would let a real comparison refusal read as
        safe. Must fail closed (unavailable/malformed) instead."""
        import json

        from abicheck.workflows.aggregate.load import _load_report_file

        report = tmp_path / "abi-report-linux.json"
        report.write_text(
            json.dumps(
                {
                    "verdict": "not_comparable",
                    "old_dir": "/old",
                    "new_dir": "/new",
                    "libraries": [],
                    "run_outcome": {
                        "schema_version": "1",
                        "compatibility": None,
                        "assurance": None,
                        "gate": "none",
                        "operational": "none",
                        "lifecycle": "existing",
                    },
                }
            ),
            encoding="utf-8",
        )

        loaded = _load_report_file(report, prefix="abi-report-")

        assert loaded.gate is None
        assert loaded.verdict is None
        assert loaded.reason is not None and "malformed" in loaded.reason
