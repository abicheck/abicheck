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

"""clang-version AST-shape drift in the clang header backend.

Bug class ``extraction.clang_ast_shape_version_drift``: a reader keyed on one
clang release's JSON AST shape reads another release's shape as *absent*, so
a newer clang silently produces a smaller surface instead of an error.

Two instances, both found by the ``clang-latest`` CI lane on LLVM 23.1.5
while every clang-18 lane was green:

1. ``typedef struct {…} T;`` / ``typedef enum {…} T;``: clang <= 21 links the
   typedef to its unnamed tag through an ``ElaboratedType`` node's
   ``ownedTagDecl``; clang 22 removed ``ElaboratedType`` and marks the
   ``RecordType``/``EnumType`` node itself ``"isTagOwned": true`` beside its
   ``decl`` (``clang/lib/AST/JSONNodeDumper.cpp`` ``VisitTagType``). Every
   typedef'd anonymous record/enum vanished on clang 22+.
2. The opt-in streaming pruner collapsed a dependency ``FunctionDecl``/
   ``VarDecl`` whose subtree holds a class (a lambda's closure type, a local
   class), erasing method-shaped nodes its contract says are never pruned.

The oracle for (1) is the shape transform :func:`_to_clang22_shape`, written
from the LLVM source, not from the reader: every parse result must be the
same whichever shape the AST arrives in.
"""

from __future__ import annotations

import copy
import io
import json
from typing import Any

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.clang.owned_tag import owned_tag_decl
from abicheck.extract.headers.clang.streaming import (
    PRUNED_PLACEHOLDER_KIND,
    DependencyDeclPruningHook,
    load_pruned_clang_ast,
)

_TAG_TYPE_FOR_DECL = {
    "RecordDecl": "RecordType",
    "CXXRecordDecl": "RecordType",
    "EnumDecl": "EnumType",
}


