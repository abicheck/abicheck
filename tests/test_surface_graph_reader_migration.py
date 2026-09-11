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

"""ADR-063 Phase 10: the five ``BuildSourcePack.source_graph`` live-alias
readers (``internal_leak.py``, ``buildsource/cross_source_checks.py``,
``buildsource/evidence_report.py``, ``evidence_depth.py``, ``cli_graph.py``)
now prefer the canonical ``AbiSnapshot.surface_graph``, falling back to the
legacy ``build_source.source_graph`` nested field only when ``surface_graph``
is absent -- a pre-Phase-3 snapshot never populates it (see
``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 10 checklist).

Each test below builds a *pre-Phase-3-shaped* fixture -- ``surface_graph``
absent, ``build_source.source_graph`` present -- and asserts the reader's
output is byte-for-byte identical to the same fixture with ``surface_graph``
set to the *same* graph object (the ordinary post-Phase-3 shape a fresh
snapshot now carries). Identical output across both shapes is exactly the
"no regression from the migration" contract this phase's checklist asks for:
the fallback must make a pre-Phase-3 document behave exactly as it did before
this migration, while a fresh snapshot's canonical field is what actually
gets read first.
"""

from __future__ import annotations

import json

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.model import AbiSnapshot, Function, ScopeOrigin


def _decl(node_id: str, label: str, visibility: str) -> GraphNode:
    return GraphNode(
        id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
    )


def _pre_and_post_phase3_snaps(
    graph: SourceGraphSummary,
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """One snapshot shaped like a pre-Phase-3 document (``surface_graph``
    absent) and one shaped like a fresh, post-Phase-3 one (``surface_graph``
    is the identical object ``build_source.source_graph`` holds -- the
    Phase 3 assembly step's own one-object guarantee)."""
    pre = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        build_source=BuildSourcePack(root="", source_graph=graph),
    )
    post = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        build_source=BuildSourcePack(root="", source_graph=graph),
        surface_graph=graph,
    )
    return pre, post


# --------------------------------------------------------------------------- #
# internal_leak.compute_call_graph_leak_paths
# --------------------------------------------------------------------------- #


def test_internal_leak_call_graph_leak_paths_unchanged_by_migration() -> None:
    from abicheck.internal_leak import compute_call_graph_leak_paths

    graph = SourceGraphSummary(
        nodes=[
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://int", "ns::detail::helper", "source"),
        ],
        edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
    )
    pre, post = _pre_and_post_phase3_snaps(graph)

    pre_result = compute_call_graph_leak_paths(pre)
    post_result = compute_call_graph_leak_paths(post)

    assert pre_result == post_result
    assert "ns::detail::helper" in pre_result
    assert "pubFn" in pre_result["ns::detail::helper"][0]


# --------------------------------------------------------------------------- #
# buildsource/cross_source_checks.py: _check_public_to_internal_dependency
# --------------------------------------------------------------------------- #


def test_public_to_internal_dependency_unchanged_by_migration() -> None:
    from abicheck.buildsource.cross_source_checks import (
        CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
        run_crosschecks,
    )
    from abicheck.checker_policy import ChangeKind

    graph = SourceGraphSummary(
        nodes=[
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://int", "internalImpl", "source"),
        ],
        edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
    )
    pre, post = _pre_and_post_phase3_snaps(graph)
    pre.from_headers = True
    post.from_headers = True

    pre_res = run_crosschecks(pre)
    post_res = run_crosschecks(post)

    def _hits(res):
        return [
            (c.symbol, c.new_value)
            for c in res.findings
            if c.kind == ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY
        ]

    assert _hits(pre_res) == _hits(post_res) == [("pubFn", "internalImpl")]
    assert (
        pre_res.providers[CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY]
        == post_res.providers[CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY]
    )


# --------------------------------------------------------------------------- #
# buildsource/cross_source_checks.py: _check_private_header_leak
# --------------------------------------------------------------------------- #


