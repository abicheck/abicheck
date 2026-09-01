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

"""Unit tests for :mod:`abicheck.bundle_facts_library_overrides` (G38 Phase 17)
-- the per-library header/include/compile-context override manifest parser
for ``compare --old-bundle-facts``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle_facts_library_overrides import (
    BundleFactsLibraryOverrides,
    BundleFactsLibraryOverridesError,
    parse_bundle_facts_library_overrides,
)


class TestParseBundleFactsLibraryOverrides:
    def test_empty_manifest_is_valid_and_changes_nothing(self) -> None:
        result = parse_bundle_facts_library_overrides({})
        assert result == BundleFactsLibraryOverrides()

    def test_valid_manifest_parses_every_field(self) -> None:
        raw = {
            "libdpc.so": {
                "headers": ["include/dpc"],
                "includes": ["include/common", "include/extra"],
                "gcc_path": "icpx",
                "gcc_prefix": "x86_64-linux-",
                "gcc_options": ["-fsycl", "-DONEDAL_DATA_PARALLEL"],
                "sysroot": "/opt/sysroot",
                "nostdinc": True,
                "frontend": "clang",
                "frontend_context": "device",
            },
            "libcpu.so": {
                "headers": ["include/cpu"],
            },
        }
        result = parse_bundle_facts_library_overrides(
            raw, known_libraries={"libdpc.so", "libcpu.so"}
        )
        assert result.headers == {
            "libdpc.so": [Path("include/dpc")],
            "libcpu.so": [Path("include/cpu")],
        }
        assert result.includes == {
            "libdpc.so": [Path("include/common"), Path("include/extra")]
        }
        ctx = result.compile["libdpc.so"]
        assert ctx.gcc_path == "icpx"
        assert ctx.gcc_prefix == "x86_64-linux-"
        assert ctx.gcc_option_tokens == ("-fsycl", "-DONEDAL_DATA_PARALLEL")
        assert ctx.sysroot == Path("/opt/sysroot")
        assert ctx.nostdinc is True
        assert ctx.frontend == "clang"
        assert ctx.frontend_context == "device"
        # A library with only headers gets no compile-context entry at all.
        assert "libcpu.so" not in result.compile

    def test_a_library_absent_from_the_manifest_keeps_the_uniform_fallback(
        self,
    ) -> None:
        # No entry at all for "libcpu.so" -- the whole point of this manifest
        # being additive, not replacing the uniform --header/--include/
        # compile-context flags.
        result = parse_bundle_facts_library_overrides(
            {"libdpc.so": {"headers": ["x"]}},
            known_libraries={"libdpc.so", "libcpu.so"},
        )
        assert "libcpu.so" not in result.headers
        assert "libcpu.so" not in result.includes
        assert "libcpu.so" not in result.compile

    def test_top_level_must_be_a_mapping(self) -> None:
        with pytest.raises(BundleFactsLibraryOverridesError, match="must be a mapping"):
            parse_bundle_facts_library_overrides(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_entry_must_be_a_mapping(self) -> None:
        with pytest.raises(BundleFactsLibraryOverridesError, match="must be a mapping"):
            parse_bundle_facts_library_overrides({"libfoo.so": ["not", "a", "dict"]})

    def test_unrecognized_key_is_rejected(self) -> None:
        with pytest.raises(BundleFactsLibraryOverridesError, match="unrecognized"):
            parse_bundle_facts_library_overrides({"libfoo.so": {"bogus": 1}})

    def test_a_library_name_outside_known_libraries_is_rejected(self) -> None:
        with pytest.raises(
            BundleFactsLibraryOverridesError, match="not a library in this bundle"
        ):
            parse_bundle_facts_library_overrides(
                {"typo_lib.so": {"headers": ["x"]}}, known_libraries={"libfoo.so"}
            )

    def test_known_libraries_none_skips_that_check(self) -> None:
        # No known_libraries given -- any library name is accepted, matching
        # the Python-API caller's own opt-in shape.
        result = parse_bundle_facts_library_overrides(
            {"anything.so": {"headers": ["x"]}}
        )
        assert "anything.so" in result.headers

    @pytest.mark.parametrize(
        "field_name,bad_value",
        [
            ("headers", "not-a-list"),
            ("headers", [1, 2]),
            ("includes", "not-a-list"),
            ("includes", [1, 2]),
            ("gcc_path", 5),
            ("gcc_prefix", 5),
            ("gcc_options", "not-a-list"),
            ("gcc_options", [1, 2]),
            ("sysroot", 5),
            ("nostdinc", "yes"),
            ("frontend", 5),
            ("frontend_context", 5),
        ],
    )
    def test_wrong_typed_field_is_rejected(
        self, field_name: str, bad_value: object
    ) -> None:
        with pytest.raises(BundleFactsLibraryOverridesError):
            parse_bundle_facts_library_overrides({"libfoo.so": {field_name: bad_value}})

    def test_empty_library_name_is_rejected(self) -> None:
        with pytest.raises(BundleFactsLibraryOverridesError, match="non-empty strings"):
            parse_bundle_facts_library_overrides({"": {"headers": ["x"]}})

    def test_gcc_options_defaults_to_no_tokens(self) -> None:
        result = parse_bundle_facts_library_overrides(
            {"libfoo.so": {"gcc_path": "gcc"}}
        )
        assert result.compile["libfoo.so"].gcc_option_tokens == ()
