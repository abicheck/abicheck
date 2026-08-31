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

"""ADR-061 Phase 2 item 5 (post-render mutation) -- ``DiffResult``'s
``ReportSideFacts`` mixin (``old_evidence_depth``/``new_evidence_depth``/
``suppression_audit``) and the JSON builders that now read them directly.

Split into its own file (rather than growing ``test_cov95_cli.py``, which is
on ``architecture/debt.yaml``'s no-growth ledger) -- see
``abicheck/report_side_facts.py``'s own docstring for why the fields
themselves live in a separate module too.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(version: str = "1.0", library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )


class TestEvidenceDepthOutOfBandPack:
    """``old_evidence_depth``/``new_evidence_depth`` with an out-of-band pack
    directory.

    ADR-061 Phase 2 item 5: these two JSON fields used to be spliced in by
    ``cli_compare_helpers._fold_evidence_depth_into_json``, a post-render
    ``json.loads``/``json.dumps`` re-parse of ``compare``'s own rendered
    text. They are now resolved once (mirroring ``analysis_assurance``'s own
    identical out-of-band-pack resolution -- see
    ``TestAnalysisAssuranceOutOfBandPack`` in ``tests/test_analysis_
    assurance.py``, the sibling gap this class itself already referenced)
    and attached onto ``DiffResult.old_evidence_depth``/``new_evidence_
    depth`` before rendering, so ``reporter.to_json`` emits them directly.
    Driven through the real ``compare`` CLI end-to-end (a real on-disk
    ``BuildSourcePack``, not a mocked ``_resolve_side_pack``) so this proves
    the production pipeline, not just the removed fold-in function.

    Regression this still guards against (Codex review, original finding):
    an out-of-band ``--build-info``/``--sources`` *pack directory* (as
    opposed to a raw checkout, which gets embedded into the snapshot before
    this point) is resolved via ``_resolve_side_pack`` and used to produce
    the comparison's real build/source findings, but is never attached back
    onto the snapshot object itself -- so reading only ``snap.build_source``
    would report the snapshot's own (absent) embedded depth instead of the
    pack that actually backed the run.
    """

    def _write_snapshots(self, tmp_path: Path) -> tuple[Path, Path]:
        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        assert old_snap.build_source is None
        assert new_snap.build_source is None
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new_snap), encoding="utf-8")
        return old_p, new_p

    def _write_build_pack(self, tmp_path: Path, name: str) -> Path:
        """A real, on-disk out-of-band pack with L3 build evidence only
        (--> "build" depth, not "source")."""
        from abicheck.buildsource import pack_io
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
        from abicheck.buildsource.pack import BuildSourcePack

        pack_dir = tmp_path / name
        pack = BuildSourcePack(
            root=pack_dir,
            build_evidence=BuildEvidence(
                compile_units=[CompileUnit(id="cu1", source="a.c")]
            ),
        )
        pack_io.write(pack)
        return pack_dir

    def test_out_of_band_pack_depth_beats_absent_embedded_snapshot(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._write_snapshots(tmp_path)
        old_pack_dir = self._write_build_pack(tmp_path, "old_pack")
        new_pack_dir = self._write_build_pack(tmp_path, "new_pack")

        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--build-info", "old=" + str(old_pack_dir),
                "--build-info", "new=" + str(new_pack_dir),
                "--format", "json",
            ],
        )
        assert result.exit_code in (0, 1, 2, 4), result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["old_evidence_depth"] == "build"
        assert payload["new_evidence_depth"] == "build"

    def test_no_pack_args_falls_back_to_snapshot_embedded_depth(
        self, tmp_path: Path
    ) -> None:
        # Without --build-info/--sources, behavior is unchanged: depth comes
        # straight from each snapshot's own embedded build_source (or
        # absence thereof).
        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        new_snap.from_headers = True
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json"],
        )
        assert result.exit_code in (0, 1, 2, 4), result.output
        payload = json.loads(result.output)
        assert payload["old_evidence_depth"] == "binary"
        assert payload["new_evidence_depth"] == "headers"

    def test_report_mode_leaf_and_root_cause_also_carry_it(
        self, tmp_path: Path
    ) -> None:
        # The pre-existing fold-in applied unconditionally whenever fmt ==
        # "json", regardless of --report-mode -- the new pre-render fields
        # must reach every JSON builder (_to_json_leaf/_to_json_root_cause)
        # the same way, not just the default full mode.
        old_p, new_p = self._write_snapshots(tmp_path)
        for mode in ("leaf", "root-cause"):
            result = CliRunner().invoke(
                main,
                [
                    "compare", str(old_p), str(new_p),
                    "--format", "json", "--report-mode", mode,
                ],
            )
            assert result.exit_code in (0, 1, 2, 4), result.output
            payload = json.loads(result.output)
            assert payload["old_evidence_depth"] == "binary", mode
            assert payload["new_evidence_depth"] == "binary", mode


class TestEvidenceDepthAndSuppressionAuditJsonByteParity:
    """ADR-061 Phase 2 item 5: byte-identical JSON parity proof.

    Directly reconstructs what the *removed* post-render fold-ins used to
    produce -- ``cli_compare_helpers._fold_evidence_depth_into_json`` and
    ``cli_compare_fold._fold_suppression_audit_into_text``'s JSON branch,
    both a ``json.loads``/mutate/``json.dumps`` round trip over the *base*
    ``to_json()`` text -- and asserts that reconstruction is byte-for-byte
    identical to what the new pre-render ``DiffResult`` fields produce
    directly through ``reporter.to_json``. The CLI-level tests elsewhere
    (``TestEvidenceDepthOutOfBandPack`` above,
    ``tests/test_cli_compare_audit_suppressions.py``) already prove the
    *values* are right; this proves the exact serialized bytes two
    independently-computed code paths agree on, matching this ADR's own
    "byte-identical output" parity discipline.
    """

    def _base_result(self) -> DiffResult:
        return DiffResult(old_version="1.0", new_version="2.0", library="libfoo.so")

    def test_evidence_depth_matches_reconstructed_old_fold(self) -> None:
        from abicheck.reporter import to_json

        result = self._base_result()
        base_text = to_json(result)  # neither field set -> pre-fold document

        # New mechanism: attach the facts once, then render.
        result.old_evidence_depth = "build"
        result.new_evidence_depth = "source"
        new_text = to_json(result)

        # Old mechanism, reconstructed: render the base document once (as
        # `_render_compare_report` used to before its fourth fold-in), then
        # splice the two fields in via the exact json.loads/dumps sequence
        # `_fold_evidence_depth_into_json` performed.
        payload = json.loads(base_text)
        payload["old_evidence_depth"] = "build"
        payload["new_evidence_depth"] = "source"
        old_text = json.dumps(payload, indent=2)

        assert new_text == old_text

    def test_suppression_audit_matches_reconstructed_old_fold(self) -> None:
        from abicheck.reporter import to_json
        from abicheck.reporter_contract_blocks import suppression_rule_label
        from abicheck.suppression import Suppression, SuppressionAudit

        result = self._base_result()
        base_text = to_json(result)

        rule = Suppression(symbol="never_matches_anything", reason="workaround")
        audit = SuppressionAudit(
            stale_rules=[rule],
            high_risk_matches=[],
            expired_rules=[],
            near_expiry_rules=[],
            match_counts={},
            total_rules=1,
        )
        result.suppression_audit = audit
        new_text = to_json(result)

        payload = json.loads(base_text)
        payload["suppression_audit"] = {
            "total_rules": audit.total_rules,
            "stale_rules": [
                suppression_rule_label(r, i) for i, r in enumerate(audit.stale_rules)
            ],
            "high_risk_matches": [],
            "expired_rules": [],
            "near_expiry_rules": [],
        }
        old_text = json.dumps(payload, indent=2)

        assert new_text == old_text

    def test_both_together_match_reconstructed_old_fold_order(self) -> None:
        """Order matters for byte-identity: the old pipeline folded
        suppression_audit *before* evidence_depth (both appended at dict
        end), so the new mechanism must attach/emit them in the same
        relative order to stay byte-identical when both are present."""
        from abicheck.reporter import to_json
        from abicheck.suppression import Suppression, SuppressionAudit

        result = self._base_result()
        base_text = to_json(result)

        audit = SuppressionAudit(
            stale_rules=[],
            high_risk_matches=[],
            expired_rules=[],
            near_expiry_rules=[Suppression(symbol="s", label="near")],
            match_counts={},
            total_rules=1,
        )
        result.suppression_audit = audit
        result.old_evidence_depth = "headers"
        result.new_evidence_depth = "binary"
        new_text = to_json(result)

        payload = json.loads(base_text)
        payload["suppression_audit"] = {
            "total_rules": 1,
            "stale_rules": [],
            "high_risk_matches": [],
            "expired_rules": [],
            "near_expiry_rules": ["near (symbol=s)"],
        }
        payload["old_evidence_depth"] = "headers"
        payload["new_evidence_depth"] = "binary"
        old_text = json.dumps(payload, indent=2)

        assert new_text == old_text


class TestStatJsonIncludesSideFacts:
    """``to_stat_json`` (``--stat --format json``) is a fourth JSON builder
    with its own separate ``return`` -- CodeRabbit review on #965 found it
    still called ``render_json`` directly, so it was the one JSON mode
    silently omitting ``suppression_audit``/``old_evidence_depth``/
    ``new_evidence_depth`` while the other three already emitted them.
    """

    def _base_result(self) -> DiffResult:
        return DiffResult(old_version="1.0", new_version="2.0", library="libfoo.so")

    def test_evidence_depth_present_in_stat_json(self) -> None:
        from abicheck.reporter import to_stat_json

        result = self._base_result()
        result.old_evidence_depth = "build"
        result.new_evidence_depth = "source"
        payload = json.loads(to_stat_json(result))

        assert payload["old_evidence_depth"] == "build"
        assert payload["new_evidence_depth"] == "source"

    def test_suppression_audit_present_in_stat_json(self) -> None:
        from abicheck.reporter import to_stat_json
        from abicheck.suppression import Suppression, SuppressionAudit

        result = self._base_result()
        rule = Suppression(symbol="never_matches_anything", reason="workaround")
        result.suppression_audit = SuppressionAudit(
            stale_rules=[rule],
            high_risk_matches=[],
            expired_rules=[],
            near_expiry_rules=[],
            match_counts={},
            total_rules=1,
        )
        payload = json.loads(to_stat_json(result))

        assert payload["suppression_audit"]["total_rules"] == 1

    def test_absent_by_default(self) -> None:
        """Negative control: neither field set -> neither key present,
        matching the other three JSON builders' pre-existing behavior."""
        from abicheck.reporter import to_stat_json

        payload = json.loads(to_stat_json(self._base_result()))

        assert "old_evidence_depth" not in payload
        assert "new_evidence_depth" not in payload
        assert "suppression_audit" not in payload


