# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Contract of the root-type matching primitives in ``diff_filtering``.

``_root_pattern`` (one type name as a whole-identifier regex),
``_match_root_type`` (which root type a derived finding names) and
``_root_type_name`` (the type a finding is about). The oracle for the
pattern is an independent token scan, never the regex under test.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker_types import Change
from abicheck.diff_filtering import (
    _compile_root_patterns,
    _match_root_type,
    _root_pattern,
    _root_type_name,
)
from abicheck.model.change_catalog.kinds import ChangeKind

_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _whole_token_occurs(name: str, text: str) -> bool:
    """Independent oracle: *name* occurs with no identifier character on
    either side (``:`` is not an identifier character here)."""
    start = text.find(name)
    while start != -1:
        before = text[start - 1] if start else ""
        after = text[start + len(name)] if start + len(name) < len(text) else ""
        if before not in _IDENT_CHARS and after not in _IDENT_CHARS:
            return True
        start = text.find(name, start + 1)
    return False


_names = st.sampled_from(["Foo", "a.b", "Ctx", "ns::Ctx", "T1", "_x", "op+"])
_neighbours = st.sampled_from(
    ["", " ", "*", "&", "<", ">", ",", ":", "a", "Z", "0", "9", "_", ".", "+"]
)


@given(name=_names, left=_neighbours, right=_neighbours, pad=_neighbours)
@settings(max_examples=150, deadline=None)
def test_root_pattern_matches_exactly_whole_tokens(name, left, right, pad) -> None:
    text = f"{pad}{left}{name}{right}{pad}"
    assert bool(_root_pattern(name).search(text)) == _whole_token_occurs(name, text)


@pytest.mark.parametrize("edge", ["a", "z", "A", "Z", "0", "9", "_"])
def test_every_identifier_class_blocks_a_boundary(edge) -> None:
    pat = _root_pattern("Foo")
    assert not pat.search(f"{edge}Foo")
    assert not pat.search(f"Foo{edge}")
    assert pat.search("Foo")


def test_regex_metacharacters_in_a_name_are_literal() -> None:
    assert _root_pattern("a.b").search("a.b")
    assert not _root_pattern("a.b").search("axb")
    assert _root_pattern("op+").search("op+ ")
    assert not _root_pattern("op+").search("opp ")


def _change(**kw) -> Change:
    base = dict(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol="f",
        description="",
        old_value=None,
        new_value=None,
    )
    base.update(kw)
    return Change(**base)


_ROOTS = {"Config": _change(), "Data": _change()}


@pytest.mark.parametrize("precompiled", [True, False])
@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        (dict(old_value="Config*", new_value="Config&"), "Config"),
        # the first root fails the both-sides check; the scan goes on to the next
        (dict(old_value="Data*", new_value="Data&"), "Data"),
        # both sides known: both must name the root
        (dict(old_value="Config*", new_value="Other*", description="Config"), None),
        (dict(old_value="Config*"), "Config"),
        (dict(new_value="Data*"), "Data"),
        (dict(description="field of Data changed"), "Data"),
        (dict(old_value="Config2*", new_value="Config2&"), None),
        (dict(description="nothing here"), None),
    ],
)
def test_match_root_type(kw, expected, precompiled) -> None:
    patterns = _compile_root_patterns(_ROOTS) if precompiled else None
    assert _match_root_type(_change(**kw), _ROOTS, patterns) == expected


def test_match_root_type_falls_back_for_a_root_missing_from_the_patterns() -> None:
    partial = {"Config": re.compile("Config")}
    assert _match_root_type(_change(new_value="Data*"), _ROOTS, partial) == "Data"


def test_match_root_type_uses_the_given_patterns() -> None:
    # A caller's precompiled pattern is authoritative for its name.
    never = {"Config": re.compile(r"(?!x)x"), "Data": re.compile(r"(?!x)x")}
    assert _match_root_type(_change(new_value="Config*"), _ROOTS, never) is None


@pytest.mark.parametrize(
    ("symbol", "qualified", "kind", "expected"),
    [
        # qualified identity: the type itself, or the type plus a member
        ("ns::Type", "ns::Type", ChangeKind.TYPE_FIELD_ADDED, "ns::Type"),
        ("ns::Type::field", "ns::Type", ChangeKind.TYPE_FIELD_ADDED, "ns::Type"),
        ("ns::Type::field", "ns::Type", ChangeKind.TYPE_SIZE_CHANGED, "ns::Type"),
        # a mere string prefix is not the same type
        ("ns::TypeX::field", "ns::Type", ChangeKind.TYPE_FIELD_ADDED, "ns::TypeX"),
        # a qualified_name the symbol does not start with is ignored
        ("Other::field", "ns::Type", ChangeKind.TYPE_FIELD_ADDED, "Other"),
        ("Other", "ns::Type", ChangeKind.TYPE_SIZE_CHANGED, "Other"),
        # no qualified_name: the legacy rule
        ("Box::flags", None, ChangeKind.TYPE_FIELD_ADDED, "Box"),
        ("ns::Box", None, ChangeKind.TYPE_SIZE_CHANGED, "ns::Box"),
        ("Box", None, ChangeKind.TYPE_FIELD_ADDED, "Box"),
        ("Box", "", ChangeKind.TYPE_FIELD_ADDED, "Box"),
    ],
)
def test_root_type_name(symbol, qualified, kind, expected) -> None:
    c = _change(kind=kind, symbol=symbol, qualified_name=qualified)
    assert _root_type_name(c) == expected
