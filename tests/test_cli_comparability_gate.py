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

"""ADR-050 D2 — the native ``abicheck compare`` CLI command's own wiring of
the comparability gate: the ``--diagnostic-comparison`` flag and the
dedicated ``except (ProfileMismatchError, ScopeMismatchError)`` branch in
``cli_compare_helpers.run_compare`` (which calls ``service.compare_snapshots``
directly, bypassing ``CompareRequest``/``run_compare_request`` — see
``abicheck/comparability.py``'s module docstring). Mirrors
``tests/test_cli_coverage.py::TestCompareApiBreakExitCode``'s pattern of
monkeypatching ``abicheck.workflows.input_resolution.load_snapshot``/``compare_snapshots``
instead of driving a real dump, since the gate's own logic is already
covered end-to-end by ``tests/test_checker_comparability_gate.py``.

``load_snapshot`` is patched via ``abicheck.workflows.input_resolution``
(where ``resolve_input`` now lives); ``compare_snapshots`` stays patched via
``abicheck.service`` (it did not move — see ADR-061 Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.checker import DiffResult, Verdict
from abicheck.cli import main
from abicheck.errors import ProfileMismatchError, ScopeMismatchError
from abicheck.model import AbiSnapshot
from abicheck.reporter import to_json
from abicheck.serialization import snapshot_to_json


def _write_placeholder_inputs(tmp_path: Path) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text("{}", encoding="utf-8")
    new_p.write_text("{}", encoding="utf-8")
    return old_p, new_p


class TestNotComparableExitCode:
    def test_scope_mismatch_exits_16(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ScopeMismatchError(
                "scope_fingerprint mismatch: new/bar.h has no counterpart on "
                "the old side"
            )

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 16
        assert "not comparable" in result.output
        assert "--diagnostic-comparison" in result.output

    def test_profile_mismatch_exits_16(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ProfileMismatchError("profile_fingerprint mismatch: dep.h changed")

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 16

    def test_dependency_scope_mismatch_exits_16_end_to_end(self, tmp_path):
        """Real JSON snapshots, no mocking of load_snapshot/compare_snapshots
        -- exercises the actual dependency-scope comparability check added
        for the dump/compare filtering asymmetry (dumper_scoping.py), the
        exact "compare a filtered dump baseline against an unfiltered one"
        scenario this axis exists to catch."""
        old_snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            from_headers=True,
            dependency_scope="filtered",
        )
        new_snap = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            from_headers=True,
            dependency_scope="full",
        )
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 16
        assert "not comparable" in result.output
        assert "dependency-scoping" in result.output

    def test_json_format_emits_verdict_null_with_reason(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ScopeMismatchError("scope drift")

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        out_p = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json", "-o", str(out_p)],
        )
        assert result.exit_code == 16
        doc = json.loads(out_p.read_text(encoding="utf-8"))
        assert doc["verdict"] is None
        assert doc["reason"]["kind"] == "scope_mismatch"
        assert "scope drift" in doc["reason"]["message"]
        assert doc["library"] == "libfoo.so.1"
        assert "report_schema_version" in doc

    def test_sarif_format_emits_failed_invocation(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ScopeMismatchError("scope drift")

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        out_p = tmp_path / "report.sarif"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "sarif", "-o", str(out_p)],
        )
        assert result.exit_code == 16
        doc = json.loads(out_p.read_text(encoding="utf-8"))
        run = doc["runs"][0]
        assert run["invocations"][0]["executionSuccessful"] is False
        assert run["invocations"][0]["exitCode"] == 16
        assert run["results"] == []
        assert "scope drift" in run["invocations"][0]["toolExecutionNotifications"][0]["message"]["text"]

    def test_junit_format_emits_errored_testcase(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ProfileMismatchError("dep.h changed")

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        out_p = tmp_path / "report.xml"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "junit", "-o", str(out_p)],
        )
        assert result.exit_code == 16
        xml = out_p.read_text(encoding="utf-8")
        assert 'errors="1"' in xml
        assert "profile_mismatch" in xml
        assert "dep.h changed" in xml

    def test_diagnostic_comparison_flag_forwarded_and_bypasses_hard_fail(
        self, tmp_path, monkeypatch
    ):
        """--diagnostic-comparison must reach compare_snapshots as a real
        keyword, not be silently dropped at the Click/run_compare boundary
        (the exact class of bug already found once for --dump-manifest on
        this same function, per the G32 plan's own acceptance criteria)."""
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.workflows.input_resolution.load_snapshot", lambda _: snap)

        captured: dict[str, object] = {}

        def _fake_compare_snapshots(*_a, **kw):
            from abicheck.checker import DiffResult, Verdict

            captured["diagnostic_comparison"] = kw.get("diagnostic_comparison")
            return DiffResult(
                old_version="1",
                new_version="1",
                library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE,
                assurance="none",
            )

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _fake_compare_snapshots
        )
        monkeypatch.setattr(
            "abicheck.service_render.to_markdown", lambda _r, **_kw: "REPORT"
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--diagnostic-comparison"],
        )
        assert result.exit_code == 0
        assert captured["diagnostic_comparison"] is True

    def test_labeled_include_reaches_snapshot_resolution(self, tmp_path, monkeypatch):
        """A labeled ``--include old:LABEL=PATH``/``new:LABEL=PATH`` (ADR-050
        D1, ``SidedIncludePathParam``) must reach
        ``cli_resolve._resolve_compare_snapshots`` as a real
        ``include_labels`` keyword — the CLI-parsing glue that was, until
        this test, the last unverified hop between the Click option and
        ``dumper_contract._attach_extraction_contract`` (see
        ``tests/test_dumper_contract.py::TestExtraIncludeLabels`` for the
        hop below this one)."""
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        old_src = tmp_path / "old" / "src"
        new_src = tmp_path / "new" / "src"
        old_src.mkdir(parents=True)
        new_src.mkdir(parents=True)

        captured: dict[str, object] = {}
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")

        def _fake_resolve(*_a, **kw):
            captured["include_labels"] = kw.get("include_labels")
            return snap, snap

        monkeypatch.setattr(
            "abicheck.cli_compare_helpers._resolve_compare_snapshots", _fake_resolve
        )
        monkeypatch.setattr(
            "abicheck.service.compare_snapshots",
            lambda *_a, **_kw: DiffResult(
                old_version="1", new_version="1", library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE, assurance="none",
            ),
        )
        monkeypatch.setattr(
            "abicheck.service_render.to_markdown", lambda _r, **_kw: "REPORT"
        )

        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--include", f"old:support={old_src}",
                "--include", f"new:support={new_src}",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["include_labels"] == {
            old_src: "support",
            new_src: "support",
        }

    def test_labeled_include_reaches_inline_source_embed(self, tmp_path, monkeypatch):
        """A labeled ``--include old:LABEL=PATH`` combined with a raw
        ``--old-sources`` tree (the inline-embed path,
        ``cli._embed_inline_source_side``) must also carry the resolved
        ``include_labels`` map through its own nested ``dump`` invocation —
        found via CodeRabbit review: ``include_labels`` reached
        ``_resolve_compare_snapshots`` (the test above) but was never
        threaded into the *inline*-embed call sites, so a raw source tree's
        temporary snapshot silently lost the label the non-inline path
        already carried."""
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        old_src = tmp_path / "old" / "src"
        old_sources = tmp_path / "old" / "tree"
        old_src.mkdir(parents=True)
        old_sources.mkdir(parents=True)

        captured: dict[str, object] = {}

        def _fake_embed(*_a, **kw):
            captured["include_labels"] = kw.get("include_labels")
            return old_p, None, None

        monkeypatch.setattr(
            "abicheck.frontends.cli.commands.compare._embed_inline_source_side", _fake_embed
        )
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr(
            "abicheck.cli_compare_helpers._resolve_compare_snapshots",
            lambda *_a, **_kw: (snap, snap),
        )
        monkeypatch.setattr(
            "abicheck.service.compare_snapshots",
            lambda *_a, **_kw: DiffResult(
                old_version="1", new_version="1", library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE, assurance="none",
            ),
        )
        monkeypatch.setattr(
            "abicheck.service_render.to_markdown", lambda _r, **_kw: "REPORT"
        )

        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--sources", f"old={old_sources}",
                "--include", f"old:support={old_src}",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["include_labels"] == {old_src: "support"}


