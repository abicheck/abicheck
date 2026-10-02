# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Invariants for building blocks that had several diverging implementations.

Bug class: one question ("is this owner internal?", "are these two spellings
the same integer type?", "which names does this library export?", "is this
stored pack intact?") answered by more than one private implementation, so
two paths through the product disagree on the same input. Each test states
the question's contract over a generated input space, against an oracle
that is not the implementation under test.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.export_accounting import _entity_owner_is_internal
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.pack_io import BUILD_EVIDENCE_REL, write
from abicheck.buildsource.pack_load import load_pack_or_raise
from abicheck.errors import SnapshotError
from abicheck.model.symbol_ownership import (
    INTERNAL_NAMESPACE_NAMES,
    has_internal_namespace_component,
)
from abicheck.name_classification import canonicalize_type_name


def _nested(*components: str) -> str:
    """Itanium ``_ZN<len><name>...Ev`` for a function nested in *components*."""
    return "_ZN" + "".join(f"{len(c)}{c}" for c in components) + "Ev"


# ── internal namespace: export accounting agrees with the vocabulary owner ──

_OWNER_SEGMENTS = sorted(INTERNAL_NAMESPACE_NAMES) + [
    "lib",
    "Simple",
    "details",
    "impls",
]


@pytest.mark.parametrize(
    "segments",
    [
        (outer, inner)
        for outer, inner in itertools.product(["lib", "a"], _OWNER_SEGMENTS)
    ],
)
def test_export_accounting_agrees_with_the_namespace_owner(segments) -> None:
    symbol = _nested(*segments, "foo")
    oracle = segments[-1] in INTERNAL_NAMESPACE_NAMES or segments[0] in (
        INTERNAL_NAMESPACE_NAMES
    )
    assert _entity_owner_is_internal(symbol) is oracle
    assert has_internal_namespace_component(symbol) is oracle


@pytest.mark.parametrize("name", sorted(INTERNAL_NAMESPACE_NAMES))
def test_an_internal_name_as_the_entity_itself_is_not_an_internal_owner(name) -> None:
    # `lib::detail()` is an ordinary function; only `lib::detail::foo` is owned
    # by an internal namespace.
    assert _entity_owner_is_internal(_nested("lib", name)) is False


# ── integer specifier spelling: order is not significant, the type is ──

# canonical type -> its multiset of specifier words (the language's own rule)
_INT_TYPES: dict[str, tuple[tuple[str, ...], ...]] = {
    "short": (
        ("short",),
        ("short", "int"),
        ("signed", "short"),
        ("signed", "short", "int"),
    ),
    "unsigned short": (("unsigned", "short"), ("unsigned", "short", "int")),
    "int": (("int",), ("signed",), ("signed", "int")),
    "unsigned int": (("unsigned",), ("unsigned", "int")),
    "long": (("long",), ("long", "int"), ("signed", "long"), ("signed", "long", "int")),
    "unsigned long": (("unsigned", "long"), ("unsigned", "long", "int")),
    "long long": (
        ("long", "long"),
        ("long", "long", "int"),
        ("signed", "long", "long"),
    ),
    "unsigned long long": (
        ("unsigned", "long", "long"),
        ("unsigned", "long", "long", "int"),
    ),
    "signed char": (("signed", "char"),),
    "unsigned char": (("unsigned", "char"),),
}
_CONTEXTS = ("{}", "const {}", "{} const *", "{} &", "std::vector<{}>", "{} *const")


def _spellings(words: tuple[str, ...]):
    for perm in set(itertools.permutations(words)):
        yield " ".join(perm)


@pytest.mark.parametrize("context", _CONTEXTS)
def test_every_order_of_one_integer_type_canonicalizes_alike(context: str) -> None:
    for canonical, forms in _INT_TYPES.items():
        expected = canonicalize_type_name(context.format(canonical))
        for words in forms:
            for spelling in _spellings(words):
                got = canonicalize_type_name(context.format(spelling))
                assert got == expected, (spelling, context, got, expected)


@pytest.mark.parametrize("context", _CONTEXTS)
def test_distinct_integer_types_stay_distinct(context: str) -> None:
    seen = {
        canonicalize_type_name(context.format(canonical)) for canonical in _INT_TYPES
    }
    assert len(seen) == len(_INT_TYPES)
    # Plain `char` has implementation-defined sign: never folded into either.
    assert canonicalize_type_name("char") not in {
        canonicalize_type_name("signed char"),
        canonicalize_type_name("unsigned char"),
    }


@pytest.mark.parametrize(
    "name", ["longname", "ns::long_t", "size_t", "uint64_t", "my_int", "int_least8_t"]
)
def test_identifiers_containing_specifier_words_are_untouched(name: str) -> None:
    assert canonicalize_type_name(name) == name


# ── stored pack integrity ──


def _written_pack(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "pack")
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp")]
    )
    write(pack)
    return tmp_path / "pack"


def test_an_untouched_pack_loads(tmp_path) -> None:
    pack = load_pack_or_raise(_written_pack(tmp_path))
    assert pack.build_evidence is not None


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: {**d, "compile_units": []},
        lambda d: {**d, "compile_units": d["compile_units"] * 2},
        lambda d: {**d, "extra_field_added_after_write": 1},
    ],
)
def test_a_pack_edited_after_it_was_written_is_rejected(tmp_path, edit) -> None:
    root = _written_pack(tmp_path)
    payload = root / BUILD_EVIDENCE_REL
    payload.write_text(json.dumps(edit(json.loads(payload.read_text()))))
    with pytest.raises(SnapshotError, match="do not match the digests"):
        load_pack_or_raise(root)


# A specifier run that modifies another builtin is not a complete integer
# spelling; folding it would invent `unsigned int __int128`.
_EXTENDED = ("__int128", "_BitInt(8)", "__int128_t", "char16_t", "wchar_t")


@pytest.mark.parametrize("context", _CONTEXTS)
@pytest.mark.parametrize("prefix", ["unsigned", "signed", "long", "unsigned long"])
@pytest.mark.parametrize("base", _EXTENDED)
def test_a_modifier_of_an_extended_builtin_is_untouched(context, prefix, base) -> None:
    spelling = context.format(f"{prefix} {base}")
    got = canonicalize_type_name(spelling)
    assert f"{prefix} {base}" in got, (spelling, got)
