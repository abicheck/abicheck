"""castxml constructor/destructor placeholders join the real export table
and clang's own node, through the public ``dump`` workflow (evidence-entity-
model plan, Phase 1 gap: castxml ctor/dtor placeholders).

One library is built with g++ and dumped twice with the real CLI -- once with
the castxml header backend (which records no mangling for a constructor or
destructor) and once with clang's (which records the ``C1``/``D1`` spelling).
The oracle is the fixture's ground truth, written by hand below: which
special members g++ exports, and under which Itanium spellings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF fixture with Itanium linker names",
    ),
]

HEADER = """\
#pragma once
namespace ns {
struct W {
    W();
    W(int);
    W(const W&);
    virtual ~W();
    int x;
};
struct I { I() {} ~I() {} int y; };
namespace deep { struct Z { Z(long, double); ~Z(); }; }
template <class T> struct B { B(T); ~B(); T t; };
extern template struct B<int>;
}
struct Top { Top(unsigned long); };
"""
SOURCE = """\
#include "a.h"
namespace ns {
W::W() : x(0) {}
W::W(int v) : x(v) {}
W::W(const W& o) : x(o.x) {}
W::~W() {}
namespace deep { Z::Z(long, double) {} Z::~Z() {} }
template <class T> B<T>::B(T v) : t(v) {}
template <class T> B<T>::~B() {}
template struct B<int>;
}
Top::Top(unsigned long) {}
"""

#: The resolved node of every exported, non-template special member (the
#: complete-object spelling) -> the sibling variants g++ exports for it.
EXPECTED = {
    "decl://_ZN2ns1WC1Ev": {"decl://_ZN2ns1WC2Ev"},
    "decl://_ZN2ns1WC1Ei": {"decl://_ZN2ns1WC2Ei"},
    "decl://_ZN2ns1WC1ERKS0_": {"decl://_ZN2ns1WC2ERKS0_"},
    "decl://_ZN2ns1WD1Ev": {"decl://_ZN2ns1WD0Ev", "decl://_ZN2ns1WD2Ev"},
    "decl://_ZN2ns4deep1ZC1Eld": {"decl://_ZN2ns4deep1ZC2Eld"},
    "decl://_ZN2ns4deep1ZD1Ev": {"decl://_ZN2ns4deep1ZD2Ev"},
    "decl://_ZN3TopC1Em": {"decl://_ZN3TopC2Em"},
}


_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="castxml ctor/dtor placeholders are unresolved (Phase 1 gap)",
)


def _dump(d: Path, frontend: str) -> Path:
    target = d / f"{frontend}.json"
    proc = subprocess.run(
        ["abicheck", "dump", "liba.so", "-H", "a.h", "-o", str(target)],
        cwd=d,
        env={**os.environ, "ABICHECK_AST_FRONTEND": frontend},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return target


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    d = tmp_path_factory.mktemp("special_members")
    (d / "a.h").write_text(HEADER)
    (d / "a.cpp").write_text(SOURCE)
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", "liba.so", "a.cpp"], check=True, cwd=d
    )
    return d


def _load(path: Path):
    from abicheck.serialization import load_snapshot

    return load_snapshot(path)


def _special_member_nodes(snap):
    from abicheck.model.graph_entity_identity import snapshot_identities

    ids = snapshot_identities(snap)
    return {
        i.node_id: set(i.aliases)
        for f, i in zip(snap.functions, ids.functions)
        if i.resolved and ("C1E" in i.node_id or "D1E" in i.node_id)
    }


@_XFAIL
def test_castxml_placeholders_resolve_to_the_exported_variant_families(
    built: Path,
) -> None:
    snap = _load(_dump(built, "castxml"))
    # castxml really recorded placeholders, not manglings -- the premise.
    assert any(f.mangled.startswith("__abicheck_ctor__ns::W(") for f in snap.functions)
    assert _special_member_nodes(snap) == EXPECTED


@_XFAIL
def test_every_special_member_export_joins_a_declaration(built: Path) -> None:
    from abicheck.compare.export_join import join_exports
    from abicheck.model.graph_join import JoinState

    j = join_exports(_load(_dump(built, "castxml")))
    for node, variants in EXPECTED.items():
        rec = j.declaration(node)
        assert rec.state is JoinState.MATCHED, node
        spellings = {c.split("/", 3)[-1] for c in rec.candidates}
        assert {f"decl://{s}" for s in spellings} <= {node, *variants}
    # Inline `ns::I`'s special members have no export: no evidence, no merge.
    unresolved = [
        r
        for r in j.join.left.values()
        if r.subject.startswith("unresolved://") and "ns::I" in r.subject
    ]
    assert unresolved and all(r.state is JoinState.UNMATCHED for r in unresolved)


@_XFAIL
def test_castxml_and_clang_dumps_share_the_special_member_nodes(built: Path) -> None:
    if shutil.which("clang++") is None and shutil.which("clang") is None:
        pytest.skip("clang not available")
    castxml_nodes = set(_special_member_nodes(_load(_dump(built, "castxml"))))
    clang_nodes = set(_special_member_nodes(_load(_dump(built, "clang"))))
    assert set(EXPECTED) <= castxml_nodes
    assert set(EXPECTED) <= clang_nodes
