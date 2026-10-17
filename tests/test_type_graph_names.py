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

from abicheck.buildsource.type_graph_names import _base_type_name


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