class TestSuppressionRuleLabelFallbacks:
    """``suppression_rule_label``'s two remaining un-tested branches (Codecov
    patch-coverage gap on PR #965): a rule may have a label with no matching
    selectors at all, or neither a label/reason nor any selector -- distinct
    from the already-covered "no label, has selectors" and "label and
    selectors" cases exercised via the JSON round-trip tests above.

    Both states are unreachable through ``Suppression.__init__`` directly --
    its own ``__post_init__`` validation requires at least one of the exact
    fields ``suppression_rule_label`` also reads as selectors (`symbol`,
    `symbol_pattern`, `type_pattern`, `member_name`, `source_location`,
    `namespace`, `finding_id`), so a validly *constructed* rule can never
    have an empty ``parts`` list. ``suppression_rule_label`` itself reads
    every field via ``getattr(rule, field, None)`` rather than direct
    attribute access, though -- a deliberately duck-typed contract, not one
    scoped to ``Suppression``'s current constructor invariant -- so these
    branches are tested by clearing the validated field on an already-valid,
    mutable ``Suppression`` after construction, the way a rule missing that
    field entirely (e.g. loaded from an older or hand-built object) would
    read to this function."""

    def test_label_only_no_selectors_returns_bare_label(self) -> None:
        from abicheck.reporter_contract_blocks import suppression_rule_label
        from abicheck.suppression import Suppression

        rule = Suppression(symbol="placeholder", label="workaround")
        rule.symbol = None
        assert suppression_rule_label(rule, 0) == "workaround"

    def test_no_label_no_selectors_falls_back_to_bucket_index(self) -> None:
        from abicheck.reporter_contract_blocks import suppression_rule_label
        from abicheck.suppression import Suppression

        rule = Suppression(symbol="placeholder")
        rule.symbol = None
        assert suppression_rule_label(rule, 2) == "rule#2"
