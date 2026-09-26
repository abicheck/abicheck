"""`materialize_locations` undoes clang's sticky location encoding.

clang's JSON writer omits a location's ``file``/``line`` when unchanged from
the previously *written* location in document order. The oracle here is an
independent encoder of that rule (`_clang_encode`): generate trees whose
locations are all explicit, encode them the way clang does, and require
`materialize_locations` to restore the explicit form exactly. The encoder
shares no code with the implementation.

Regression context: `dumper_clang._walk` tracked the current file only from
the nodes it visits and does not descend into function bodies, so a file
change clang wrote inside one leaked into the next declaration. On Intel SVS,
`svs::threads::CACHE_LINE_BYTES` (`threadlocal.h:55`) was recorded at
`/usr/include/c++/13/concepts:55`, and dependency scoping then dropped it.
"""

from __future__ import annotations

import copy
import random

import pytest

from abicheck.extract.headers.clang.locations import materialize_locations

_FILES = ["/usr/include/c++/13/concepts", "/proj/include/a.h", "/proj/include/b.h"]


def _loc(rng: random.Random) -> dict:
    loc = {
        "offset": rng.randrange(10_000),
        "file": rng.choice(_FILES),
        "line": rng.randrange(1, 40),
        "col": rng.randrange(1, 80),
        "tokLen": 1,
    }
    if rng.random() < 0.2:
        loc["includedFrom"] = {"file": rng.choice(_FILES)}
    return loc


def _node(rng: random.Random, depth: int) -> dict:
    node: dict = {"id": hex(rng.randrange(1 << 32)), "kind": "X"}
    if rng.random() < 0.3:
        node["loc"] = {"spellingLoc": _loc(rng), "expansionLoc": _loc(rng)}
    else:
        node["loc"] = _loc(rng)
    node["range"] = {"begin": _loc(rng), "end": _loc(rng)}
    if depth and rng.random() < 0.8:
        node["inner"] = [_node(rng, depth - 1) for _ in range(rng.randrange(1, 4))]
    return node


