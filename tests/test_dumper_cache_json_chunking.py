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

"""Contract tests for ``dumper_cache._write_json_chunked``.

The function replaces ``json.dump`` on the DPC++ AST cache-write path purely
for speed, so its entire contract is **"same bytes as ``json.dump``, bounded
peak memory"**. Both halves are stated as invariants over generated documents
rather than one hand-written tree:

* the byte-identity oracle is ``json.dumps``, an independent second encoder
  (the C one-shot path), not a re-derivation of this function's own splitting
  rule -- so a wrong separator, a mis-escaped key, a dropped ``", "`` or a
  coerced non-``str`` key shows up as a diff, whatever the document's shape;
* the bound is asserted by observing the *fragments handed to ``write``*, on a
  document far larger than the bound, since a "streaming" writer that in fact
  built one big string would still pass every equality assertion.

Root ``AGENTS.md``'s third-party-boundary rule is why the round-trip cases go
through the real ``_atomic_write_json`` entry point at a scale that actually
crosses the chunking threshold, not only through the encoder helper.
"""

from __future__ import annotations

import io
import json
import math
import random
from pathlib import Path

import pytest

from abicheck.dumper_cache import (
    _JSON_CHUNK_BYTE_LIMIT,
    _JSON_CHUNK_MAX_DEPTH,
    _JSON_CHUNK_NODE_LIMIT,
    _JSON_WRITE_BUFFER,
    _atomic_write_json,
    _subtree_exceeds,
    _write_json_chunked,
)


def _encode(obj: object) -> str:
    buf = io.StringIO()
    _write_json_chunked(obj, buf.write)
    return buf.getvalue()


