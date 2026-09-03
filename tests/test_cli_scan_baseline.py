# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Fast unit coverage for the extracted ``cli_scan_baseline`` helpers.

These pure helpers moved out of ``cli_scan`` in the size-split; the compiler-free
paths (provenance set, header expansion, risk-rules loader, estimate renderer)
are exercised here so the split code stays covered in the fast lane.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import click
import pytest

from abicheck import cli_scan_baseline as csb, cli_scan_helpers as csh
from abicheck.buildsource.risk import RiskRules
from abicheck.buildsource.scan_levels import EvidenceDepth, SourceMethod


class TestPackCoverage:
    """`_pack_coverage` reads embedded L3/L4/L5 rows defensively (CodeRabbit)."""

    def test_no_pack_returns_not_collected_rows(self) -> None:
        rows = csh._pack_coverage(types.SimpleNamespace(build_source=None))
        assert [r["layer"] for r in rows] == ["L3_build", "L4_source_abi", "L5_source_graph"]
        assert all(r["status"] == "not_collected" for r in rows)

    def test_mixed_to_dict_and_plain_dict_entries(self) -> None:
        # _l3_collected tolerates plain-dict rows; _pack_coverage must match it —
        # a row lacking to_dict() is passed through unchanged, not AttributeError'd.
        class _Row:
            def to_dict(self) -> dict[str, str]:
                return {"layer": "L3_build", "status": "collected"}

        plain = {"layer": "L4_source_abi", "status": "partial"}
        snap = types.SimpleNamespace(
            build_source=types.SimpleNamespace(
                manifest=types.SimpleNamespace(coverage=[_Row(), plain])
            )
        )
        rows = csh._pack_coverage(snap)
        assert rows == [
            {"layer": "L3_build", "status": "collected"},
            {"layer": "L4_source_abi", "status": "partial"},
        ]


class TestSeverityBlockingCompatibleFindings:
    """`_add_severity_blocking_compatible_findings` — naming the actual blocker.

    Codex review: under a `severity.addition: error` config a compatible diff exits 1
    and the report named only the blocking category and count.
    """

    @staticmethod
    def _diff(n_adds: int = 3, n_breaks: int = 0):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult

        adds = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol=f"_Z3n{i:02d}v", description="add")
            for i in range(n_adds)
        ]
        breaks = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"_Z3r{i:02d}v", description="rm")
            for i in range(n_breaks)
        ]
        return DiffResult(
            changes=breaks + adds, old_version="1", new_version="2", library="l"
        ), breaks

    def _apply(self, summary, diff, categories=("addition",)):
        from abicheck.cli_scan_baseline import (
            _add_severity_blocking_compatible_findings,
        )

        _add_severity_blocking_compatible_findings(
            summary, diff, {"blocking": True, "blocking_categories": list(categories)}
        )

    def test_the_blocking_finding_is_named(self):
        diff, _ = self._diff()
        summary: dict = {}
        self._apply(summary, diff)
        named = [f for f in summary["findings"] if f["bucket"] == "compatible"]
        assert [f["symbol"] for f in named] == ["_Z3n00v", "_Z3n01v", "_Z3n02v"]

    def test_blocking_findings_reserve_slots_under_cap_pressure(self):
        # The cap is spent first by the legacy buckets, and under a demoting
        # preset those are exactly the findings that did *not* gate -- a plain
        # append handed all 20 slots to them and dropped the real blocker.
        from abicheck.cli_scan_baseline import _MAX_BASELINE_FINDINGS

        diff, breaks = self._diff(n_adds=3, n_breaks=25)
        summary = {
            "findings": [
                {"bucket": "breaking", "kind": "func_removed", "symbol": c.symbol}
                for c in breaks[:_MAX_BASELINE_FINDINGS]
            ]
        }
        self._apply(summary, diff)
        findings = summary["findings"]
        assert len(findings) <= _MAX_BASELINE_FINDINGS
        assert [f["symbol"] for f in findings if f["bucket"] == "compatible"] == [
            "_Z3n00v",
            "_Z3n01v",
            "_Z3n02v",
        ]
        assert summary["findings_truncated"] is True

    def test_breaking_findings_are_not_evicted_by_many_blockers(self):
        # The mirror of the reservation test above, and the failure the
        # reservation itself caused: 20+ error-level additions alongside an
        # ABI break exited 4 while itemizing only additions (Codex review).
        # Both are causes of the exit code; naming only one is wrong either
        # way round.
        from abicheck.cli_scan_baseline import _MAX_BASELINE_FINDINGS

        diff, breaks = self._diff(n_adds=25, n_breaks=25)
        summary = {
            "findings": [
                {"bucket": "breaking", "kind": "func_removed", "symbol": c.symbol}
                for c in breaks[:_MAX_BASELINE_FINDINGS]
            ]
        }
        self._apply(summary, diff)
        buckets = [f["bucket"] for f in summary["findings"]]
        assert len(summary["findings"]) <= _MAX_BASELINE_FINDINGS
        assert buckets.count("breaking") > 0, "the higher-exit cause must survive"
        assert buckets.count("compatible") > 0, "the blocking additions must too"

    def test_only_the_blamed_category_is_pulled_in(self):
        # a `severity.addition: error` config makes additions block; a quality finding
        # is equally compatible but did not fail the run, so spending report
        # slots on it would crowd out the one that did.
        diff, _ = self._diff()
        summary: dict = {}
        self._apply(summary, diff, categories=("quality_issues",))
        assert not summary.get("findings")

    def test_a_non_blocking_gate_adds_nothing(self):
        from abicheck.cli_scan_baseline import (
            _add_severity_blocking_compatible_findings,
        )

        diff, _ = self._diff()
        summary: dict = {}
        _add_severity_blocking_compatible_findings(
            summary, diff, {"blocking": False, "blocking_categories": []}
        )
        assert not summary.get("findings")