def _locations_in_document_order(tree):
    """Yield every bare location dict, in document order (the oracle's own walk)."""
    if isinstance(tree, dict):
        if "offset" in tree and "col" in tree:
            yield tree
        for key, value in tree.items():
            if key != "includedFrom":
                yield from _locations_in_document_order(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from _locations_in_document_order(value)


def _without_included_from(tree):
    """The oracle's own copy of *tree* with every location's `includedFrom`
    removed -- what materialization is documented to leave."""
    tree = copy.deepcopy(tree)
    for loc in _locations_in_document_order(tree):
        loc.pop("includedFrom", None)
    return tree


def _clang_encode(explicit):
    """clang's rule: drop `file`/`line` equal to the last written one."""
    encoded = copy.deepcopy(explicit)
    last_file = last_line = None
    for loc in _locations_in_document_order(encoded):
        if loc["file"] == last_file:
            del loc["file"]
            if loc["line"] == last_line:
                del loc["line"]
        else:
            last_file = loc["file"]
        last_line = loc["line"] if "line" in loc else last_line
    return encoded


@pytest.mark.parametrize("seed", range(200))
def test_materialize_inverts_clang_sticky_encoding(seed):
    rng = random.Random(seed)
    explicit = {
        "kind": "TranslationUnitDecl",
        "inner": [_node(rng, 4) for _ in range(3)],
    }
    encoded = _clang_encode(explicit)
    # Vacuity guard: the encoding really omitted something to restore.
    if seed == 0:
        assert encoded != explicit
    assert materialize_locations(encoded) == _without_included_from(explicit)


def test_materialize_is_idempotent_and_passes_non_trees_through():
    rng = random.Random(7)
    tree = _clang_encode({"inner": [_node(rng, 3)]})
    once = copy.deepcopy(materialize_locations(tree))
    assert materialize_locations(tree) == once
    marker = object()
    assert materialize_locations(marker) is marker


def test_included_from_does_not_move_the_current_file():
    tree = {
        "inner": [
            {
                "loc": {
                    "offset": 1,
                    "file": "/a.h",
                    "line": 3,
                    "col": 1,
                    "includedFrom": {"file": "/other.h"},
                }
            },
            {"loc": {"offset": 2, "col": 5}},
        ]
    }
    materialize_locations(tree)
    assert tree["inner"][1]["loc"]["file"] == "/a.h"
    assert tree["inner"][1]["loc"]["line"] == 3
    assert "includedFrom" not in tree["inner"][0]["loc"]


def test_only_included_from_is_dropped():
    """Every other location key survives, including `tokLen`/`offset`,
    which the header-graph call projection reads from `range`."""
    rng = random.Random(3)
    tree = {"inner": [_node(rng, 3) for _ in range(4)]}
    keys_before = {
        k
        for loc in _locations_in_document_order(tree)
        for k in loc
        if k != "includedFrom"
    }
    materialize_locations(tree)
    keys_after = {k for loc in _locations_in_document_order(tree) for k in loc}
    assert keys_after == keys_before >= {"offset", "col", "tokLen", "file", "line"}


def test_deep_tree_does_not_hit_the_recursion_limit():
    node: dict = {"loc": {"offset": 0, "file": "/a.h", "line": 1, "col": 1}}
    root = node
    for _ in range(20_000):
        child: dict = {"loc": {"offset": 0, "col": 1}}
        node["inner"] = [child]
        node = child
    materialize_locations(root)
    assert node["loc"]["file"] == "/a.h"


_DEP = "/usr/include/c++/13/bits/dep.h"
_OWN = "/proj/include/a.h"


def _decl(rng: random.Random, depth: int) -> dict:
    """A declaration: dependency functions (prunable) or project nodes."""
    in_dep = rng.random() < 0.5
    files = [_DEP, "/usr/include/c++/13/bits/other.h"] if in_dep else [_OWN]
    node: dict = {
        "id": hex(rng.randrange(1 << 32)),
        "kind": "FunctionDecl" if in_dep else "CXXRecordDecl",
        "name": "f",
        "loc": {
            "offset": 1,
            "file": files[0],
            "line": rng.randrange(1, 9),
            "col": 1,
        },
    }
    if depth:
        node["inner"] = [
            {
                "kind": "ParmVarDecl",
                "loc": {
                    "offset": 2,
                    "file": rng.choice(files),
                    "line": rng.randrange(1, 9),
                    "col": 2,
                },
            }
            for _ in range(rng.randrange(0, 3))
        ]
    return node


@pytest.mark.parametrize("seed", range(100))
def test_pruning_keeps_the_location_state_of_what_it_removes(seed):
    """A pruned subtree may have written the file/line the next declaration
    inherits; the placeholder must carry that state forward. Oracle: the
    same tree materialized without pruning."""
    import io
    import json

    from abicheck.dumper_clang_streaming import (
        PRUNED_PLACEHOLDER_KIND,
        load_pruned_clang_ast,
    )

    rng = random.Random(seed)
    explicit = {
        "kind": "TranslationUnitDecl",
        "inner": [_decl(rng, 1) for _ in range(8)],
    }
    encoded = _clang_encode(explicit)
    pruned, count = load_pruned_clang_ast(
        io.BytesIO(json.dumps(encoded).encode()), header_roots=(_OWN,)
    )
    materialize_locations(pruned)
    expected = {
        n["id"]: (n["loc"]["file"], n["loc"]["line"])
        for n in materialize_locations(copy.deepcopy(encoded))["inner"]
    }
    for n in pruned["inner"]:
        if n.get("kind") != PRUNED_PLACEHOLDER_KIND:
            assert (n["loc"]["file"], n["loc"]["line"]) == expected[n["id"]], n["id"]
    if seed == 0:
        assert count > 0  # vacuity guard: pruning engaged
