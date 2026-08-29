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

"""Direct unit tests for :func:`abicheck.binary_utils.strip_vendor_hash`."""

from __future__ import annotations

from abicheck.binary_utils import strip_vendor_hash


class TestStripVendorHash:
    def test_strips_lowercase_hex_hash_before_so(self) -> None:
        assert strip_vendor_hash("libfoo-abcdef123456.so.1") == "libfoo.so.1"

    def test_strips_uppercase_hex_hash_before_so(self) -> None:
        # auditwheel/delocate can emit an uppercase-hex hash -- checking
        # only lowercase let two releases differing solely in the case of
        # this generated hash key as unrelated libraries (Codex).
        assert strip_vendor_hash("libfoo-ABCDEF123456.so.1") == "libfoo.so.1"

    def test_strips_mixed_case_hex_hash_before_so(self) -> None:
        assert strip_vendor_hash("libfoo-AbCdEf123456.so.1") == "libfoo.so.1"

    def test_strips_uppercase_hex_hash_before_dylib(self) -> None:
        assert strip_vendor_hash("libfoo-ABCDEF123456.dylib") == "libfoo.dylib"

    def test_strips_uppercase_hash_before_uppercase_extension(self) -> None:
        # The extension spelling itself is matched case-insensitively too.
        assert strip_vendor_hash("libfoo-ABCDEF123456.SO.1") == "libfoo.SO.1"

    def test_lowercase_and_uppercase_hash_reduce_to_the_same_key(self) -> None:
        # The actual bug: two releases differing only in the generated
        # hash's case must key identically, or compare-release reports
        # spurious removal+addition noise every rebuild (G9).
        assert strip_vendor_hash("libfoo-abcdef123456.so.1") == strip_vendor_hash(
            "libfoo-ABCDEF123456.so.1"
        )

    def test_purely_decimal_suffix_is_still_not_stripped_case_insensitively(
        self,
    ) -> None:
        # The at-least-one-hex-letter guard still applies regardless of case.
        assert strip_vendor_hash("libfoo-100200.so.1") == "libfoo-100200.so.1"

    def test_short_hyphenated_name_is_untouched_regardless_of_case(self) -> None:
        assert strip_vendor_hash("libFOO-CAFE.so") == "libFOO-CAFE.so"

    def test_case_of_the_unrelated_stem_is_preserved(self) -> None:
        # Only the matched hash/extension span is affected -- the rest of
        # the name keeps its original case.
        assert strip_vendor_hash("LibFoo-ABCDEF123456.so.1") == "LibFoo.so.1"

    def test_numeric_suffix_without_a_library_extension_is_untouched(self) -> None:
        # Bug class (CodeRabbit review, PR #942): the numeric-version
        # lookahead branch matched *any* ``.<digits>`` continuation
        # regardless of what followed, so a non-library filename that merely
        # happens to contain a hyphenated hex run before a numeric segment
        # was mis-stripped -- exercised across several sibling shapes, not
        # just the one reported input, since a single fixed example only
        # forecloses that exact string.
        assert strip_vendor_hash("plugin-abcdef.1.json") == "plugin-abcdef.1.json"
        assert (
            strip_vendor_hash("libfoo-abcdef123456.1.txt")
            == "libfoo-abcdef123456.1.txt"
        )
        assert strip_vendor_hash("data-cafebabe.2.tar") == "data-cafebabe.2.tar"
        assert strip_vendor_hash("libfoo-abcdef123456.1") == "libfoo-abcdef123456.1"

    def test_numeric_version_segment_before_the_library_extension_is_still_stripped(
        self,
    ) -> None:
        # The lookahead branch this bug lives in exists precisely for a
        # version segment genuinely sitting between the hash and the
        # extension (e.g. auditwheel's own multi-component SONAME) -- the
        # fix must not regress that real, intended case.
        assert strip_vendor_hash("libfoo-abcdef123456.1.2.so") == "libfoo.1.2.so"
        assert strip_vendor_hash("libfoo-abcdef123456.1.dylib") == "libfoo.1.dylib"
