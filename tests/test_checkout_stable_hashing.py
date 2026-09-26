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

import pytest

from abicheck.buildsource.source_extractors.clang_nodes import (
    _signature,
    _subtree_hash,
)
from abicheck.model.graph_entity_identity import signature_key
from abicheck.workflows.ownership_request import (
    with_target_roots,
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


def _ownership_fingerprint(headers, public_dirs=()):
    from abicheck.extract.ownership_stamp import recorded_rules
    from abicheck.model.extraction_scope import ExtractionScope

    request = with_target_roots(None, headers, public_dirs)
    return ExtractionScope(ownership_rules=recorded_rules(request)).fingerprint


@pytest.mark.parametrize(
    "old_base, new_base",
    [
        ("inc_old", "inc_new"),  # sibling copies in one checkout
        ("rel-1/include", "rel-2/include"),  # two release trees
        ("a/b/c/include", "x/include"),  # different depths
        ("same", "same"),
    ],
)
@pytest.mark.parametrize("layout", [("",), ("pub", "ext"), ("pub", "pub/sub")])
def test_same_header_layout_is_one_ownership_rule_per_side(
    tmp_path, old_base, new_base, layout
):
    """ADR-075: a side's roots are recorded against its own operand anchor,
    so where a release tree sits on disk never reads as a rule change."""
    (tmp_path / ".git").mkdir()  # a shared checkout must not be the anchor

    def roots(base):
        dirs = [tmp_path / base / rel for rel in layout]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return dirs

    assert _ownership_fingerprint(roots(old_base)) == _ownership_fingerprint(
        roots(new_base)
    )


def test_a_different_header_layout_is_still_a_different_rule(tmp_path):
    for rel in ("o/pub", "o/ext", "n/pub", "n/other"):
        (tmp_path / rel).mkdir(parents=True)
    old = _ownership_fingerprint([tmp_path / "o/pub", tmp_path / "o/ext"])
    new = _ownership_fingerprint([tmp_path / "n/pub", tmp_path / "n/other"])
    assert old != new
    # One root versus two is a different rule too.
    assert _ownership_fingerprint([tmp_path / "n/pub"]) != new
