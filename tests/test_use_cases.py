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
    UseCaseDefinition,
    UseCaseResolution,
    _public_entry_index,
    explain_use_case_impact,
    load_use_case_manifest,
    parse_use_case_manifest,
    resolve_use_case_entrypoints,
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
    with pytest.raises(UseCaseManifestError, match="invalid YAML"):
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
    (Codex review, fresh evidence). Translated by the shared
    ``yaml_strict`` loader since the three copies of that translation were
    consolidated."""
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("- use_case: 2023-99-99\n")
    with pytest.raises(UseCaseManifestError, match="invalid YAML scalar"):
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
    with pytest.raises(UseCaseManifestError, match="unhashable mapping key"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_of_an_empty_file_is_empty(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("")
    assert load_use_case_manifest(manifest) == []


# ── entrypoint resolution (_public_entry_index) ─────────────────────────────


def _resolved_targets(library, use_case, *entrypoints):
    """The node ids *entrypoints* resolve to -- the targets
    ``explain_use_case_impact`` walks from and ``resolve_use_case_entrypoints``
    reports as resolved."""
    index = _public_entry_index(library)
    resolution = resolve_use_case_entrypoints(
        [UseCaseDefinition(use_case=use_case, entrypoints=entrypoints)], library
    )[0]
    targets = {index[e] for e in entrypoints if e in index}
    assert set(resolution.resolved_entrypoints) == {
        e for e in entrypoints if e in index
    }
    return targets


def test_entrypoint_resolves_entrypoints_by_id_and_label() -> None:
    library = _library_graph()
    assert _resolved_targets(
        library, "training-workflow", "train", "binary_symbol://_Z5evalv"
    ) == {"decl://train", "binary_symbol://_Z5evalv"}


def test_entrypoint_skips_an_unresolvable_entrypoint_silently() -> None:
    """An entrypoint the library graph cannot resolve is dropped, not an
    error -- the same 'absence, never a wrong answer' discipline
    consumer_graph.py already follows."""
    library = _library_graph()
    assert _resolved_targets(library, "training-workflow", "does_not_exist") == set()


def test_entrypoint_skips_an_ambiguous_label_silently() -> None:
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
    assert _resolved_targets(library, "training-workflow", "train") == set()


def test_entrypoint_coalesces_a_mapped_decl_and_symbol_pair() -> None:
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
    # Coalesced onto the mapped binary_symbol node, matching
    # consumer_graph.py's own preference for the exported representation.
    assert _resolved_targets(library, "training-workflow", "train") == {
        "binary_symbol://train"
    }


def test_entrypoint_does_not_coalesce_a_decl_mapped_to_two_symbols() -> None:
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
    assert _resolved_targets(library, "training-workflow", "train") == {"decl://train"}


def test_entrypoint_an_exact_id_wins_over_a_colliding_label() -> None:
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
    assert _resolved_targets(library, "training-workflow", "decl://train") == {
        "decl://train"
    }


def test_entrypoint_does_not_resolve_an_internal_declaration() -> None:
    """A non-public decl is never a valid entrypoint target, even if a
    manifest names it by label."""
    library = _library_graph()
    assert _resolved_targets(library, "training-workflow", "detail::helper") == set()


def test_entrypoint_does_not_resolve_a_public_type() -> None:
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
    assert _resolved_targets(library, "training-workflow", "Config") == set()
    # The exact node id is rejected the same way -- a type is never public
    # in this module's sense, regardless of how it's spelled.
    assert (
        _resolved_targets(library, "training-workflow", "record_type://Config") == set()
    )


# ── resolve_use_case_entrypoints ────────────────────────────────────────────


def test_resolve_use_case_entrypoints_splits_resolved_and_unresolved() -> None:
    library = _library_graph()
    resolutions = resolve_use_case_entrypoints(
        [
            UseCaseDefinition(
                use_case="uc1",
                entrypoints=("train", "does_not_exist"),
                tests=("test_train",),
            )
        ],
        library,
    )
    assert len(resolutions) == 1
    r = resolutions[0]
    assert r.use_case == "uc1"
    assert r.resolved_entrypoints == ("train",)
    assert r.unresolved_entrypoints == ("does_not_exist",)
    assert r.tests == ("test_train",)


def test_resolve_use_case_entrypoints_empty_definitions_list_returns_empty() -> None:
    assert resolve_use_case_entrypoints([], _library_graph()) == []


def test_resolve_use_case_entrypoints_no_entrypoints_declared() -> None:
    library = _library_graph()
    resolutions = resolve_use_case_entrypoints(
        [UseCaseDefinition(use_case="uc1")], library
    )
    assert resolutions == [UseCaseResolution(use_case="uc1")]


def test_resolve_use_case_entrypoints_preserves_manifest_declared_order() -> None:
    # A local graph with two public entries (`_library_graph()` only has
    # one) so both resolved_entrypoints and unresolved_entrypoints can pin
    # order, not just the latter — CodeRabbit review.
    library = SourceGraphSummary()
    library.add_node(
        GraphNode(
            id="decl://evaluate",
            kind="source_decl",
            label="evaluate",
            attrs={"visibility": "public_header"},
        )
    )
    library.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    resolution = resolve_use_case_entrypoints(
        [
            UseCaseDefinition(
                use_case="uc1",
                # Declared out of alphabetical order and interleaved with
                # unresolvable names, on both sides of the split.
                entrypoints=(
                    "does_not_exist_a",
                    "evaluate",
                    "train",
                    "does_not_exist_b",
                ),
            )
        ],
        library,
    )[0]
    assert resolution.resolved_entrypoints == ("evaluate", "train")
    assert resolution.unresolved_entrypoints == (
        "does_not_exist_a",
        "does_not_exist_b",
    )


# ── explain_use_case_impact ──────────────────────────────────────────────


def _walkable_library_graph() -> SourceGraphSummary:
    """Two independent public entries, one of which calls an internal
    helper — the minimal shape needed to distinguish direct vs. transitive
    use-case attribution. `train` -> (calls) -> `detail::helper`; `evaluate`
    stands alone."""
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
        GraphNode(id="binary_symbol://train", kind="binary_symbol", label="train")
    )
    g.add_edge(
        GraphEdge(
            src="decl://train",
            dst="binary_symbol://train",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_node(
        GraphNode(
            id="decl://_ZN6detail6helperEv",
            kind="source_decl",
            label="detail::helper",
            attrs={"visibility": "source"},
        )
    )
    g.add_node(
        GraphNode(
            id="binary_symbol://_ZN6detail6helperEv",
            kind="binary_symbol",
            label="_ZN6detail6helperEv",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://_ZN6detail6helperEv",
            dst="binary_symbol://_ZN6detail6helperEv",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://train", dst="decl://_ZN6detail6helperEv", kind="DECL_CALLS_DECL"
        )
    )
    g.add_node(
        GraphNode(
            id="decl://evaluate",
            kind="source_decl",
            label="evaluate",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_node(
        GraphNode(id="binary_symbol://evaluate", kind="binary_symbol", label="evaluate")
    )
    g.add_edge(
        GraphEdge(
            src="decl://evaluate",
            dst="binary_symbol://evaluate",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    return g


def test_explain_use_case_impact_direct_and_transitive_attribution() -> None:
    library = _walkable_library_graph()
    definitions = [
        UseCaseDefinition(use_case="training workflow", entrypoints=("train",)),
        UseCaseDefinition(use_case="evaluation workflow", entrypoints=("evaluate",)),
    ]
    result = explain_use_case_impact(
        definitions,
        library,
        symbols=["train", "_ZN6detail6helperEv", "evaluate", "does_not_exist"],
    )
    assert result == {
        "train": ("training workflow",),
        "_ZN6detail6helperEv": ("training workflow",),
        "evaluate": ("evaluation workflow",),
    }


def test_explain_use_case_impact_overlapping_use_cases_both_attributed() -> None:
    library = _walkable_library_graph()
    definitions = [
        UseCaseDefinition(use_case="uc_a", entrypoints=("train",)),
        UseCaseDefinition(use_case="uc_b", entrypoints=("train",)),
    ]
    result = explain_use_case_impact(
        definitions, library, symbols=["_ZN6detail6helperEv"]
    )
    assert result == {"_ZN6detail6helperEv": ("uc_a", "uc_b")}


def test_explain_use_case_impact_unresolvable_entrypoint_yields_no_attribution() -> (
    None
):
    library = _walkable_library_graph()
    definitions = [
        UseCaseDefinition(use_case="ghost", entrypoints=("does_not_exist_entry",))
    ]
    result = explain_use_case_impact(definitions, library, symbols=["train"])
    assert result == {}


def test_explain_use_case_impact_empty_symbols_or_definitions() -> None:
    library = _walkable_library_graph()
    definitions = [UseCaseDefinition(use_case="uc1", entrypoints=("train",))]
    assert explain_use_case_impact(definitions, library, symbols=[]) == {}
    assert explain_use_case_impact([], library, symbols=["train"]) == {}


def test_explain_use_case_impact_type_shaped_symbol_never_attributed() -> None:
    # No SOURCE_DECL_MAPS_TO_SYMBOL edge backs a bare type name -- the same
    # structural limitation explain_required_symbols has, documented rather
    # than silently different.
    library = _walkable_library_graph()
    definitions = [UseCaseDefinition(use_case="uc1", entrypoints=("train",))]
    result = explain_use_case_impact(definitions, library, symbols=["SomeInternalType"])
    assert result == {}


def _versioned_symbol_library_graph() -> SourceGraphSummary:
    """One declaration mapping to two versioned exports (`foo@V1`/`foo@V2`)
    -- the shape a single ``foo`` definition symbol-versioned across two
    releases produces."""
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://foo",
            kind="source_decl",
            label="foo",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_node(
        GraphNode(id="binary_symbol://foo@V1", kind="binary_symbol", label="foo@V1")
    )
    g.add_node(
        GraphNode(id="binary_symbol://foo@V2", kind="binary_symbol", label="foo@V2")
    )
    g.add_edge(
        GraphEdge(
            src="decl://foo",
            dst="binary_symbol://foo@V1",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://foo",
            dst="binary_symbol://foo@V2",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    return g


def test_explain_use_case_impact_exact_versioned_entrypoint_stays_pinned() -> None:
    # Codex review, fresh evidence: a use case naming one exact versioned
    # export must be attributed to that version alone -- not the other
    # version merely because they share a declaration.
    library = _versioned_symbol_library_graph()
    definitions = [UseCaseDefinition(use_case="v1 caller", entrypoints=("foo@V1",))]
    result = explain_use_case_impact(definitions, library, symbols=["foo@V1", "foo@V2"])
    assert result == {"foo@V1": ("v1 caller",)}


def test_explain_use_case_impact_bare_decl_entrypoint_covers_every_version() -> None:
    # The undisambiguated counterpart: a use case naming the shared
    # declaration itself (no specific version) legitimately covers every
    # version it maps to.
    library = _versioned_symbol_library_graph()
    definitions = [UseCaseDefinition(use_case="either version", entrypoints=("foo",))]
    result = explain_use_case_impact(definitions, library, symbols=["foo@V1", "foo@V2"])
    assert result == {
        "foo@V1": ("either version",),
        "foo@V2": ("either version",),
    }


def test_explain_use_case_impact_two_use_cases_pin_different_versions() -> None:
    library = _versioned_symbol_library_graph()
    definitions = [
        UseCaseDefinition(use_case="v1 caller", entrypoints=("foo@V1",)),
        UseCaseDefinition(use_case="v2 caller", entrypoints=("foo@V2",)),
    ]
    result = explain_use_case_impact(definitions, library, symbols=["foo@V1", "foo@V2"])
    assert result == {
        "foo@V1": ("v1 caller",),
        "foo@V2": ("v2 caller",),
    }


def _multi_decl_one_symbol_library_graph() -> SourceGraphSummary:
    """Two *distinct* declarations both mapping onto the **same** exported
    symbol (`binary_symbol://foo`) -- the shape an inline/weak definition
    captured once per TU produces, all mangling to the identical symbol
    name. `decl://foo_declaration_only` has no outgoing call edges (a
    forward declaration / extern with no captured body in this TU's
    evidence); `decl://foo_with_body` is the sibling declaration that
    actually carries the definition, calling `detail::helper`."""
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="binary_symbol://foo", kind="binary_symbol", label="foo"))
    g.add_node(
        GraphNode(
            id="decl://foo_declaration_only",
            kind="source_decl",
            label="foo",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://foo_declaration_only",
            dst="binary_symbol://foo",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_node(
        GraphNode(
            id="decl://foo_with_body",
            kind="source_decl",
            label="foo",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://foo_with_body",
            dst="binary_symbol://foo",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_node(
        GraphNode(
            id="decl://_ZN6detail6helperEv",
            kind="source_decl",
            label="detail::helper",
            attrs={"visibility": "source"},
        )
    )
    g.add_node(
        GraphNode(
            id="binary_symbol://_ZN6detail6helperEv",
            kind="binary_symbol",
            label="_ZN6detail6helperEv",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://_ZN6detail6helperEv",
            dst="binary_symbol://_ZN6detail6helperEv",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://foo_with_body",
            dst="decl://_ZN6detail6helperEv",
            kind="DECL_CALLS_DECL",
        )
    )
    return g


def test_explain_use_case_impact_walks_every_decl_mapped_to_the_same_symbol() -> None:
    # Codex review, fresh evidence: when more than one declaration maps to
    # the same exported symbol, a manifest entry naming that symbol must
    # walk from EVERY mapped declaration, not just whichever one an
    # arbitrary edge-iteration order retained -- otherwise transitive
    # attribution silently depends on which sibling decl "won".
    library = _multi_decl_one_symbol_library_graph()
    definitions = [UseCaseDefinition(use_case="caller", entrypoints=("foo",))]
    result = explain_use_case_impact(
        definitions, library, symbols=["foo", "_ZN6detail6helperEv"]
    )
    assert result == {
        "foo": ("caller",),
        "_ZN6detail6helperEv": ("caller",),
    }


def test_explain_use_case_impact_merges_repeated_use_case_entries() -> None:
    # Codex review, fresh evidence: a manifest may repeat the same
    # `use_case` name across separate list entries -- `parse_use_case_manifest`
    # never rejects it, so entries sharing a name are one use case.
    # Attribution must union their entrypoints, not keep only the last
    # entry's entrypoint set.
    library = _walkable_library_graph()
    definitions = [
        UseCaseDefinition(use_case="shared", entrypoints=("train",)),
        UseCaseDefinition(use_case="shared", entrypoints=("evaluate",)),
    ]
    result = explain_use_case_impact(
        definitions, library, symbols=["train", "_ZN6detail6helperEv", "evaluate"]
    )
    assert result == {
        "train": ("shared",),
        "_ZN6detail6helperEv": ("shared",),
        "evaluate": ("shared",),
    }
