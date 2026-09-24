"""The clang constant/default fingerprint must be checkout-stable.

Bug class: a clang location-bearing spelling (``(lambda at <path>:L:C)``,
``(unnamed struct at ...)``) reaching a fingerprinted slot other than
``type.qualType`` -- a node's own ``name`` (a closure's implicit
``~(lambda at ...)`` destructor), a referenced decl's ``name``, or its
resolved ``qualified_name``. Invariant: relocating the header (every slot
rewritten from one root to another) never changes the fingerprint.
"""

from __future__ import annotations

import copy
import itertools

import pytest

from abicheck.clang_layout_tool import _bare_base_name
from abicheck.dumper_clang_expr import _expr_fingerprint

ROOTS = ["/old/inc", "/new/inc", "/home/ci/build-7/include", "rel/inc", "C:\\src\\inc"]
SPELLINGS = [
    "(lambda at {r}/h.h:4:32)",
    "(unnamed struct at {r}/h.h:9:1)",
    "(unnamed enum at {r}/h.h:2:3)",
]


def _tree(root: str, spelling: str) -> tuple[dict, dict[str, str]]:
    loc = spelling.format(r=root)
    node = {
        "kind": "CallExpr",
        "type": {"qualType": "int"},
        "inner": [
            {"kind": "CXXDestructorDecl", "name": f"~{loc}"},
            {"kind": "MemberExpr", "name": "operator()", "type": {"qualType": loc}},
            {
                "kind": "DeclRefExpr",
                "type": {"qualType": loc},
                "referencedDecl": {
                    "id": "0x1",
                    "kind": "CXXMethodDecl",
                    "name": f"~{loc}",
                    "type": {"qualType": "void ()"},
                },
            },
        ],
    }
    return node, {"0x1": f"spy::detail::{loc}::~{loc}"}


@pytest.mark.parametrize(("a", "b"), list(itertools.combinations(ROOTS, 2)))
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_fingerprint_independent_of_checkout_root(
    a: str, b: str, spelling: str
) -> None:
    na, ia = _tree(a, spelling)
    nb, ib = _tree(b, spelling)
    assert _expr_fingerprint(na, lambda: ia) == _expr_fingerprint(nb, lambda: ib)


def test_fingerprint_still_distinguishes_real_changes() -> None:
    # Oracle: a different referenced name is a different initializer.
    n1, i1 = _tree("/old", SPELLINGS[0])
    n2 = copy.deepcopy(n1)
    n2["inner"][2]["referencedDecl"]["name"] = "other"
    assert _expr_fingerprint(n1, lambda: i1) != _expr_fingerprint(n2, lambda: i1)


@pytest.mark.parametrize(("a", "b"), list(itertools.combinations(ROOTS[:4], 2)))
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_layout_tool_base_key_independent_of_checkout_root(
    a: str, b: str, spelling: str
) -> None:
    ka = _bare_base_name("ns::" + spelling.format(r=a))
    kb = _bare_base_name("ns::" + spelling.format(r=b))
    assert ka == kb
    assert a not in ka
