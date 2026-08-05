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

"""Tests for the consumer graph and the consumer/source join (G29 Phase 4,
ADR-057)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.appcompat import AppRequirements, scope_diff_to_app
from abicheck.buildsource.graph_impact import (
    _TIER_CONSUMER_PROVEN,
    _TIER_EXACT,
    _TIER_OVERAPPROX,
    _consumer_required_nodes,
    _graph_path_tier,
    select_preferred_graph_path,
    structured_proof_path,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import (
    EDGE_KINDS,
    NODE_KINDS,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    _symbol_node_id,
)
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.impact.consumer_graph import (
    CONSUMER_EDGE_KINDS,
    CONSUMER_NODE_KINDS,
    CONSUMER_PROVENANCE,
    build_consumer_graph,
    consumer_node_id,
    consumer_required_symbol_nodes,
    explain_required_symbol,
    explain_required_symbols,
    join_consumer_graph,
    symbol_node_id,
)
from abicheck.model import AbiSnapshot

_DISPATCHER = "_ZN6detail21train_ops_dispatcherEv"


def _reqs(*symbols: str, versions: dict[str, str] | None = None) -> AppRequirements:
    return AppRequirements(
        needed_libs=["libtrain.so.1"],
        undefined_symbols=set(symbols),
        required_versions=dict(versions or {}),
    )


def _library_graph(
    *, entry_consumer_compiled: bool = True, virtual_call: bool = False
) -> SourceGraphSummary:
    """A minimal library graph: public inline ``train()`` calls internal
    ``detail::train_ops_dispatcher()``, which maps to the exported symbol the
    consumer requires.

    The two knobs are set at construction, not patched afterwards:
    ``SourceGraphSummary.add_node``/``add_edge`` re-derive ``attrs`` from the
    entity's ``facts`` (ADR-046 D2), so an attr assigned after registration is
    discarded the next time the entity is registered anywhere.
    """
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            # An inline/template body is compiled into the consumer, so
            # is_consumer_compiled_public_entry accepts it as a walk entry;
            # False models an ordinary out-of-line exported function.
            attrs={
                "visibility": "public_header",
                "consumer_compiled_body": entry_consumer_compiled,
            },
        )
    )
    g.add_node(
        GraphNode(
            id="decl://dispatcher",
            kind="source_decl",
            label="detail::train_ops_dispatcher",
            attrs={"visibility": "source"},
        )
    )
    g.add_node(
        GraphNode(id=_symbol_node_id(_DISPATCHER), kind="binary_symbol", label=_DISPATCHER)
    )
    g.add_node(GraphNode(id=_symbol_node_id("_Z5trainv"), kind="binary_symbol", label="_Z5trainv"))
    g.add_edge(
        GraphEdge(
            src="decl://train",
            dst="decl://dispatcher",
            kind="DECL_CALLS_DECL",
            confidence="high",
            attrs={"call_kind": "virtual"} if virtual_call else {},
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://dispatcher",
            dst=_symbol_node_id(_DISPATCHER),
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            confidence="high",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://train",
            dst=_symbol_node_id("_Z5trainv"),
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            confidence="high",
        )
    )
    return g


# ── schema registration ───────────────────────────────────────────────────


def test_consumer_kinds_are_registered_in_the_graph_schema() -> None:
    """An unregistered kind is silently preserved on load but invisible to
    every schema-driven consumer — register or the join is undiscoverable."""
    assert CONSUMER_NODE_KINDS <= NODE_KINDS
    assert CONSUMER_EDGE_KINDS <= EDGE_KINDS


def test_symbol_node_id_matches_source_graph() -> None:
    """The join is *only* the shared node id — pin this module's copy of the
    format to ``source_graph``'s own so a rename cannot silently produce two
    disconnected graphs."""
    assert symbol_node_id(_DISPATCHER) == _symbol_node_id(_DISPATCHER)


# ── build_consumer_graph ──────────────────────────────────────────────────


def test_build_consumer_graph_emits_requirement_edges() -> None:
    g = build_consumer_graph("training-service", _reqs(_DISPATCHER, "_Z5trainv"))
    consumer = consumer_node_id("training-service")
    assert g.has_node(consumer)
    reqs = {e.dst for e in g.edges if e.kind == "CONSUMER_REQUIRES_SYMBOL"}
    assert reqs == {symbol_node_id(_DISPATCHER), symbol_node_id("_Z5trainv")}
    assert all(e.src == consumer for e in g.edges if e.kind == "CONSUMER_REQUIRES_SYMBOL")
    assert {n.provenance for n in g.nodes} == {CONSUMER_PROVENANCE}


def test_build_consumer_graph_scopes_to_the_given_symbol_set() -> None:
    """An ELF consumer's raw undefined-symbol table spans every DT_NEEDED
    library; recording all of it would attribute another library's
    requirements to this one."""
    g = build_consumer_graph(
        "training-service",
        _reqs(_DISPATCHER, "_ZN5other4funcEv"),
        symbols=frozenset({_DISPATCHER}),
    )
    reqs = {e.dst for e in g.edges if e.kind == "CONSUMER_REQUIRES_SYMBOL"}
    assert reqs == {symbol_node_id(_DISPATCHER)}


def test_build_consumer_graph_records_version_requirements() -> None:
    g = build_consumer_graph(
        "training-service",
        _reqs(_DISPATCHER, versions={"TRAIN_1.0": "libtrain.so.1"}),
    )
    version_edges = [e for e in g.edges if e.kind == "CONSUMER_REQUIRES_VERSION"]
    assert len(version_edges) == 1
    assert version_edges[0].dst == "libtrain.so.1"
    assert version_edges[0].attrs["version"] == "TRAIN_1.0"
    assert g._node_by_id["libtrain.so.1"].kind == "external_dependency"


def test_build_consumer_graph_is_empty_of_requirements_without_symbols() -> None:
    g = build_consumer_graph("training-service", _reqs())
    assert [e for e in g.edges if e.kind == "CONSUMER_REQUIRES_SYMBOL"] == []
    assert g.has_node(consumer_node_id("training-service"))


# ── join_consumer_graph ───────────────────────────────────────────────────


def test_join_folds_onto_one_shared_symbol_node() -> None:
    library = _library_graph()
    before = len(library.nodes)
    consumer = build_consumer_graph("training-service", _reqs(_DISPATCHER))
    joined = join_consumer_graph(library, consumer)
    # The consumer contributes exactly one *new* node (itself): its required
    # symbol folds onto the library's existing binary_symbol node.
    assert len(joined.nodes) == before + 1
    sym = next(n for n in joined.nodes if n.id == symbol_node_id(_DISPATCHER))
    assert {f.producer for f in sym.facts} >= {CONSUMER_PROVENANCE}


def test_join_does_not_mutate_the_library_graph() -> None:
    """The library graph is read off a shared snapshot — folding one
    ``--used-by`` consumer's requirements into it would leak them into every
    other analysis of the same run."""
    library = _library_graph()
    nodes, edges = len(library.nodes), len(library.edges)
    lib_symbol = next(n for n in library.nodes if n.id == symbol_node_id(_DISPATCHER))
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    assert (len(library.nodes), len(library.edges)) == (nodes, edges)
    assert consumer_required_symbol_nodes(library) == frozenset()
    # The membership counts above are not enough: add_node's ADR-046 D2 merge
    # mutates the *stored* node in place, so a shallow re-registration would
    # leave the library's own symbol node carrying the consumer's fact while
    # every count stayed identical.
    assert CONSUMER_PROVENANCE not in {f.producer for f in lib_symbol.facts}
    joined_symbol = next(
        n for n in joined.nodes if n.id == symbol_node_id(_DISPATCHER)
    )
    assert joined_symbol is not lib_symbol


def test_join_carries_over_coverage_honesty_flags() -> None:
    library = _library_graph()
    library.extractor_passes = {"call_graph": True}
    library.narrowed_passes = {"type_graph": True}
    library.degraded_passes = {"include_graph": True}
    library.coverage = {"source_decls": 2}
    joined = join_consumer_graph(library, build_consumer_graph("app", _reqs()))
    assert joined.extractor_passes == {"call_graph": True}
    assert joined.narrowed_passes == {"type_graph": True}
    assert joined.degraded_passes == {"include_graph": True}
    assert joined.coverage == {"source_decls": 2}


def test_consumer_required_symbol_nodes_reads_the_join() -> None:
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    assert consumer_required_symbol_nodes(joined) == {symbol_node_id(_DISPATCHER)}


# ── explain_required_symbol(s) ────────────────────────────────────────────


def test_explain_names_the_public_entry_that_produced_the_dependency() -> None:
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("training-service", _reqs(_DISPATCHER))
    )
    explained = explain_required_symbol(
        joined, _DISPATCHER, consumer="training-service"
    )
    assert explained is not None
    assert explained.consumer == "training-service"
    assert explained.public_entries == ("train",)
    assert explained.declarations == ("detail::train_ops_dispatcher",)
    assert explained.is_direct() is False
    # The chain closes on the symbol node, so it renders as one contiguous
    # node/edge/node walk ending at what the consumer actually requires.
    assert [e.kind for e in explained.entry_path] == [
        "DECL_CALLS_DECL",
        "SOURCE_DECL_MAPS_TO_SYMBOL",
    ]
    steps = structured_proof_path(joined, explained.entry_path)
    assert steps[0]["id"] == "decl://train"
    assert steps[-1]["id"] == symbol_node_id(_DISPATCHER)


def test_explain_reports_a_direct_public_entry_requirement() -> None:
    """A required symbol whose own declaration is the public entry is the
    strongest answer, not a missing one — reported with no path."""
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs("_Z5trainv"))
    )
    explained = explain_required_symbol(joined, "_Z5trainv", consumer="app")
    assert explained is not None
    assert explained.is_direct() is True
    assert explained.public_entries == ("train",)
    assert explained.entry_path == []


def test_explain_returns_none_without_a_declaration_mapping() -> None:
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs("_Z7unknownv"))
    )
    assert explain_required_symbol(joined, "_Z7unknownv", consumer="app") is None


def test_explain_returns_none_without_a_consumer_compiled_public_entry() -> None:
    """An ordinary out-of-line exported function's body is compiled into the
    library, never into a consumer — walking from it would attribute an
    internal implementation-detail call to code that cannot see it."""
    library = _library_graph(entry_consumer_compiled=False)
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    assert explain_required_symbol(joined, _DISPATCHER, consumer="app") is None


def test_explain_returns_none_on_a_graph_with_no_edges_at_all() -> None:
    assert explain_required_symbol(SourceGraphSummary(), _DISPATCHER) is None


def test_explain_returns_nothing_when_no_entry_is_consumer_compiled() -> None:
    """The whole-graph degenerate case: every declaration the library exports
    has a library-only body, so there is no walk to start."""
    library = _library_graph(entry_consumer_compiled=False)
    for node_id in ("decl://train", "decl://dispatcher"):
        library._node_by_id[node_id].facts[0].attrs["consumer_compiled_body"] = False
        library.add_node(library._node_by_id[node_id])  # re-resolve from facts
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER, "_Z5trainv"))
    )
    assert explain_required_symbols(joined, [_DISPATCHER, "_Z5trainv"]) == {}


def test_batch_reuses_one_walk_across_several_walked_symbols() -> None:
    """The BFS is computed once and shared: two symbols that both need it get
    the same answers as if each had been asked alone."""
    library = _library_graph()
    library.add_node(
        GraphNode(
            id="decl://dispatcher2",
            kind="source_decl",
            label="detail::eval_ops_dispatcher",
            attrs={"visibility": "source"},
        )
    )
    library.add_edge(
        GraphEdge(
            src="decl://train",
            dst="decl://dispatcher2",
            kind="DECL_CALLS_DECL",
            confidence="high",
        )
    )
    second = "_ZN6detail20eval_ops_dispatcherEv"
    library.add_edge(
        GraphEdge(
            src="decl://dispatcher2",
            dst=_symbol_node_id(second),
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            confidence="high",
        )
    )
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER, second))
    )
    batch = explain_required_symbols(joined, [_DISPATCHER, second], consumer="app")
    assert set(batch) == {_DISPATCHER, second}
    assert all(e.public_entries == ("train",) for e in batch.values())
    assert batch[second].declarations == ("detail::eval_ops_dispatcher",)


def test_explain_returns_nothing_when_no_entry_reaches_the_declaration() -> None:
    """Entries exist and the symbol has a declaration, but nothing public
    reaches it — an honest "no explanation", not a fabricated one."""
    library = _library_graph()
    library.edges[:] = [
        e for e in library.edges if e.kind != "DECL_CALLS_DECL"
    ]
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    assert explain_required_symbols(joined, [_DISPATCHER]) == {}


def test_explain_required_symbols_is_empty_for_an_empty_symbol_list() -> None:
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    assert explain_required_symbols(joined, []) == {}


def test_explain_tolerates_a_mapping_edge_with_no_declaration_node() -> None:
    """A hand-built or partially-collected graph can carry a
    SOURCE_DECL_MAPS_TO_SYMBOL edge whose source declaration has no node —
    that must degrade to "not a direct requirement", not raise."""
    library = _library_graph()
    library.add_edge(
        GraphEdge(
            src="decl://never-declared",
            dst=_symbol_node_id(_DISPATCHER),
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            confidence="high",
        )
    )
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    explained = explain_required_symbol(joined, _DISPATCHER, consumer="app")
    assert explained is not None
    assert explained.public_entries == ("train",)


def test_explain_required_symbols_batches_without_changing_answers() -> None:
    joined = join_consumer_graph(
        _library_graph(),
        build_consumer_graph("app", _reqs(_DISPATCHER, "_Z5trainv", "_Z7unknownv")),
    )
    batch = explain_required_symbols(
        joined, [_DISPATCHER, "_Z5trainv", "_Z7unknownv"], consumer="app"
    )
    # The unexplainable symbol is absent rather than present-and-empty.
    assert set(batch) == {_DISPATCHER, "_Z5trainv"}
    for sym, one in batch.items():
        alone = explain_required_symbol(joined, sym, consumer="app")
        assert alone is not None
        assert (alone.public_entries, alone.declarations) == (
            one.public_entries,
            one.declarations,
        )


def test_explain_prefers_one_entry_and_keeps_the_rest_as_alternatives() -> None:
    library = _library_graph()
    library.add_node(
        GraphNode(
            id="decl://train2",
            kind="source_decl",
            label="train_batch",
            attrs={"visibility": "public_header", "consumer_compiled_body": True},
        )
    )
    library.add_node(
        GraphNode(id="decl://mid", kind="source_decl", label="mid", attrs={"visibility": "source"})
    )
    library.add_edge(
        GraphEdge(
            src="decl://train2", dst="decl://mid", kind="DECL_CALLS_DECL", confidence="high"
        )
    )
    library.add_edge(
        GraphEdge(
            src="decl://mid",
            dst="decl://dispatcher",
            kind="DECL_CALLS_DECL",
            confidence="high",
        )
    )
    library.add_edge(
        GraphEdge(
            src="decl://train2",
            dst=_symbol_node_id("_Z11train_batchv"),
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            confidence="high",
        )
    )
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    explained = explain_required_symbol(joined, _DISPATCHER, consumer="app")
    assert explained is not None
    # Both public entries reach the dispatcher; the shorter chain wins the
    # within-tier tie-break and the other is kept, not dropped.
    assert set(explained.public_entries) == {"train", "train_batch"}
    assert explained.public_entries[0] == "train"
    assert len(explained.alternative_entry_paths) == 1


# ── ADR-046 D6 tier 1: consumer-proven ────────────────────────────────────


def test_consumer_required_nodes_is_empty_without_a_consumer_graph() -> None:
    """Every pre-ADR-057 graph, and every run without --used-by: the tier must
    be inert, not merely unlikely."""
    assert _consumer_required_nodes(_library_graph()) == frozenset()


def test_a_path_reaching_a_consumer_required_symbol_is_tier_one() -> None:
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    node_by_id = {n.id: n for n in joined.nodes}
    path = [
        e
        for e in joined.edges
        if e.src == "decl://train" and e.dst == "decl://dispatcher"
    ] + [
        e
        for e in joined.edges
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
        and e.dst == symbol_node_id(_DISPATCHER)
    ]
    required = _consumer_required_nodes(joined)
    assert _graph_path_tier(node_by_id, path, required) == _TIER_CONSUMER_PROVEN
    # Without the consumer evidence the identical path is only "exact".
    assert _graph_path_tier(node_by_id, path, frozenset()) == _TIER_EXACT


def test_an_overapprox_path_is_never_consumer_proven() -> None:
    """A virtual/function-pointer hop makes the *chain* an over-approximation;
    that some consumer requires its endpoint does not make the chain proven."""
    library = _library_graph(virtual_call=True)
    joined = join_consumer_graph(
        library, build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    node_by_id = {n.id: n for n in joined.nodes}
    path = [
        e
        for e in joined.edges
        if e.src == "decl://train" and e.dst == "decl://dispatcher"
    ]
    tier = _graph_path_tier(node_by_id, path, _consumer_required_nodes(joined))
    assert tier == _TIER_OVERAPPROX


def test_select_preferred_graph_path_prefers_the_consumer_proven_candidate() -> None:
    """Tier beats length: a longer consumer-proven chain outranks a shorter
    merely-exact one."""
    joined = join_consumer_graph(
        _library_graph(), build_consumer_graph("app", _reqs(_DISPATCHER))
    )
    joined.add_node(
        GraphNode(id="decl://other", kind="source_decl", label="other", attrs={"visibility": "source"})
    )
    joined.add_edge(
        GraphEdge(
            src="decl://train", dst="decl://other", kind="DECL_CALLS_DECL", confidence="high"
        )
    )
    call = next(
        e for e in joined.edges if e.src == "decl://train" and e.dst == "decl://dispatcher"
    )
    mapping = next(
        e
        for e in joined.edges
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
        and e.dst == symbol_node_id(_DISPATCHER)
    )
    short_but_unproven = [
        next(e for e in joined.edges if e.dst == "decl://other")
    ]
    long_but_proven = [call, mapping]
    picked = select_preferred_graph_path(joined, [short_but_unproven, long_but_proven])
    assert picked is long_but_proven


# ── end-to-end through scope_diff_to_app ──────────────────────────────────


class TestScopeDiffToAppConsumerImpact:
    """The wiring: ``compare --used-by``'s ``CONSUMER_REQUIRED_SYMBOL_REMOVED``
    overlay picks up the join's answer, and is byte-for-byte unchanged when
    there is no graph to join against."""

    def _snapshots(
        self, *, with_graph: bool
    ) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = AbiSnapshot(
            library="libtrain.so.1",
            version="1.0",
            elf=ElfMetadata(
                soname="libtrain.so.1",
                symbols=[ElfSymbol(name="_Z5trainv"), ElfSymbol(name=_DISPATCHER)],
            ),
        )
        if with_graph:
            old.build_source = BuildSourcePack(
                root=Path(""), source_graph=_library_graph()
            )
        new = AbiSnapshot(
            library="libtrain.so.1",
            version="2.0",
            elf=ElfMetadata(
                soname="libtrain.so.1", symbols=[ElfSymbol(name="_Z5trainv")]
            ),
        )
        return old, new

    def _run(
        self, tmp_path: Path, *, with_graph: bool, old_as_path: bool = False
    ):
        old, new = self._snapshots(with_graph=with_graph)
        diff = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtrain.so.1",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        reqs = AppRequirements(undefined_symbols={"_Z5trainv", _DISPATCHER})
        # The shape every real caller produces for a real binary OLD: the
        # positional operand is the *path*, and the snapshot carrying the
        # graph arrives separately (Codex review on PR #672).
        old_operand: Path | AbiSnapshot = old
        old_snapshot: AbiSnapshot | None = None
        if old_as_path:
            old_operand = tmp_path / "libtrain.so.1"
            old_operand.write_bytes(b"\x7fELF" + b"\x00" * 100)
            old_snapshot = old
        with patch(
            "abicheck.appcompat.parse_app_requirements", return_value=reqs
        ), patch("abicheck.appcompat._detect_app_format", return_value="elf"), patch(
            "abicheck.appcompat._lib_elf_meta",
            side_effect=lambda lib: old.elf if lib is old_operand else new.elf,
        ):
            result = scope_diff_to_app(
                diff,
                tmp_path / "training-service",
                old_operand,
                new,
                old_snapshot=old_snapshot,
            )
        (overlay,) = [
            c
            for c in result.breaking_for_app
            if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]
        return result, overlay

    def _overlay(
        self, tmp_path: Path, *, with_graph: bool, old_as_path: bool = False
    ) -> Change:
        return self._run(tmp_path, with_graph=with_graph, old_as_path=old_as_path)[1]

    def test_overlay_names_the_public_entry_behind_the_requirement(
        self, tmp_path: Path
    ) -> None:
        overlay = self._overlay(tmp_path, with_graph=True)
        assert overlay.symbol == _DISPATCHER
        assert overlay.affected_public_roots == ["train"]
        assert overlay.impact_is_direct is False
        steps = overlay.impact_proof_path
        assert steps is not None
        assert steps[0]["id"] == "decl://train"
        assert steps[-1]["id"] == symbol_node_id(_DISPATCHER)
        assert "training-service requires" in overlay.reachability_proof_path
        assert "train" in overlay.reachability_proof_path

    def test_cached_impact_assessment_carries_the_proof_path(
        self, tmp_path: Path
    ) -> None:
        """The enrichment must land *before* the overlay's ImpactAssessment is
        cached — attached afterwards it would never reach any report."""
        overlay = self._overlay(tmp_path, with_graph=True)
        assessment = overlay.impact_assessment
        assert assessment is not None
        assert assessment.proof_path is not None
        assert assessment.proof_path.root == "train"
        assert assessment.proof_path.target == _DISPATCHER
        assert [s.kind for s in assessment.proof_path.steps if s.step_type == "edge"] == [
            "DECL_CALLS_DECL",
            "SOURCE_DECL_MAPS_TO_SYMBOL",
        ]

    def test_verdict_and_finding_set_are_unchanged_by_the_join(
        self, tmp_path: Path
    ) -> None:
        """Enrichment only: the join may add evidence to a finding, never a
        finding, a verdict, or a severity."""
        joined_result, joined = self._run(tmp_path, with_graph=True)
        plain_result, plain = self._run(tmp_path, with_graph=False)
        assert joined_result.verdict == plain_result.verdict
        assert joined_result.missing_symbols == plain_result.missing_symbols
        assert [c.kind for c in joined_result.breaking_for_app] == [
            c.kind for c in plain_result.breaking_for_app
        ]
        assert (joined.kind, joined.symbol, joined.description) == (
            plain.kind,
            plain.symbol,
            plain.description,
        )
        assert joined.effective_verdict == plain.effective_verdict
        assert joined.reachability_state == plain.reachability_state

    def test_without_a_library_graph_the_overlay_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """Every pre-ADR-057 invocation: no graph, no enrichment, no change."""
        overlay = self._overlay(tmp_path, with_graph=False)
        assert overlay.affected_public_roots is None
        assert overlay.impact_proof_path is None
        assert overlay.reachability_proof_path is None

    def test_a_real_binary_old_still_gets_the_join_via_old_snapshot(
        self, tmp_path: Path
    ) -> None:
        """The regression Codex caught: every caller passes the *path* when
        OLD is a real binary (``cli_compare_helpers``/``mcp_server``'s
        ``old_input if detect_binary_format(...) else old_snapshot``,
        ``check_appcompat``'s own dump), so reading the graph off that operand
        alone made the join fire only for a saved-JSON OLD — the inverse of
        the primary usage, skipping exactly the ``--old-sources`` runs that
        asked for the richest evidence."""
        overlay = self._overlay(tmp_path, with_graph=True, old_as_path=True)
        assert overlay.affected_public_roots == ["train"]
        assert overlay.impact_proof_path is not None
        assert overlay.impact_assessment is not None
        assert overlay.impact_assessment.proof_path is not None
        assert overlay.impact_assessment.proof_path.root == "train"

    def test_old_snapshot_is_graph_lookup_only(self, tmp_path: Path) -> None:
        """It must not change which symbols read as missing — the path
        operand still owns every export/version read."""
        via_path, _ = self._run(tmp_path, with_graph=True, old_as_path=True)
        via_snapshot, _ = self._run(tmp_path, with_graph=True)
        assert via_path.missing_symbols == via_snapshot.missing_symbols
        assert via_path.verdict == via_snapshot.verdict
        assert via_path.required_symbols == via_snapshot.required_symbols


class TestCoveredChangeEnrichment:
    """ADR-057 D8: the ordinary flow, where the diff already has its own
    `FUNC_REMOVED` for the missing symbol so no overlay is created."""

    def _run(self, tmp_path: Path, *, prior_evidence: bool = False):
        old = AbiSnapshot(
            library="libtrain.so.1",
            version="1.0",
            elf=ElfMetadata(
                soname="libtrain.so.1",
                symbols=[ElfSymbol(name="_Z5trainv"), ElfSymbol(name=_DISPATCHER)],
            ),
        )
        old.build_source = BuildSourcePack(root=Path(""), source_graph=_library_graph())
        new = AbiSnapshot(
            library="libtrain.so.1",
            version="2.0",
            elf=ElfMetadata(
                soname="libtrain.so.1", symbols=[ElfSymbol(name="_Z5trainv")]
            ),
        )
        removed = Change(ChangeKind.FUNC_REMOVED, _DISPATCHER, "removed")
        if prior_evidence:
            removed.reachability_proof_path = "already computed by another producer"
        diff = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtrain.so.1",
            changes=[removed],
            verdict=Verdict.BREAKING,
        )
        reqs = AppRequirements(undefined_symbols={"_Z5trainv", _DISPATCHER})
        with patch(
            "abicheck.appcompat.parse_app_requirements", return_value=reqs
        ), patch("abicheck.appcompat._detect_app_format", return_value="elf"):
            result = scope_diff_to_app(
                diff, tmp_path / "training-service", old, new
            )
        return result, removed

    def test_the_existing_removal_finding_gets_the_proof_path(
        self, tmp_path: Path
    ) -> None:
        """Codex's finding: `uncovered_missing_symbols` drops a symbol the
        diff already covers, so before this the common case got nothing."""
        result, removed = self._run(tmp_path)
        # No overlay — the FUNC_REMOVED already covers the symbol.
        assert not [
            c
            for c in result.breaking_for_app
            if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]
        assert removed.affected_public_roots == ["train"]
        assert removed.impact_proof_path is not None
        assert removed.impact_proof_path[-1]["id"] == symbol_node_id(_DISPATCHER)

    def test_the_wording_on_a_shared_finding_names_no_consumer(
        self, tmp_path: Path
    ) -> None:
        """The `Change` is shared with `DiffResult.changes`, which the
        *unscoped* report also renders — "X is reachable from public entry
        train" is a library fact; "training-service requires X" is not."""
        _result, removed = self._run(tmp_path)
        assert removed.reachability_proof_path is not None
        assert "training-service" not in removed.reachability_proof_path
        assert "is reachable from public entry train" in removed.reachability_proof_path

    def test_a_finding_with_its_own_evidence_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """`attach_impact_metadata` assigns its whole field set including
        `None`, so enriching a producer-populated finding would erase what
        that producer computed — and this is a shared object."""
        _result, removed = self._run(tmp_path, prior_evidence=True)
        assert removed.reachability_proof_path == "already computed by another producer"
        assert removed.impact_proof_path is None
        assert removed.affected_public_roots is None

    def test_enrichment_changes_no_verdict_or_finding_set(
        self, tmp_path: Path
    ) -> None:
        result, removed = self._run(tmp_path)
        assert result.verdict == Verdict.BREAKING
        assert [c.kind for c in result.breaking_for_app] == [ChangeKind.FUNC_REMOVED]
        assert removed.kind == ChangeKind.FUNC_REMOVED
        assert removed.effective_verdict is None
