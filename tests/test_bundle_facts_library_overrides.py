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

"""Unit tests for :mod:`abicheck.workflows.bundle_facts_library_overrides`
(G38 Phase 17) -- the per-library header/include/compile-context override
manifest parser for ``compare --old-bundle-facts``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.workflows.bundle_facts_library_overrides import (
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

    def test_non_string_key_is_rejected_cleanly_not_a_raw_typeerror(self) -> None:
        """Codex review: a YAML mapping can carry a non-string key (e.g. a
        bare integer); the unrecognized-key check's own ``sorted(unknown)``
        would otherwise raise a raw ``TypeError`` comparing ``str`` to
        ``int`` instead of ``BundleFactsLibraryOverridesError``."""
        with pytest.raises(BundleFactsLibraryOverridesError, match="keys must be strings"):
            parse_bundle_facts_library_overrides({"libfoo.so": {"bogus": 1, 2: 3}})

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

    def test_no_base_dir_leaves_relative_paths_unresolved(self) -> None:
        # Default (no base_dir): unchanged from every pre-existing test above
        # -- a Python-API caller with no real manifest file behind the raw
        # dict gets the path exactly as written, resolution deferred to it.
        result = parse_bundle_facts_library_overrides(
            {
                "libfoo.so": {
                    "headers": ["include/foo"],
                    "sysroot": "opt/sysroot",
                }
            }
        )
        assert result.headers["libfoo.so"] == [Path("include/foo")]
        assert result.compile["libfoo.so"].sysroot == Path("opt/sysroot")

    def test_base_dir_anchors_relative_headers_includes_and_sysroot(self) -> None:
        """Codex review: a manifest is a portable document -- a relative
        ``headers``/``includes``/``sysroot`` path must resolve against the
        manifest file's own directory, not the process's current working
        directory, mirroring ``dump_manifest.load_manifest()``'s identical
        ``base_dir`` handling."""
        base_dir = Path("/some/manifest/dir")
        result = parse_bundle_facts_library_overrides(
            {
                "libfoo.so": {
                    "headers": ["include/foo"],
                    "includes": ["include/extra"],
                    "sysroot": "opt/sysroot",
                }
            },
            base_dir=base_dir,
        )
        assert result.headers["libfoo.so"] == [Path("/some/manifest/dir/include/foo")]
        assert result.includes["libfoo.so"] == [
            Path("/some/manifest/dir/include/extra")
        ]
        assert result.compile["libfoo.so"].sysroot == Path(
            "/some/manifest/dir/opt/sysroot"
        )

    def test_base_dir_never_alters_an_already_absolute_path(self) -> None:
        base_dir = Path("/some/manifest/dir")
        result = parse_bundle_facts_library_overrides(
            {
                "libfoo.so": {
                    "headers": ["/abs/include/foo"],
                    "sysroot": "/abs/sysroot",
                }
            },
            base_dir=base_dir,
        )
        assert result.headers["libfoo.so"] == [Path("/abs/include/foo")]
        assert result.compile["libfoo.so"].sysroot == Path("/abs/sysroot")
