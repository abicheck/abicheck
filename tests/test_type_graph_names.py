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


"""Textual type-name spelling for the type/reference graph (ADR-041 P0).

Unit tests for :func:`~abicheck.buildsource.type_graph_names._base_type_name`:
the cv/pointer/reference/array/signature stripping that reduces a clang
``qualType`` spelling to the base name the graph keys on. The parser-level
tests that exercise the same rules through a full AST stay in
``test_type_graph.py``.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.type_graph_names import _base_type_name, _is_excluded_type


def test_base_type_name_strips_pointer_and_cv() -> None:
    assert _base_type_name("const detail::Impl *") == "detail::Impl"
    assert _base_type_name("ns::Widget &") == "ns::Widget"
    assert _base_type_name("ns::Widget &&") == "ns::Widget"
    assert _base_type_name("struct Foo") == "Foo"
    assert _base_type_name("int") == "int"
    assert _base_type_name("int[4]") == "int"
    assert _base_type_name("") == ""


def test_base_type_name_strips_array_bounds_before_pointer_suffix() -> None:
    # An array of pointers ("detail::Impl *[4]") ends in "]", not "*", so the
    # suffix-stripping loop never fires before a one-shot bracket strip *after*
    # the loop — leaving the trailing "*" behind (Codex review).
    assert _base_type_name("detail::Impl *[4]") == "detail::Impl"


def test_base_type_name_strips_callback_signature_argument() -> None:
    # std::function<detail::Impl ()>'s single template argument spells as
    # the written function-signature form "detail::Impl ()", not a plain
    # type name — the "()" parameter-list suffix wasn't recognized by any
    # existing stripping rule, so "Impl ()" was looked up as a type literally
    # named that (Codex review).
    assert _base_type_name("detail::Impl ()") == "detail::Impl"


# --- Builtin exclusion is independent of the producer's spelling ----------
#
# Oracle: C11 6.7.2p2's list of valid arithmetic type-specifier multisets,
# written out here independently of the implementation. Each may appear in
# any order, and producers do differ: clang's ``qualType`` writes ``long``,
# CastXML/GCC write ``long int`` / ``long unsigned int``. Before the fix the
# CastXML-fed header graph kept ``long int`` as a type node, so a function
# using ``long`` as both parameter and return read as a
# ``public_dependency_role_changed`` (formal/RET_FRAGMENT.md, 112 of 1176
# cases when ``clang`` was not on PATH).

_C11_SPECIFIER_MULTISETS = (
    ("void",),
    ("char",),
    ("signed", "char"),
    ("unsigned", "char"),
    ("short",),
    ("signed", "short"),
    ("short", "int"),
    ("signed", "short", "int"),
    ("unsigned", "short"),
    ("unsigned", "short", "int"),
    ("int",),
    ("signed",),
    ("signed", "int"),
    ("unsigned",),
    ("unsigned", "int"),
    ("long",),
    ("signed", "long"),
    ("long", "int"),
    ("signed", "long", "int"),
    ("unsigned", "long"),
    ("unsigned", "long", "int"),
    ("long", "long"),
    ("signed", "long", "long"),
    ("long", "long", "int"),
    ("signed", "long", "long", "int"),
    ("unsigned", "long", "long"),
    ("unsigned", "long", "long", "int"),
    ("float",),
    ("double",),
    ("long", "double"),
    ("_Bool",),
    ("float", "_Complex"),
    ("double", "_Complex"),
    ("long", "double", "_Complex"),
    # GNU extensions both producers emit for x86-64.
    ("__int128",),
    ("unsigned", "__int128"),
)


@pytest.mark.parametrize(
    "spelling",
    sorted(
        {
            " ".join(p)
            for ms in _C11_SPECIFIER_MULTISETS
            for p in itertools.permutations(ms)
        }
    ),
)
def test_every_fundamental_spelling_is_excluded(spelling: str) -> None:
    assert _is_excluded_type(spelling)


@pytest.mark.parametrize(
    "name",
    [
        "s1",
        "s10",
        "long_t",
        "int_wrapper",
        "longer",
        "ns::long",
        "unsigned_char",
        "Widget",
        "std::size_t_like",
    ],
)
def test_record_and_typedef_names_are_not_excluded(name: str) -> None:
    assert not _is_excluded_type(name)
