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

"""Direct unit coverage for ``dedup_versioned_namespace_alias_items`` --
the same-snapshot using-declaration alias collapse used by
``dumper_castxml.py``'s ``_iter_public_constants``.
"""

from __future__ import annotations

from abicheck.qualified_name_segments import dedup_versioned_namespace_alias_items


class TestDedupVersionedNamespaceAliasItems:
    def test_alias_pair_with_matching_value_collapses_to_the_stripped_name(
        self,
    ) -> None:
        items = [
            ("detail::v1::x", "42", "a.h"),
            ("detail::x", "42", "a.h"),
        ]
        result = dedup_versioned_namespace_alias_items(items)
        assert result == [("detail::x", "42", "a.h")]

    def test_differing_values_are_not_merged(self) -> None:
        # A using-declaration can never legally re-export a constant under a
        # different value -- a value mismatch means these are two genuinely
        # distinct declarations, not an alias pair, so both survive.
        items = [
            ("v1::kOther", "1", "a.h"),
            ("kOther", "2", "a.h"),
        ]
        result = dedup_versioned_namespace_alias_items(items)
        assert result == items

    def test_no_alias_present_leaves_the_versioned_item_untouched(self) -> None:
        # The common case: a versioned-namespace declaration with no
        # `using` re-export in the same snapshot has no alias spelling to
        # collapse onto.
        items = [("detail::v1::kVersionOnly", "9", "a.h")]
        result = dedup_versioned_namespace_alias_items(items)
        assert result == items

    def test_leaf_name_shaped_like_a_version_segment_is_never_stripped(self) -> None:
        # `version_strip_segments` never treats the last segment as a
        # namespace -- a constant literally named `v1` must not be corrupted
        # into its enclosing scope even if that scope happens to also exist.
        items = [("ns::v1", "1", "a.h"), ("ns", "1", "a.h")]
        result = dedup_versioned_namespace_alias_items(items)
        assert result == items

    def test_unrelated_items_are_unaffected(self) -> None:
        items = [("a::b", "1", "x.h"), ("c::d", "2", "y.h")]
        result = dedup_versioned_namespace_alias_items(items)
        assert result == items

    def test_input_order_does_not_affect_the_outcome(self) -> None:
        forward = [("detail::v1::x", "42", "a.h"), ("detail::x", "42", "a.h")]
        backward = [("detail::x", "42", "a.h"), ("detail::v1::x", "42", "a.h")]
        assert dedup_versioned_namespace_alias_items(forward) == [
            ("detail::x", "42", "a.h")
        ]
        assert dedup_versioned_namespace_alias_items(backward) == [
            ("detail::x", "42", "a.h")
        ]
