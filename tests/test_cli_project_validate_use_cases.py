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

from click.testing import CliRunner, Result

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _run(args: list[str]) -> Result:
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


def _snapshot_with_walkable_graph(
    tmp_path: Path, name: str, *, with_train_function: bool
) -> Path:
    """A snapshot carrying both a real ``train`` `Function` (so
    `compare_snapshots` sees a genuine diff when it's removed) and a
    matching graph node/edge pair, mirroring ``test_use_cases.py``'s own
    ``_walkable_library_graph`` for exactly the one entrypoint these tests
    need."""
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    graph.add_node(
        GraphNode(id="binary_symbol://train", kind="binary_symbol", label="train")
    )
    graph.add_edge(
        GraphEdge(
            src="decl://train",
            dst="binary_symbol://train",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    functions = (
        [Function(name="train", mangled="train", return_type="void")]
        if with_train_function
        else []
    )
    snap = AbiSnapshot(library="libfoo.so", version=name, functions=functions)
    snap.build_source = BuildSourcePack(root="", source_graph=graph)
    out = tmp_path / f"{name}.json"
    save_snapshot(snap, out)
    return out


def _snapshot_with_or_without_train(
    tmp_path: Path, name: str, *, present: bool
) -> Path:
    """A snapshot that either fully carries `train` (function + graph node)
    or has neither — unlike `_snapshot_with_walkable_graph`, the graph node
    itself is absent too when `present=False`, so this can build a genuinely
    OLD-side-blind pair for testing NEW-side-only attribution (Codex
    review)."""
    graph = SourceGraphSummary()
    functions: list[Function] = []
    if present:
        graph.add_node(
            GraphNode(
                id="decl://train",
                kind="source_decl",
                label="train",
                attrs={"visibility": "public_header"},
            )
        )
        graph.add_node(
            GraphNode(id="binary_symbol://train", kind="binary_symbol", label="train")
        )
        graph.add_edge(
            GraphEdge(
                src="decl://train",
                dst="binary_symbol://train",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            )
        )
        functions = [Function(name="train", mangled="train", return_type="void")]
    snap = AbiSnapshot(library="libfoo.so", version=name, functions=functions)
    snap.build_source = BuildSourcePack(root="", source_graph=graph)
    out = tmp_path / f"{name}.json"
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

    def test_manifest_read_failure_is_a_usage_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """load_use_case_manifest() deliberately leaves a missing/unreadable
        MANIFEST as a bare OSError (its own docstring) -- Click's
        exists=True path check only proves the file was there at argument
        parsing time, not at the read a moment later (a TOCTOU race: the
        file is deleted, or a permission error, in between). Simulated here
        via monkeypatch rather than an actual race/chmod, since this suite
        runs as root in some environments where a permission bit is a
        no-op."""
        import abicheck.impact.use_cases as use_cases_mod

        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")

        def _raise_os_error(path):
            raise OSError("permission denied")

        monkeypatch.setattr(use_cases_mod, "load_use_case_manifest", _raise_os_error)
        res = _run([str(manifest)])
        assert res.exit_code == 64
        assert res.exception is None or isinstance(res.exception, SystemExit)
        assert "permission denied" in res.output

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


class TestDiffImpactAgainstNew:
    """``--against-new``: the manifest folded into a real two-snapshot diff
    (G29 Phase 4) via `impact.use_cases.explain_use_case_impact`."""

    def test_against_new_requires_against(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        new_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "new", with_train_function=False
        )
        res = _run([str(manifest), "--against-new", str(new_snapshot)])
        assert res.exit_code == 64
        assert "--against-new requires --against" in res.output

    def test_removed_function_attributed_to_declaring_use_case_text(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        new_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "new", with_train_function=False
        )
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(new_snapshot),
            ]
        )
        assert res.exit_code == 0, res.output
        assert "1 change(s), 1 attributed" in res.output
        assert "training workflow:" in res.output
        assert "func_removed: train" in res.output

    def test_removed_function_attributed_to_declaring_use_case_json(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        new_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "new", with_train_function=False
        )
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(new_snapshot),
                "--format",
                "json",
            ]
        )
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["against_new"] == str(new_snapshot)
        assert payload["diff_impact"] == {
            "total_changes": 1,
            "unattributed_changes": 0,
            "by_use_case": {
                "training workflow": [{"symbol": "train", "kind": "func_removed"}]
            },
        }

    def test_change_unreachable_from_any_use_case_is_unattributed(
        self, tmp_path: Path
    ) -> None:
        # A use case whose own entrypoint does not name the removed symbol
        # at all -- the change is real but attributed to no declared use
        # case, reported distinctly from "no changes at all".
        manifest = _write_manifest(
            tmp_path,
            "- use_case: unrelated workflow\n  entrypoints: [does_not_exist]\n",
        )
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        new_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "new", with_train_function=False
        )
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(new_snapshot),
            ]
        )
        assert res.exit_code == 0, res.output
        assert "1 change(s), 0 attributed" in res.output
        assert "no change is reachable from any declared use case" in res.output

    def test_no_changes_between_identical_snapshots(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        same_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old-copy", with_train_function=True
        )
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(same_snapshot),
            ]
        )
        assert res.exit_code == 0, res.output
        assert "0 change(s), 0 attributed" in res.output

    def test_added_function_attributed_via_the_new_side_graph(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: a symbol added on the NEW side never
        # existed in OLD's own graph, so attribution must also try NEW's
        # graph, not only OLD's -- an added declared entrypoint must not
        # silently read as unattributed just because it's new.
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old_snapshot = _snapshot_with_or_without_train(tmp_path, "old", present=False)
        new_snapshot = _snapshot_with_or_without_train(tmp_path, "new", present=True)
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(new_snapshot),
            ]
        )
        assert res.exit_code == 0, res.output
        assert "1 change(s), 1 attributed" in res.output
        assert "training workflow:" in res.output
        assert "func_added: train" in res.output

    def test_malformed_against_new_snapshot_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        bad_new = tmp_path / "bad_new.abi.json"
        bad_new.write_text(json.dumps({"not": "a snapshot"}))
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(bad_new),
            ]
        )
        assert res.exit_code == 64

    def test_nonexistent_against_new_path_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        old_snapshot = _snapshot_with_walkable_graph(
            tmp_path, "old", with_train_function=True
        )
        res = _run(
            [
                str(manifest),
                "--against",
                str(old_snapshot),
                "--against-new",
                str(tmp_path / "does-not-exist.json"),
            ]
        )
        assert res.exit_code != 0
