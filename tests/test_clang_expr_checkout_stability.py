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


@pytest.mark.integration
def test_real_clang_lambda_initializer_fingerprint_is_checkout_stable(tmp_path) -> None:
    """Real ``clang -ast-dump=json`` output, the same header under two roots."""
    import json
    import shutil
    import subprocess

    from abicheck.dumper_clang_expr import _index_decl_id_qualified_names

    clang = shutil.which("clang++") or shutil.which("clang")
    if clang is None:
        pytest.skip("clang not available")
    src = "namespace spy { namespace detail {\nstatic constexpr int width = []() { int n = 0; for (int i = 0; i < 3; ++i) n += i; return n; }();\n}}\n"

    def find(n):
        if isinstance(n, dict):
            if n.get("kind") == "VarDecl" and n.get("name") == "width":
                return n
            for c in n.get("inner") or []:
                r = find(c)
                if r:
                    return r
        return None

    prints = []
    for side in ("old", "new"):
        h = tmp_path / side / "inc" / "h.h"
        h.parent.mkdir(parents=True)
        h.write_text(src)
        out = subprocess.run(
            [
                clang,
                "-std=c++20",
                "-x",
                "c++",
                "-fsyntax-only",
                "-Xclang",
                "-ast-dump=json",
                str(h),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        root = json.loads(out)
        assert (
            json.dumps(str(h))[1:-1] in out
        )  # leak source present (JSON-escaped, so Windows too)
        idx = _index_decl_id_qualified_names(root)
        prints.append(_expr_fingerprint(find(root)["inner"][0], lambda idx=idx: idx))
    assert prints[0] == prints[1]


def test_unnamed_referenced_decl_keeps_type_only_stub() -> None:
    # A referenced decl without "name" (e.g. an implicit one) still fingerprints by type.
    def ref(t: str) -> dict:
        return {
            "kind": "DeclRefExpr",
            "referencedDecl": {"kind": "VarDecl", "type": {"qualType": t}},
        }

    assert _expr_fingerprint(ref("int")) != _expr_fingerprint(ref("long"))
    assert _expr_fingerprint(ref("(lambda at /a/h.h:1:2)")) == _expr_fingerprint(
        ref("(lambda at /b/h.h:1:2)")
    )
