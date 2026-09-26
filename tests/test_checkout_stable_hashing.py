# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Hashes of clang spellings must not depend on where the checkout lives.

Oracle: a spelling's checkout-independent content is (marker kind, header
basename, line, col) plus the surrounding text -- computed here by the
test, not by the normalizer under test. Two spellings that agree on it must
hash equal (must-merge); any disagreement must hash differently
(must-not-merge).
"""

from __future__ import annotations

import itertools
import random

from abicheck.buildsource.source_extractors.clang_nodes import (
    _signature,
    _subtree_hash,
)
from abicheck.model.graph_entity_identity import signature_key
from abicheck.workflows.ownership_request import (
    find_checkout_root,
    shared_checkout_root,
)

_PREFIXES = ["/w/old", "/home/u/src/new tree", "/mnt/cached/a/b/c", "/tmp/x"]


def _spelling(
    prefix: str, marker: str, rel: str, line: int, col: int, wrap: str
) -> str:
    return wrap.format(f"({marker} at {prefix}/{rel}:{line}:{col})")


_WRAPS = ["{}", "std::shared_ptr<{}> (int)", "Holder<{}, int>", "void ({} &&)"]


def _cases(seed: int = 7, n: int = 60):
    rng = random.Random(seed)
    for _ in range(n):
        yield (
            rng.choice(["lambda", "unnamed struct", "anonymous union"]),
            rng.choice(["include/svs/lib/tuples.h", "a.hpp", "x/y/z.h"]),
            rng.randint(1, 400),
            rng.randint(1, 80),
            rng.choice(_WRAPS),
        )


def test_signature_key_merges_relocations_and_separates_everything_else():
    cases = list(_cases())
    keys = {}
    for case in cases:
        for prefix in _PREFIXES:
            keys.setdefault(case, set()).add(signature_key(_spelling(prefix, *case)))
    # Must-merge: one key per case, whatever the checkout prefix.
    assert all(len(v) == 1 for v in keys.values())

    # Must-not-merge: the oracle content (marker, basename, line, col, wrap).
    def oracle(c):
        return (c[0], c[1].rsplit("/", 1)[-1], c[2], c[3], c[4])

    for a, b in itertools.combinations(set(cases), 2):
        if oracle(a) != oracle(b):
            assert keys[a] != keys[b], (a, b)


def _fn(qual: str, body_value: str) -> dict:
    return {
        "kind": "FunctionDecl",
        "type": {"qualType": qual},
        "inner": [
            {
                "kind": "CompoundStmt",
                "inner": [
                    {
                        "kind": "CallExpr",
                        "type": {"qualType": qual},
                        "inner": [
                            {
                                "kind": "DeclRefExpr",
                                "referencedDecl": {"id": "0x1", "name": qual},
                                "type": {"qualType": qual},
                            },
                            {"kind": "IntegerLiteral", "value": body_value},
                        ],
                    }
                ],
            }
        ],
    }


def test_body_fingerprint_is_relocation_invariant_but_sees_real_edits():
    for case in _cases(seed=11, n=25):
        spellings = [_spelling(p, *case) for p in _PREFIXES]
        hashes = {_subtree_hash(_fn(s, "2")["inner"][0]) for s in spellings}
        sigs = {_signature(_fn(s, "2")) for s in spellings}
        assert len(hashes) == 1 and len(sigs) == 1
        edited = _subtree_hash(_fn(spellings[0], "3")["inner"][0])
        assert edited not in hashes
        moved = _spelling(_PREFIXES[0], case[0], case[1], case[2] + 1, case[3], case[4])
        assert _subtree_hash(_fn(moved, "2")["inner"][0]) not in hashes


def test_plain_spellings_are_untouched():
    for s in [
        "int (int)",
        "std::vector<Category>",
        "static_cast<at>",
        'Tag<"(lambda at /a/b.h:1:2)">',
    ]:
        assert _signature({"type": {"qualType": s}}) == s


def test_checkout_root_lookup(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "include/x").mkdir(parents=True)
    (b / "include").mkdir(parents=True)
    (a / ".git").mkdir()
    (b / ".git").write_text("gitdir: elsewhere\n")  # a worktree marker is a file
    assert find_checkout_root(a / "include/x") == str(a)
    assert find_checkout_root(b / "include") == str(b)
    assert shared_checkout_root([str(a / "include"), str(a / "include/x")]) == str(a)
    # Spanning two checkouts, or leaving one, anchors nothing.
    assert shared_checkout_root([str(a / "include"), str(b / "include")]) is None
    assert shared_checkout_root([]) is None
    outside = tmp_path.parent / "no_vcs_here_xyz"
    assert find_checkout_root(outside) in (None, find_checkout_root(tmp_path.parent))