def test_private_header_leak_source_index_provider_unchanged_by_migration() -> None:
    from abicheck.buildsource.cross_source_checks import (
        CHECK_PRIVATE_HEADER_LEAK,
        PROVIDER_SOURCE_INDEX,
        run_crosschecks,
    )
    from abicheck.model import RecordType

    graph = SourceGraphSummary(
        nodes=[
            GraphNode(id="decl://use", kind="source_decl", label="use"),
            GraphNode(id="type://Impl", kind="record_type", label="Impl"),
        ]
    )
    pre, post = _pre_and_post_phase3_snaps(graph)
    for snap in (pre, post):
        snap.from_headers = True
        snap.functions = [
            Function(
                name="use",
                mangled="_Z3usev",
                return_type="Impl *",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ]
        snap.types = [
            RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER),
        ]

    pre_res = run_crosschecks(pre)
    post_res = run_crosschecks(post)

    assert (
        pre_res.providers[CHECK_PRIVATE_HEADER_LEAK]
        == post_res.providers[CHECK_PRIVATE_HEADER_LEAK]
    )
    assert PROVIDER_SOURCE_INDEX in pre_res.providers[CHECK_PRIVATE_HEADER_LEAK]


# --------------------------------------------------------------------------- #
# buildsource/evidence_report.py: diff_embedded_build_source's L5 graph diff
# --------------------------------------------------------------------------- #


def test_side_source_graph_unchanged_by_migration() -> None:
    """:func:`_side_source_graph` (the helper ``diff_embedded_build_source``'s
    L5 diff now goes through) resolves the identical graph whether it comes
    via the fallback (pre-Phase-3 shape) or the canonical field (post-Phase-3
    shape) -- and, per the module's own out-of-band-pack contract, never
    substitutes ``surface_graph`` for an explicit ``--old/new-sources`` pack
    unrelated to the snapshot."""
    from abicheck.buildsource.evidence_report import _side_source_graph

    graph = SourceGraphSummary(nodes=[_decl("decl://a", "a", "public_header")])
    pre, post = _pre_and_post_phase3_snaps(graph)

    assert _side_source_graph(pre, pre.build_source) is graph
    assert _side_source_graph(post, post.build_source) is graph

    unrelated_graph = SourceGraphSummary(
        nodes=[_decl("decl://b", "b", "public_header")]
    )
    out_of_band_pack = BuildSourcePack(root="", source_graph=unrelated_graph)
    assert _side_source_graph(post, out_of_band_pack) is unrelated_graph