def _to_clang22_shape(node: Any) -> Any:
    """Rewrite a clang <= 21 JSON AST into clang >= 22's shape: every
    ``ElaboratedType`` that owns its tag becomes the tag type node itself
    (``RecordType``/``EnumType``) with ``decl`` = the owned decl ref and
    ``isTagOwned: true`` -- what ``JSONNodeDumper::VisitTagType`` writes."""
    if isinstance(node, list):
        return [_to_clang22_shape(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _to_clang22_shape(v) for k, v in node.items() if k != "ownedTagDecl"}
    owned = node.get("ownedTagDecl")
    if node.get("kind") == "ElaboratedType" and isinstance(owned, dict):
        out["kind"] = _TAG_TYPE_FOR_DECL[owned["kind"]]
        out["decl"] = copy.deepcopy(owned)
        out["isTagOwned"] = True
        out.pop("inner", None)  # the wrapped tag type node was this one
    return out


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _anon_enum(eid: str, file: str, names: list[str]) -> dict:
    return {
        "kind": "EnumDecl",
        "id": eid,
        "name": "",
        "loc": {"file": file, "line": 1},
        "inner": [{"kind": "EnumConstantDecl", "name": n} for n in names],
    }


def _anon_record(rid: str, file: str, fields: list[str]) -> dict:
    return {
        "kind": "RecordDecl",
        "id": rid,
        "name": "",
        "tagUsed": "struct",
        "completeDefinition": True,
        "loc": {"file": file, "line": 1},
        "inner": [
            {"kind": "FieldDecl", "name": f, "type": {"qualType": "int"}}
            for f in fields
        ],
    }


def _owning_typedef(name: str, decl_kind: str, decl_id: str, file: str) -> dict:
    """clang <= 21 shape: ElaboratedType{ownedTagDecl} wrapping the tag type."""
    tag_type = _TAG_TYPE_FOR_DECL[decl_kind]
    return {
        "kind": "TypedefDecl",
        "name": name,
        "loc": {"file": file, "line": 2},
        "type": {"qualType": name},
        "inner": [
            {
                "kind": "ElaboratedType",
                "ownedTagDecl": {"id": decl_id, "kind": decl_kind, "name": ""},
                "inner": [
                    {
                        "kind": tag_type,
                        "decl": {"id": decl_id, "kind": decl_kind, "name": ""},
                    }
                ],
            }
        ],
    }


def _named_typedef(
    name: str, decl_kind: str, decl_id: str, tag: str, file: str
) -> dict:
    """``typedef struct Tag Name;`` -- the typedef does not own the tag, in
    either clang shape (no ``ownedTagDecl``; no ``isTagOwned``)."""
    return {
        "kind": "TypedefDecl",
        "name": name,
        "loc": {"file": file, "line": 3},
        "type": {"qualType": f"struct {tag}"},
        "inner": [
            {
                "kind": "ElaboratedType",
                "inner": [
                    {
                        "kind": _TAG_TYPE_FOR_DECL[decl_kind],
                        "decl": {"id": decl_id, "kind": decl_kind, "name": tag},
                    }
                ],
            }
        ],
    }


# Several independent header shapes, each with the surface it must yield.
_CORPUS: dict[str, tuple[dict, set[str], set[str]]] = {
    "c_enum_and_struct": (
        _tu(
            _anon_enum("0xe1", "api.h", ["A", "B"]),
            _owning_typedef("level_t", "EnumDecl", "0xe1", "api.h"),
            _anon_record("0xr1", "api.h", ["x", "y"]),
            _owning_typedef("point_t", "RecordDecl", "0xr1", "api.h"),
        ),
        {"point_t"},
        {"level_t"},
    ),
    "two_records_two_enums": (
        _tu(
            _anon_record("0xr1", "a.h", ["w"]),
            _owning_typedef("size_t2", "RecordDecl", "0xr1", "a.h"),
            _anon_record("0xr2", "b.h", ["p", "q", "r"]),
            _owning_typedef("box_t", "RecordDecl", "0xr2", "b.h"),
            _anon_enum("0xe1", "a.h", ["ON"]),
            _owning_typedef("mode_t2", "EnumDecl", "0xe1", "a.h"),
            _anon_enum("0xe2", "b.h", ["R", "G", "B"]),
            _owning_typedef("color_t", "EnumDecl", "0xe2", "b.h"),
        ),
        {"size_t2", "box_t"},
        {"mode_t2", "color_t"},
    ),
    # Negative control: a typedef naming an existing *named* tag must not
    # rename anything, and an anonymous tag nobody owns stays dropped.
    "named_tag_alias_and_unowned_anon": (
        _tu(
            {
                "kind": "RecordDecl",
                "id": "0xnamed",
                "name": "node",
                "tagUsed": "struct",
                "completeDefinition": True,
                "loc": {"file": "c.h", "line": 1},
                "inner": [
                    {"kind": "FieldDecl", "name": "v", "type": {"qualType": "int"}}
                ],
            },
            _named_typedef("node_t", "RecordDecl", "0xnamed", "node", "c.h"),
            _anon_record("0xorphan", "c.h", ["z"]),
            _anon_enum("0xeorphan", "c.h", ["Q"]),
        ),
        {"node"},
        set(),
    ),
}


def _surface(root: dict) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    parser = _ClangAstParser(root, set(), set())
    records = {t.name: [f.name for f in t.fields] for t in parser.parse_types()}
    enums = {e.name: [m.name for m in e.members] for e in parser.parse_enums()}
    return records, enums


@pytest.mark.parametrize("case", sorted(_CORPUS))
def test_surface_is_identical_in_both_clang_ast_shapes(case: str) -> None:
    root, want_records, want_enums = _CORPUS[case]
    old = _surface(root)
    new = _surface(_to_clang22_shape(root))
    assert set(old[0]) == want_records
    assert set(old[1]) == want_enums
    assert new == old


def test_shape_transform_really_removes_the_old_key() -> None:
    """Guards the oracle: a transform that left ``ownedTagDecl`` in place
    would make the parity test above pass against the old reader."""
    root, _, _ = _CORPUS["two_records_two_enums"]
    text = json.dumps(_to_clang22_shape(root))
    assert "ownedTagDecl" not in text
    assert "ElaboratedType" not in text
    assert text.count('"isTagOwned": true') == 4


_DECL = {"id": "0x1", "kind": "RecordDecl", "name": ""}


@pytest.mark.parametrize("owned_key", ["absent", "dict", "string"])
@pytest.mark.parametrize("is_tag_owned", ["absent", True, False, "true"])
@pytest.mark.parametrize("decl_key", ["absent", "dict"])
def test_owned_tag_decl_exhaustive_small_domain(
    owned_key: str, is_tag_owned: Any, decl_key: str
) -> None:
    """Every combination of the three keys either shape can carry. Oracle,
    stated independently of the implementation: a dict ``ownedTagDecl`` is
    the owned tag; otherwise the ``decl`` dict is, iff ``isTagOwned`` is
    exactly JSON ``true``; nothing else owns a tag."""
    node: dict[str, Any] = {"kind": "X"}
    owned = {"id": "0xowned", "kind": "EnumDecl", "name": ""}
    if owned_key == "dict":
        node["ownedTagDecl"] = owned
    elif owned_key == "string":
        node["ownedTagDecl"] = "0xowned"
    if is_tag_owned != "absent":
        node["isTagOwned"] = is_tag_owned
    if decl_key == "dict":
        node["decl"] = _DECL
    if owned_key == "dict":
        expected = owned
    elif is_tag_owned is True and decl_key == "dict":
        expected = _DECL
    else:
        expected = None
    assert owned_tag_decl(node) == expected


@pytest.mark.parametrize(
    "node", [None, "0xowned", 7, ["ownedTagDecl"], ("isTagOwned", True)]
)
def test_owned_tag_decl_of_a_non_object_node_is_none(node: Any) -> None:
    """Readers pass ``inner`` children through unchecked; a malformed child
    (not a JSON object) owns no tag rather than raising."""
    assert owned_tag_decl(node) is None


# --- streaming pruner: a class inside a prunable subtree --------------------

_DEP = "/usr/include/c++/14/bits/ranges_util.h"
_PROJECT = "/home/user/project/api.h"
_METHOD_KINDS = (
    "CXXMethodDecl",
    "CXXConstructorDecl",
    "CXXDestructorDecl",
    "CXXConversionDecl",
)


def _closure(method_kind: str) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "tagUsed": "class",
        "loc": {"file": _DEP, "line": 5},
        "inner": [{"kind": method_kind, "name": "operator()", "loc": {"line": 5}}],
    }