class TestResolveMaxBaselineFindings:
    """`_resolve_max_baseline_findings` -- CLI/API override, env var, default.

    Low-effort DX follow-up: the report cap used to be hard-coded with no
    override and no per-kind visibility into what a truncation cut.
    """

    def test_no_override_no_env_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ABICHECK_MAX_BASELINE_FINDINGS", raising=False)
        assert csb._resolve_max_baseline_findings(None) == csb._MAX_BASELINE_FINDINGS

    def test_explicit_override_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ABICHECK_MAX_BASELINE_FINDINGS", "5")
        assert csb._resolve_max_baseline_findings(50) == 50

    def test_explicit_zero_or_negative_is_a_usage_error(self) -> None:
        # Plain ValueError, not click.ClickException: this shared helper is
        # also reached from the Python API, whose callers never import click
        # (Codex review). The CLI's own --max-findings IntRange(min=1) means
        # this branch is unreachable from cli_scan.scan_cmd.
        with pytest.raises(ValueError, match="positive integer"):
            csb._resolve_max_baseline_findings(0)

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ABICHECK_MAX_BASELINE_FINDINGS", "7")
        assert csb._resolve_max_baseline_findings(None) == 7

    def test_malformed_env_var_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ABICHECK_MAX_BASELINE_FINDINGS", "not-a-number")
        assert csb._resolve_max_baseline_findings(None) == csb._MAX_BASELINE_FINDINGS

    def test_non_positive_env_var_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ABICHECK_MAX_BASELINE_FINDINGS", "0")
        assert csb._resolve_max_baseline_findings(None) == csb._MAX_BASELINE_FINDINGS