def test_evidence_report_graph_diff_unchanged_by_migration() -> None:
    from abicheck.buildsource.evidence_report import diff_embedded_build_source

    old_graph = SourceGraphSummary(
        nodes=[
            GraphNode(id="binary_symbol://_Z1av", kind="binary_symbol", label="_Z1av"),
            GraphNode(
                id="decl://a",
                kind="source_decl",
                label="a",
                attrs={"visibility": "public_header"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://a",
                dst="binary_symbol://_Z1av",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
        ],
    )
    new_graph = SourceGraphSummary(
        nodes=[
            GraphNode(id="binary_symbol://_Z1av", kind="binary_symbol", label="_Z1av"),
            GraphNode(
                id="decl://a",
                kind="source_decl",
                label="a",
                attrs={"visibility": "public_header"},
            ),
        ],
        edges=[],
    )

    def _snap(graph: SourceGraphSummary, with_surface_graph: bool) -> AbiSnapshot:
        kw = dict(
            library="libfoo.so",
            version="1.0",
            build_source=BuildSourcePack(root="", source_graph=graph),
        )
        if with_surface_graph:
            kw["surface_graph"] = graph
        return AbiSnapshot(**kw)

    def _run(with_surface_graph: bool):
        old_snap = _snap(old_graph, with_surface_graph)
        new_snap = _snap(new_graph, with_surface_graph)
        changes, _coverage_rows, _metrics = diff_embedded_build_source(
            None,
            None,
            None,
            None,
            "source",
            new_snap,
            old_snapshot=old_snap,
        )
        return changes

    pre_changes = _run(with_surface_graph=False)
    post_changes = _run(with_surface_graph=True)

    assert [(c.kind, c.symbol) for c in pre_changes] == [
        (c.kind, c.symbol) for c in post_changes
    ]
    assert pre_changes, "dropping the mapping edge must produce an L5 graph finding"


# --------------------------------------------------------------------------- #
# evidence_depth.depth_label_for
# --------------------------------------------------------------------------- #


def test_depth_label_for_unchanged_by_migration() -> None:
    from abicheck.evidence_depth import depth_label_for

    graph = SourceGraphSummary(nodes=[_decl("decl://a", "a", "public_header")])
    pre, post = _pre_and_post_phase3_snaps(graph)

    assert depth_label_for(pre, pre.build_source) == "source"
    assert depth_label_for(post, post.build_source) == "source"


def test_depth_label_for_out_of_band_pack_ignores_unrelated_surface_graph() -> None:
    """An explicit out-of-band pack (never attached to *snap*) must not have
    its emptiness judged by an unrelated ``snap.surface_graph`` -- the
    ``evidence_depth`` module's own long-standing "never default *pack* to
    ``snap.build_source``" contract (docstring at the top of the module),
    unaffected by this migration."""
    from abicheck.evidence_depth import depth_label_for

    graph = SourceGraphSummary(nodes=[_decl("decl://a", "a", "public_header")])
    unrelated_pack = BuildSourcePack(root="", source_graph=None)
    snap = AbiSnapshot(library="libfoo.so", version="1.0", surface_graph=graph)

    # snap.surface_graph is populated, but *pack* is an unrelated, empty
    # out-of-band pack -- the answer must come from *pack*, not *snap*.
    assert depth_label_for(snap, unrelated_pack) != "source"


# --------------------------------------------------------------------------- #
# cli_graph._load_source_graph
# --------------------------------------------------------------------------- #


def test_load_source_graph_from_embedded_snapshot_unchanged_by_migration(
    tmp_path,
) -> None:
    """A pre-Phase-3 **flat** document (``surface_graph`` absent, no
    ``sections`` envelope, ``build_source.source_graph`` present) yields the
    identical graph as a real, current **sectioned** document written by
    ``serialization.snapshot_to_json`` for the same evidence (``surface_graph``
    populated) -- proving ``_load_source_graph`` handles both wire shapes,
    including the sectioned one this module's own reader didn't originally
    account for (``sections.graph.payload.surface_graph``, schema v42+)."""
    from abicheck import serialization
    from abicheck.cli_graph import _load_source_graph

    graph = SourceGraphSummary(nodes=[_decl("decl://a", "a", "public_header")])

    pre_path = tmp_path / "pre_phase3.abi.json"
    pre_path.write_text(
        json.dumps(
            {
                "schema_version": 20,
                "library": "libfoo.so",
                "version": "1.0",
                "build_source": {"source_graph": graph.to_dict()},
            }
        )
    )

    post_path = tmp_path / "post_phase3.abi.json"
    pre, post = _pre_and_post_phase3_snaps(graph)
    post_path.write_text(serialization.snapshot_to_json(post))

    pre_graph = _load_source_graph(pre_path)
    post_graph = _load_source_graph(post_path)

    assert pre_graph.to_dict() == post_graph.to_dict()
    assert [n.id for n in pre_graph.nodes] == ["decl://a"]


def test_load_source_graph_prefers_surface_graph_over_build_source() -> None:
    """When both are present, the top-level ``surface_graph`` key wins --
    the canonical location, per ADR-063 Phase 3/10."""
    from abicheck.cli_graph import _embedded_source_graph

    surface = SourceGraphSummary(
        nodes=[_decl("decl://surface", "surface", "public_header")]
    )
    legacy = SourceGraphSummary(
        nodes=[_decl("decl://legacy", "legacy", "public_header")]
    )
    data = {
        "schema_version": 45,
        "library": "libfoo.so",
        "version": "1.0",
        "surface_graph": surface.to_dict(),
        "build_source": {"source_graph": legacy.to_dict()},
    }

    result = _embedded_source_graph(data)
    assert result is not None
    assert [n.id for n in result.nodes] == ["decl://surface"]


def test_embedded_source_graph_ignores_non_snapshot_documents() -> None:
    """A bare graph JSON or an unrelated JSON object (no ``schema_version``,
    no ``sections``) is not a snapshot document at all -- returns ``None``
    so the caller falls through to its own bare-graph-JSON contract."""
    from abicheck.cli_graph import _embedded_source_graph

    bare_graph = {"nodes": [{"id": "decl://a", "kind": "source_decl"}], "edges": []}
    assert _embedded_source_graph(bare_graph) is None

    pack_manifest = {"build_source_pack_version": 1, "coverage": []}
    assert _embedded_source_graph(pack_manifest) is None
