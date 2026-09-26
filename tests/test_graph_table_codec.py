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

"""The compact graph table (schema v49; evidence-entity-model Phase 5b).

The oracle throughout is ``SourceGraphSummary.from_dict(graph.to_dict())``
-- the pre-v49 load path, which shares no code with the table encoder -- so
"encode then decode" is checked against what an old-format round trip gives,
not against the codec's own formula.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.storage.graph_section_codec import GraphSection
from abicheck.storage.graph_table_codec import (
    decode_graph_table,
    encode_graph_table,
    graph_table_to_legacy_dict,
    is_graph_table,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "header_graph"


def _through_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """What a real write/read does to the payload: the D8 section wrapper's
    canonicalize+freeze+thaw, then JSON text and back."""
    thawed = GraphSection(surface_graph=payload).to_document()["surface_graph"]
    return json.loads(json.dumps(thawed, indent=2))


def _oracle(graph: SourceGraphSummary) -> dict[str, Any]:
    """What storing *graph* in the pre-v49 form and loading it gives -- the
    same section wrapper and JSON step (its canonical form sorts nested dict
    keys in either encoding), then ``from_dict``."""
    legacy = _through_storage(graph.to_dict())
    return SourceGraphSummary.from_dict(legacy).to_dict()


# ── generated graphs ─────────────────────────────────────────────────────

_ID_PREFIXES = ("decl://", "type://", "header://", "source://", "symbol://", "")
_text = st.text(min_size=0, max_size=12)  # includes unicode and ""
_long_common = st.builds(
    lambda tail: "decl://" + "n" * 200 + tail, st.sampled_from(["a", "b", "ä", "𝔸"])
)
_ids = st.one_of(
    st.builds(lambda p, t: p + t, st.sampled_from(_ID_PREFIXES), _text),
    _long_common,  # ids that collide after naive truncation
    st.just("decl://lambda at /a/x.h:4:37"),  # normalized on load
)
_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-3, 3),
    st.sampled_from([0.0, 1.0, 1.5]),
    _text,
)
_value = st.recursive(
    _scalar,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3), st.dictionaries(_text, inner, max_size=3)
    ),
    max_leaves=6,
)
_attrs = st.dictionaries(
    st.sampled_from(["name", "qualified_name", "role", "n", "flag", "é", ""]),
    _value,
    max_size=4,
)
_producer = st.sampled_from(["header_ast_l2", "castxml", "clang", ""])
_confidence = st.sampled_from(["high", "reduced", "unknown", "weird"])
_facts = st.lists(
    st.builds(GraphFact, producer=_producer, confidence=_confidence, attrs=_attrs),
    max_size=3,
)
_labels = st.sampled_from(["same", "same", "", "ユニコード", "x" * 50])


@st.composite
def _graphs(draw: st.DrawFn) -> SourceGraphSummary:
    g = SourceGraphSummary()
    ids = draw(st.lists(_ids, max_size=8))
    for node_id in ids:
        g.add_node(
            GraphNode(
                id=node_id,
                kind=draw(
                    st.sampled_from(["source_decl", "header", "record_type", "novel"])
                ),
                label=draw(_labels),
                attrs=draw(_attrs),
                provenance=draw(_producer),
                confidence=draw(_confidence),
                facts=draw(_facts),
            )
        )
    endpoints = ids + ["decl://dangling"]
    for _ in range(draw(st.integers(0, 10))):
        g.add_edge(
            GraphEdge(
                src=draw(st.sampled_from(endpoints)),
                dst=draw(st.sampled_from(endpoints)),
                kind=draw(st.sampled_from(["DECL_HAS_TYPE", "SOURCE_DECLARES", "X"])),
                attrs=draw(_attrs),
                provenance=draw(_producer),
                confidence=draw(_confidence),
                facts=draw(_facts),
            )
        )
    g.extractor_passes = draw(st.dictionaries(_text, st.booleans(), max_size=2))
    g.narrowed_scope = {
        k: frozenset(v)
        for k, v in draw(
            st.dictionaries(_text, st.lists(_text, max_size=2), max_size=2)
        ).items()
    }
    if draw(st.booleans()):
        g.resolve_entities()
    return g.finalize()


class TestRoundTripProperty:
    @settings(max_examples=250, deadline=None)
    @given(_graphs())
    def test_decode_encode_is_the_old_format_round_trip(
        self, graph: SourceGraphSummary
    ) -> None:
        payload = _through_storage(encode_graph_table(graph))
        assert is_graph_table(payload)
        assert decode_graph_table(payload).to_dict() == _oracle(graph)

    @settings(max_examples=100, deadline=None)
    @given(_graphs())
    def test_encoding_is_deterministic_and_stable_under_reload(
        self, graph: SourceGraphSummary
    ) -> None:
        # A freshly built graph may still change on its first load (load-time
        # id normalization and coalescing, as in the pre-v49 form); from then
        # on, save -> load -> save must be byte-stable.
        loaded = decode_graph_table(_through_storage(encode_graph_table(graph)))
        second = _through_storage(encode_graph_table(loaded))
        third = _through_storage(encode_graph_table(decode_graph_table(second)))
        assert json.dumps(third) == json.dumps(second)
        assert json.dumps(encode_graph_table(loaded)) == json.dumps(
            encode_graph_table(loaded)
        )

    def test_empty_graph(self) -> None:
        graph = SourceGraphSummary().finalize()
        payload = _through_storage(encode_graph_table(graph))
        assert payload["nodes"]["id"] == [] and payload["edges"]["src"] == []
        assert decode_graph_table(payload).to_dict() == _oracle(graph)

    @pytest.mark.parametrize(
        ("a", "b"), [(1, True), (0, False), (0.5, "0.5"), (None, "None"), ("1", 1)]
    )
    def test_values_equal_in_python_but_distinct_in_json_stay_distinct(
        self, a: Any, b: Any
    ) -> None:
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://a", kind="k", attrs={"v": a}))
        g.add_node(GraphNode(id="decl://b", kind="k", attrs={"v": b}))
        g.finalize()
        decoded = decode_graph_table(_through_storage(encode_graph_table(g)))
        values = [n.attrs["v"] for n in decoded.nodes]
        # Typed exactly as the pre-v49 form stores them (its canonical form
        # writes an integral float as an int; that is not this codec's doing).
        expected = [n["attrs"]["v"] for n in _oracle(g)["nodes"]]
        assert [(type(v), v) for v in values] == [(type(v), v) for v in expected]
        # Interning never merges two values the stored JSON keeps apart.
        assert json.dumps(values[0]) != json.dumps(values[1])

    def test_decoded_containers_are_not_shared_between_entities(self) -> None:
        g = SourceGraphSummary()
        for node_id in ("decl://a", "decl://b"):
            g.add_node(GraphNode(id=node_id, kind="k", attrs={"p": [1, {"q": 2}]}))
        decoded = decode_graph_table(_through_storage(encode_graph_table(g.finalize())))
        first, second = (n.attrs["p"] for n in decoded.nodes)
        first[1]["q"] = 99
        assert second == [1, {"q": 2}]


def _containers(value: Any) -> list[Any]:
    """Every dict/list reachable from *value*, itself included."""
    out: list[Any] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            out.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            out.append(item)
            stack.extend(item)
    return out


class TestDirectDecoder:
    """``decode_graph_table`` builds entities directly and decodes each
    interned fact row once. Its oracle is the per-entity legacy-dict path it
    replaced, ``from_dict(graph_table_to_legacy_dict(payload))`` -- a
    different construction route over the same payload."""

    @settings(max_examples=250, deadline=None)
    @given(_graphs())
    def test_equals_the_legacy_dict_path_including_order(
        self, graph: SourceGraphSummary
    ) -> None:
        payload = _through_storage(encode_graph_table(graph))
        direct = decode_graph_table(payload)
        legacy = SourceGraphSummary.from_dict(graph_table_to_legacy_dict(payload))
        assert json.dumps(direct.to_dict(), default=str) == json.dumps(
            legacy.to_dict(), default=str
        )
        assert [n.id for n in direct.nodes] == [n.id for n in legacy.nodes]
        assert [e.relation_key() for e in direct.edges] == [
            e.relation_key() for e in legacy.edges
        ]

    @settings(max_examples=150, deadline=None)
    @given(_graphs())
    def test_no_fact_or_container_is_shared_between_entities(
        self, graph: SourceGraphSummary
    ) -> None:
        # Interned rows are decoded once, so a missed copy would alias one
        # entity's mutable evidence into another's.
        decoded = decode_graph_table(_through_storage(encode_graph_table(graph)))
        owner: dict[int, int] = {}
        for index, entity in enumerate([*decoded.nodes, *decoded.edges]):
            for fact in entity.facts:
                # A flat top-level attrs dict may be shared (nothing mutates
                # one: identity normalization is copy-on-write); the fact
                # itself and any nested container stay per-entity.
                nested = [c for c in _containers(fact.attrs) if c is not fact.attrs]
                for obj in (fact, *nested):
                    assert owner.setdefault(id(obj), index) == index

    def test_normalizing_one_entity_never_changes_a_sibling_sharing_its_row(
        self,
    ) -> None:
        """Must-not-merge for the shared attrs dict: a decl node's identity
        normalization (which rewrites a checkout-dependent `name`) must not
        reach a non-decl node citing the same interned fact row."""
        raw = "(lambda at /w/old/a.h:4:37)"
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="header://h", kind="header", attrs={"name": raw}))
        g.add_node(GraphNode(id="decl://x", kind="function", attrs={"name": raw}))
        payload = _through_storage(encode_graph_table(g.finalize()))
        decoded = {n.id: n for n in decode_graph_table(payload).nodes}
        assert decoded["header://h"].facts[0].attrs["name"] == raw
        assert decoded["header://h"].attrs["name"] == raw
        assert decoded["decl://x"].facts[0].attrs["name"] != raw
        assert "/w/old" not in decoded["decl://x"].attrs["name"]

    def test_one_interned_fact_across_many_entities(self) -> None:
        g = SourceGraphSummary()
        for i in range(5):
            g.add_node(
                GraphNode(id=f"header://h{i}", kind="header", attrs={"v": [i % 1]})
            )
        payload = _through_storage(encode_graph_table(g.finalize()))
        assert len(payload["facts"]) == 1  # the case the per-row memo serves
        decoded = decode_graph_table(payload)
        decoded.nodes[0].facts[0].attrs["v"].append("mutated")
        assert [n.facts[0].attrs["v"] for n in decoded.nodes[1:]] == [[0]] * 4


# ── the load-scoped normalization memo ──────────────────────────────────


class TestIdentityNormalizationMemo:
    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(
            st.one_of(_ids, _text, st.just("f(lambda at /x/a.h:1:2)")), max_size=12
        )
    )
    def test_memo_never_changes_a_result(self, identities: list[str]) -> None:
        from abicheck.model import graph_identity as gi

        bare = [gi._normalize_graph_identity(i) for i in identities]
        with gi.identity_normalization_memo():
            memoized = [gi._normalize_graph_identity(i) for i in identities * 2]
        assert memoized == bare * 2

    def test_memo_is_scoped_and_reentrant(self) -> None:
        from abicheck.model import graph_identity as gi

        assert gi._NORMALIZE_MEMO_STATE.memo is None
        with gi.identity_normalization_memo():
            gi._normalize_graph_identity("decl://lambda at /a/b.h:1:2")
            with gi.identity_normalization_memo():
                assert gi._NORMALIZE_MEMO_STATE.memo
            assert gi._NORMALIZE_MEMO_STATE.memo  # the outer scope still holds it
        assert gi._NORMALIZE_MEMO_STATE.memo is None

    def test_memo_is_released_when_decoding_raises(self) -> None:
        from abicheck.model import graph_identity as gi

        payload = _sample_payload()
        payload["nodes"]["id"][0] = 99
        with pytest.raises(ValueError):
            decode_graph_table(payload)
        assert gi._NORMALIZE_MEMO_STATE.memo is None


# ── size ─────────────────────────────────────────────────────────────────


def test_table_is_smaller_than_the_legacy_form_on_real_graphs() -> None:
    for path in sorted(_FIXTURES.glob("*.json")):
        graph = load_snapshot(path).surface_graph
        assert isinstance(graph, SourceGraphSummary)
        legacy = len(json.dumps(graph.to_dict(), separators=(",", ":")))
        table = len(json.dumps(encode_graph_table(graph), separators=(",", ":")))
        assert table * 2 < legacy, path.name


# ── corrupt tables ───────────────────────────────────────────────────────


def _sample_payload() -> dict[str, Any]:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://a", kind="k", label="a", attrs={"role": "r"}))
    g.add_edge(GraphEdge(src="decl://a", dst="decl://b", kind="K"))
    return _through_storage(encode_graph_table(g.finalize()))


def _set(path: tuple[Any, ...], value: Any):  # type: ignore[no-untyped-def]
    def apply(doc: dict[str, Any]) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return apply


_CORRUPTIONS = {
    "string_index_out_of_range": _set(("nodes", "id", 0), 10_000),
    "negative_index": _set(("edges", "dst", 0), -1),
    "bool_is_not_an_index": _set(("nodes", "kind", 0), True),
    "column_length_mismatch": _set(("edges", "kind"), []),
    "missing_column": lambda d: d["nodes"].pop("label"),
    "strings_not_a_list": _set(("strings",), {"0": "x"}),
    "non_string_in_table": _set(("strings", 0), 7),
    "odd_attrs_row": _set(("attrs", 0), [0]),
    "short_fact_row": _set(("facts", 0), [0, 0]),
    "fact_index_out_of_range": _set(("nodes", "facts", 0), [0, 99]),
    "unknown_encoding": _set(("encoding",), "graph-table/99"),
    "entity_with_no_facts": _set(("nodes", "facts", 0), []),
    "columns_block_not_an_object": _set(("edges",), [[0]]),
}


@pytest.mark.parametrize("case", sorted(_CORRUPTIONS))
def test_corruption_is_a_value_error_never_a_partial_graph(case: str) -> None:
    payload = _sample_payload()
    _CORRUPTIONS[case](payload)
    with pytest.raises(ValueError, match="graph table"):
        graph_table_to_legacy_dict(payload)


def test_corrupt_table_in_a_stored_snapshot_raises_at_first_access(
    tmp_path: Path,
) -> None:
    from abicheck.model.snapshot import AbiSnapshot
    from abicheck.serialization import snapshot_from_dict

    path = tmp_path / "s.json"
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="decl://a", kind="k"))
    save_snapshot(
        AbiSnapshot(library="l.so", version="1", surface_graph=graph.finalize()), path
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sections"]["graph"]["payload"]["surface_graph"]["nodes"]["id"] = [5]
    snap = snapshot_from_dict(doc)
    for _ in range(2):
        with pytest.raises(ValueError, match="out of range"):
            _ = snap.surface_graph


# ── migration: a stored pre-v49 snapshot ─────────────────────────────────


class TestPreV49Migration:
    @pytest.mark.parametrize(
        "path", sorted(_FIXTURES.glob("*.json")), ids=lambda p: p.stem
    )
    def test_old_document_loads_to_the_graph_from_dict_gives(self, path: Path) -> None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["schema_version"] == 48
        raw = doc["sections"]["graph"]["payload"]["surface_graph"]
        assert not is_graph_table(raw)
        graph = load_snapshot(path).surface_graph
        assert isinstance(graph, SourceGraphSummary)
        assert graph.to_dict() == SourceGraphSummary.from_dict(raw).to_dict()

    @pytest.mark.parametrize(
        "path", sorted(_FIXTURES.glob("*.json")), ids=lambda p: p.stem
    )
    def test_resaving_writes_the_table_and_keeps_the_graph(
        self, path: Path, tmp_path: Path
    ) -> None:
        old = load_snapshot(path)
        out = tmp_path / path.name
        save_snapshot(old, out)
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert is_graph_table(doc["sections"]["graph"]["payload"]["surface_graph"])
        new = load_snapshot(out)
        assert new == old
        assert new.surface_graph.to_dict() == old.surface_graph.to_dict()  # type: ignore[union-attr]


# ── old vs. new encoding: identical compare JSON ─────────────────────────

_CASES = sorted({p.name.split("__")[0] for p in _FIXTURES.glob("*__v1.json")})
_VOLATILE = ("duration", "seconds", "timestamp", "generated_at", "elapsed")


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _stable(v)
            for k, v in value.items()
            if not any(token in k for token in _VOLATILE)
        }
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _compare_json(old: Path, new: Path, out: Path) -> dict[str, Any]:
    from click.testing import CliRunner

    from abicheck.cli import main

    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={out}"]
    )
    assert result.exit_code in (0, 1, 2, 4), result.output
    return _stable(json.loads(out.read_text(encoding="utf-8")))


@pytest.mark.parametrize("case", _CASES)
def test_old_and_new_encodings_give_identical_compare_json(
    case: str, tmp_path: Path
) -> None:
    legacy = [_FIXTURES / f"{case}__v{i}.json" for i in (1, 2)]
    table = []
    for path in legacy:
        out = tmp_path / path.name
        save_snapshot(load_snapshot(path), out)
        stored = json.loads(out.read_text(encoding="utf-8"))
        # Both configurations really differ on disk.
        assert is_graph_table(stored["sections"]["graph"]["payload"]["surface_graph"])
        table.append(out)
    old_report = _compare_json(*legacy, tmp_path / "old.json")
    new_report = _compare_json(*table, tmp_path / "new.json")
    assert old_report["changes"]
    assert old_report == new_report


@settings(max_examples=150, deadline=None)
@given(
    _graphs(),
    st.dictionaries(
        st.sampled_from(["future_key", "call_edges"]), st.just({"x": 1}), max_size=2
    ),
)
def test_finalize_recomputes_every_coverage_entry_not_persisted(
    graph: SourceGraphSummary, extra: dict[str, Any]
) -> None:
    from abicheck.storage.graph_table_codec import _observed_coverage

    # Forward-compatible keys (top-level, and nested inside an owned section)
    # must survive; everything else finalize must rebuild identically.
    graph.coverage = {**graph.coverage, **extra}
    graph.finalize()
    full = json.loads(json.dumps(graph.coverage))
    graph.coverage = _observed_coverage(graph.coverage)
    graph.finalize()
    assert json.loads(json.dumps(graph.coverage)) == full


class TestDirectDecoderRejectsCorruption:
    """Every malformed entity entry is a ``ValueError`` on the direct path,
    exactly as it was on the legacy-dict path."""

    def _payload(self) -> dict[str, Any]:
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://a", kind="k", attrs={"v": 1}))
        g.add_edge(GraphEdge(src="decl://a", dst="decl://a", kind="X"))
        return _through_storage(encode_graph_table(g.finalize()))

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda p: p.update(encoding="graph-table/999"),
            lambda p: p["nodes"]["facts"].__setitem__(0, []),
            lambda p: p["edges"]["facts"].__setitem__(0, "0"),
            lambda p: p["nodes"]["facts"].__setitem__(0, 10_000),
            lambda p: p["edges"]["facts"].__setitem__(0, [0, 10_000]),
        ],
        ids=["encoding", "empty-list", "non-int", "out-of-range", "range-in-list"],
    )
    def test_corrupt_entry_raises(self, mutate: Any) -> None:
        payload = self._payload()
        mutate(payload)
        with pytest.raises(ValueError, match="graph table"):
            graph_table_to_legacy_dict(payload)
        with pytest.raises(ValueError, match="graph table"):
            decode_graph_table(payload)

    def test_multi_fact_entries_and_occurrences_match_the_legacy_path(self) -> None:
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://a", kind="k"))
        for site in ("s1", "s2"):
            g.add_edge(
                GraphEdge(
                    src="decl://a",
                    dst="decl://b",
                    kind="DECL_CALLS_DECL",
                    provenance=f"p{site}",
                    attrs={"callsite_id": site},
                )
            )
        payload = _through_storage(encode_graph_table(g.finalize()))
        assert any(type(f) is list for f in payload["edges"]["facts"])
        direct = decode_graph_table(payload)
        legacy = SourceGraphSummary.from_dict(graph_table_to_legacy_dict(payload))
        assert direct.to_dict() == legacy.to_dict()
        assert len(direct.edges[0].occurrences) == 2