def _holder(kind: str, payload: dict | None) -> dict:
    node: dict[str, Any] = {
        "kind": kind,
        "id": f"0x{kind}",
        "name": "f",
        "loc": {"file": _DEP, "line": 4},
    }
    if payload is not None:
        node["inner"] = [{"kind": "CompoundStmt", "inner": [payload]}]
    return node


@pytest.mark.parametrize("holder", ["FunctionDecl", "VarDecl"])
@pytest.mark.parametrize("method_kind", _METHOD_KINDS)
def test_pruner_keeps_a_prunable_node_holding_a_method(
    holder: str, method_kind: str
) -> None:
    hook = DependencyDeclPruningHook((_PROJECT,))
    node = _holder(holder, _closure(method_kind))
    kept = hook(list(node.items()))
    assert kept["kind"] == holder
    assert hook.pruned_count == 0


@pytest.mark.parametrize("holder", ["FunctionDecl", "VarDecl"])
def test_pruner_still_prunes_the_same_node_without_a_class(holder: str) -> None:
    """Negative control: the method rule must not disable pruning."""
    hook = DependencyDeclPruningHook((_PROJECT,))
    node = _holder(holder, {"kind": "ReturnStmt"})
    pruned = hook(list(node.items()))
    assert pruned["kind"] == PRUNED_PLACEHOLDER_KIND
    assert hook.pruned_count == 1


def test_pruned_load_conserves_every_method_shaped_node() -> None:
    """End to end through :func:`load_pruned_clang_ast`: the count of
    method-shaped nodes is identical with and without pruning, over a TU
    mixing prunable holders with and without nested classes."""
    tu = _tu(
        *(_holder("FunctionDecl", _closure(k)) for k in _METHOD_KINDS),
        *(_holder("VarDecl", _closure(k)) for k in _METHOD_KINDS),
        _holder("FunctionDecl", None),
        _holder("VarDecl", {"kind": "IntegerLiteral"}),
    )

    def count(value: Any) -> int:
        if isinstance(value, dict):
            own = 1 if value.get("kind") in _METHOD_KINDS else 0
            return own + sum(count(v) for v in value.values())
        if isinstance(value, list):
            return sum(count(v) for v in value)
        return 0

    raw = json.dumps(tu).encode()
    pruned_root, pruned_count = load_pruned_clang_ast(
        io.BytesIO(raw), header_roots=(_PROJECT,)
    )
    assert pruned_count == 2  # the two class-free holders
    assert count(pruned_root) == count(json.loads(raw)) == 2 * len(_METHOD_KINDS)
