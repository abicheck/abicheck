# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Default-lane unit tests for ``extract/headers/castxml/abi_fundamental.py``.

castxml nodes are built as ``xml.etree`` elements and the parser context is a
small fake exposing the three attributes the reader uses (``resolve``,
``vector_spellings``, ``plain_char_unsigned``). The expected facts come from a
hand-written LP64 x86-64 table, and castxml's ``size``/``align`` are in bits
as castxml writes them. No castxml or compiler runs.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace
from typing import Any
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml import abi_fundamental
from abicheck.extract.headers.castxml.abi_fundamental import (
    castxml_abi_fundamental,
    castxml_type_abi_fact,
    probe_plain_char_unsigned,
)
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.fact import Fact

_S = FundamentalEncoding.SIGNED
_U = FundamentalEncoding.UNSIGNED
_B = FundamentalEncoding.BOOLEAN
_F = FundamentalEncoding.FLOAT

#: Hand-written LP64 x86-64 oracle: castxml's canonical FundamentalType name ->
#: (encoding, bytes). castxml reports ``size``/``align`` in bits, so each row
#: is rendered as ``bytes * 8``.
_LP64_FUNDAMENTALS: tuple[tuple[str, FundamentalEncoding, int], ...] = (
    ("signed char", _S, 1),
    ("unsigned char", _U, 1),
    ("short int", _S, 2),
    ("short unsigned int", _U, 2),
    ("int", _S, 4),
    ("unsigned int", _U, 4),
    ("long int", _S, 8),
    ("long unsigned int", _U, 8),
    ("long long int", _S, 8),
    ("long long unsigned int", _U, 8),
    ("__int128", _S, 16),
    ("unsigned __int128", _U, 16),
    ("_Bool", _B, 1),
    ("bool", _B, 1),
    ("float", _F, 4),
    ("double", _F, 8),
    ("long double", _F, 16),
    ("char16_t", _U, 2),
    ("char32_t", _U, 4),
    ("char8_t", _U, 1),
    ("_Float16", _F, 2),
    ("_Float32", _F, 4),
    ("_Float64", _F, 8),
    ("__float128", _F, 16),
)


class _Ctx:
    """The three attributes the reader reads from a parser context."""

    def __init__(
        self,
        nodes: dict[str, Element],
        *,
        vector_spellings: dict[str, str] | None = None,
        plain_char_unsigned: bool | None = False,
    ) -> None:
        self._nodes = nodes
        self.vector_spellings = vector_spellings or {}
        self.plain_char_unsigned = plain_char_unsigned

    def resolve(self, type_id: str) -> Element | None:
        return self._nodes.get(type_id)


def _node(tag: str, type_id: str, **attrs: str) -> Element:
    return Element(tag, {"id": type_id, **attrs})


def _fundamental(type_id: str, name: str, bytes_: int) -> Element:
    return _node(
        "FundamentalType",
        type_id,
        name=name,
        size=str(bytes_ * 8),
        align=str(bytes_ * 8),
    )


def _ctx(*nodes: Element, **kwargs: Any) -> _Ctx:
    return _Ctx({n.get("id", ""): n for n in nodes}, **kwargs)


def _fact(encoding: FundamentalEncoding, size: int, lanes: int = 0) -> AbiFundamental:
    """The fact a scalar (or vector, aligned to its own size) must carry."""
    return AbiFundamental(encoding, size, size, lanes)


# ---------------------------------------------------------------------------
# FundamentalType: the closed builtin set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "encoding", "size"),
    _LP64_FUNDAMENTALS,
    ids=[row[0] for row in _LP64_FUNDAMENTALS],
)
def test_fundamental_carries_its_encoding_and_size_from_castxml_bits(
    name: str, encoding: FundamentalEncoding, size: int
) -> None:
    ctx = _ctx(_fundamental("_t", name, size))
    assert castxml_abi_fundamental(ctx, "_t") == _fact(encoding, size)


@pytest.mark.parametrize(
    "plain_unsigned", [False, True], ids=["plain-signed", "plain-unsigned"]
)
def test_plain_char_takes_the_probed_target_signedness(plain_unsigned: bool) -> None:
    ctx = _ctx(
        _node("FundamentalType", "_c", name="char", size="8", align="8"),
        plain_char_unsigned=plain_unsigned,
    )
    expected = _U if plain_unsigned else _S
    assert castxml_abi_fundamental(ctx, "_c") == _fact(expected, 1)


