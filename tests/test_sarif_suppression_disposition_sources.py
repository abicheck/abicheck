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

"""Two-sided SARIF names the rule that actually suppressed each finding.

Bug class (``report.finding_entry_builder_parity``): a renderer read the
collapsed display label ``Change.suppression_rule`` (``label or reason``)
instead of the run's ADR-067 D3 record, so SARIF's suppression
``justification`` said ``suppressed by --suppress rule: <label>`` for every
source -- dropping the reason, the file and the expiry, and misattributing a
built-in release policy decision (the lockstep SONAME suppression) to a
``--suppress`` rule -- while the same run's JSON published the full record.

General invariant: for every suppressed finding of a run, whatever disposed
of it (a suppression document, an inline rule list, a release policy
decision, or nothing recorded), the SARIF suppression's ``properties`` equal
the JSON report's ``suppressed_changes[].rule`` for that finding, and its
``justification`` states every field of that record a reader needs: the
label, the reason, the stated id, the source and the expiry. The JSON
report is the oracle; it is produced by a separate renderer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.suppression.rules import Suppression, SuppressionList
from abicheck.report.diff_report import to_json
from abicheck.sarif import to_sarif
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.disposition import supersede_as_suppressed

_SYMBOLS = ("full_rule", "label_only", "reason_only", "id_only", "bare_rule")

#: One rule per symbol, covering every combination of stated fields a
#: suppression document can carry (``bare_rule`` states nothing at all).
_SUPPRESSION_YAML = """\
version: 1
suppressions:
  - id: waiver-full
    symbol: full_rule
    label: vendor-hook
    reason: removed with the vendor debug hook
    expires: 2099-01-31
  - symbol: label_only
    label: internal-only
  - symbol: reason_only
    reason: never part of the public API
  - id: waiver-id-only
    symbol: id_only
  - symbol: bare_rule
"""


def _fn(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn(s) for s in _SYMBOLS],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1", version="2.0", functions=[], from_headers=True
    )
    return old, new


def _json_rules(report: dict) -> dict[str | None, dict | None]:
    """symbol -> the JSON report's recorded rule (the oracle)."""
    return {
        entry["symbol"]: entry.get("rule")
        for entry in report["suppression"]["suppressed_changes"]
    }


def _sarif_suppressions(sarif: dict) -> dict[str | None, dict]:
    out = {}
    for result in sarif["runs"][0]["results"]:
        if result.get("suppressions"):
            (suppression,) = result["suppressions"]
            out[result["properties"]["symbol"]] = suppression
    return out


def _expected_source(rule: dict) -> str:
    if rule["source_file"]:
        return str(rule["source_file"])
    if rule["entry_index"] is not None:
        return "inline suppression rule"
    return "abicheck policy"


def _assert_sarif_states_the_json_record(report: dict, sarif: dict) -> int:
    """The invariant, checked field by field against the JSON oracle."""
    oracle = _json_rules(report)
    rendered = _sarif_suppressions(sarif)
    assert set(rendered) == set(oracle)
    for symbol, rule in oracle.items():
        suppression = rendered[symbol]
        assert suppression["kind"] == "external"
        text = suppression["justification"]
        if rule is None:
            assert "properties" not in suppression
            continue
        assert suppression["properties"] == rule
        for field in ("label", "reason", "expires"):
            if rule[field]:
                assert str(rule[field]) in text, (symbol, field, text)
        if "rule_identity" in rule:
            # An id-less rule: its derived id appears only if nothing else
            # names it.
            if not (rule["label"] or rule["reason"]):
                assert rule["rule_id"] in text
        elif rule["rule_id"]:
            assert rule["rule_id"] in text, (symbol, text)
        assert _expected_source(rule) in text, (symbol, text)
        assert "--suppress rule:" not in text
    return len(oracle)


