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

"""``compare --use-cases``'s two-sided attribution engine.

The sibling ``test_use_cases.py`` covers the one-sided primitives
(``explain_use_case_impact``/``resolve_use_case_entrypoints``) against a
single graph. What lives here is the part that only exists because a
comparison has *two* snapshots: which side each answer came from, and what
happens when the two disagree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.inline import BuildSourcePack
from abicheck.buildsource.source_graph import (
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.cli import main
from abicheck.impact.use_case_impact import (
    UseCaseImpact,
    build_use_case_impact,
)
from abicheck.impact.use_cases import UseCaseDefinition, UseCaseResolution
from abicheck.model import AbiSnapshot


def _graph(*entries: str) -> SourceGraphSummary:
    """A graph whose only public entries are *entries*, each mapping to the
    like-named binary symbol."""
    g = SourceGraphSummary()
    for name in entries:
        g.add_node(
            GraphNode(
                id=f"decl://{name}",
                kind="source_decl",
                label=name,
                attrs={"visibility": "public_header"},
            )
        )
        g.add_node(
            GraphNode(id=f"binary_symbol://{name}", kind="binary_symbol", label=name)
        )
        g.add_edge(
            GraphEdge(
                src=f"decl://{name}",
                dst=f"binary_symbol://{name}",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            )
        )
    return g


def _snapshot(graph: SourceGraphSummary | None, version: str) -> AbiSnapshot:
    snap = AbiSnapshot(library="libfoo.so", version=version)
    if graph is not None:
        snap.build_source = BuildSourcePack(
            root=Path("."), manifest={}, source_graph=graph
        )
    return snap


def _change(symbol: str) -> Change:
    return Change(ChangeKind.FUNC_REMOVED, symbol, f"{symbol} removed")


class TestNoGraphAtAll:
    def test_neither_side_has_a_graph_yields_none(self) -> None:
        # None rather than an empty block: an emitted block would read as "no
        # use case is affected" for a run that never looked.
        assert (
            build_use_case_impact(
                [UseCaseDefinition(use_case="uc", entrypoints=("train",))],
                _snapshot(None, "1"),
                _snapshot(None, "2"),
                [_change("train")],
                manifest="uc.yaml",
            )
            is None
        )


class TestAttributionUnionsBothSides:
    def test_a_new_side_only_symbol_is_still_attributed(self) -> None:
        """A symbol added on NEW never existed in OLD's graph, so attributing
        against OLD alone would read every addition as unattributed."""
        impact = build_use_case_impact(
            [UseCaseDefinition(use_case="uc", entrypoints=("train", "predict"))],
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train", "predict"), "2"),
            [_change("train"), _change("predict")],
            manifest="uc.yaml",
        )
        assert impact is not None
        assert [(c.symbol, c.kind) for c in impact.by_use_case["uc"]] == [
            ("train", "func_removed"),
            ("predict", "func_removed"),
        ]
        # Each row carries the report's own finding_id, so two findings that
        # share a symbol and kind stay distinguishable and joinable.
        assert all(c.finding_id for c in impact.by_use_case["uc"])
        assert impact.unattributed_changes == 0

    def test_a_symbol_no_entrypoint_reaches_is_counted_unattributed(self) -> None:
        impact = build_use_case_impact(
            [UseCaseDefinition(use_case="uc", entrypoints=("train",))],
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train"), "2"),
            [_change("train"), _change("orphan")],
            manifest="uc.yaml",
        )
        assert impact is not None
        assert impact.total_changes == 2
        assert impact.unattributed_changes == 1


class TestResolutionsUnionBothSides:
    """The Codex finding this module's ``_merge_resolutions`` exists for.

    Attribution was already two-sided; the emitted ``use_cases[]`` resolution
    was not, so it could list an entrypoint as unresolved right beside a
    finding attributed *through* that same entrypoint.
    """

    _DEFS = [
        UseCaseDefinition(use_case="uc", entrypoints=("train", "predict")),
    ]

    def _resolution(self, impact: object) -> UseCaseResolution:
        assert impact is not None
        (only,) = impact.resolutions  # type: ignore[attr-defined]
        return only

    def test_a_new_side_only_entrypoint_is_not_reported_unresolved(self) -> None:
        impact = build_use_case_impact(
            self._DEFS,
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train", "predict"), "2"),
            [_change("predict")],
            manifest="uc.yaml",
        )
        r = self._resolution(impact)
        assert r.resolved_entrypoints == ("train", "predict")
        assert r.unresolved_entrypoints == ()
        # The contradiction this guards against, stated directly: nothing may
        # be listed unresolved that the attribution beside it went through.
        assert impact is not None
        attributed = {c.symbol for c in impact.by_use_case["uc"]}
        assert attributed and not attributed & set(r.unresolved_entrypoints)

    def test_an_old_side_only_entrypoint_is_not_reported_unresolved(self) -> None:
        # The mirror case: a removed entrypoint resolves only against OLD.
        impact = build_use_case_impact(
            self._DEFS,
            _snapshot(_graph("train", "predict"), "1"),
            _snapshot(_graph("train"), "2"),
            [_change("predict")],
            manifest="uc.yaml",
        )
        r = self._resolution(impact)
        assert r.resolved_entrypoints == ("train", "predict")
        assert r.unresolved_entrypoints == ()

    def test_only_the_new_side_has_a_graph(self) -> None:
        # The degenerate shape of the same bug: an OLD-only resolution left
        # `use_cases` empty while `by_use_case` was populated.
        impact = build_use_case_impact(
            self._DEFS,
            _snapshot(None, "1"),
            _snapshot(_graph("train"), "2"),
            [_change("train")],
            manifest="uc.yaml",
        )
        r = self._resolution(impact)
        assert r.resolved_entrypoints == ("train",)
        assert r.unresolved_entrypoints == ("predict",)
        assert impact is not None and impact.by_use_case["uc"]

    def test_an_entrypoint_neither_side_resolves_stays_unresolved(self) -> None:
        impact = build_use_case_impact(
            [UseCaseDefinition(use_case="uc", entrypoints=("train", "ghost"))],
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train"), "2"),
            [_change("train")],
            manifest="uc.yaml",
        )
        r = self._resolution(impact)
        assert r.resolved_entrypoints == ("train",)
        assert r.unresolved_entrypoints == ("ghost",)

    def test_declared_order_survives_the_merge(self) -> None:
        # resolve_use_case_entrypoints preserves the manifest author's order;
        # merging two sides must not sort or interleave it.
        defs = [
            UseCaseDefinition(
                use_case="uc", entrypoints=("zeta", "alpha", "ghost", "mid")
            )
        ]
        impact = build_use_case_impact(
            defs,
            _snapshot(_graph("alpha"), "1"),
            _snapshot(_graph("zeta", "mid"), "2"),
            [_change("alpha")],
            manifest="uc.yaml",
        )
        r = self._resolution(impact)
        assert r.resolved_entrypoints == ("zeta", "alpha", "mid")
        assert r.unresolved_entrypoints == ("ghost",)


class TestReportShape:
    def test_to_dict_carries_both_halves(self) -> None:
        impact = build_use_case_impact(
            [UseCaseDefinition(use_case="uc", entrypoints=("train",), tests=("t1",))],
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train"), "2"),
            [_change("train")],
            manifest="uc.yaml",
        )
        assert impact is not None
        d = impact.to_dict()
        assert d["manifest"] == "uc.yaml"
        assert d["use_case_count"] == 1
        assert d["total_changes"] == 1
        assert d["unattributed_changes"] == 0
        assert d["use_cases"] == [
            {
                "use_case": "uc",
                "resolved_entrypoints": ["train"],
                "unresolved_entrypoints": [],
                "tests": ["t1"],
            }
        ]
        (row,) = d["by_use_case"]["uc"]
        assert (row["symbol"], row["kind"]) == ("train", "func_removed")
        assert row["finding_id"]


class TestSetInputsAreRejected:
    def test_a_directory_compare_rejects_the_manifest(self, tmp_path: Path) -> None:
        """Attribution walks one pair's own call graphs, which the per-library
        release fan-out never builds -- so accepting the flag there would
        attribute nothing and say so nowhere (Codex review)."""
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "libfoo.so").write_bytes(b"\x7fELF" + b"\x00" * 100)
        (new_dir / "libfoo.so").write_bytes(b"\x7fELF" + b"\x00" * 100)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text("use_cases: []\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--use-cases", str(manifest)],
        )
        assert result.exit_code == 64, result.output
        assert "--use-cases is not supported for directory/package" in result.output

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_the_dry_run_agrees_with_the_real_run(
        self, tmp_path: Path, extra: list[str]
    ) -> None:
        # A dry run must not report the combination as valid when the real run
        # would reject it -- the rejection is validated ahead of the emit.
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "libfoo.so").write_bytes(b"\x7fELF" + b"\x00" * 100)
        (new_dir / "libfoo.so").write_bytes(b"\x7fELF" + b"\x00" * 100)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text("use_cases: []\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--use-cases", str(manifest), *extra],
        )
        assert result.exit_code == 64, result.output


class TestStatKeepsItsSummaryOnlyShape:
    """``--stat``'s documented contract is one shape: "With --format json,
    emits only the summary object". A use-case attribution block is the
    opposite of a summary, so the combination is rejected rather than
    silently dropping the manifest — the same reasoning as the set-input
    rejection above (Codex review)."""

    @staticmethod
    def _pair(tmp_path: Path) -> tuple[Path, Path]:
        from abicheck.model import Function, Visibility
        from abicheck.serialization import snapshot_to_json

        paths = []
        for name, version, funcs in (("old", "1", ["a", "b"]), ("new", "2", ["a"])):
            snap = AbiSnapshot(
                library="libfoo.so",
                version=version,
                functions=[
                    Function(
                        name=f,
                        mangled=f,
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                    for f in funcs
                ],
            )
            path = tmp_path / f"{name}.json"
            path.write_text(snapshot_to_json(snap), encoding="utf-8")
            paths.append(path)
        return paths[0], paths[1]

    @pytest.mark.parametrize("fmt", ["json", "markdown", "review"])
    def test_stat_with_use_cases_is_a_usage_error(
        self, tmp_path: Path, fmt: str
    ) -> None:
        # Every format, not just json: the rendered-text paths appended the
        # detailed section after the one-line stat output for the same reason.
        old, new = self._pair(tmp_path)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text("use_cases: []\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old), str(new),
                "--stat", "--format", fmt, "--use-cases", str(manifest),
            ],
        )
        assert result.exit_code == 64, result.output
        assert "--use-cases is not supported with --stat" in result.output

    @pytest.mark.parametrize("fmt", ["sarif", "junit", "html"])
    def test_a_format_that_cannot_carry_the_block_is_rejected(
        self, tmp_path: Path, fmt: str
    ) -> None:
        """None of these renderers reads ``DiffResult.use_case_impact``, so
        the attribution was resolved and then dropped -- an apparently
        successful report with the requested data silently missing (Codex
        review). Same reasoning as ``--stat``: a manifest that attributes
        nothing, and says so nowhere, is the failure ``--use-cases`` is
        already rejected for set inputs to avoid."""
        old, new = self._pair(tmp_path)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text("use_cases: []\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old), str(new), "--use-cases", str(manifest),
                "--format", fmt, "-o", str(tmp_path / f"r.{fmt}"),
            ],
        )
        assert result.exit_code == 64, result.output
        assert f"--use-cases is not supported with --format {fmt}" in result.output

    @pytest.mark.parametrize("fmt", ["json", "markdown", "review"])
    def test_the_carrying_formats_stay_accepted(
        self, tmp_path: Path, fmt: str
    ) -> None:
        # The rejection must be scoped to the formats that really drop it.
        old, new = self._pair(tmp_path)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text("use_cases: []\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--use-cases", str(manifest),
             "--format", fmt],
        )
        assert "--use-cases is not supported" not in result.output, result.output

    def test_stat_without_the_manifest_still_emits_the_summary(
        self, tmp_path: Path
    ) -> None:
        # The rejection must not have narrowed --stat itself.
        import json

        old, new = self._pair(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--stat", "--format", "json"]
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output[result.output.find("{"):])
        assert "use_case_impact" not in doc
        assert "changes" not in doc

    def test_the_renderer_itself_never_adds_the_block(self) -> None:
        """The CLI rejection is a guard, not the contract's only enforcement:
        a direct caller of the summary-only renderer must get the same shape
        even when the result carries an attribution."""
        import json

        from abicheck.checker import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.reporter import to_stat_json

        result = DiffResult(
            old_version="1", new_version="2", library="libfoo.so",
            changes=[_change("train")], verdict=Verdict.BREAKING,
        )
        result.use_case_impact = build_use_case_impact(
            [UseCaseDefinition(use_case="uc", entrypoints=("train",))],
            _snapshot(_graph("train"), "1"),
            _snapshot(_graph("train"), "2"),
            [_change("train")],
            manifest="uc.yaml",
        )
        assert result.use_case_impact is not None  # the block really exists
        assert "use_case_impact" not in json.loads(to_stat_json(result))


class TestRestrictedToTheDisplayedFindings:
    """``--show-only`` filters what the report lists; the attribution was
    built from every finding, so the block named changes absent from the
    findings section beside it (Codex review)."""

    _DEFS = [UseCaseDefinition(use_case="uc", entrypoints=("train", "predict"))]

    def _impact(self, changes: list[Change]) -> UseCaseImpact:
        graph = _graph("train", "predict")
        impact = build_use_case_impact(
            self._DEFS,
            _snapshot(graph, "1"),
            _snapshot(graph, "2"),
            changes,
            manifest="uc.yaml",
        )
        assert impact is not None
        return impact

    def test_a_dropped_finding_leaves_the_block(self) -> None:
        changes = [_change("train"), _change("predict")]
        impact = self._impact(changes)
        shown = impact.restricted_to([changes[0]])
        assert [c.symbol for c in shown.by_use_case["uc"]] == ["train"]

    def test_the_counters_describe_the_displayed_set(self) -> None:
        changes = [_change("train"), _change("orphan")]
        impact = self._impact(changes)
        assert (impact.total_changes, impact.unattributed_changes) == (2, 1)
        # Only the unattributed one is displayed: the counters must follow
        # what the reader sees, not the set they cannot.
        shown = impact.restricted_to([changes[1]])
        assert (shown.total_changes, shown.unattributed_changes) == (1, 1)
        assert shown.by_use_case == {}

    def test_a_use_case_left_with_nothing_is_dropped(self) -> None:
        changes = [_change("train")]
        impact = self._impact(changes)
        assert impact.by_use_case
        assert self._impact(changes).restricted_to([]).by_use_case == {}

    def test_resolutions_survive_the_projection(self) -> None:
        # The manifest's own entrypoint resolution is a property of the
        # comparison, not of which findings are on screen.
        impact = self._impact([_change("train")])
        assert impact.restricted_to([]).resolutions == impact.resolutions

    def test_two_findings_sharing_symbol_and_kind_project_independently(
        self,
    ) -> None:
        """The join is on finding_id, not (symbol, kind).

        One symbol can emit the same ChangeKind twice -- two parameters of
        one function each changing pointer depth -- and joining on
        (symbol, kind) would keep or drop both together.
        """
        from abicheck.checker_policy import ChangeKind

        first = Change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED, "train", "param 0: int* -> int**"
        )
        second = Change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED, "train", "param 1: int* -> int**"
        )
        impact = self._impact([first, second])
        rows = impact.by_use_case["uc"]
        assert len(rows) == 2
        assert len({c.finding_id for c in rows}) == 2, "the two rows collided"
        shown = impact.restricted_to([first])
        assert [c.finding_id for c in shown.by_use_case["uc"]] == [rows[0].finding_id]


class TestOneRowPerUseCaseNotPerManifestEntry:
    """A manifest may split one use case across several entries.

    `explain_use_case_impact` already unions their entrypoints by name, so
    attribution landed under a single key while the count and resolutions
    were computed per entry -- `use_case_count: 2` for one use case, a
    duplicate `use_cases` row, and the text renderer printing the same
    combined findings twice (Codex review).
    """

    _SPLIT = [
        UseCaseDefinition(use_case="training", entrypoints=("train",), tests=("t1",)),
        UseCaseDefinition(use_case="training", entrypoints=("predict",), tests=("t2",)),
    ]

    def _impact(self, definitions: list[UseCaseDefinition]) -> UseCaseImpact:
        graph = _graph("train", "predict")
        impact = build_use_case_impact(
            definitions,
            _snapshot(graph, "1"),
            _snapshot(graph, "2"),
            [_change("train"), _change("predict")],
            manifest="uc.yaml",
        )
        assert impact is not None
        return impact

    def test_the_count_and_rows_follow_the_use_cases(self) -> None:
        impact = self._impact(self._SPLIT)
        assert impact.use_case_count == 1
        assert [r.use_case for r in impact.resolutions] == ["training"]
        assert list(impact.by_use_case) == ["training"]

    def test_the_split_entries_union_their_entrypoints_and_tests(self) -> None:
        (row,) = self._impact(self._SPLIT).resolutions
        assert row.resolved_entrypoints == ("train", "predict")
        assert row.tests == ("t1", "t2")

    def test_a_repeat_inside_one_use_case_is_not_listed_twice(self) -> None:
        (row,) = self._impact([
            UseCaseDefinition(use_case="training", entrypoints=("train",)),
            UseCaseDefinition(use_case="training", entrypoints=("train", "predict")),
        ]).resolutions
        assert row.resolved_entrypoints == ("train", "predict")

    def test_distinct_use_cases_are_still_separate(self) -> None:
        # The coalescing must key on the name, not collapse everything.
        impact = self._impact([
            UseCaseDefinition(use_case="training", entrypoints=("train",)),
            UseCaseDefinition(use_case="serving", entrypoints=("predict",)),
        ])
        assert impact.use_case_count == 2
        assert [r.use_case for r in impact.resolutions] == ["training", "serving"]

    def test_the_rendered_section_does_not_repeat_the_use_case(self) -> None:
        from abicheck.impact.use_case_impact import render_use_case_impact_lines

        lines = render_use_case_impact_lines(self._impact(self._SPLIT))
        assert sum(1 for line in lines if "training:" in line) == 1, lines