class TestCompareReleaseNotComparable:
    """ADR-050 D2 wired into the directory/package (release) fan-out
    (``cli_compare_release._compare_one_library`` /
    ``_compare_release_libraries``) — a real, on-disk mismatched-contract
    snapshot pair (mirrors ``test_mcp_server_unit.py``'s
    ``_make_mismatched_scope_pair``) drives the *entire* release pipeline,
    not just ``_compare_one_library`` in isolation, so the post-processing
    loop's ``elif v == "not_comparable":`` echo branch actually executes."""

    def _make_release(self, tmp_path: Path) -> tuple[Path, Path]:
        from abicheck.comparability import compute_extraction_contract

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        a = tmp_path / "v1" / "a.h"
        old_h = tmp_path / "v1" / "foo.h"
        new_h = tmp_path / "v2" / "bar.h"
        old_h.parent.mkdir(parents=True)
        new_h.parent.mkdir(parents=True)
        a.write_text("int g(void);\n")
        old_h.write_text("int f(void);\n")
        new_h.write_text("int f(void);\n")
        old = AbiSnapshot(
            library="libtest.so",
            version="1.0",
            contract=compute_extraction_contract(declared_headers=[a, old_h]),
        )
        new = AbiSnapshot(
            library="libtest.so",
            version="2.0",
            contract=compute_extraction_contract(declared_headers=[a, new_h]),
        )
        (old_dir / "libtest.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libtest.json").write_text(snapshot_to_json(new), encoding="utf-8")
        return old_dir, new_dir

    def test_release_fanout_exits_16_and_echoes_reason(self, tmp_path):
        old_dir, new_dir = self._make_release(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir)],
        )
        assert result.exit_code == 16
        assert "Not comparable: libtest.json" in result.output

    def test_release_fanout_writes_verdict_null_report(self, tmp_path):
        old_dir, new_dir = self._make_release(tmp_path)
        out_dir = tmp_path / "reports"
        out_dir.mkdir()
        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--output-dir", str(out_dir)],
        )
        assert result.exit_code == 16
        doc = json.loads((out_dir / "libtest.json").read_text(encoding="utf-8"))
        assert doc["verdict"] is None
        assert doc["reason"]["kind"] == "scope_mismatch"
        # CodeRabbit review, PR #631: a not_comparable release artifact must
        # keep the same old_version/new_version pair identity a normal
        # to_json(result) report carries, or a consumer can't tell which
        # release pair this report is for. compare's release fan-out defaults
        # these to the literal "old"/"new" labels when --old-version/
        # --new-version aren't given (unrelated to each snapshot's own
        # AbiSnapshot.version) -- what matters here is that the field is
        # present at all, matching to_json(result)'s normal shape.
        assert doc["old_version"] == "old"
        assert doc["new_version"] == "new"


class TestJsonReporterContractFields:
    """reporter.to_json's ADR-050 D2 (schema 2.17) contract_coverage/assurance
    fields -- a unit test of _add_confidence_evidence directly, since neither
    field is reachable through the CLI-level tests above (those exercise the
    hard-fail path, where no DiffResult is ever constructed, not an ordinary
    completed diff carrying one of these two fields)."""

    def _result(self, **kwargs: object) -> DiffResult:
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so.1",
            verdict=Verdict.NO_CHANGE,
            **kwargs,
        )

    def test_contract_coverage_and_assurance_present_when_set(self):
        result = self._result(contract_coverage="partial", assurance="none")
        doc = json.loads(to_json(result))
        assert doc["contract_coverage"] == "partial"
        assert doc["assurance"] == "none"

    def test_contract_coverage_and_assurance_omitted_when_unset(self):
        result = self._result()
        doc = json.loads(to_json(result))
        assert "contract_coverage" not in doc
        assert "assurance" not in doc
