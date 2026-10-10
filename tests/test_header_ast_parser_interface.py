# SPDX-License-Identifier: Apache-2.0
"""The attributes production reads off a header parser by name, and what
the shared SemanticIR normalization does when one is missing.

Bug class: a duck-typed ``getattr(parser, "_root", None)`` reader whose
attribute was deleted from one parser keeps running on the default. Stage D
of the dead-code plan removed ``_CastxmlParser._root``/``_pub_header_segs``/
``_pub_dir_segs``; ``parse_header_ast_fields`` then keyed every castxml
parse in a request on ``id(None)``, so a live ``compare`` gave the NEW side
the OLD side's SemanticIR and a grown record lost its ``type_size_changed``
(``tests/test_contract_type_identities_integration.py`` and
``test_release_public_surface_integration.py`` caught it on real binaries).

Two invariants, each failing on that state:

* every attribute ``abicheck/`` reads with ``getattr(parser, "<name>")`` is
  set on the parser by some ``setattr(parser, "<name>", ...)``, carried by
  both concrete parsers, or listed below as backend-specific with a reason;
* under a shared acquisition scope each parse's SemanticIR equals the one it
  gets alone (the oracle: the same call outside any scope), whatever subset
  of the scope attributes its parser exposes, and only parses of one AST
  share.
"""

from __future__ import annotations

import ast
import functools
import itertools
from pathlib import Path
from xml.etree.ElementTree import Element

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.header_ast_fields import (
    AST_SCOPE_ATTRIBUTES,
    parse_header_ast_fields,
)
from abicheck.extract.headers.castxml.dumper import _CastxmlParser
from abicheck.model.entities import RecordType
from abicheck.model.identity import entity_id_for_type
from abicheck.storage.header_ast_cache import ast_acquisition_scope

ROOT = Path(__file__).resolve().parent.parent

#: Read by name but carried by one backend only, on purpose.
BACKEND_SPECIFIC = {
    "_target_triple": "clang records the probed triple; castxml's AST root is per-target already",
    "_is_cxx": "clang parses C and C++ ASTs; castxml always parses C++",
}


@functools.cache
def _trees() -> list[ast.AST]:
    return [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "abicheck").rglob("*.py"))
    ]


def _named_attribute_calls(call: str, *, receiver: str | None) -> set[str]:
    """String attribute names passed to ``call(<receiver>, "<name>", ...)``;
    any receiver when *receiver* is ``None``."""
    names: set[str] = set()
    for tree in _trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == call
                and len(node.args) >= 2
                and (
                    receiver is None
                    or (
                        isinstance(node.args[0], ast.Name)
                        and node.args[0].id == receiver
                    )
                )
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                names.add(node.args[1].value)
    return names


def _parsers() -> list[object]:
    return [
        _CastxmlParser(Element("CastXML"), set(), set()),
        _ClangAstParser({"kind": "TranslationUnitDecl", "inner": []}, set(), set()),
    ]


def test_every_attribute_read_off_a_parser_is_provided() -> None:
    read = _named_attribute_calls("getattr", receiver="parser") | set(
        AST_SCOPE_ATTRIBUTES
    )
    assert {"_root", "_pub_header_segs", "_pub_dir_segs"} <= read
    # Stamped onto a parser after construction (the dumper's toolchain and
    # frontend-context annotations), so no class carries them.
    set_by_callers = _named_attribute_calls("setattr", receiver=None)
    parsers = _parsers()
    missing = {
        (type(p).__name__, name)
        for name in read - set_by_callers - set(BACKEND_SPECIFIC)
        for p in parsers
        if not hasattr(p, name)
    }
    assert not missing
    assert set(BACKEND_SPECIFIC) <= read, "drop an entry nothing reads"


class _Stub:
    """A parser whose output is one struct per name, exposing only the
    scope attributes it is given."""

    def __init__(self, names: tuple[str, ...], attrs: dict[str, object]) -> None:
        self._names = names
        for key, value in attrs.items():
            setattr(self, key, value)
        self._abicheck_neutral_factory = lambda: self

    def parse_functions(self):
        return []

    def parse_variables(self):
        return []

    def parse_types(self):
        return [
            RecordType(
                name=n,
                kind="struct",
                qualified_name=n,
                entity_id=entity_id_for_type((), n),
            )
            for n in self._names
        ]

    def parse_enums(self):
        return []

    def parse_typedefs(self):
        return {}

    def parse_typedefs_qualified(self):
        return {}

    def parse_constants(self):
        return {}

    def parse_typedef_entity_ids(self):
        return {}

    def parse_constant_entity_ids(self):
        return {}


def _attrs(subset: tuple[str, ...], root: object, segs: tuple[str, ...]) -> dict:
    values = {"_root": root, "_pub_header_segs": segs, "_pub_dir_segs": segs}
    return {k: values[k] for k in subset}


SUBSETS = [
    s
    for r in range(len(AST_SCOPE_ATTRIBUTES) + 1)
    for s in itertools.combinations(AST_SCOPE_ATTRIBUTES, r)
]


@pytest.mark.parametrize("subset", SUBSETS)
@pytest.mark.parametrize("same_root", [False, True])
def test_each_parse_gets_its_own_ir_unless_it_is_the_same_ast(
    subset, same_root
) -> None:
    shared_root = {"ast": 0}
    old = _Stub(("f", "g"), _attrs(subset, shared_root, ("a",)))
    new_root = shared_root if same_root else {"ast": 1}
    new = _Stub(("f", "h"), _attrs(subset, new_root, ("a",)))
    alone = [
        parse_header_ast_fields(p, producer="castxml").semantic_ir for p in (old, new)
    ]
    assert alone[0] != alone[1]
    with ast_acquisition_scope():
        scoped = [
            parse_header_ast_fields(p, producer="castxml").semantic_ir
            for p in (old, new)
        ]
    assert scoped[0] == alone[0]
    if same_root and "_root" in subset:
        # One AST, one normalization: the second parse reuses the first's.
        assert scoped[1] == alone[0]
    else:
        assert scoped[1] == alone[1]