def _random_document(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth > 6 or roll < 0.25:
        return rng.choice(
            [
                None,
                True,
                False,
                0,
                -1,
                12345678901234567890,  # past 64-bit: must stay exact
                1.5,
                math.nan,
                math.inf,
                -math.inf,
                "",
                'ünïcode "quoted"\n\t\\',
                "x" * 40,
                "a/b c",
            ]
        )
    if roll < 0.6:
        return {
            f"k{i}": _random_document(rng, depth + 1) for i in range(rng.randint(0, 6))
        }
    return [_random_document(rng, depth + 1) for _ in range(rng.randint(0, 8))]


@pytest.mark.parametrize("seed", range(8))
def test_byte_identical_to_json_dumps_on_random_documents(seed: int) -> None:
    """250 generated documents per seed encode exactly as ``json.dumps`` does."""
    rng = random.Random(seed)
    for _ in range(250):
        document = _random_document(rng)
        assert _encode(document) == json.dumps(document)


@pytest.mark.parametrize(
    "document",
    [
        {},
        [],
        {"a": {}},
        {"a": []},
        [[[]]],
        {"a": ()},
        (1, 2, [3]),  # tuples encode as arrays
        {1: "a", "b": 2},  # non-str keys: json coerces, we must not diverge
        {True: 1, None: 2, 2.5: "x"},
        {"": ""},
        {"nan": math.nan, "inf": math.inf},
    ],
)
def test_byte_identical_on_edge_shaped_documents(document: object) -> None:
    assert _encode(document) == json.dumps(document)


def _wide_ast(n: int) -> dict[str, object]:
    """A translation unit with *n* sibling declarations (flat, huge)."""
    return {
        "kind": "TranslationUnitDecl",
        "id": "0x1",
        "inner": [
            {"id": f"0x{i:x}", "kind": "VarDecl", "name": f"v{i}", "loc": {"line": i}}
            for i in range(n)
        ],
    }


def test_large_document_is_written_in_bounded_fragments() -> None:
    """Every fragment handed to ``write`` stays near the write buffer size.

    This is the assertion the equality tests cannot make: it observes the
    mechanism (the fragments) rather than the result, so a regression that
    encoded the whole document in one piece -- which is exactly what the
    obvious ``write(json.dumps(obj))`` "simplification" does -- fails here
    even though the bytes would still be correct.
    """
    document = _wide_ast(200_000)
    fragments: list[str] = []
    _write_json_chunked(document, fragments.append)

    assert "".join(fragments) == json.dumps(document)
    encoded_size = sum(len(f) for f in fragments)
    assert encoded_size > 8 * _JSON_WRITE_BUFFER, "fixture too small to bound anything"
    # One buffer's worth, plus at most the last delegated chunk appended to it.
    assert max(len(f) for f in fragments) < 4 * _JSON_WRITE_BUFFER
    assert len(fragments) > 8


def test_a_single_oversized_subtree_is_split_not_encoded_whole() -> None:
    """The split follows *size*, not depth -- one giant subtree still splits.

    The tempting cheaper rule ("descend a fixed number of levels, then
    delegate") passes every equality test and then holds the whole document in
    memory for a tree whose bulk sits under one namespace, which is the shape
    the bound exists for.
    """
    nested: object = _wide_ast(120_000)
    for _ in range(3):
        nested = {"kind": "NamespaceDecl", "name": "n", "inner": [nested]}
    fragments: list[str] = []
    _write_json_chunked(nested, fragments.append)
    assert "".join(fragments) == json.dumps(nested)
    assert max(len(f) for f in fragments) < 4 * _JSON_WRITE_BUFFER


def test_small_documents_are_delegated_whole_to_the_c_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subtree under the node limit costs exactly one ``json.dumps`` call.

    Pins the "don't pay Python-level traversal for a small AST" half of the
    design: without it, a change that always descended would keep the bytes
    identical while making the common small-header case slower.
    """
    calls: list[object] = []
    real_dumps = json.dumps

    def counting_dumps(obj: object, *args: object, **kwargs: object) -> str:
        calls.append(obj)
        return real_dumps(obj, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("abicheck.dumper_cache.json.dumps", counting_dumps)
    small = {"kind": "TranslationUnitDecl", "inner": [{"kind": "VarDecl"}]}
    assert _encode(small) == real_dumps(small)
    assert calls == [small]


def test_subtree_exceeds_stops_early_and_answers_both_ways() -> None:
    """The probe is exact at the boundary and bounded in cost above it."""
    assert not _subtree_exceeds("scalar", 1)
    assert not _subtree_exceeds([1, 2], 3)  # list + 2 scalars == 3 nodes
    assert _subtree_exceeds([1, 2], 2)
    assert not _subtree_exceeds({"a": [1]}, 3)
    assert _subtree_exceeds({"a": [1]}, 2)

    # Cost bound: the probe stops the moment the limit is passed, so its work
    # is O(limit) rather than O(size of the subtree).
    huge = list(range(500_000))
    assert _subtree_exceeds(huge, 10)

    # A *subclass* of list/dict is counted as an opaque leaf, not walked --
    # deliberate: the encoder half applies the same exact-type test and hands
    # such a container to ``json.dumps`` whole (which encodes it correctly, as
    # an array/object). Under-counting there can only ever delegate earlier,
    # never produce wrong bytes.
    class _ListSubclass(list):  # type: ignore[type-arg]
        pass

    assert not _subtree_exceeds(_ListSubclass(range(100)), 1)
    assert _encode(_ListSubclass(range(5))) == json.dumps(_ListSubclass(range(5)))


def test_cyclic_input_terminates_with_the_json_circular_reference_error() -> None:
    """A hand-built cycle raises ``ValueError``, never recurses without bound.

    Impossible from ``json.load`` output, which is the only real caller, but
    the depth cap's job is to make the failure mode the same as
    ``json.dump``'s rather than a ``RecursionError`` from this walk.
    """
    cyclic: dict[str, object] = {"kind": "TranslationUnitDecl"}
    cyclic["inner"] = [cyclic]
    with pytest.raises(ValueError, match="Circular reference"):
        _encode(cyclic)


def test_depth_cap_delegates_instead_of_recursing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the cap a large subtree is delegated whole rather than descended."""
    monkeypatch.setattr("abicheck.dumper_cache._JSON_CHUNK_MAX_DEPTH", 2)
    document = {"a": {"b": {"c": _wide_ast(1_000)}}}
    fragments: list[str] = []
    _write_json_chunked(document, fragments.append)
    assert "".join(fragments) == json.dumps(document)
    assert _JSON_CHUNK_MAX_DEPTH > 2  # the real cap is not this test's value


def test_atomic_write_json_round_trips_a_document_past_the_chunk_threshold(
    tmp_path: Path,
) -> None:
    """The real public entry point, at a scale that actually chunks.

    Root ``AGENTS.md``'s third-party-boundary rule: a toy fixture through this
    function exercises none of the splitting, so the one test that proves the
    cache file is still loadable uses a document larger than
    ``_JSON_CHUNK_NODE_LIMIT`` nodes.
    """
    document = _wide_ast(_JSON_CHUNK_NODE_LIMIT // 4 + 10)
    assert _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT)
    target = tmp_path / "cache" / "key.json"
    target.parent.mkdir()

    _atomic_write_json(target, document)

    assert target.read_text(encoding="utf-8") == json.dumps(document)
    assert json.loads(target.read_text(encoding="utf-8")) == document
    assert list(target.parent.iterdir()) == [target]


def test_non_ascii_survives_the_utf8_temp_file_round_trip(tmp_path: Path) -> None:
    """Escaping is ``ensure_ascii``, so the file is ASCII either way -- check it."""
    target = tmp_path / "key.json"
    document = {"name": "ünïcode", "inner": ["日本語", " "]}
    _atomic_write_json(target, document)
    raw = target.read_text(encoding="utf-8")
    assert raw == json.dumps(document)
    assert json.loads(raw) == document


def _byte_heavy_ast(count: int, spelling_length: int) -> dict[str, object]:
    """Few nodes, enormous strings -- the shape a node-count bound misses.

    Models a real DPC++ AST full of long template-qualified spellings.
    """
    spelling = "ns::Template<" + "T" * spelling_length + ">"
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "FunctionDecl", "name": f"f{i}", "type": {"qualType": spelling}}
            for i in range(count)
        ],
    }


