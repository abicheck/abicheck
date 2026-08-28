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

"""``check_fact_field_readers()``'s own diagnostic wording, and the
explicit "no control-flow analysis" contract it now states (ADR-063
Phase 0, ``docs/contribute/plans/one-semantic-pipeline.md``).

Split into its own small file rather than appended to
``test_fact_field_readers.py`` (only ~24 lines of headroom left under
the architecture gate's 1200-line test-file cap).

A Codex review round read the diagnostic's previous wording -- "either
migrate this reader to check .status first, or add its stable key to
KNOWN_UNMIGRATED_READERS" -- as promising that a *preceding* `.status`
check on the sibling `Fact[...]` makes a still-present direct legacy-field
read compliant. No such recognition exists (this scan is a plain,
position-blind AST walk with no control-flow analysis at all, and
nothing in the real codebase exercises this pattern -- see the plan
doc's own "Still not landed" note), so the wording was tightened to say
so explicitly rather than adding genuine control-flow analysis under
review pressure for a pattern no real reader uses yet."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_ai_readiness import Findings
from scripts.fact_field_readers import check_fact_field_readers


class TestDiagnosticDoesNotPromiseControlFlowRecognition:
    """The finding text must not claim a preceding `.status` check
    exempts a direct read -- and, as a positive control, a real
    preceding `.status` check still produces a finding, confirming the
    wording matches the actual (position-blind) behavior."""

    def test_diagnostic_states_no_control_flow_analysis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fact_field_readers as gate

        pkg = tmp_path / "abicheck"
        pkg.mkdir()
        (pkg / "a_new_reader.py").write_text("def f(rec):\n    return rec.bases\n")

        monkeypatch.setattr(gate, "ROOT", tmp_path)
        monkeypatch.setattr(gate, "PKG", pkg)

        findings = Findings()
        check_fact_field_readers(findings)
        errors = [m for c, m in findings.errors if c == "fact-field-readers"]
        assert len(errors) == 1
        assert "no control-flow analysis" in errors[0]
        assert "NOT recognized as compliant" in errors[0]

    def test_a_preceding_status_check_still_produces_a_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real `.status` check immediately before the legacy read is
        still flagged -- this scan is a plain attribute-read walk with no
        notion of "preceded by an applicable guard," and the diagnostic
        now says so explicitly rather than implying otherwise."""
        import scripts.fact_field_readers as gate

        pkg = tmp_path / "abicheck"
        pkg.mkdir()
        (pkg / "a_new_reader.py").write_text(
            "def f(rec):\n"
            "    if rec.bases_fact.status:\n"
            "        return rec.bases\n"
            "    return []\n"
        )

        monkeypatch.setattr(gate, "ROOT", tmp_path)
        monkeypatch.setattr(gate, "PKG", pkg)

        findings = Findings()
        check_fact_field_readers(findings)
        errors = [m for c, m in findings.errors if c == "fact-field-readers"]
        assert len(errors) == 1
        assert "a_new_reader.py:3" in errors[0]