def test_plain_char_with_unknown_probe_has_no_fact() -> None:
    """Unprobed signedness is not guessed: the fact is absent."""
    ctx = _ctx(
        _node("FundamentalType", "_c", name="char", size="8", align="8"),
        plain_char_unsigned=None,
    )
    assert castxml_abi_fundamental(ctx, "_c") is None


def test_context_without_a_probe_attribute_has_no_plain_char_fact() -> None:
    class _NoProbe:
        def __init__(self, node: Element) -> None:
            self._node = node

        def resolve(self, type_id: str) -> Element | None:
            return self._node if type_id == "_c" else None

    node = _node("FundamentalType", "_c", name="char", size="8", align="8")
    assert castxml_abi_fundamental(_NoProbe(node), "_c") is None


@pytest.mark.parametrize(
    "attrs",
    [
        {"size": "32"},
        {"align": "32"},
        {"size": "0", "align": "32"},
        {"size": "32", "align": "0"},
        {"size": "abc", "align": "32"},
        {"size": "12", "align": "12"},
        {"size": "-32", "align": "32"},
        {"size": "", "align": "32"},
    ],
    ids=[
        "no-align",
        "no-size",
        "zero-size",
        "zero-align",
        "non-numeric-size",
        "not-whole-bytes",
        "negative-size",
        "empty-size",
    ],
)
def test_fundamental_with_missing_or_bad_bit_counts_has_no_fact(
    attrs: dict[str, str],
) -> None:
    node = _node("FundamentalType", "_t", name="int", **attrs)
    assert castxml_abi_fundamental(_ctx(node), "_t") is None


@pytest.mark.parametrize(
    "name",
    ["void", "wchar_t", "size_t", "nullptr_t", "_Complex double", "unsigned_long"],
)
def test_fundamental_name_outside_the_builtin_set_has_no_fact(name: str) -> None:
    node = _node("FundamentalType", "_t", name=name, size="64", align="64")
    assert castxml_abi_fundamental(_ctx(node), "_t") is None


def test_fundamental_without_a_name_attribute_has_no_fact() -> None:
    node = _node("FundamentalType", "_t", size="32", align="32")
    assert castxml_abi_fundamental(_ctx(node), "_t") is None


# ---------------------------------------------------------------------------
# Transparent wrappers and the depth bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wrapper", ["Typedef", "CvQualifiedType", "ElaboratedType"])
def test_transparent_wrapper_reports_the_fact_of_what_it_names(wrapper: str) -> None:
    ctx = _ctx(
        _node(wrapper, "_w", type="_i"),
        _fundamental("_i", "unsigned int", 4),
    )
    assert castxml_abi_fundamental(ctx, "_w") == _fact(_U, 4)


def test_typedef_chain_of_three_resolves_to_the_fundamental() -> None:
    ctx = _ctx(
        _node("Typedef", "_t1", type="_t2"),
        _node("Typedef", "_t2", type="_t3"),
        _node("CvQualifiedType", "_t3", type="_i"),
        _fundamental("_i", "long int", 8),
    )
    assert castxml_abi_fundamental(ctx, "_t1") == _fact(_S, 8)


def test_typedef_chain_at_the_depth_bound_still_resolves() -> None:
    """Sixteen wrappers in front of the fundamental: the deepest read is at
    depth 16, which the bound admits."""
    nodes = [_node("Typedef", f"_t{i}", type=f"_t{i + 1}") for i in range(16)]
    nodes.append(_fundamental("_t16", "short int", 2))
    assert castxml_abi_fundamental(_ctx(*nodes), "_t0") == _fact(_S, 2)


def test_typedef_chain_past_the_depth_bound_has_no_fact() -> None:
    nodes = [_node("Typedef", f"_t{i}", type=f"_t{i + 1}") for i in range(17)]
    nodes.append(_fundamental("_t17", "short int", 2))
    assert castxml_abi_fundamental(_ctx(*nodes), "_t0") is None


def test_typedef_cycle_is_cut_by_the_depth_bound() -> None:
    ctx = _ctx(
        _node("Typedef", "_a", type="_b"),
        _node("Typedef", "_b", type="_a"),
    )
    assert castxml_abi_fundamental(ctx, "_a") is None


