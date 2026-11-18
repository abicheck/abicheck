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
"""The castxml specialization detector on the source text castxml points at."""

from __future__ import annotations

import pytest

from abicheck.extract.headers.castxml.calling_convention import _code_window
from abicheck.extract.headers.castxml.explicit_instantiation import (
    _declares_specialization,
)


def _detect(text: str, line_no: int, name: str = "tpz") -> bool:
    lines = text.split("\n")
    code, start, end = _code_window(lines, line_no)
    return _declares_specialization(code, start, end, name)


@pytest.mark.parametrize(
    ("text", "line_no", "expected"),
    [
        # One-line explicit specialization.
        ("template <> int tpz<int>(int v);", 1, True),
        # Introducer on the line above the declarator (wrapped declaration).
        ("template <>\nint tpz<int>(int v);", 2, True),
        # The primary template is not a specialization.
        ("template <typename T> int tpz(T v);", 1, False),
        # An explicit instantiation declaration is not a specialization.
        ("extern template int tpz<long>(long);", 1, False),
        # A previous specialization closed by a brace does not reach the next
        # declarator on the following line.
        ("template <> struct Box<int> { int v; };\nint tpz(int v);", 2, False),
        # A previous declaration ending in ';' does not reach the declarator.
        ("template <> int other<int>(int);\nint tpz(int v);", 2, False),
        # A template<> spelled only in a comment is not code.
        ("// template <> int tpz<int>(int v);\nint tpz(int v);", 2, False),
    ],
)
def test_spelled_introducer_marks_only_its_own_declarator(
    text: str, line_no: int, expected: bool
) -> None:
    assert _detect(text, line_no) is expected
