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

"""ADR-049 Phase 5 -- ``compare --audit-suppressions``.

Wires the existing, previously-orphaned ``SuppressionList.audit()``/
``SuppressionAudit`` (``suppression.py``) into the ``compare`` CLI: an
additional hygiene check over the ``--suppress`` rule file (stale/high-risk/
expired/near-expiry rules) against this run's own findings, folded into the
rendered report the same way ``--contract-evaluation`` is (see
``cli_compare_fold.py``'s ``_fold_suppression_audit_into_text``)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av")],
        from_headers=True,
    )
    return old, new


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    old, new = _breaking_pair()
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _write_suppression(tmp_path: Path, yaml_text: str) -> Path:
    p = tmp_path / "suppress.yml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


class TestRequiresSuppress:
    def test_rejected_without_suppress(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--audit-suppressions"]
        )
        assert result.exit_code != 0
        assert "--audit-suppressions requires --suppress" in result.output


class TestRejectedOnSetInputs:
    def test_rejected_on_directory_inputs(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        suppress = _write_suppression(tmp_path, "version: 1\nsuppressions: []\n")

        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_dir), str(new_dir),
                "--suppress", str(suppress), "--audit-suppressions",
            ],
        )
        assert result.exit_code != 0
        assert "not supported for directory/package" in result.output
        assert "--audit-suppressions" in result.output


class TestJsonReport:
    def test_stale_rule_reported(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    reason: workaround\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
                "--format", "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        audit = payload["suppression_audit"]
        assert audit["total_rules"] == 1
        assert audit["stale_rules"] == ["workaround"]
        assert audit["high_risk_matches"] == []

    def test_high_risk_match_reported(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5api_bv\n"
            "    reason: intentional removal\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        audit = payload["suppression_audit"]
        assert audit["stale_rules"] == []
        assert len(audit["high_risk_matches"]) == 1
        match = audit["high_risk_matches"][0]
        assert match["rule"] == "intentional removal"
        assert match["symbol"] == "_Z5api_bv"

    def test_omitted_by_default(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5api_bv\n"
            "    reason: intentional removal\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "suppression_audit" not in payload


class TestMarkdownReport:
    def test_stale_rule_rendered(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    reason: workaround\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "## Suppression Audit" in result.output
        assert "stale rule(s)" in result.output

    def test_high_risk_match_rendered(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5api_bv\n"
            "    reason: intentional removal\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "## Suppression Audit" in result.output
        assert "High-risk matches" in result.output
        assert "`intentional removal` suppressed" in result.output

    def test_omitted_by_default(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    reason: workaround\n",
        )
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--suppress", str(suppress)],
        )
        assert result.exit_code == 4, result.output
        assert "## Suppression Audit" not in result.output


class TestHelpAll:
    def test_help_all_mentions_flag(self):
        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "--audit-suppressions" in result.output