@pytest.mark.parametrize("type_id", ["_missing", ""], ids=["unknown-id", "empty-id"])
def test_unresolved_type_id_has_no_fact(type_id: str) -> None:
    ctx = _ctx(_fundamental("_i", "int", 4))
    assert castxml_abi_fundamental(ctx, type_id) is None


@pytest.mark.parametrize("tag", ["PointerType", "ReferenceType", "Struct", "ArrayType"])
def test_non_fundamental_node_kinds_have_no_fact(tag: str) -> None:
    ctx = _ctx(
        _node(tag, "_p", type="_i", size="64", align="64"), _fundamental("_i", "int", 4)
    )
    assert castxml_abi_fundamental(ctx, "_p") is None


def test_typedef_without_a_type_reference_has_no_fact() -> None:
    assert castxml_abi_fundamental(_ctx(_node("Typedef", "_t")), "_t") is None


# ---------------------------------------------------------------------------
# Enumeration: the underlying integer, when sizes agree
# ---------------------------------------------------------------------------


def test_enumeration_takes_its_underlying_integer_when_the_sizes_agree() -> None:
    ctx = _ctx(
        _node("Enumeration", "_e", type="_u", size="32"),
        _fundamental("_u", "unsigned int", 4),
    )
    assert castxml_abi_fundamental(ctx, "_e") == _fact(_U, 4)


def test_enumeration_whose_size_disagrees_with_its_underlying_integer_has_no_fact() -> (
    None
):
    ctx = _ctx(
        _node("Enumeration", "_e", type="_u", size="64"),
        _fundamental("_u", "int", 4),
    )
    assert castxml_abi_fundamental(ctx, "_e") is None


def test_enumeration_without_a_size_has_no_fact() -> None:
    ctx = _ctx(_node("Enumeration", "_e", type="_u"), _fundamental("_u", "int", 4))
    assert castxml_abi_fundamental(ctx, "_e") is None


def test_enumeration_without_an_underlying_type_has_no_fact() -> None:
    ctx = _ctx(_node("Enumeration", "_e", size="32"))
    assert castxml_abi_fundamental(ctx, "_e") is None


def test_enumeration_over_a_vector_has_no_fact() -> None:
    """An enumeration's underlying type is never a vector: a lane-carrying
    underlying fact is refused."""
    ctx = _ctx(
        _node("Enumeration", "_e", type="_v", size="128"),
        _node("Unimplemented", "_v", type_class="Vector"),
        vector_spellings={"_v": "float __attribute__((vector_size(16)))"},
    )
    assert castxml_abi_fundamental(ctx, "_e") is None


# ---------------------------------------------------------------------------
# AtomicType: natural alignment of a scalar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inner", "name", "bytes_", "encoding"),
    [
        ("_i", "int", 4, _S),
        ("_i", "unsigned char", 1, _U),
        ("_i", "double", 8, _F),
        ("_i", "_Bool", 1, _B),
    ],
    ids=["int", "uchar", "double", "bool"],
)
def test_atomic_scalar_is_naturally_aligned_to_its_size(
    inner: str, name: str, bytes_: int, encoding: FundamentalEncoding
) -> None:
    ctx = _ctx(
        _node(
            "AtomicType", "_a", type=inner, size=str(bytes_ * 8), align=str(bytes_ * 8)
        ),
        _fundamental(inner, name, bytes_),
    )
    assert castxml_abi_fundamental(ctx, "_a") == _fact(encoding, bytes_)


def test_atomic_reports_the_alignment_castxml_states() -> None:
    """castxml states an atomic's own alignment: it is read, not recomputed."""
    ctx = _ctx(
        _node("AtomicType", "_a", type="_i", size="32", align="64"),
        _fundamental("_i", "int", 4),
    )
    assert castxml_abi_fundamental(ctx, "_a") == AbiFundamental(_S, 4, 8)


@pytest.mark.parametrize(
    "attrs",
    [{"size": "32"}, {"align": "32"}],
    ids=["no-align", "no-size"],
)
def test_atomic_without_its_bit_counts_has_no_fact(attrs: dict[str, str]) -> None:
    ctx = _ctx(
        _node("AtomicType", "_a", type="_i", **attrs), _fundamental("_i", "int", 4)
    )
    assert castxml_abi_fundamental(ctx, "_a") is None


