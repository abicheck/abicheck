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
    UseCaseChange,
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
        assert impact.by_use_case["uc"] == (
            UseCaseChange(symbol="train", kind="func_removed"),
            UseCaseChange(symbol="predict", kind="func_removed"),
        )
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
        assert d["by_use_case"] == {
            "uc": [{"symbol": "train", "kind": "func_removed"}]
        }


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
