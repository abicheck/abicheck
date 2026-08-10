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

"""``abicheck project validate-use-cases`` (G29 Phase 4, ADR-057 amendment).

Gives ``impact-use-cases.yaml`` its first real front door: the manifest
parser and ``resolve_use_case_entrypoints`` (both already unit-tested in
``tests/test_use_cases.py``) previously had no CLI caller at all — a
manifest author had no way to find out a declared entrypoint failed to
resolve short of writing their own Python calling the module directly.
This file exercises the CLI wiring end to end, not the resolution logic
itself (that's ``test_use_cases.py``'s job).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.serialization import save_snapshot


def _run(args: list[str]):
    return CliRunner().invoke(main, ["project", "validate-use-cases", *args])


def _write_manifest(tmp_path: Path, text: str) -> Path:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text(text)
    return manifest


def _snapshot_with_graph(tmp_path: Path) -> Path:
    """A minimal snapshot carrying a source graph with one public entry
    (`train`) — mirrors ``test_use_cases.py``'s own ``_library_graph``."""
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.build_source = BuildSourcePack(root="", source_graph=graph)
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)
    return out


def _snapshot_without_graph(tmp_path: Path) -> Path:
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)
    return out


class TestStructuralValidationOnly:
    """No ``--against``: only the manifest's own structure is checked."""

    def test_valid_manifest_exits_0(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path,
            "- use_case: training workflow\n  entrypoints: [train]\n",
        )
        res = _run([str(manifest)])
        assert res.exit_code == 0, res.output
        assert "OK" in res.output
        assert "1 use case" in res.output

    def test_empty_manifest_is_valid(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "")
        res = _run([str(manifest)])
        assert res.exit_code == 0, res.output
        assert "0 use case" in res.output

    def test_malformed_manifest_is_a_usage_error(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "not_a_list: true\n")
        res = _run([str(manifest)])
        assert res.exit_code == 64
        assert "top-level document" in res.output

    def test_unknown_field_is_a_usage_error(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoint: [train]\n")
        res = _run([str(manifest)])
        assert res.exit_code == 64
        assert "unknown field" in res.output

    def test_nonexistent_manifest_is_a_usage_error(self, tmp_path: Path) -> None:
        res = _run([str(tmp_path / "does-not-exist.yaml")])
        assert res.exit_code != 0

    def test_json_format_carries_no_use_cases_key_without_against(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        res = _run([str(manifest), "--format", "json"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["ok"] is True
        assert payload["use_case_count"] == 1
        assert "use_cases" not in payload
        assert "against" not in payload

    def test_output_flag_writes_file(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        out_file = tmp_path / "report.json"
        res = _run([str(manifest), "--format", "json", "-o", str(out_file)])
        assert res.exit_code == 0, res.output
        assert res.output == ""
        payload = json.loads(out_file.read_text())
        assert payload["ok"] is True


class TestResolutionAgainstALibraryGraph:
    """With ``--against``: entrypoints resolve against a real source graph."""

    def test_resolved_and_unresolved_entrypoints_reported_text(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            "- use_case: training workflow\n"
            "  entrypoints: [train, does_not_exist]\n"
            "  tests: [test_train_e2e]\n",
        )
        snapshot = _snapshot_with_graph(tmp_path)
        res = _run([str(manifest), "--against", str(snapshot)])
        assert res.exit_code == 0, res.output
        assert "resolved: train" in res.output
        assert "unresolved" in res.output
        assert "does_not_exist" in res.output
        assert "test_train_e2e" in res.output

    def test_resolved_and_unresolved_entrypoints_reported_json(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            "- use_case: training workflow\n  entrypoints: [train, does_not_exist]\n",
        )
        snapshot = _snapshot_with_graph(tmp_path)
        res = _run([str(manifest), "--against", str(snapshot), "--format", "json"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["against"] == str(snapshot)
        assert payload["use_cases"] == [
            {
                "use_case": "training workflow",
                "resolved_entrypoints": ["train"],
                "unresolved_entrypoints": ["does_not_exist"],
                "tests": [],
            }
        ]

    def test_unresolved_entrypoint_never_fails_the_command(
        self, tmp_path: Path
    ) -> None:
        """Per the manifest format's own discipline: absence is never
        evidence of a wrong answer. Even a use case with *zero* resolved
        entrypoints still exits 0."""
        manifest = _write_manifest(
            tmp_path,
            "- use_case: nothing resolves\n  entrypoints: [does_not_exist]\n",
        )
        snapshot = _snapshot_with_graph(tmp_path)
        res = _run([str(manifest), "--against", str(snapshot)])
        assert res.exit_code == 0, res.output

    def test_no_entrypoints_declared_is_reported_distinctly(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: bare\n")
        snapshot = _snapshot_with_graph(tmp_path)
        res = _run([str(manifest), "--against", str(snapshot)])
        assert res.exit_code == 0, res.output
        assert "no entrypoints declared" in res.output

    def test_snapshot_without_a_source_graph_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        snapshot = _snapshot_without_graph(tmp_path)
        res = _run([str(manifest), "--against", str(snapshot)])
        assert res.exit_code == 64
        assert "carries no source graph" in res.output

    def test_malformed_snapshot_json_is_a_usage_error_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        """snapshot_from_dict's own raise surface for a malformed document
        isn't a closed set (KeyError, TypeError, ValueError, ...) -- every
        one of them must land as a clean usage error, not an internal
        crash, since AGAINST is arbitrary user-supplied JSON."""
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        bad_snapshot = tmp_path / "bad.abi.json"
        bad_snapshot.write_text(json.dumps({"not": "a snapshot"}))
        res = _run([str(manifest), "--against", str(bad_snapshot)])
        assert res.exit_code == 64
        assert res.exception is None or isinstance(res.exception, SystemExit)

    def test_nonexistent_against_path_is_a_usage_error(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        res = _run([str(manifest), "--against", str(tmp_path / "does-not-exist.json")])
        assert res.exit_code != 0
