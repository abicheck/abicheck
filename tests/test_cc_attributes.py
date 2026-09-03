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

"""Tests for :mod:`abicheck.model.cc_attributes` (ADR-061 D1, split out of
``diff_symbols.py`` so ``extract``'s ``tu_merge.py`` can use
``is_cc_attribute`` without a forbidden ``extract -> compare`` edge).
"""

from __future__ import annotations

from abicheck import diff_symbols, tu_merge
from abicheck.model import cc_attributes


def test_is_cc_attribute_matches_known_calling_conventions() -> None:
    assert cc_attributes.is_cc_attribute("stdcall")
    assert cc_attributes.is_cc_attribute("fastcall(regparm=3)")
    assert not cc_attributes.is_cc_attribute("noreturn")


def test_diff_symbols_reexports_the_identical_function_object() -> None:
    assert diff_symbols._is_cc_attribute is cc_attributes.is_cc_attribute


def test_tu_merge_imports_the_same_function_object() -> None:
    assert tu_merge._is_cc_attribute is cc_attributes.is_cc_attribute