class TestSuppressionDocumentViaCli:
    """Source: a ``--suppress`` document, through the public CLI."""

    def test_every_rule_shape_matches_the_json_record(self, tmp_path: Path) -> None:
        old, new = _snapshots()
        (tmp_path / "old.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (tmp_path / "new.json").write_text(snapshot_to_json(new), encoding="utf-8")
        supp = tmp_path / "waivers.yaml"
        supp.write_text(_SUPPRESSION_YAML, encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(tmp_path / "old.json"),
                str(tmp_path / "new.json"),
                "--suppress",
                str(supp),
                "-o",
                f"json={tmp_path / 'r.json'}",
                "-o",
                f"sarif={tmp_path / 'r.sarif'}",
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        sarif = json.loads((tmp_path / "r.sarif").read_text(encoding="utf-8"))
        # Every per-symbol removal, plus whatever summary findings the same
        # rules also hid (each held to the same invariant).
        assert _assert_sarif_states_the_json_record(report, sarif) >= len(_SYMBOLS)
        by_symbol = _sarif_suppressions(sarif)
        assert set(_SYMBOLS) <= set(by_symbol)
        # Pinned on two shapes, so the format is stated, not only implied.
        assert by_symbol["full_rule"]["justification"] == (
            "vendor-hook: removed with the vendor debug hook "
            f"(rule waiver-full; from {supp}; expires 2099-01-31)"
        )
        assert by_symbol["reason_only"]["justification"] == (
            f"never part of the public API (from {supp})"
        )


def _inline_result() -> DiffResult:
    old, new = _snapshots()
    rules = SuppressionList(
        [
            Suppression(symbol="full_rule", label="vendor-hook", reason="hook removed"),
            Suppression(symbol="label_only", label="internal-only"),
            Suppression(symbol="reason_only", reason="never public"),
            Suppression(symbol="id_only", id="waiver-id-only"),
            Suppression(symbol="bare_rule"),
        ]
    )
    return compare(old, new, suppression=rules)


class TestInlineRuleList:
    """Source: a typed ``SuppressionList`` with no file behind it."""

    def test_every_rule_shape_matches_the_json_record(self) -> None:
        result = _inline_result()
        report = json.loads(to_json(result))
        sarif = to_sarif(result)
        assert _assert_sarif_states_the_json_record(report, sarif) == len(_SYMBOLS)
        assert all(
            "inline suppression rule" in s["justification"]
            for s in _sarif_suppressions(sarif).values()
        )


class TestReleasePolicyDecision:
    """Source: a built-in policy decision with no user rule behind it -- the
    release-level lockstep SONAME suppression's own mechanism."""

    def test_policy_rule_is_named_not_attributed_to_suppress(self) -> None:
        old, new = _snapshots()
        result = compare(old, new)
        moved = [c for c in result.changes if c.symbol == "full_rule"]
        assert moved
        result.changes = [c for c in result.changes if c not in moved]
        result.suppressed_changes = [*result.suppressed_changes, *moved]
        result.suppressed_count += len(moved)
        supersede_as_suppressed(
            result,
            moved,
            application_point="lockstep_soname_suppression",
            rule_id="lockstep_soname_bump",
            reason="a sibling member broke ABI, so the SONAME bump is required",
        )
        report = json.loads(to_json(result))
        sarif = to_sarif(result)
        assert _assert_sarif_states_the_json_record(report, sarif) == 1
        (suppression,) = _sarif_suppressions(sarif).values()
        assert suppression["justification"] == (
            "lockstep_soname_bump: a sibling member broke ABI, so the SONAME "
            "bump is required (abicheck policy)"
        )


class TestNothingRecorded:
    """Source: none -- a hand-built result whose suppression no ledger saw.
    Only the collapsed label is knowable, and no record is fabricated."""

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("intentional", "suppressed by an abicheck --suppress rule: intentional"),
            (None, "suppressed by an abicheck --suppress rule"),
        ],
    )
    def test_falls_back_to_the_display_label(
        self, label: str | None, expected: str
    ) -> None:
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="full_rule",
            description="removed",
            suppression_rule=label,
        )
        result = DiffResult(
            old_version="1",
            new_version="2",
            library="libfoo.so.1",
            changes=[],
            suppressed_changes=[change],
        )
        report = json.loads(to_json(result))
        sarif = to_sarif(result)
        assert _assert_sarif_states_the_json_record(report, sarif) == 1
        (suppression,) = _sarif_suppressions(sarif).values()
        assert suppression == {"kind": "external", "justification": expected}
