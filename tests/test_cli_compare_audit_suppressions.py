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
rendered report the same way ``--contract`` is (see
``cli_compare_fold.py``'s ``_fold_suppression_audit_into_text``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.schemas import load_compare_report_schema
from abicheck.serialization import snapshot_to_json

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

_requires_jsonschema = pytest.mark.skipif(
    jsonschema is None, reason="jsonschema not installed"
)


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

    def test_rejected_without_suppress_even_with_dry_run(self, tmp_path):
        # Regression (Codex review, fresh evidence): --dry-run exits via
        # emit_dry_run's SystemExit before the CLI ever reaches the later,
        # post-suppression-loading guard -- without an earlier check,
        # `--audit-suppressions --dry-run` (no --suppress) reported "ok" for
        # an invocation the identical non-dry-run call rejects outright.
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--audit-suppressions", "--dry-run",
            ],
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
        assert audit["stale_rules"] == ["workaround (symbol=never_matches_anything)"]
        assert audit["high_risk_matches"] == []

    @_requires_jsonschema
    def test_suppression_audit_validates_against_packaged_schema(self, tmp_path):
        # Regression (Codex review, fresh evidence): report_schema_version
        # must actually bump (and the packaged schema declare the new key)
        # whenever an additive top-level key like suppression_audit is
        # introduced -- jsonschema.validate alone wouldn't catch a missing
        # bump, since the schema's own additionalProperties: true accepts an
        # undeclared key silently.
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
        assert "suppression_audit" in payload
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)
        assert "suppression_audit" in schema["properties"]

    def test_label_falls_back_to_selector_not_bucket_index(self, tmp_path):
        # Regression (Codex/CodeRabbit review, fresh evidence): a rule with
        # neither label nor reason previously fell back to its position
        # *within the filtered bucket* (e.g. "rule#0"), not its real
        # position in the suppression file -- misleading whenever a rule
        # isn't first in the file, or when two buckets' "rule#0" entries
        # are actually two different rules. A second, unlabeled rule
        # (no `reason`) is the sole stale one here; it must render using
        # one of its own matching selectors, not a fabricated "rule#0".
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5api_bv\n"
            "    reason: intentional removal\n"
            "  - symbol: never_matches_anything\n",
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
        assert audit["total_rules"] == 2
        assert audit["stale_rules"] == ["symbol=never_matches_anything"]

    def test_label_includes_every_conjunctive_selector_not_just_the_first(
        self, tmp_path
    ):
        # Regression (Codex review, fresh evidence, second round on the same
        # fallback): two unlabeled rules sharing their first populated
        # selector (symbol) but differing on a second (change_kind) must not
        # render as the identical, ambiguous label -- Suppression selectors
        # combine conjunctively, so both fields are part of what identifies
        # a rule, not just whichever one this loop finds first.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: func_removed\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: var_removed\n",
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
        assert audit["total_rules"] == 2
        assert len(audit["stale_rules"]) == 2
        assert len(set(audit["stale_rules"])) == 2, (
            "two distinct rules must not render as the same label"
        )
        for label in audit["stale_rules"]:
            assert "symbol=never_matches_anything" in label
            assert "change_kind=" in label

    def test_shared_reason_still_disambiguated_by_selectors(self, tmp_path):
        # Regression (Codex review, fresh evidence, third round on the same
        # label): label/reason is a free-form grouping tag with no
        # uniqueness guarantee -- two distinct rules sharing one reason but
        # differing on their selector must still render distinct
        # identifiers, not the identical bare reason for both.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    reason: shared grouping tag\n"
            "  - symbol: also_never_matches\n"
            "    reason: shared grouping tag\n",
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
        assert audit["total_rules"] == 2
        assert len(audit["stale_rules"]) == 2
        assert len(set(audit["stale_rules"])) == 2, (
            "two rules sharing a reason must still render distinct labels"
        )
        for label in audit["stale_rules"]:
            assert label.startswith("shared grouping tag (symbol=")

    def test_label_includes_reachability_gates(self, tmp_path):
        # Regression (Codex review, fresh evidence): two rules sharing every
        # listed selector but differing by `reachability` (or
        # allow_public_break/allow_unknown_reachability) match disjoint
        # findings and must not render identically -- these gates affect
        # matching exactly like symbol/change_kind/etc.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    reachability: public-only\n"
            "  - symbol: never_matches_anything\n"
            "    reachability: unreachable-only\n",
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
        assert audit["total_rules"] == 2
        assert len(audit["stale_rules"]) == 2
        assert len(set(audit["stale_rules"])) == 2, (
            "rules differing only by reachability must not render identically"
        )
        for label in audit["stale_rules"]:
            assert "reachability=" in label

    def test_label_includes_binding_selector(self, tmp_path):
        # Regression (Codex review, fresh evidence): two rules sharing every
        # other selector but differing by `binding` (e.g. weak vs. global)
        # match disjoint findings and must not render identically.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: func_removed\n"
            "    binding: weak\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: func_removed\n"
            "    binding: global\n",
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
        assert audit["total_rules"] == 2
        assert len(audit["stale_rules"]) == 2
        assert len(set(audit["stale_rules"])) == 2, (
            "rules differing only by binding must not render identically"
        )
        for label in audit["stale_rules"]:
            assert "binding=" in label

    def test_label_includes_expires_date(self, tmp_path):
        # Regression (Codex review, fresh evidence): two otherwise-identical
        # rules (same symbol and label) differing only by their `expires`
        # date rendered as the identical label in near_expiry_rules/
        # expired_rules -- exactly the buckets where expiry is the one thing
        # that distinguishes them, so a reader couldn't tell which deadline
        # belonged to which rule.
        from datetime import date, timedelta

        old_p, new_p = _write_pair(tmp_path)
        soon1 = (date.today() + timedelta(days=10)).isoformat()
        soon2 = (date.today() + timedelta(days=20)).isoformat()
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    label: shared-label\n"
            f'    expires: "{soon1}"\n'
            "  - symbol: never_matches_anything\n"
            "    label: shared-label\n"
            f'    expires: "{soon2}"\n',
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
        assert len(audit["near_expiry_rules"]) == 2
        assert len(set(audit["near_expiry_rules"])) == 2, (
            "rules differing only by expires must not render identically"
        )
        assert any(soon1 in label for label in audit["near_expiry_rules"])
        assert any(soon2 in label for label in audit["near_expiry_rules"])

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
        assert match["rule"] == "intentional removal (symbol=_Z5api_bv)"
        assert match["symbol"] == "_Z5api_bv"

    def test_high_risk_respects_policy_file_demotion(self, tmp_path):
        # Regression (Codex review, fresh evidence): audit() used to classify
        # "high risk" solely via the static BREAKING_KINDS import, ignoring
        # any active --policy override. A policy demoting func_removed
        # away from BREAKING means the suppressed change here is no longer
        # actually breaking under this run's own policy, so it must not be
        # reported as a high-risk match either.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5api_bv\n"
            "    reason: intentional removal\n",
        )
        policy = tmp_path / "policy.yml"
        policy.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: risk\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
                "--policy", str(policy), "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        audit = payload["suppression_audit"]
        assert audit["stale_rules"] == []
        assert audit["high_risk_matches"] == []

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

    @pytest.mark.parametrize("report_mode", ["leaf", "root-cause"])
    def test_present_under_every_report_mode(self, tmp_path, report_mode):
        # ADR-061 Phase 2 item 5: the removed post-render fold applied
        # unconditionally whenever fmt == "json", regardless of
        # --report-mode -- reporter.to_json's own per-mode JSON builders
        # (_to_json_leaf/_to_json_root_cause) must all carry the key now.
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
                "--format", "json", "--report-mode", report_mode,
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        audit = payload["suppression_audit"]
        assert audit["total_rules"] == 1
        assert audit["stale_rules"] == ["workaround (symbol=never_matches_anything)"]


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

    def test_stale_rules_rendered_with_disambiguated_labels(self, tmp_path):
        # Regression (Codex review, fresh evidence): the markdown/text/
        # review branch used to rely on audit.summary()'s own per-rule stale
        # detail, which named a rule by only its first populated selector --
        # two unlabeled rules sharing that selector (symbol) but differing
        # on another (change_kind) rendered identically. Stale rules must
        # now get an explicit list using the fully disambiguated label, the
        # same as high-risk/expired/near-expiry.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: func_removed\n"
            "  - symbol: never_matches_anything\n"
            "    change_kind: var_removed\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--suppress", str(suppress), "--audit-suppressions",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Stale rules (matched nothing):" in result.output
        assert "symbol=never_matches_anything, change_kind=func_removed" in result.output
        assert "symbol=never_matches_anything, change_kind=var_removed" in result.output

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
        # Markdown demangles by default (ADR-061 Phase 2 item 5 closure):
        # this appended section now goes through the same whole-report
        # demangle pass as everything else, so the mangled `_Z5api_bv`
        # selector echo renders as its demangled form here too, consistent
        # with every other symbol in a demangled report.
        assert "`intentional removal (symbol=api_b())` suppressed" in result.output

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

    def test_expired_rule_labeled_not_just_counted(self, tmp_path):
        # Regression (Codex review, fresh evidence): audit.summary() only
        # reports a count for expired/near-expiry rules ("1 expired
        # rule(s)"), and this fold-in added detail lines only for
        # high-risk matches -- unlike the JSON branch (which already names
        # every expired/near-expiry rule), the default markdown/text/review
        # report gave no way to tell *which* rule needs action.
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            '  - symbol: never_matches_anything\n'
            '    reason: stale workaround\n'
            '    expires: "2000-01-01"\n',
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
        assert "Expired rules:" in result.output
        assert (
            "`stale workaround (symbol=never_matches_anything, "
            "expires=2000-01-01)`" in result.output
        )


