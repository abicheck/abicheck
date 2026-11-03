"""Clang header backend: the ``explicit`` specifier is read tri-state from
the declaration's source range, never fabricated from an absent JSON key
(case106: Clang 18 never serializes ``explicit``)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from abicheck.extract.headers.clang.explicit_specifier import (
    classify_specifier_tokens,
    explicit_specifier_fact,
)
from abicheck.extract.headers.clang.locations import materialize_locations
from abicheck.model.fact import FactStatus


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ([], False),
        (["explicit"], True),
        (["constexpr", "explicit"], True),
        (["explicit", "constexpr"], True),
        (["explicit", "(", "true", ")"], True),
        (["explicit", "(", "false", ")"], False),
        (["explicit", "(", "B", ")"], "dependent"),
        (["explicit", "(", "sizeof", "(", "T", ")", ">", "4", ")"], "dependent"),
        (["[[", "nodiscard", "]]", "explicit"], True),
        (["__attribute__", "(", "(", "visibility", "(", "x", ")", ")", ")"], False),
        (["EXPL"], None),
        (["S", "::"], None),
        (["inline", "virtual"], False),
    ],
)
def test_classify_specifier_tokens(tokens, expected):
    assert classify_specifier_tokens(tokens) == expected


HEADER = """\
#define EXPL explicit
#define NOTHING
template <bool B> struct T { explicit(B) T(int); };
struct S {
  explicit S(int);
  S(double);
  explicit operator int() const;
  operator long() const;
  explicit(true) S(char);
  explicit(false) S(short);
  EXPL operator bool() const;
  explicit /* comment: operator */
    operator char() const;
  // explicit in a comment only
  operator unsigned() const;
  NOTHING operator float() const;
  constexpr explicit S(long long) {}
};
"""

# (class name, ctor/conv type) -> (status, value)
EXPECTED = {
    ("T<B>", "void (int)"): (FactStatus.UNSUPPORTED, None),
    ("S", "void (int)"): (FactStatus.PRESENT, True),
    ("S", "void (double)"): (FactStatus.PRESENT, False),
    ("operator int", "int () const"): (FactStatus.PRESENT, True),
    ("operator long", "long () const"): (FactStatus.PRESENT, False),
    ("S", "void (char)"): (FactStatus.PRESENT, True),
    ("S", "void (short)"): (FactStatus.PRESENT, False),
    ("operator bool", "bool () const"): (FactStatus.PRESENT, True),
    ("operator char", "char () const"): (FactStatus.PRESENT, True),
    ("operator unsigned int", "unsigned int () const"): (FactStatus.PRESENT, False),
    # An empty macro contributes no token: Clang's range starts after it.
    ("operator float", "float () const"): (FactStatus.PRESENT, False),
    ("S", "void (long long)"): (FactStatus.PRESENT, True),
}


def _nodes(root):
    stack = [root]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("kind") in ("CXXConstructorDecl", "CXXConversionDecl"):
                yield n
            stack.extend(n.get("inner", []))


@pytest.fixture(scope="module")
def clang_ast(tmp_path_factory):
    clang = shutil.which("clang++")
    if clang is None:
        pytest.skip("clang++ not available")
    d = tmp_path_factory.mktemp("explicit")
    hdr = d / "h.hpp"
    hdr.write_text(HEADER)
    out = subprocess.run(
        [clang, "-std=c++20", "-fsyntax-only", "-Xclang", "-ast-dump=json", str(hdr)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return hdr, materialize_locations(json.loads(out))


@pytest.mark.integration
def test_explicit_fact_from_real_clang_ast(clang_ast):
    _hdr, root = clang_ast
    seen = {}
    for n in _nodes(root):
        if n.get("isImplicit"):
            continue
        fact = explicit_specifier_fact(n)
        seen[(n["name"], n["type"]["qualType"])] = (fact.status, fact.value)
    assert seen == EXPECTED


@pytest.mark.integration
def test_unreadable_source_is_not_collected_not_false(clang_ast):
    hdr, root = clang_ast
    cache: dict[str, bytes | None] = {str(hdr): None}  # simulate deleted header
    for n in _nodes(root):
        fact = explicit_specifier_fact(n, file_cache=cache)
        assert fact.status is FactStatus.NOT_COLLECTED
        assert fact.value is None


def test_serialized_key_is_authoritative():
    fact = explicit_specifier_fact({"explicit": True})
    assert fact.status is FactStatus.PRESENT and fact.value is True


@pytest.mark.parametrize("status_value", [(True,), (False,), None])
def test_explicit_fact_survives_snapshot_round_trip(status_value):
    from abicheck.model import AbiSnapshot, Function
    from abicheck.model.fact import Fact
    from abicheck.serialization import snapshot_from_dict
    from abicheck.storage.snapshot_encode import snapshot_to_dict

    fact = Fact.present(status_value[0]) if status_value else Fact.not_collected("x")
    fn = Function(
        name="S::operator int",
        mangled="_ZNK1ScviEv",
        return_type="int",
        is_explicit=status_value[0] if status_value else None,
        is_explicit_fact=fact,
    )
    snap = AbiSnapshot(library="libs.so", version="1", functions=[fn])
    back = snapshot_from_dict(snapshot_to_dict(snap)).declarations.functions[0]
    assert back.is_explicit == (status_value[0] if status_value else None)
    assert back.is_explicit_fact.status is fact.status
