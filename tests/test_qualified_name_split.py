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

"""``model.qualified_name_split.split_top_level_scopes`` -- the shared
bracket-depth-aware ``"::"`` splitter ``qualified_name_segments.raw_segments``
now delegates to (ADR-063 Phase 6, PDB EntityId slice). Mirrors
``qualified_name_segments.py``'s own existing coverage of the identical
algorithm (via ``raw_segments``/``segments``), since this is a mechanical
relocation, not new logic -- see that module's own tests for the broader
segment-splitting behavior this function's callers build on.
"""

from __future__ import annotations

from abicheck.model.qualified_name_split import split_top_level_scopes


def test_empty_string_yields_no_segments() -> None:
    assert split_top_level_scopes("") == []


def test_bare_name_is_its_own_single_segment() -> None:
    assert split_top_level_scopes("Widget") == ["Widget"]


def test_simple_namespace_chain() -> None:
    assert split_top_level_scopes("ns::inner::Widget") == ["ns", "inner", "Widget"]


def test_template_arguments_are_kept_intact_on_their_own_segment() -> None:
    assert split_top_level_scopes("ns::Vector<int>") == ["ns", "Vector<int>"]


def test_scope_separator_inside_template_arguments_is_not_a_split_point() -> None:
    assert split_top_level_scopes("ns::Map<std::pair<int, int>>::iterator") == [
        "ns",
        "Map<std::pair<int, int>>",
        "iterator",
    ]


def test_nested_template_depth_is_tracked_correctly() -> None:
    """A closing '>>' at the end of a doubly-nested template must not be
    mistaken for a single-level close -- depth must reach exactly 0 only
    after both '>' characters are consumed."""
    assert split_top_level_scopes("A<B<C>>::D") == ["A<B<C>>", "D"]


def test_leading_and_trailing_scope_separators_produce_no_empty_segments() -> None:
    """A leading '::' (global-scope qualification) or a stray trailing one
    must not manufacture an empty segment a caller could mistake for a
    real (if empty-named) scope."""
    assert split_top_level_scopes("::ns::Widget") == ["ns", "Widget"]
    assert split_top_level_scopes("ns::Widget::") == ["ns", "Widget"]


def test_whitespace_around_a_segment_is_stripped() -> None:
    assert split_top_level_scopes("ns :: Widget") == ["ns", "Widget"]


def test_unbalanced_closing_angle_bracket_does_not_underflow_depth() -> None:
    """A stray '>' with no matching '<' (malformed input, e.g. a comparison
    operator leaking into what's assumed to be a pure name) must not drive
    depth negative and start treating a REAL top-level '::' after it as
    still nested."""
    assert split_top_level_scopes("A>::B") == ["A>", "B"]
