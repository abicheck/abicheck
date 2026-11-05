# Copyright 2026 Nikolay Petrov
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

"""A base spelled relative to its scope resolves on every clang release, and
an unresolvable base never reads as a confirmed "no vtable".

Bug class ``extraction.clang_ast_shape_version_drift`` (sibling of
``test_clang_ast_shape_version_drift.py``): clang <= 21 gave a base specifier
``class D : public detail::I`` inside ``namespace lib`` a
``desugaredQualType: "lib::detail::I"``; clang 22 removed ``ElaboratedType``
and writes only ``qualType: "detail::I"`` (an alias base's
``desugaredQualType`` is now relative to the alias's scope). The vtable walk
keyed bases on that spelling, so on LLVM 23.1.5 the base did not resolve and
a class with only overrides got ``vtable_fact`` PRESENT and EMPTY -- a
confirmed "no vtable" for a polymorphic class (catalog case76).

Oracles, none of them the resolver's own helpers:

* every fixture is emitted in *both* JSON shapes by :func:`_bases_json`,
  written from ``JSONNodeDumper``'s behaviour, and the expected vtable is a
  hand-written mangled-name list;
* the lookup property test computes what C++ name lookup finds from an
  explicit per-scope member table, by enumeration;
* the live probe runs the clang on ``PATH`` (clang 18 and the pinned newest
  LLVM in ``clang-latest.yml``'s two legs).
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.clang.base_resolution import BaseResolver
from abicheck.model.fact import FactStatus

_SHAPES = ("clang18", "clang22")
_FILE = "include/lib.h"


def _bases_json(
    shape: str, written: str, canonical: str, alias: tuple[str, str] | None = None
) -> dict[str, Any]:
    """One ``bases`` entry as clang *shape* writes it.

    *alias* is ``(alias decl id, alias target as written in the alias)``.
    clang <= 21: ``desugaredQualType`` is the fully qualified *canonical*
    spelling whenever it differs from the written one. clang >= 22: a
    non-alias base has no ``desugaredQualType``; an alias base's is the
    target as the alias wrote it.
    """
    type_obj: dict[str, Any] = {"qualType": written}
    if alias is not None:
        type_obj["typeAliasDeclId"] = alias[0]
    if shape == "clang18":
        if canonical != written:
            type_obj["desugaredQualType"] = canonical
    elif alias is not None:
        type_obj["desugaredQualType"] = alias[1]
    return {"type": type_obj, "access": "public", "isVirtual": False}


def _ns(name: str, *inner: dict) -> dict:
    return {"kind": "NamespaceDecl", "name": name, "inner": list(inner)}


def _cls(name: str, *inner: dict, bases: list[dict] | None = None) -> dict:
    node: dict[str, Any] = {
        "kind": "CXXRecordDecl",
        "name": name,
        "tagUsed": "struct",
        "loc": {"file": _FILE, "line": 1},
        "completeDefinition": True,
        "inner": list(inner),
    }
    if bases:
        node["bases"] = bases
    return node


def _vmethod(name: str, mangled: str, *, override: bool = False) -> dict:
    node: dict[str, Any] = {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": mangled,
        "type": {"qualType": "int ()"},
        "inner": [{"kind": "OverrideAttr"}] if override else [],
    }
    if not override:
        node["virtual"] = True
    return node


def _alias(alias_id: str, name: str, target: str) -> dict:
    return {
        "kind": "TypeAliasDecl",
        "id": alias_id,
        "name": name,
        "type": {"qualType": target},
    }


def _types(root: dict) -> dict[str, Any]:
    return {
        (t.qualified_name or t.name): t
        for t in _ClangAstParser(root, set(), set()).parse_types()
    }


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


# Each case: (root builder taking the shape, derived qualname, expected vtable).
def _relative_nested(shape: str) -> dict:
    # case76's shape: `class D : public detail::I` inside `namespace lib`.
    return _tu(
        _ns(
            "lib",
            _ns("detail", _cls("I", _vmethod("run", "_ZN3lib6detail1I3runEv"))),
            _cls(
                "D",
                _vmethod("run", "_ZN3lib1D3runEv", override=True),
                bases=[_bases_json(shape, "detail::I", "lib::detail::I")],
            ),
        )
    )


def _same_namespace(shape: str) -> dict:
    return _tu(
        _ns(
            "lib",
            _cls("A", _vmethod("f", "_ZN3lib1A1fEv")),
            _cls(
                "C",
                _vmethod("f", "_ZN3lib1C1fEv", override=True),
                bases=[_bases_json(shape, "A", "lib::A")],
            ),
        )
    )


def _shadowed(shape: str) -> dict:
    # An inner `detail` hides the outer one: lookup must pick lib::a::detail.
    return _tu(
        _ns(
            "lib",
            _ns("detail", _cls("I", _vmethod("x", "_ZN3lib6detail1I1xEv"))),
            _ns(
                "a",
                _ns("detail", _cls("I", _vmethod("h", "_ZN3lib1a6detail1I1hEv"))),
                _cls(
                    "S",
                    _vmethod("h", "_ZN3lib1a1S1hEv", override=True),
                    bases=[_bases_json(shape, "detail::I", "lib::a::detail::I")],
                ),
            ),
        )
    )


def _alias_elsewhere(shape: str) -> dict:
    # `namespace other { using OA = detail::I; }` then `struct V : other::OA`.
    return _tu(
        _ns(
            "lib",
            _ns("detail", _cls("I", _vmethod("run", "_ZN3lib6detail1I3runEv"))),
            _ns("other", _alias("0xa1", "OA", "detail::I")),
            _cls(
                "V",
                _vmethod("run", "_ZN3lib1V3runEv", override=True),
                bases=[
                    _bases_json(
                        shape, "other::OA", "lib::detail::I", ("0xa1", "detail::I")
                    )
                ],
            ),
        )
    )


def _enclosing_class_member(shape: str) -> dict:
    # A base nested in an enclosing class: `struct Outer { struct In {...};
    # struct D : In {...}; };`.
    return _tu(
        _ns(
            "lib",
            _cls(
                "Outer",
                _cls("In", _vmethod("g", "_ZN3lib5Outer2In1gEv")),
                _cls(
                    "D",
                    _vmethod("g", "_ZN3lib5Outer1D1gEv", override=True),
                    bases=[_bases_json(shape, "In", "lib::Outer::In")],
                ),
            ),
        )
    )


def _global_anchored(shape: str) -> dict:
    return _tu(
        _ns("lib", _cls("A", _vmethod("f", "_ZN3lib1A1fEv"))),
        _ns(
            "app",
            _cls(
                "G",
                _vmethod("f", "_ZN3app1G1fEv", override=True),
                bases=[_bases_json(shape, "::lib::A", "lib::A")],
            ),
        ),
    )


_RESOLVABLE = {
    "relative_nested": (_relative_nested, "lib::D", ["_ZN3lib1D3runEv"]),
    "same_namespace": (_same_namespace, "lib::C", ["_ZN3lib1C1fEv"]),
    "shadowed": (_shadowed, "lib::a::S", ["_ZN3lib1a1S1hEv"]),
    "alias_elsewhere": (_alias_elsewhere, "lib::V", ["_ZN3lib1V3runEv"]),
    "enclosing_class_member": (
        _enclosing_class_member,
        "lib::Outer::D",
        ["_ZN3lib5Outer1D1gEv"],
    ),
    "global_anchored": (_global_anchored, "app::G", ["_ZN3app1G1fEv"]),
}


@pytest.mark.parametrize("shape", _SHAPES)
@pytest.mark.parametrize("case", sorted(_RESOLVABLE))
def test_relative_base_vtable_is_identical_in_both_shapes(
    case: str, shape: str
) -> None:
    build, derived, expected = _RESOLVABLE[case]
    rec = _types(build(shape))[derived]
    assert rec.vtable == expected
    assert rec.vtable_fact.status is FactStatus.PRESENT
    assert rec.vtable_fact.value == expected
    assert rec.vptr_offset_bits == 0


def test_clang22_shape_really_drops_the_desugared_spelling() -> None:
    """Guard the oracle: the clang 22 fixture is not secretly the old one."""
    base = _bases_json("clang22", "detail::I", "lib::detail::I")
    assert "desugaredQualType" not in base["type"]
    alias = _bases_json("clang22", "other::OA", "lib::detail::I", ("0xa1", "detail::I"))
    assert alias["type"]["desugaredQualType"] == "detail::I"


def _unresolvable(shape: str) -> dict:
    # `detail::Missing` is declared nowhere in the TU.
    return _tu(
        _ns(
            "lib",
            _cls(
                "D",
                _vmethod("run", "_ZN3lib1D3runEv", override=True),
                bases=[_bases_json(shape, "detail::Missing", "lib::detail::Missing")],
            ),
        )
    )


def _forward_only(shape: str) -> dict:
    fwd = {
        "kind": "CXXRecordDecl",
        "name": "I",
        "tagUsed": "struct",
        "loc": {"file": _FILE, "line": 1},
    }
    return _tu(
        _ns(
            "lib",
            fwd,
            _cls(
                "D",
                _vmethod("run", "_ZN3lib1D3runEv", override=True),
                bases=[_bases_json(shape, "I", "lib::I")],
            ),
        )
    )


def _unresolvable_grandbase(shape: str) -> dict:
    # D's own base resolves; that base's base does not.
    return _tu(
        _ns(
            "lib",
            _cls(
                "M",
                _vmethod("m", "_ZN3lib1M1mEv"),
                bases=[_bases_json(shape, "Gone", "lib::Gone")],
            ),
            _cls(
                "D",
                _vmethod("m", "_ZN3lib1D1mEv", override=True),
                bases=[_bases_json(shape, "M", "lib::M")],
            ),
        )
    )


@pytest.mark.parametrize("shape", _SHAPES)
@pytest.mark.parametrize(
    "build", [_unresolvable, _forward_only, _unresolvable_grandbase]
)
def test_unresolved_base_never_reads_as_a_confirmed_vtable(build, shape: str) -> None:
    rec = _types(build(shape))["lib::D"]
    assert rec.vtable_fact.status is FactStatus.PARTIAL
    assert rec.vtable_fact.status is not FactStatus.PRESENT
    assert any("unresolved base" in d for d in rec.vtable_fact.diagnostics)


# ── C++ lookup property, against an independent enumeration ──────────────

#: A small namespace tree: scope -> names it declares (records and
#: namespaces alike). Shadowing (`detail` at two depths, `I` at three) is
#: deliberate.
_MEMBERS: dict[tuple[str, ...], set[str]] = {
    (): {"lib", "I"},
    ("lib",): {"detail", "a", "I"},
    ("lib", "detail"): {"I"},
    ("lib", "a"): {"detail", "K"},
    ("lib", "a", "detail"): {"I"},
}
_RECORDS = ["I", "lib::I", "lib::detail::I", "lib::a::K", "lib::a::detail::I"]


def _cxx_lookup(spelling: str, scope: tuple[str, ...]) -> str | None:
    """[basic.lookup.unqual] + [basic.lookup.qual] over :data:`_MEMBERS`."""
    parts = spelling.split("::")
    for depth in range(len(scope), -1, -1):
        here = scope[:depth]
        if parts[0] in _MEMBERS.get(here, set()):
            path = (*here, parts[0])
            for part in parts[1:]:
                if part not in _MEMBERS.get(path, set()):
                    return None
                path = (*path, part)
            name = "::".join(path)
            return name if name in _RECORDS else None
    return None


def _all_spellings() -> list[str]:
    names = ["lib", "detail", "a", "I", "K"]
    return ["::".join(p) for n in (1, 2, 3) for p in itertools.product(names, repeat=n)]


@pytest.mark.parametrize("scope", sorted(_MEMBERS))
def test_resolver_matches_cxx_lookup_exhaustively(scope: tuple[str, ...]) -> None:
    resolver = BaseResolver(_RECORDS)
    derived = "::".join([*scope, "Derived"])
    for spelling in _all_spellings():
        base = {"type": {"qualType": spelling}}
        expected = _cxx_lookup(spelling, scope)
        # A spelling that is itself a record key is taken as written (the
        # clang <= 21 fully qualified form); lookup decides everything else.
        if spelling in _RECORDS:
            expected = spelling if expected is None else expected
            got = resolver.resolve(base, derived)
            assert got in {spelling, expected}, (spelling, scope, got)
            continue
        assert resolver.resolve(base, derived) == expected, (spelling, scope)


# ── live probe: the clang on PATH (both clang-latest.yml legs) ────────────

_PROBE = """
namespace lib {
namespace detail { struct I { virtual ~I(); virtual int run() = 0; }; }
namespace other { using OA = detail::I; }
struct D : public detail::I { int run() override; };
struct V : other::OA { int run() override; };
namespace a {
namespace detail { struct I { virtual void h(); }; }
struct S : detail::I { void h() override; };
}
template <class T> struct Dep : T { virtual void g(); };
}
"""


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang++") is None, reason="clang++ not on PATH")
def test_live_clang_resolves_relative_bases(tmp_path: Path) -> None:
    header = tmp_path / "lib.h"
    header.write_text(_PROBE, encoding="utf-8")
    out = subprocess.run(
        [
            "clang++",
            "-x",
            "c++",
            "-std=c++17",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    types = _types(json.loads(out))
    expected = {
        "lib::D": ["_ZN3lib1DD1Ev", "_ZN3lib1D3runEv"],
        "lib::V": ["_ZN3lib1VD1Ev", "_ZN3lib1V3runEv"],
        "lib::a::S": ["_ZN3lib1a1S1hEv"],
    }
    for name, vtable in expected.items():
        rec = types[name]
        assert rec.vtable == vtable, name
        assert rec.vtable_fact.status is FactStatus.PRESENT, name
    # A dependent base (`T`) cannot be resolved: unknown, never confirmed.
    assert types["lib::Dep"].vtable_fact.status is FactStatus.PARTIAL