def test_atomic_of_a_vector_has_no_fact() -> None:
    ctx = _ctx(
        _node("AtomicType", "_a", type="_v", size="128", align="128"),
        _node("Unimplemented", "_v", type_class="Vector"),
        vector_spellings={"_v": "float __attribute__((vector_size(16)))"},
    )
    assert castxml_abi_fundamental(ctx, "_a") is None


def test_atomic_of_an_unknown_inner_has_no_fact() -> None:
    ctx = _ctx(
        _node("AtomicType", "_a", type="_s", size="64", align="64"),
        _node("Struct", "_s", size="64"),
    )
    assert castxml_abi_fundamental(ctx, "_a") is None


# ---------------------------------------------------------------------------
# Unimplemented Vector: the joined clang/GCC spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spelling", "encoding", "size", "lanes"),
    [
        ("float __attribute__((vector_size(16)))", _F, 16, 4),
        ("double __attribute__((vector_size(32)))", _F, 32, 4),
        ("int __attribute__((vector_size(16)))", _S, 16, 4),
        ("unsigned char __attribute__((vector_size(8)))", _U, 8, 8),
        ("__int128 __attribute__((vector_size(32)))", _S, 32, 2),
        ("_Float16 __attribute__((vector_size(8)))", _F, 8, 4),
    ],
    ids=["float4", "double4-avx", "int4", "uchar8", "int128-x2", "half4"],
)
def test_vector_spelling_gives_size_lanes_and_element_class(
    spelling: str, encoding: FundamentalEncoding, size: int, lanes: int
) -> None:
    node = _node("Unimplemented", "_v", type_class="Vector")
    ctx = _ctx(node, vector_spellings={"_v": spelling})
    assert castxml_abi_fundamental(ctx, "_v") == _fact(encoding, size, lanes)


def test_vector_of_plain_char_follows_the_probed_char_signedness() -> None:
    node = _node("Unimplemented", "_v", type_class="Vector")
    spelling = "char __attribute__((vector_size(16)))"
    unsigned = _ctx(node, vector_spellings={"_v": spelling}, plain_char_unsigned=True)
    signed = _ctx(node, vector_spellings={"_v": spelling}, plain_char_unsigned=False)
    assert castxml_abi_fundamental(unsigned, "_v") == _fact(_U, 16, 16)
    assert castxml_abi_fundamental(signed, "_v") == _fact(_S, 16, 16)


def test_vector_of_plain_char_with_unknown_probe_has_no_fact() -> None:
    node = _node("Unimplemented", "_v", type_class="Vector")
    ctx = _ctx(
        node,
        vector_spellings={"_v": "char __attribute__((vector_size(16)))"},
        plain_char_unsigned=None,
    )
    assert castxml_abi_fundamental(ctx, "_v") is None


@pytest.mark.parametrize(
    "spelling",
    [
        None,
        "float",
        "float __attribute__((vector_size(6)))",
        "float __attribute__((vector_size(0)))",
        "struct S __attribute__((vector_size(16)))",
        "_Complex double __attribute__((vector_size(16)))",
        "long double __attribute__((vector_size(32)))",
        "_Float32 __attribute__((vector_size(16)))",
    ],
    ids=[
        "no-spelling",
        "no-vector-size",
        "not-whole-elements",
        "zero-bytes",
        "struct-element",
        "complex-element",
        "element-without-byte-size-table-entry",
        "float32-without-byte-size-table-entry",
    ],
)
def test_vector_without_a_usable_joined_spelling_has_no_fact(
    spelling: str | None,
) -> None:
    node = _node("Unimplemented", "_v", type_class="Vector")
    vectors = {} if spelling is None else {"_v": spelling}
    assert castxml_abi_fundamental(_ctx(node, vector_spellings=vectors), "_v") is None


@pytest.mark.parametrize("type_class", ["Complex", "BitInt", "Function"])
def test_unimplemented_non_vector_type_classes_have_no_fact(type_class: str) -> None:
    node = _node("Unimplemented", "_u", type_class=type_class)
    ctx = _ctx(node, vector_spellings={"_u": "float __attribute__((vector_size(16)))"})
    assert castxml_abi_fundamental(ctx, "_u") is None


# ---------------------------------------------------------------------------
# castxml_type_abi_fact: the Fact a declaration stores
# ---------------------------------------------------------------------------


def test_type_fact_is_a_present_fact_carrying_the_fundamental() -> None:
    ctx = _ctx(_fundamental("_i", "int", 4))
    assert castxml_type_abi_fact(ctx, "_i") == Fact.present(_fact(_S, 4))