class TestAccumulateKindCounts:
    """`_accumulate_kind_counts` directly -- the ``if counter:`` empty-input
    branch is never reached through `_baseline_summary`/`_add_severity_
    blocking_compatible_findings` (both only call it when a truncation
    actually happened), so it needs its own direct coverage.
    """

    def test_empty_kinds_with_no_existing_field_adds_nothing(self) -> None:
        summary: dict = {}
        csb._accumulate_kind_counts(summary, "findings_truncated_kinds", [])
        assert "findings_truncated_kinds" not in summary

    def test_non_empty_kinds_are_counted_and_sorted(self) -> None:
        summary: dict = {}
        csb._accumulate_kind_counts(
            summary, "findings_truncated_kinds", ["b_kind", "a_kind", "a_kind"]
        )
        assert summary["findings_truncated_kinds"] == {"a_kind": 2, "b_kind": 1}

    def test_a_second_call_accumulates_onto_the_first(self) -> None:
        summary: dict = {"findings_truncated_kinds": {"a_kind": 1}}
        csb._accumulate_kind_counts(
            summary, "findings_truncated_kinds", ["a_kind", "b_kind"]
        )
        assert summary["findings_truncated_kinds"] == {"a_kind": 2, "b_kind": 1}


class TestBaselineSummaryTruncationKinds:
    """`_baseline_summary` reports a per-kind breakdown of truncated findings."""

    @staticmethod
    def _diff(kinds: list[str]):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult

        kind_map = {
            "func_removed": ChangeKind.FUNC_REMOVED,
            "type_size_changed": ChangeKind.TYPE_SIZE_CHANGED,
        }
        breaks = [
            Change(kind=kind_map[k], symbol=f"_Z{i:02d}v", description="d")
            for i, k in enumerate(kinds)
        ]
        return DiffResult(changes=breaks, old_version="1", new_version="2", library="l")

    def test_truncated_findings_report_a_per_kind_breakdown(self) -> None:
        kinds = ["func_removed"] * 15 + ["type_size_changed"] * 10
        diff = self._diff(kinds)
        summary = csb._baseline_summary(diff, max_findings=5)
        assert summary["findings_truncated"] is True
        assert len(summary["findings"]) == 5
        # 15 + 10 = 25 total, 5 kept -> 20 cut, split across the two kinds in
        # the same 15:10 ratio the input carried (kept greedily bucket by
        # bucket, so which 5 survive is deterministic but not what this test
        # pins -- only that every cut instance is accounted for).
        assert sum(summary["findings_truncated_kinds"].values()) == 20
        assert set(summary["findings_truncated_kinds"]) == {
            "func_removed",
            "type_size_changed",
        }

    def test_max_findings_override_changes_the_cap(self) -> None:
        diff = self._diff(["func_removed"] * 8)
        summary = csb._baseline_summary(diff, max_findings=3)
        assert len(summary["findings"]) == 3
        assert summary["findings_truncated"] is True
        assert summary["findings_truncated_kinds"] == {"func_removed": 5}

    def test_no_truncation_below_the_cap_has_no_kind_breakdown(self) -> None:
        diff = self._diff(["func_removed"] * 3)
        summary = csb._baseline_summary(diff, max_findings=5)
        assert "findings_truncated" not in summary
        assert "findings_truncated_kinds" not in summary