def test_a_byte_heavy_subtree_is_split_even_though_its_node_count_is_small() -> None:
    """Both bounds are checked, because neither implies the other.

    Regression test for a defect found in review: the split was gated on node
    count alone, so a document well under ``_JSON_CHUNK_NODE_LIMIT`` nodes but
    carrying very long string values was treated as a small subtree and handed
    to one ``json.dumps`` call -- reintroducing the second full-size encoded
    copy this writer exists to avoid.

    The fixture is deliberately small by count and large by bytes, and the
    assertion observes the fragments rather than the bytes, since the output is
    byte-identical either way.
    """
    document = _byte_heavy_ast(count=400, spelling_length=60_000)
    assert not _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT), (
        "fixture must be small by node count, or it tests the other bound"
    )
    assert _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT, _JSON_CHUNK_BYTE_LIMIT)

    fragments: list[str] = []
    _write_json_chunked(document, fragments.append)
    assert "".join(fragments) == json.dumps(document)
    encoded_size = sum(len(f) for f in fragments)
    assert encoded_size > 2 * _JSON_CHUNK_BYTE_LIMIT, "fixture too small to bound"
    assert max(len(f) for f in fragments) < 4 * _JSON_WRITE_BUFFER
    assert len(fragments) > 8


def test_the_byte_probe_is_opt_in_and_estimates_below_the_real_encoding() -> None:
    """A negative byte limit checks node count alone; the estimate never overshoots.

    Under-estimating is the safe direction for a bound that decides "small
    enough to encode whole" only when it answers ``False`` -- an estimate above
    the true size would split more than necessary, one below it never delegates
    something too large.
    """
    document = _byte_heavy_ast(count=4, spelling_length=1_000)
    assert not _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT, -1)

    real = len(json.dumps(document))
    # The estimate is bracketed: it must exceed a limit just under the real
    # encoded size, and must not exceed one comfortably above it.
    assert _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT, real // 2)
    assert not _subtree_exceeds(document, _JSON_CHUNK_NODE_LIMIT, real * 2)