def test_type_fact_is_none_when_castxml_states_no_fundamental() -> None:
    ctx = _ctx(_node("Struct", "_s", size="64"))
    assert castxml_type_abi_fact(ctx, "_s") is None


# ---------------------------------------------------------------------------
# Private bit-count reader: the byte conversion and its refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bits", "expected"),
    [
        ("8", 1),
        ("64", 8),
        ("128", 16),
        ("0", None),
        ("12", None),
        ("-8", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
    ids=[
        "one",
        "eight",
        "sixteen",
        "zero",
        "partial-byte",
        "negative",
        "non-numeric",
        "empty",
        "absent",
    ],
)
def test_bit_count_becomes_bytes_only_when_it_is_a_positive_whole_byte_count(
    bits: str | None, expected: int | None
) -> None:
    assert abi_fundamental._bytes(bits) == expected


@pytest.mark.parametrize(
    ("name", "plain", "expected"),
    [
        ("char", True, _U),
        ("char", False, _S),
        ("char", None, None),
        ("int", None, _S),
        ("unsigned long", False, _U),
        ("nope", True, None),
    ],
)
def test_builtin_encoding_reads_plain_char_from_the_probe_and_everything_else_from_the_set(
    name: str, plain: bool | None, expected: FundamentalEncoding | None
) -> None:
    assert abi_fundamental._builtin_encoding(name, plain) == expected


# ---------------------------------------------------------------------------
# probe_plain_char_unsigned: resolves the binary, then asks the probe
# ---------------------------------------------------------------------------


@pytest.fixture
def probe_seam(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the resolver, ``which`` and the probe; record what each saw."""
    seen: dict[str, Any] = {}

    def resolve(
        compiler: str, gcc_path: str | None, gcc_prefix: str | None
    ) -> tuple[str, str]:
        seen["resolve"] = (compiler, gcc_path, gcc_prefix)
        return ("cc-under-test", "gnu")

    def which(name: str) -> str | None:
        seen["which"] = name
        return seen.get("which_result")

    def probe(cc_bin: str, option_tokens: tuple[str, ...]) -> bool | None:
        seen["probe"] = (cc_bin, option_tokens)
        return seen.get("probe_result")

    monkeypatch.setattr(abi_fundamental, "_resolve_compiler_binary", resolve)
    monkeypatch.setattr(abi_fundamental, "shutil", SimpleNamespace(which=which))
    monkeypatch.setattr(abi_fundamental, "plain_char_is_unsigned", probe)
    return seen


@pytest.mark.parametrize("probe_result", [True, False, None])
def test_probe_plain_char_returns_the_probe_answer_for_the_resolved_binary(
    probe_seam: dict[str, Any], probe_result: bool | None
) -> None:
    probe_seam["which_result"] = "/usr/bin/cc-under-test"
    probe_seam["probe_result"] = probe_result
    answer = probe_plain_char_unsigned("gcc", "/opt/gcc", "x86_64-", ("-m32",))
    assert answer is probe_result
    assert probe_seam["resolve"] == ("gcc", "/opt/gcc", "x86_64-")
    assert probe_seam["which"] == "cc-under-test"
    assert probe_seam["probe"] == ("/usr/bin/cc-under-test", ("-m32",))


def test_probe_plain_char_uses_the_bare_binary_when_it_is_not_on_path(
    probe_seam: dict[str, Any],
) -> None:
    probe_seam["which_result"] = None
    probe_seam["probe_result"] = True
    probe_plain_char_unsigned("gcc", None, None, ())
    assert probe_seam["probe"] == ("cc-under-test", ())


def test_probe_plain_char_with_real_shutil_still_passes_a_resolvable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a stub ``which``, the real one answers for the resolved name."""
    seen: dict[str, Any] = {}

    def probe(cc_bin: str, option_tokens: tuple[str, ...]) -> bool:
        seen["cc"] = cc_bin
        return False

    monkeypatch.setattr(
        abi_fundamental, "_resolve_compiler_binary", lambda *_a: ("sh", "gnu")
    )
    monkeypatch.setattr(abi_fundamental, "plain_char_is_unsigned", probe)
    assert probe_plain_char_unsigned("gcc", None, None, ()) is False
    assert seen["cc"] == shutil.which("sh")