class TestBaselineSummaryKeysArePinned:
    """Every top-level key `_baseline_summary` can emit is pinned here.

    A generalized forcing function for the review round on PR #724 that
    found `findings_truncated_kinds`/`suppressed_truncated_kinds` shipped
    without a `SCAN_SCHEMA_VERSION` bump: this test only tracks key *names*
    (it can't check whether the version was actually bumped), but a real new
    key now fails CI here immediately, which is what makes it discoverable
    at all -- silently adding a key that isn't in `_KNOWN_KEYS` is the thing
    that let a schema bump go unnoticed the first time. Landing a new key
    means updating this set *and*, per
    ``abicheck/schemas/__init__.py``'s ``SCAN_SCHEMA_VERSION`` docstring,
    bumping the version with a history entry, and documenting it under the
    ``output-formats`` topic (``docs/use/output-formats.md`` /
    ``docs/_meta/topics.yaml``).
    """

    _KNOWN_KEYS = frozenset(
        {
            "breaking",
            "api_break",
            "risk",
            "compatible",
            "not_evaluated",
            "detectors",
            "findings",
            "findings_truncated",
            "findings_truncated_kinds",
            "suppressed_count",
            "suppressed",
            "suppressed_truncated",
            "suppressed_truncated_kinds",
            "additions",
            "additions_truncated",
            "additions_total",
            "quality",
            "quality_truncated",
            "quality_total",
            "policy",
            "policy_overrides",
            "policy_reclassify",
            "policy_file",
            "analysis_assurance",
            "analysis_assurance_exit_contribution",
            "coverage_warnings",
        }
    )

    def _diff_exercising_every_optional_key(self):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        breaking = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"_Zb{i:02d}v", description="d")
            for i in range(30)
        ]
        suppressed = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=f"_Zs{i:02d}v",
                description="d",
                suppression_rule="r",
            )
            for i in range(30)
        ]
        # An addition-shaped `ChangeKind` (FUNC_ADDED is in ADDITION_KINDS),
        # more than the cap (5) so `additions_truncated`/`additions_total`
        # are reachable too.
        additions = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol=f"_Za{i:02d}v", description="d")
            for i in range(30)
        ]
        # A compatible-but-non-addition ("quality") kind, same shape, so
        # `quality`/`quality_truncated`/`quality_total` are reachable too.
        quality = [
            Change(
                kind=ChangeKind.FIELD_BECAME_VOLATILE, symbol=f"_Zq{i:02d}v", description="d"
            )
            for i in range(30)
        ]
        from pathlib import Path

        from abicheck.analysis_assurance import AnalysisAssurance
        from abicheck.checker_policy import Verdict
        from abicheck.policy_file import PolicyFile
        from abicheck.reclassify import ReclassifyRule

        policy_file = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_VISIBILITY_CHANGED: Verdict.COMPATIBLE_WITH_RISK},
            reclassify=[
                ReclassifyRule(
                    to_verdict=Verdict.COMPATIBLE_WITH_RISK,
                    change_kind="func_removed",
                    symbol_pattern="_Z.*",
                    binding="weak",
                    reason="COMDAT-inline demotions",
                )
            ],
            source_path=Path("policy.yml"),
        )
        return types.SimpleNamespace(
            breaking=breaking,
            source_breaks=[],
            risk=[],
            compatible=additions + quality,
            not_evaluated=[breaking[0]],
            detector_results=[
                types.SimpleNamespace(
                    name="d1", changes_count=1, enabled=True, coverage_gap=None
                )
            ],
            suppressed_changes=suppressed,
            policy="strict_abi",
            policy_file=policy_file,
            coverage_warnings=["old and new binaries are byte-identical (sha256 match)"],
            # P0.4: `analysis_assurance_report_dict` (`analysis_assurance.py`)
            # only emits the key for a real `AnalysisAssurance` instance
            # (`isinstance` check) -- a plain `SimpleNamespace`/dict fixture
            # would silently leave this branch unexercised the same way an
            # absent attribute does (Codex review).
            analysis_assurance=AnalysisAssurance(status="partial"),
        )

    def test_every_emitted_key_is_pinned(self) -> None:
        diff = self._diff_exercising_every_optional_key()
        summary = csb._baseline_summary(diff, max_findings=5)
        unknown = set(summary) - self._KNOWN_KEYS
        assert not unknown, (
            f"_baseline_summary emitted unpinned key(s) {sorted(unknown)} -- "
            "add them to _KNOWN_KEYS here, and see this class's docstring "
            "for the schema-version/doc-registration checklist that goes "
            "with landing a real new report field."
        )

    def test_pinned_keys_are_all_actually_reachable(self) -> None:
        # The complementary direction: a key that's pinned here but that
        # _baseline_summary can no longer produce (renamed, removed) would
        # otherwise go unnoticed -- this input is constructed specifically
        # to trigger every optional branch at once.
        diff = self._diff_exercising_every_optional_key()
        summary = csb._baseline_summary(diff, max_findings=5)
        assert self._KNOWN_KEYS <= set(summary)


