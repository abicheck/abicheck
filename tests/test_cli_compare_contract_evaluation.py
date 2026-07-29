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

"""ADR-049 Phase 3 — the native ``abicheck compare`` CLI's own ``--contract-
evaluation`` flag.

``service.compare_snapshots``/``run_compare_request``/``service.run_compare``
already forward a ``contract_evaluation`` keyword (``tests/test_service_unit.
py``'s ``TestContractEvaluationThreading``) and the MCP ``abi_compare`` tool
already exposes it, but until now no CLI front end did — ``cli.py`` was at
its 2000-line AI-readiness hard cap, blocking a new ``@click.option`` (see
``docs/contribute/plans/public-contract-default.md`` Phase 3). Extracting the
ADR-043 app-usage/required-symbol option family into
``cli_options.app_usage_scope_options`` freed the headroom; this file covers
the resulting CLI flag the same way ``tests/test_cli_comparability_gate.py``
covers the sibling ``--diagnostic-comparison`` escape hatch and
``tests/test_compare_dispatch.py`` covers its directory/package rejection."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.checker import DiffResult, Verdict
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


class TestFlagForwarded:
    def test_contract_evaluation_flag_forwarded_to_compare_snapshots(
        self, tmp_path, monkeypatch
    ):
        """--contract-evaluation must reach compare_snapshots as a real
        keyword, not be silently dropped at the Click/run_compare boundary
        (the same class of regression test_cli_comparability_gate.py already
        guards for --diagnostic-comparison)."""
        old_p, new_p = _write_pair(tmp_path)

        captured: dict[str, object] = {}

        def _fake_compare_snapshots(*_a, **kw):
            captured["contract_evaluation"] = kw.get("contract_evaluation")
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE,
            )

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _fake_compare_snapshots
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--contract-evaluation"],
        )
        assert result.exit_code == 0
        assert captured["contract_evaluation"] is True

    def test_contract_evaluation_defaults_to_false(self, tmp_path, monkeypatch):
        old_p, new_p = _write_pair(tmp_path)

        captured: dict[str, object] = {}

        def _fake_compare_snapshots(*_a, **kw):
            captured["contract_evaluation"] = kw.get("contract_evaluation")
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE,
            )

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _fake_compare_snapshots
        )

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0
        assert captured["contract_evaluation"] is False


class TestEndToEndJsonReport:
    def test_flag_stamps_contract_relevance_in_json_report(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract-evaluation",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        stamped = [c for c in payload["changes"] if "contract_relevance" in c]
        assert stamped, "fixture must produce at least one shadow-evaluated finding"
        for c in stamped:
            assert c["contract_relevance"] in {
                "IN_CONTRACT",
                "UNKNOWN_UNRESOLVED",
                "UNKNOWN_UNPROVEN",
                "PROVEN_OUT_OF_CONTRACT",
                "NOT_APPLICABLE",
            }
            assert isinstance(c["contract_reason_code"], str)

    def test_omitted_by_default(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json"],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        for c in payload["changes"]:
            assert "contract_relevance" not in c
            assert "contract_reason_code" not in c
            assert "contract_assurance" not in c

    def test_help_all_mentions_flag(self):
        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "--contract-evaluation" in result.output


class TestRejectedOnSetInputs:
    def test_contract_evaluation_rejected_on_directory_inputs(self, tmp_path):
        # The per-library directory/package (release) fan-out doesn't wire
        # ADR-049 Phase 3's shadow evaluator -- reject the flag loudly
        # rather than silently accept and ignore it (mirrors
        # test_compare_dispatch.py's identical --diagnostic-comparison
        # coverage).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(
            snapshot_to_json(old), encoding="utf-8"
        )
        (new_dir / "libfoo.json").write_text(
            snapshot_to_json(new), encoding="utf-8"
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--contract-evaluation"],
        )
        assert result.exit_code != 0
        assert "not supported for directory/package" in result.output
        assert "--contract-evaluation" in result.output
