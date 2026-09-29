"""Enum end-marker recognition and serialization-tag heuristic tightening.

Bug class: name-convention heuristics that match raw lowercase suffixes are
spelling-dependent (``dnnl_graph_op_last_symbol`` / ``LastSymbol`` missed,
``format_tag`` vs ``dnnl_format_tag_t`` classified differently). The oracle
here is an independent enumeration of spellings of one underlying word
sequence: every spelling must classify identically.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.diff_helpers import identifier_tokens, is_sentinel_enum_member
from abicheck.diff_serialization import (
    _enum_type_is_tag_registry,
    detect_serialization_tag_changes,
)
from abicheck.model import AbiSnapshot, EnumMember, EnumType
from abicheck.model.change_catalog.kinds import ChangeKind


def _spellings(words: list[str]) -> list[str]:
    snake = "_".join(words)
    camel = "".join(w.capitalize() for w in words)
    return [
        snake,
        snake.upper(),
        camel,
        "k" + camel,
        camel[0].lower() + camel[1:],
        "ns::" + camel,
        "dnnl_" + snake,
        "DNNL_" + snake.upper(),
    ]


SENTINEL_TAILS = [
    ["last"],
    ["max"],
    ["count"],
    ["end"],
    ["sentinel"],
    ["num"],
    ["last", "symbol"],
    ["last", "entry"],
    ["max", "value"],
    ["enum", "count"],
]
PREFIXES = [[], ["op"], ["graph", "op"], ["format", "tag"], ["data", "type"]]


@pytest.mark.parametrize(
    ("prefix", "tail"), list(itertools.product(PREFIXES, SENTINEL_TAILS))
)
def test_every_spelling_of_a_sentinel_is_recognised(prefix, tail):
    for name in _spellings(prefix + tail):
        assert is_sentinel_enum_member(name), name


@pytest.mark.parametrize(
    "words",
    [
        ["backend"],
        ["blast"],
        ["maximum", "size"],
        ["last", "used", "mode"],
        ["append"],
        ["counter"],
        ["end", "point"],
        ["number", "format"],
        ["format", "tag"],
        ["undef"],
        ["any"],
        ["abc"],
        ["numa", "node"],
        ["max", "pool", "algo"],
    ],
)
def test_ordinary_names_are_not_sentinels(words):
    for name in _spellings(words):
        assert not is_sentinel_enum_member(name), name


def test_reported_onednn_names():
    assert is_sentinel_enum_member("dnnl_graph_op_last_symbol")
    assert is_sentinel_enum_member("LastSymbol")
    assert is_sentinel_enum_member("dnnl_format_tag_last")
    assert is_sentinel_enum_member("NUM_KINDS")


def test_tokenizer_is_spelling_independent():
    for words in (["foo", "last", "symbol"], ["http", "server"]):
        forms = {tuple(identifier_tokens(s)) for s in _spellings(words)[:3]}
        assert forms == {tuple(words)}


@pytest.mark.parametrize(
    "name",
    [
        "format_tag",
        "dnnl_format_tag_t",
        "dnnl::memory::format_tag",
        "FormatTag",
        "dispatch_tag",
    ],
)
def test_bare_tag_enum_type_is_not_a_registry(name):
    assert not _enum_type_is_tag_registry(name)


@pytest.mark.parametrize(
    "name",
    [
        "mylib::SerializationTag",
        "mylib_serialization_tag_t",
        "SERIALIZATION_TAG",
        "model_tag_id",
        "TagId",
        "ns::TagIds",
    ],
)
def test_strong_tag_enum_type_is_a_registry(name):
    assert _enum_type_is_tag_registry(name)


def _enum_snap(ver, ename, members):
    return AbiSnapshot(
        library="lib",
        version=ver,
        enums=[
            EnumType(
                name=ename,
                underlying_type="int",
                members=[EnumMember(name=n, value=v) for n, v in members],
            )
        ],
    )


@pytest.mark.parametrize("ename", ["dnnl::memory::format_tag", "dnnl_format_tag_t"])
def test_format_tag_last_shift_is_not_a_serialization_tag(ename):
    old = _enum_snap("1", ename, [("abc", 1), ("format_tag_last", 2)])
    new = _enum_snap("2", ename, [("abc", 1), ("abcd", 2), ("format_tag_last", 3)])
    assert detect_serialization_tag_changes(old, new) == []


@pytest.mark.parametrize("sentinel", ["kmeans_last", "Count", "LastSymbol", "tag_max"])
def test_sentinel_in_real_tag_registry_is_skipped(sentinel):
    old = _enum_snap("1", "mylib::SerializationTag", [("a_model", 1), (sentinel, 2)])
    new = _enum_snap(
        "2", "mylib::SerializationTag", [("a_model", 1), ("b_model", 2), (sentinel, 3)]
    )
    assert detect_serialization_tag_changes(old, new) == []


def test_real_tag_registry_member_change_still_flagged():
    old = _enum_snap("1", "mylib_serialization_tag_t", [("a_model", 1), ("b_model", 2)])
    new = _enum_snap("2", "mylib_serialization_tag_t", [("a_model", 2), ("b_model", 1)])
    kinds = {c.kind for c in detect_serialization_tag_changes(old, new)}
    assert kinds == {ChangeKind.SERIALIZATION_TAG_CHANGED}


@pytest.mark.parametrize(
    ("ename", "sentinel"),
    [
        ("dnnl_graph_op_kind_t", "dnnl_graph_op_last_symbol"),
        ("dnnl::graph::op::kind", "LastSymbol"),
    ],
)
def test_compare_classifies_shifted_end_marker_as_last_member(ename, sentinel):
    from abicheck.checker import compare

    old = _enum_snap("1", ename, [("a", 86), (sentinel, 87)])
    new = _enum_snap("2", ename, [("a", 86), ("b", 87), (sentinel, 88)])
    kinds = {c.kind for c in compare(old, new).changes if c.symbol.endswith(sentinel)}
    assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in kinds
    assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED not in kinds
    assert ChangeKind.SERIALIZATION_TAG_CHANGED not in kinds
