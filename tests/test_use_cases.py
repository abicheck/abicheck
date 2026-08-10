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

"""Tests for the declared use-case manifest and graph join (G29 Phase 4
slice 2, ADR-057 amendment)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.source_graph import (
    EDGE_KINDS,
    NODE_KINDS,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.errors import UseCaseManifestError
from abicheck.impact.use_cases import (
    USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS,
    USE_CASE_PROVENANCE,
    UseCaseDefinition,
    build_use_case_graph,
    join_use_case_graph,
    load_use_case_manifest,
    parse_use_case_manifest,
    test_case_node_id as make_test_case_node_id,
    use_case_node_id,
)


def _library_graph() -> SourceGraphSummary:
    """A minimal library graph: one public entry (`train`), one exported
    symbol with no declaration (`_Z5evalv`), and one internal-only decl
    (`detail::helper`, never resolvable as an entrypoint)."""
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_node(
        GraphNode(
            id="decl://helper",
            kind="source_decl",
            label="detail::helper",
            attrs={"visibility": "source"},
        )
    )
    g.add_node(
        GraphNode(id="binary_symbol://_Z5evalv", kind="binary_symbol", label="_Z5evalv")
    )
    return g


# ── schema registration ───────────────────────────────────────────────────


def test_use_case_kinds_are_registered_in_the_graph_schema() -> None:
    assert USE_CASE_NODE_KINDS <= NODE_KINDS
    assert USE_CASE_EDGE_KINDS <= EDGE_KINDS
    assert USE_CASE_NODE_KINDS == {"use_case", "test_case"}
    assert USE_CASE_EDGE_KINDS == {
        "USE_CASE_USES_ENTRY",
        "TEST_COVERS_USE_CASE",
        "TRACE_OBSERVED_ENTRY",
        "TRACE_OBSERVED_EDGE",
    }


# ── manifest parsing ──────────────────────────────────────────────────────


def test_parse_empty_document_is_a_valid_empty_manifest() -> None:
    assert parse_use_case_manifest(None) == []
    assert parse_use_case_manifest([]) == []


def test_parse_a_valid_manifest() -> None:
    raw = [
        {
            "use_case": "training-workflow",
            "entrypoints": ["train", "_Z5evalv"],
            "tests": ["test_train_e2e"],
        },
        {"use_case": "no-entrypoints-declared"},
    ]
    defs = parse_use_case_manifest(raw)
    assert defs == [
        UseCaseDefinition(
            use_case="training-workflow",
            entrypoints=("train", "_Z5evalv"),
            tests=("test_train_e2e",),
        ),
        UseCaseDefinition(use_case="no-entrypoints-declared"),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        {"use_case": "not-a-list"},
        "also not a list",
        42,
    ],
)
def test_parse_rejects_a_non_list_top_level_document(raw: object) -> None:
    with pytest.raises(UseCaseManifestError, match="top-level document"):
        parse_use_case_manifest(raw)


def test_parse_rejects_a_non_mapping_entry() -> None:
    with pytest.raises(UseCaseManifestError, match="entry 0 must be a mapping"):
        parse_use_case_manifest(["just a string"])


@pytest.mark.parametrize(
    "raw_entry",
    [
        {"use_case": "x", "entrypoint": ["train"]},  # misspelled entrypoints
        {"use_case": "x", "test": ["t"]},  # misspelled tests
        {"use_case": "x", "extra_field": 1},
    ],
)
def test_parse_rejects_an_unknown_field(raw_entry: dict) -> None:
    """A misspelled/unknown field must be a hard error, not silently
    ignored -- mapping.get(...) treats an unknown key as absent, which
    would otherwise load successfully while quietly dropping the coverage
    the author actually declared."""
    with pytest.raises(UseCaseManifestError, match="unknown field"):
        parse_use_case_manifest([raw_entry])


def test_parse_rejects_unknown_fields_with_heterogeneous_key_types() -> None:
    """A syntactically valid entry may mix key types (e.g. an integer key
    alongside a string one) -- the unknown-field check must not raise a
    bare TypeError comparing incomparable types while sorting them for the
    error message."""
    with pytest.raises(UseCaseManifestError, match="unknown field"):
        parse_use_case_manifest([{"use_case": "x", 1: "a", "z": "b"}])


@pytest.mark.parametrize(
    "raw_entry", [{}, {"use_case": ""}, {"use_case": "   "}, {"use_case": 5}]
)
def test_parse_rejects_a_missing_or_blank_use_case_name(raw_entry: dict) -> None:
    with pytest.raises(UseCaseManifestError, match="use_case"):
        parse_use_case_manifest([raw_entry])


def test_parse_rejects_a_non_list_entrypoints_field() -> None:
    with pytest.raises(UseCaseManifestError, match="entrypoints"):
        parse_use_case_manifest([{"use_case": "x", "entrypoints": "train"}])


def test_parse_rejects_a_non_string_entrypoints_element() -> None:
    with pytest.raises(UseCaseManifestError, match="entrypoints"):
        parse_use_case_manifest([{"use_case": "x", "entrypoints": ["train", 5]}])


def test_parse_rejects_a_non_list_tests_field() -> None:
    with pytest.raises(UseCaseManifestError, match="tests"):
        parse_use_case_manifest([{"use_case": "x", "tests": "test_train"}])


def test_load_use_case_manifest_from_disk(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text(
        "- use_case: training-workflow\n"
        "  entrypoints: [train]\n"
        "  tests: [test_train_e2e]\n"
    )
    defs = load_use_case_manifest(manifest)
    assert defs == [
        UseCaseDefinition(
            use_case="training-workflow",
            entrypoints=("train",),
            tests=("test_train_e2e",),
        )
    ]


def test_load_use_case_manifest_rejects_a_malformed_file(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("not_a_list: true\n")
    with pytest.raises(UseCaseManifestError, match="top-level document"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_wraps_a_yaml_syntax_error(tmp_path: Path) -> None:
    """A syntactically invalid document (not merely a structurally wrong
    one) must still surface through the public UseCaseManifestError
    contract, not a bare yaml.YAMLError -- so every caller can catch one
    exception type for any invalid manifest."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("- use_case: [unterminated flow sequence\n")
    with pytest.raises(UseCaseManifestError, match="invalid YAML syntax"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_wraps_a_utf8_decoding_error(tmp_path: Path) -> None:
    """A manifest file that isn't valid UTF-8 must still surface through the
    public UseCaseManifestError contract, not a bare UnicodeDecodeError --
    the read used to happen outside this function's guarded block entirely
    (Codex review, fresh evidence)."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_bytes(b"- use_case: \xff\xfe not valid utf-8\n")
    with pytest.raises(UseCaseManifestError, match="not valid UTF-8"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_wraps_an_invalid_timestamp_scalar(
    tmp_path: Path,
) -> None:
    """A value PyYAML's implicit resolver recognizes as a timestamp but
    with an invalid component (no such month) makes PyYAML's own
    timestamp constructor raise a bare ValueError, not a yaml.YAMLError --
    a document shape neither the syntax nor the UTF-8 guard catches
    (Codex review, fresh evidence)."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("- use_case: 2023-99-99\n")
    with pytest.raises(UseCaseManifestError, match="invalid scalar value"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_missing_file_is_a_plain_oserror(
    tmp_path: Path,
) -> None:
    """A missing/unreadable path is an ordinary filesystem error, left
    as-is rather than wrapped -- distinct from 'the file exists but its
    contents are malformed'."""
    with pytest.raises(OSError):
        load_use_case_manifest(tmp_path / "does-not-exist.yaml")


def test_load_use_case_manifest_rejects_a_duplicate_key(tmp_path: Path) -> None:
    """A repeated mapping key (e.g. two 'entrypoints:' lines pasted into one
    entry) must be a hard error, not PyYAML's default of silently keeping
    only the last value -- the latter would drop declared coverage with no
    signal at all."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text(
        "- use_case: training-workflow\n  entrypoints: [train]\n  entrypoints: [eval]\n"
    )
    with pytest.raises(UseCaseManifestError, match="duplicate key"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_wraps_an_unhashable_key(tmp_path: Path) -> None:
    """A syntactically valid document with an unhashable mapping key (a YAML
    sequence used as a key) must still surface through the public
    UseCaseManifestError contract, not a bare TypeError -- the duplicate-key
    override must keep the hashability check PyYAML's own default
    constructor already performs."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("- {[a, b]: x}\n")
    with pytest.raises(UseCaseManifestError, match="unhashable key"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_of_an_empty_file_is_empty(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("")
    assert load_use_case_manifest(manifest) == []


# ── build_use_case_graph ──────────────────────────────────────────────────


def test_build_use_case_graph_emits_the_use_case_node() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow")], library
    )
    assert graph.has_node(use_case_node_id("training-workflow"))
    node = next(n for n in graph.nodes if n.id == use_case_node_id("training-workflow"))
    assert node.kind == "use_case"
    assert node.label == "training-workflow"
    assert node.provenance == USE_CASE_PROVENANCE


def test_build_use_case_graph_resolves_entrypoints_by_id_and_label() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow",
                entrypoints=("train", "binary_symbol://_Z5evalv"),
            )
        ],
        library,
    )
    edges = {e.dst for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"}
    assert edges == {"decl://train", "binary_symbol://_Z5evalv"}
    assert all(
        e.src == use_case_node_id("training-workflow")
        for e in graph.edges
        if e.kind == "USE_CASE_USES_ENTRY"
    )


def test_build_use_case_graph_skips_an_unresolvable_entrypoint_silently() -> None:
    """An entrypoint the library graph cannot resolve is dropped, not an
    error -- the same 'absence, never a wrong answer' discipline
    consumer_graph.py already follows."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("does_not_exist",)
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []
    # The use_case node itself is still emitted.
    assert graph.has_node(use_case_node_id("training-workflow"))


def test_build_use_case_graph_skips_an_ambiguous_label_silently() -> None:
    """Two public entries sharing one label (a common shape for C++
    overloads) must never resolve a bare-label entrypoint to an arbitrary
    one of them -- ambiguous is exactly the 'no answer' case, not a
    coin-flip pick."""
    library = _library_graph()
    library.add_node(
        GraphNode(
            id="decl://train_overload_2",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []


def test_build_use_case_graph_coalesces_a_mapped_decl_and_symbol_pair() -> None:
    """An ordinary exported C function's decl and the binary_symbol it maps
    to (via SOURCE_DECL_MAPS_TO_SYMBOL) share the exact same label -- this
    must resolve as ONE public entry, not a two-way ambiguity, or the most
    common real-world entrypoint shape (a plain C function's label matching
    its own linker symbol) would silently fail to resolve at all (Codex
    review, fresh evidence)."""
    library = _library_graph()
    library.add_node(
        GraphNode(id="binary_symbol://train", kind="binary_symbol", label="train")
    )
    library.add_edge(
        GraphEdge(
            src="decl://train",
            dst="binary_symbol://train",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    edges = {e.dst for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"}
    # Coalesced onto the mapped binary_symbol node, matching
    # consumer_graph.py's own preference for the exported representation.
    assert edges == {"binary_symbol://train"}


def test_build_use_case_graph_does_not_coalesce_a_decl_mapped_to_two_symbols() -> None:
    """A decl mapped to more than one public symbol (versioned exports,
    aliases, or facts merged from multiple graph producers) must not
    coalesce onto whichever destination happens to iterate last -- an
    order-dependent, wrong-but-confident pick instead of the 'degrade to
    no answer' this module otherwise guarantees for a genuine ambiguity
    (Codex review, fresh evidence).

    Resolved by the decl's own LABEL ("train"), not its exact id -- an
    exact-id lookup would trivially resolve to itself regardless of
    coalescing and never exercise the buggy code path at all (own review
    finding, caught before landing: the id always resolves via `index`,
    built before coalescing ever runs). The label is unique among this
    fixture's public nodes, so it is coalescing -- not label-ambiguity --
    that decides the outcome here: the buggy last-write-wins dict
    comprehension resolved it to whichever symbol was registered last
    (an ``in {binary_symbol://train@v1, binary_symbol://train@v2}``
    silent, order-dependent pick); the fix must resolve it to the decl's
    own id instead, uncoalesced."""
    library = _library_graph()
    library.add_node(
        GraphNode(id="binary_symbol://train@v1", kind="binary_symbol", label="train_v1")
    )
    library.add_node(
        GraphNode(id="binary_symbol://train@v2", kind="binary_symbol", label="train_v2")
    )
    library.add_edge(
        GraphEdge(
            src="decl://train",
            dst="binary_symbol://train@v1",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    library.add_edge(
        GraphEdge(
            src="decl://train",
            dst="binary_symbol://train@v2",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    edges = {e.dst for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"}
    assert edges == {"decl://train"}


def test_build_use_case_graph_an_exact_id_wins_over_a_colliding_label() -> None:
    """A manifest naming a node's exact id must resolve to that node even
    when some *other* public node's own label happens to collide with that
    id string -- an id lookup is never shadowed by an ambiguous or
    unrelated label registration."""
    library = _library_graph()
    library.add_node(
        GraphNode(
            id="decl://other",
            kind="source_decl",
            label="decl://train",  # collides with train's own id
            attrs={"visibility": "public_header"},
        )
    )
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("decl://train",)
            )
        ],
        library,
    )
    edges = {e.dst for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"}
    assert edges == {"decl://train"}


def test_build_use_case_graph_does_not_resolve_an_internal_declaration() -> None:
    """A non-public decl is never a valid entrypoint target, even if a
    manifest names it by label."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("detail::helper",)
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []


def test_build_use_case_graph_does_not_resolve_a_public_type() -> None:
    """A public record_type/enum_type/typedef is never a valid entrypoint
    target, even though it can share PUBLIC_VISIBILITIES with a real decl --
    a type is never something a use case 'exercises', and has no outgoing
    call-graph edge for a consumer-impact walk to follow (Codex review,
    fresh evidence)."""
    library = _library_graph()
    library.add_node(
        GraphNode(
            id="record_type://Config",
            kind="record_type",
            label="Config",
            attrs={"visibility": "public_header"},
        )
    )
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("Config",))],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []
    # The exact node id is rejected the same way -- a type is never public
    # in this module's sense, regardless of how it's spelled.
    graph2 = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("record_type://Config",)
            )
        ],
        library,
    )
    assert [e for e in graph2.edges if e.kind == "USE_CASE_USES_ENTRY"] == []