class TestUsedByScopedOnlyChange:
    """Regression (Codex review, PR #658, fresh evidence): --audit-
    suppressions ran before --used-by/--required-symbol scoping applied,
    so a rule matching only a scoping-synthesized finding (e.g.
    CONSUMER_REQUIRED_SYMBOL_REMOVED, never present in result.changes) was
    misreported as stale."""

    def _setup(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        new_snap = AbiSnapshot(library="libfoo.so", version="2.0", functions=[])
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        return app, old, new

    def test_rule_matching_scoped_only_change_is_not_reported_stale(
        self, tmp_path, monkeypatch
    ):
        import abicheck.appcompat as appcompat_mod
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.diff_helpers import make_change

        app, old, new = self._setup(tmp_path, monkeypatch)
        synthetic = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="_Z5entryv",
            name=app.name,
        )
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old), new_lib_path=str(new),
            required_symbols={"_Z5entryv"}, required_symbol_count=1,
            breaking_for_app=[synthetic], verdict=Verdict.BREAKING,
        )
        monkeypatch.setattr(
            appcompat_mod, "scope_diff_to_app", lambda *a, **k: scoped
        )
        suppress = _write_suppression(
            tmp_path,
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z5entryv\n"
            "    reason: expected app-facing removal\n",
        )

        result = CliRunner().invoke(
            main,
            [
                "compare", str(old), str(new),
                "--used-by", str(app),
                "--suppress", str(suppress), "--audit-suppressions",
                "--format", "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        audit = payload["suppression_audit"]
        assert audit["stale_rules"] == []


class TestHelpAll:
    def test_help_all_mentions_flag(self):
        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "--audit-suppressions" in result.output
