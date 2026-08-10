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

    Codex review: under `--severity-addition error` a compatible diff exits 1
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
        # `--severity-addition error` makes additions block; a quality finding
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