class TestBaselinePolicyDisclosure:
    """``scan --format json`` policy-audit disclosure (Codex review, upstream
    ask #2): the resolved ``policy_overrides``/``policy_reclassify`` rule set
    and each finding's own ``reclassified_by`` attribution, mirroring what
    ``compare``'s JSON report already discloses via
    ``reporter._add_policy_overrides``/``_reclassified_by_for_change``.
    """

    @staticmethod
    def _diff_with_policy_file(policy_file):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="_Z1fv", description="d", symbol_binding="weak"
        )
        return types.SimpleNamespace(
            breaking=[change],
            source_breaks=[],
            risk=[],
            compatible=[],
            not_evaluated=[],
            detector_results=[],
            suppressed_changes=[],
            policy="strict_abi",
            policy_file=policy_file,
        )

    def test_no_policy_file_omits_disclosure_keys(self) -> None:
        diff = self._diff_with_policy_file(None)
        summary = csb._baseline_summary(diff)
        assert "policy_overrides" not in summary
        assert "policy_reclassify" not in summary
        assert "policy_file" not in summary
        assert "reclassified_by" not in summary["findings"][0]

    def test_kind_global_override_is_disclosed(self) -> None:
        from pathlib import Path

        from abicheck.checker_policy import ChangeKind, Verdict
        from abicheck.policy_file import PolicyFile

        policy_file = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE_WITH_RISK},
            source_path=Path("policy.yml"),
        )
        diff = self._diff_with_policy_file(policy_file)
        summary = csb._baseline_summary(diff)
        assert summary["policy_overrides"] == {"func_removed": "COMPATIBLE_WITH_RISK"}
        assert summary["policy_file"] == "policy.yml"

    def test_selector_scoped_reclassify_rule_is_disclosed_active_only(self) -> None:
        from pathlib import Path

        from abicheck.checker_policy import Verdict
        from abicheck.policy_file import PolicyFile
        from abicheck.reclassify import ReclassifyRule

        active = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            symbol_pattern="_Z1.*",
            binding="weak",
            reason="COMDAT-inline demotions",
        )
        import datetime

        expired = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE,
            symbol_pattern="_Z2.*",
            expires=datetime.date(2000, 1, 1),
        )
        policy_file = PolicyFile(
            base_policy="strict_abi",
            reclassify=[active, expired],
            source_path=Path("policy.yml"),
        )
        diff = self._diff_with_policy_file(policy_file)
        summary = csb._baseline_summary(diff)
        assert len(summary["policy_reclassify"]) == 1
        assert summary["policy_reclassify"][0]["to"] == "COMPATIBLE_WITH_RISK"
        assert summary["policy_reclassify"][0]["binding"] == "weak"
        assert summary["policy_file"] == "policy.yml"
        # The rule actually decided this finding's verdict -- disclosed
        # per-finding too, not just as part of the active rule set.
        assert summary["findings"][0]["reclassified_by"] == "COMDAT-inline demotions"

    def test_non_matching_rule_leaves_finding_undisclosed(self) -> None:
        from pathlib import Path

        from abicheck.checker_policy import Verdict
        from abicheck.policy_file import PolicyFile
        from abicheck.reclassify import ReclassifyRule

        rule = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            symbol_pattern="_Znomatch.*",
            reason="unrelated",
        )
        policy_file = PolicyFile(
            base_policy="strict_abi", reclassify=[rule], source_path=Path("policy.yml")
        )
        diff = self._diff_with_policy_file(policy_file)
        summary = csb._baseline_summary(diff)
        # The rule is still disclosed as part of the active rule set...
        assert len(summary["policy_reclassify"]) == 1
        # ...but never attributed to a finding it didn't actually decide.
        assert "reclassified_by" not in summary["findings"][0]


