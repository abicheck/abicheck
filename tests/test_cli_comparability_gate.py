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
monkeypatching ``abicheck.service.load_snapshot``/``compare_snapshots``
instead of driving a real dump, since the gate's own logic is already
covered end-to-end by ``tests/test_checker_comparability_gate.py``."""

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
        monkeypatch.setattr("abicheck.service.load_snapshot", lambda _: snap)

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
        monkeypatch.setattr("abicheck.service.load_snapshot", lambda _: snap)

        def _raise(*_a, **_kw):
            raise ProfileMismatchError("profile_fingerprint mismatch: dep.h changed")

        monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 16

    def test_json_format_emits_verdict_null_with_reason(self, tmp_path, monkeypatch):
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.service.load_snapshot", lambda _: snap)

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

    def test_diagnostic_comparison_flag_forwarded_and_bypasses_hard_fail(
        self, tmp_path, monkeypatch
    ):
        """--diagnostic-comparison must reach compare_snapshots as a real
        keyword, not be silently dropped at the Click/run_compare boundary
        (the exact class of bug already found once for --dump-manifest on
        this same function, per the G32 plan's own acceptance criteria)."""
        old_p, new_p = _write_placeholder_inputs(tmp_path)
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        monkeypatch.setattr("abicheck.service.load_snapshot", lambda _: snap)

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