def test_build_use_case_graph_emits_test_case_nodes_and_edges() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", tests=("test_train_e2e",))],
        library,
    )
    assert graph.has_node(make_test_case_node_id("test_train_e2e"))
    (edge,) = [e for e in graph.edges if e.kind == "TEST_COVERS_USE_CASE"]
    assert edge.src == make_test_case_node_id("test_train_e2e")
    assert edge.dst == use_case_node_id("training-workflow")


def test_build_use_case_graph_emits_a_test_case_regardless_of_entrypoint_resolution() -> (
    None
):
    """Unlike an entrypoint, a test identifier has no graph node kind to
    fail to resolve against."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow",
                entrypoints=("does_not_exist",),
                tests=("test_train_e2e",),
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []
    assert [e for e in graph.edges if e.kind == "TEST_COVERS_USE_CASE"] != []


def test_build_use_case_graph_handles_several_use_cases_independently() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(use_case="uc1", entrypoints=("train",)),
            UseCaseDefinition(use_case="uc2", entrypoints=("does_not_exist",)),
        ],
        library,
    )
    assert graph.has_node(use_case_node_id("uc1"))
    assert graph.has_node(use_case_node_id("uc2"))
    edges = [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"]
    assert [e.src for e in edges] == [use_case_node_id("uc1")]


# ── join_use_case_graph ───────────────────────────────────────────────────


def test_join_folds_the_use_case_node_onto_the_shared_entry_node() -> None:
    library = _library_graph()
    before_nodes = len(library.nodes)
    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    # The use-case graph contributes exactly one *new* node (the use_case
    # itself); its entry edge folds onto the library's existing decl node.
    assert len(joined.nodes) == before_nodes + 1
    entry = next(n for n in joined.nodes if n.id == "decl://train")
    assert {f.producer for f in entry.facts} >= {USE_CASE_PROVENANCE}


def test_join_never_flips_an_unproven_node_to_consumer_compiled() -> None:
    """A public entry that is only an unproven call-graph fallback node
    (no ``consumer_compiled_body`` attr, ``provenance="call_graph"``,
    ``CONF_REDUCED``) must still read as NOT consumer-compiled
    (``is_consumer_compiled_node`` False) after a use case references it --
    the use-case placeholder registration must never outrank real
    reachability-provenance evidence and let a later call-graph traversal
    wrongly walk through an unproven body (Codex review, fresh evidence)."""
    from abicheck.buildsource.graph_facts import CONF_REDUCED
    from abicheck.buildsource.source_graph import is_consumer_compiled_node

    library = SourceGraphSummary()
    library.add_node(
        GraphNode(
            id="decl://dispatch",
            kind="source_decl",
            label="dispatch",
            attrs={"visibility": "public_header"},
            provenance="call_graph",
            confidence=CONF_REDUCED,
        )
    )
    node_by_id = {n.id: n for n in library.nodes}
    assert is_consumer_compiled_node("decl://dispatch", node_by_id) is False

    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("dispatch",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    joined_node_by_id = {n.id: n for n in joined.nodes}
    entry = joined_node_by_id["decl://dispatch"]
    assert {f.producer for f in entry.facts} >= {USE_CASE_PROVENANCE, "call_graph"}
    assert (
        is_consumer_compiled_node("decl://dispatch", joined_node_by_id) is False
    ), "a use-case reference must never make an unproven node look consumer-compiled"


def test_join_never_flips_a_tied_unknown_confidence_node_either() -> None:
    """The residual gap the earlier CONF_UNKNOWN placeholder fix didn't
    close (Codex review, fresh evidence): when the *real* fact backing a
    public entry is ALSO CONF_UNKNOWN (e.g. a loaded/hand-built graph node
    with no confidence recorded), the placeholder's confidence rank ties
    rather than loses, and the producer-name tiebreak alone
    ("declared_use_case" < "kythe") would let it win provenance anyway.
    join_use_case_graph's provenance/confidence restoration must close
    this regardless of the tiebreak outcome."""
    from abicheck.buildsource.graph_facts import CONF_UNKNOWN
    from abicheck.buildsource.source_graph import is_consumer_compiled_node

    library = SourceGraphSummary()
    library.add_node(
        GraphNode(
            id="decl://dispatch",
            kind="source_decl",
            label="dispatch",
            attrs={"visibility": "public_header"},
            provenance="kythe",
            confidence=CONF_UNKNOWN,
        )
    )
    node_by_id = {n.id: n for n in library.nodes}
    assert is_consumer_compiled_node("decl://dispatch", node_by_id) is False

    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("dispatch",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    joined_node_by_id = {n.id: n for n in joined.nodes}
    entry = joined_node_by_id["decl://dispatch"]
    assert {f.producer for f in entry.facts} >= {USE_CASE_PROVENANCE, "kythe"}
    assert entry.provenance == "kythe"
    assert (
        is_consumer_compiled_node("decl://dispatch", joined_node_by_id) is False
    ), "a tied-confidence use-case placeholder must never win the provenance tiebreak"


def test_join_does_not_mutate_the_library_graph() -> None:
    library = _library_graph()
    nodes, edges = len(library.nodes), len(library.edges)
    lib_entry = next(n for n in library.nodes if n.id == "decl://train")
    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    assert (len(library.nodes), len(library.edges)) == (nodes, edges)
    # Membership counts alone don't prove non-mutation: add_node's ADR-046
    # D2 merge mutates the *stored* node in place, so a shallow
    # re-registration would leave the library's own node carrying the
    # use-case fact while every count stayed identical.
    assert USE_CASE_PROVENANCE not in {f.producer for f in lib_entry.facts}
    joined_entry = next(n for n in joined.nodes if n.id == "decl://train")
    assert joined_entry is not lib_entry


def test_join_carries_over_coverage_honesty_flags() -> None:
    library = _library_graph()
    library.extractor_passes = {"call_graph": True}
    library.narrowed_passes = {"type_graph": True}
    library.degraded_passes = {"include_graph": True}
    library.coverage = {"source_decls": 3}
    joined = join_use_case_graph(library, build_use_case_graph([], library))
    assert joined.extractor_passes == {"call_graph": True}
    assert joined.narrowed_passes == {"type_graph": True}
    assert joined.degraded_passes == {"include_graph": True}
    assert joined.coverage == {"source_decls": 3}


def test_join_of_an_empty_use_case_graph_is_a_pure_copy() -> None:
    library = _library_graph()
    joined = join_use_case_graph(library, SourceGraphSummary())
    assert len(joined.nodes) == len(library.nodes)
    assert len(joined.edges) == len(library.edges)
    assert joined is not library


def test_join_clears_the_inherited_graph_id(tmp_path) -> None:
    """Codex review, fresh evidence: join_use_case_graph is this module's
    documented public Python API (docs/contribute/use-case-impact.md), so a
    caller following that doc and calling joined.to_dict() on the result is a real,
    reachable path -- not join_consumer_graph's situation, whose result
    never leaves appcompat.py's own in-memory analysis. Left un-cleared, the
    deep copy inherits the library graph's own finalized graph_id even
    though the node/edge content just changed, and to_dict() only
    recomputes an id when the stored value is empty -- silently describing
    this join's different content under the library graph's own unrelated,
    now-stale id."""
    library = _library_graph()
    library.finalize()
    assert library.graph_id  # sanity: the library graph really is finalized
    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    assert joined.graph_id == ""
    # A caller who does want an id for the joined graph gets one that
    # actually reflects the new content, not the stale inherited one.
    assert joined.to_dict()["graph_id"] != library.graph_id


# ── end-to-end: a use_case node joined onto a public entry ────────────────


def test_end_to_end_use_case_joins_onto_the_public_entry_node() -> None:
    library = _library_graph()
    defs = parse_use_case_manifest(
        [
            {
                "use_case": "training-workflow",
                "entrypoints": ["train"],
                "tests": ["test_train_e2e"],
            }
        ]
    )
    use_cases = build_use_case_graph(defs, library)
    joined = join_use_case_graph(library, use_cases)

    uc = next(n for n in joined.nodes if n.id == use_case_node_id("training-workflow"))
    assert uc.kind == "use_case"
    entry_edges = [
        e for e in joined.edges if e.kind == "USE_CASE_USES_ENTRY" and e.src == uc.id
    ]
    assert [e.dst for e in entry_edges] == ["decl://train"]

    test_edges = [e for e in joined.edges if e.kind == "TEST_COVERS_USE_CASE"]
    assert [e.src for e in test_edges] == [make_test_case_node_id("test_train_e2e")]
    assert [e.dst for e in test_edges] == [uc.id]

    # The public entry node itself is unchanged in kind/identity -- the join
    # only adds an incoming edge and a fact, never rewrites it.
    entry_node = next(n for n in joined.nodes if n.id == "decl://train")
    assert entry_node.kind == "source_decl"
    assert entry_node.label == "train"
