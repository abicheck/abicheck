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

"""``impact-use-cases.yaml``'s two CLI front doors (G29 Phase 4, ADR-057).

``abicheck project validate-use-cases`` checks a manifest's own structure;
``abicheck compare --use-cases`` folds it into a real comparison and reports
which of that comparison's findings each declared use case reaches. (The
``--against``/``--against-new`` pair that used to diff two snapshots from
inside the validator is gone -- a manifest validator had grown a second
snapshot-diffing surface, and the attribution belongs where the comparison
already happens.)

This file exercises the CLI wiring end to end, not the resolution logic
itself (that's ``test_use_cases.py``'s job).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _run(args: list[str]) -> Result:
    return CliRunner().invoke(main, ["project", "validate-use-cases", *args])


def _compare(manifest: Path, old: Path, new: Path, *extra: str) -> Result:
    return CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--use-cases", str(manifest), *extra],
    )


def _json_report(res: Result) -> dict:
    """The JSON document out of a ``--format json`` run's mixed output.

    ``CliRunner`` folds stderr in, and ``compare`` writes an evidence-coverage
    preamble there, so the report starts at the first line that is a bare
    ``{``.
    """
    lines = res.output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "{")
    return json.loads("\n".join(lines[start:]))


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

    def test_json_format_reports_structure_only(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        res = _run([str(manifest), "--format", "json"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["ok"] is True
        assert payload["use_case_count"] == 1
        # Structure only: resolving entrypoints against a real library is
        # `compare --use-cases`, not this command.
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


class TestUseCaseImpactOnCompare:
    """``compare --use-cases``: the manifest folded into a real comparison.

    Resolution *and* attribution in one place -- the comparison the user is
    already running, rather than a second snapshot diff inside a validator.
    """

    def test_resolved_and_unresolved_entrypoints_reported_text(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            "- use_case: training workflow\n"
            "  entrypoints: [train, does_not_exist]\n"
            "  tests: [test_train_e2e]\n",
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = _compare(manifest, old, new)
        assert res.exit_code == 4, res.output
        assert "Use-case impact" in res.output
        assert "training workflow" in res.output
        assert "unresolved entrypoints: does_not_exist" in res.output

    def test_removed_function_attributed_to_declaring_use_case_text(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = _compare(manifest, old, new)
        assert res.exit_code == 4, res.output
        assert "training workflow: 1 change(s)" in res.output
        assert "- train (func_removed)" in res.output

    def test_removed_function_attributed_to_declaring_use_case_json(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = _compare(manifest, old, new, "--format", "json")
        assert res.exit_code == 4, res.output
        doc = _json_report(res)
        block = doc["use_case_impact"]
        assert block["manifest"] == str(manifest)
        assert block["use_case_count"] == 1
        assert block["unattributed_changes"] == 0
        (row,) = block["by_use_case"]["training workflow"]
        assert (row["symbol"], row["kind"]) == ("train", "func_removed")
        # finding_id is what lets a consumer join this row back to the
        # report's own findings array, so pin the join, not just the key.
        assert row["finding_id"] in {c["finding_id"] for c in doc["changes"]}
        assert block["use_cases"] == [
            {
                "use_case": "training workflow",
                "resolved_entrypoints": ["train"],
                "unresolved_entrypoints": [],
                "tests": [],
            }
        ]

    def test_change_unreachable_from_any_use_case_is_unattributed(
        self, tmp_path: Path
    ) -> None:
        # A use case whose own entrypoint does not name the removed symbol
        # at all -- the change is real but attributed to no declared use
        # case, counted rather than silently dropped.
        manifest = _write_manifest(
            tmp_path,
            "- use_case: unrelated workflow\n  entrypoints: [does_not_exist]\n",
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = _compare(manifest, old, new, "--format", "json")
        assert res.exit_code == 4, res.output
        block = _json_report(res)["use_case_impact"]
        assert block["by_use_case"] == {}
        assert block["unattributed_changes"] == block["total_changes"] == 1

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
        old = _snapshot_with_or_without_train(tmp_path, "old", present=False)
        new = _snapshot_with_or_without_train(tmp_path, "new", present=True)
        res = _compare(manifest, old, new, "--format", "json")
        doc = _json_report(res)
        block = doc["use_case_impact"]
        (row,) = block["by_use_case"]["training workflow"]
        assert (row["symbol"], row["kind"]) == ("train", "func_added")
        assert row["finding_id"] in {c["finding_id"] for c in doc["changes"]}

    def test_show_only_scopes_the_block_to_the_displayed_findings(
        self, tmp_path: Path
    ) -> None:
        """--show-only filters what the report lists, and the attribution was
        built from every finding -- so the block named a change absent from
        the findings section beside it, resurfacing exactly what the user
        filtered out (Codex review)."""
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_or_without_train(tmp_path, "old", present=False)
        new = _snapshot_with_or_without_train(tmp_path, "new", present=True)

        # Unfiltered: the added function is attributed and listed.
        full = _json_report(_compare(manifest, old, new, "--format", "json"))
        assert full["use_case_impact"]["by_use_case"]["training workflow"]

        # `--show-only removed` displays no findings here (the only change is
        # an addition), so the block must attribute none either.
        scoped = _json_report(
            _compare(
                manifest, old, new, "--format", "json", "--show-only", "removed"
            )
        )
        block = scoped["use_case_impact"]
        assert block["by_use_case"] == {}
        assert block["total_changes"] == len(scoped["changes"])
        # The manifest's own entrypoint resolution is a property of the
        # comparison, not of what is on screen, so it survives untouched.
        assert block["use_cases"] == full["use_case_impact"]["use_cases"]

    def test_show_only_scopes_the_markdown_section_too(self, tmp_path: Path) -> None:
        """The rendered-text fold has its own projection, on a different code
        path from the JSON one -- ``_fold_use_case_impact_into_text`` appends
        the section after the report body rather than emitting a key, so a
        JSON-only test leaves the fix in the text path unexercised."""
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_or_without_train(tmp_path, "old", present=False)
        new = _snapshot_with_or_without_train(tmp_path, "new", present=True)

        full = _compare(manifest, old, new, "--format", "markdown")
        assert "training workflow: 1 change(s)" in full.output, full.output

        scoped = _compare(
            manifest, old, new, "--format", "markdown", "--show-only", "removed"
        )
        # The only change is an addition, so `--show-only removed` displays
        # nothing -- and the section must attribute nothing to match. The use
        # case is still listed at zero: it is declared in the manifest either
        # way, and dropping the row would read as "not declared" rather than
        # "nothing displayed reaches it".
        assert "training workflow: 0 change(s)" in scoped.output, scoped.output

    def test_no_changes_between_identical_snapshots(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        same = _snapshot_with_walkable_graph(
            tmp_path, "old-copy", with_train_function=True
        )
        res = _compare(manifest, old, same, "--format", "json")
        assert res.exit_code == 0, res.output
        block = _json_report(res)["use_case_impact"]
        assert block["total_changes"] == 0
        assert block["unattributed_changes"] == 0

    def test_no_source_graph_on_either_side_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        """A pair with nothing to resolve entrypoints against fails loudly.

        An omitted section would read as "no use case is affected" for a run
        that never looked.
        """
        manifest = _write_manifest(tmp_path, "- use_case: x\n  entrypoints: [train]\n")
        graphless = _snapshot_without_graph(tmp_path)
        res = _compare(manifest, graphless, graphless)
        assert res.exit_code == 64, res.output
        assert "needs a source graph" in res.output

    def test_malformed_manifest_is_a_usage_error(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "- 42\n")
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = _compare(manifest, old, new)
        assert res.exit_code == 64, res.output

    def test_a_run_without_the_flag_carries_no_block(self, tmp_path: Path) -> None:
        """Omitted, not emitted empty, when the flag was never passed."""
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        res = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--format", "json"]
        )
        assert res.exit_code == 4, res.output
        assert "use_case_impact" not in _json_report(res)

    @pytest.mark.parametrize("fmt", ["sarif", "junit", "html"])
    def test_a_carrying_secondary_rescues_a_non_carrying_primary(
        self, tmp_path: Path, fmt: str
    ) -> None:
        """``--format html --write json=PATH`` does deliver the attribution.

        The secondary render reuses the same attributed ``DiffResult`` at
        ``report_mode="full"``, so rejecting on the primary format alone was
        arbitrary -- the primary-only error message had in fact been
        proposing this exact arrangement as the fix (Codex review).

        Asserting the block really lands in the secondary file, not merely
        that the invocation was accepted: "not rejected" would also pass
        against a run that silently dropped it, which is the failure the
        rejection exists to prevent in the first place.
        """
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        secondary = tmp_path / "second.json"
        res = _compare(
            manifest, old, new,
            "--format", fmt, "-o", str(tmp_path / f"r.{fmt}"),
            "--write", f"json={secondary}",
        )
        assert res.exit_code == 4, res.output
        assert "--use-cases is not supported" not in res.output
        block = json.loads(secondary.read_text(encoding="utf-8"))["use_case_impact"]
        assert block["by_use_case"]["training workflow"]

    def test_quick_profile_is_rescued_by_a_carrying_secondary_too(
        self, tmp_path: Path
    ) -> None:
        """The internal one-line format (``--profile quick``) constrains the
        *primary* shape only.

        CLI cleanup phase two, PR 1: this used to be ``--stat``, which
        constrained only the primary render the identical way -- the format
        changed, the property under test (a non-carrying primary paired with
        a carrying secondary is accepted, and the block lands only in the
        secondary) did not. The secondary always renders the full report, so
        the summary-only contract stays intact while the attribution still
        reaches the caller (Codex review, originally about --stat). Both
        halves are asserted in one run, so a fix that delivered the block by
        widening the primary format itself would fail.
        """
        manifest = _write_manifest(
            tmp_path, "- use_case: training workflow\n  entrypoints: [train]\n"
        )
        old = _snapshot_with_walkable_graph(tmp_path, "old", with_train_function=True)
        new = _snapshot_with_walkable_graph(tmp_path, "new", with_train_function=False)
        secondary = tmp_path / "full.json"
        res = _compare(
            manifest, old, new,
            "--profile", "quick",
            # Explicit flag overrides the profile's own `depth=binary`
            # (apply_compare_profile's documented precedence) -- this test's
            # `--use-cases` needs the fixtures' `build_source.source_graph`
            # (an L5 fact) to resolve entrypoints, which ADR-063 Phase 8's
            # `--depth` ceiling (project_snapshot_to_depth) now correctly
            # clears below `source`, same as a real `--depth binary` run
            # would. `quick`'s one-line *format* is what this test
            # exercises, not its depth.
            "--depth", "source",
            "--write", f"json={secondary}",
        )
        assert res.exit_code == 4, res.output
        assert "use_case_impact" in json.loads(secondary.read_text(encoding="utf-8"))
        # The primary one-line render carries no JSON at all to check for the
        # block's absence in -- it's a bare one-line summary on stdout.
        assert "use_case_impact" not in res.output
        # Confirms the primary really is the one-line format (not, say,
        # markdown that merely happens to omit the block) -- a single line,
        # no newline. stdout, not .output: an "Evidence coverage:" stderr
        # diagnostic is otherwise mixed in and legitimately multi-line
        # (CodeRabbit review; the multi-line failure this assertion first
        # hit against `.output` confirmed that diagnostic exists).
        assert "\n" not in res.stdout.strip()