class TestPublicProvenanceSet:
    def test_directory_activates_provenance(self, tmp_path: Path) -> None:
        d = tmp_path / "include"
        d.mkdir()
        f = tmp_path / "umbrella.h"
        f.write_text("", encoding="utf-8")
        files, dirs = csb._public_provenance_set([f, d], [])
        assert files == [f]
        assert d in dirs

    def test_lone_file_does_not_activate(self, tmp_path: Path) -> None:
        f = tmp_path / "umbrella.h"
        f.write_text("", encoding="utf-8")
        # a single header file with no directory boundary → no provenance
        assert csb._public_provenance_set([f], []) == ([], [])

    def test_explicit_public_dir_carries_through(self, tmp_path: Path) -> None:
        pub = tmp_path / "pub"
        pub.mkdir()
        files, dirs = csb._public_provenance_set([], [pub])
        assert dirs == [pub]
        assert files == []


class TestExpandPublicHeaders:
    def test_expands_directory_to_files(self, tmp_path: Path) -> None:
        d = tmp_path / "inc"
        d.mkdir()
        (d / "a.h").write_text("", encoding="utf-8")
        out = csb._expand_public_headers([d])
        assert any(p.endswith("a.h") for p in out)

    def test_falls_back_on_expansion_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import abicheck.service as service

        def boom(_headers: object) -> list[Path]:
            raise RuntimeError("nope")

        monkeypatch.setattr(service, "expand_header_inputs", boom)
        # best-effort: on failure it returns the raw paths as strings
        assert csb._expand_public_headers([Path("x.h")]) == ["x.h"]


class TestLoadRiskRules:
    def test_none_returns_default(self) -> None:
        assert isinstance(csb._load_risk_rules(None), RiskRules)

    def test_valid_yaml_block(self, tmp_path: Path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text("risk_rules: {}\n", encoding="utf-8")
        assert isinstance(csb._load_risk_rules(p), RiskRules)

    def test_malformed_yaml_raises_clickexception(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("risk_rules: [unbalanced\n", encoding="utf-8")
        with pytest.raises(click.ClickException):
            csb._load_risk_rules(p)


class TestBaselineIsNativeLibrary:
    @pytest.mark.parametrize(
        "name,expected",
        [("old.json", False), ("dump.dump", False), ("snap.xml", False),
         ("libfoo.so.1", True), ("bar.dll", True), ("baz.dylib", True)],
    )
    def test_suffix_heuristic(self, name: str, expected: bool) -> None:
        # non-existent paths fall through to the filename heuristic
        assert csb._baseline_is_native_library(Path(name)) is expected


class TestEmitEstimate:
    def _fake_estimate(self) -> object:
        return types.SimpleNamespace(
            layer="L2_header", method="castxml", tus=3, est_seconds=1.5,
            note="ok", to_dict=lambda: {"layer": "L2_header", "est_seconds": 1.5},
        )

    def _call(self, monkeypatch: pytest.MonkeyPatch, fmt: str, output: Path | None,
              binary: Path) -> None:
        import abicheck.service as service

        monkeypatch.setattr(service, "estimate_scan", lambda *a, **k: [self._fake_estimate()])
        csb._emit_estimate(
            binary=binary, headers=[], includes=[], sources=None, build_info=None,
            mode="pr", resolved_method=SourceMethod.S0, eff_depth=EvidenceDepth.HEADERS,
            changed=[], seeded=False, budget_s=None, lang="c", fmt=fmt, output=output,
        )

    def test_text_output(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                         capsys: pytest.CaptureFixture[str]) -> None:
        self._call(monkeypatch, "text", None, tmp_path / "libx.so")
        out = capsys.readouterr().out
        assert "dry run" in out and "projected total" in out

    def test_json_output_to_file(self, monkeypatch: pytest.MonkeyPatch,
                                 tmp_path: Path) -> None:
        out_p = tmp_path / "est.json"
        self._call(monkeypatch, "json", out_p, tmp_path / "libx.so")
        data = json.loads(out_p.read_text(encoding="utf-8"))
        assert data["mode"] == "pr"
        assert data["estimate"][0]["layer"] == "L2_header"
