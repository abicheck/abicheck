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
    load_bundle_facts_library_overrides,
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

    def test_empty_entry_is_rejected(self) -> None:
        """Codex review: an empty entry (``libfoo.so: {}``) is a
        syntactically valid mapping that adds this library to none of
        headers/includes/compile_by_library -- it would otherwise be
        silently accepted and invisible to every later check keyed off
        those output maps (e.g. bundle_side_input.py's matched-library
        validation), with no signal the requested override was never
        applied to anything."""
        with pytest.raises(
            BundleFactsLibraryOverridesError, match="at least one override field"
        ):
            parse_bundle_facts_library_overrides({"libfoo.so": {}})

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

    def test_manifest_key_may_be_the_real_versioned_filename(self) -> None:
        # Codex review, fresh evidence: `known_libraries` (the resolved
        # NEW-side match map) and `BundleFacts.per_library_snapshots` are
        # always keyed by the bundle's *canonical* library name
        # (`_canonical_library_key`, e.g. "libfoo.so"), never by a
        # discovered filename's literal, possibly-versioned spelling. A
        # runtime package with no unversioned dev symlink only ever ships
        # "libfoo.so.1" on disk -- a manifest author keying an entry by that
        # real, on-disk filename must not be rejected as "not a library in
        # this bundle" purely because it doesn't match the canonical
        # spelling by coincidence.
        result = parse_bundle_facts_library_overrides(
            {"libfoo.so.1": {"headers": ["x"]}}, known_libraries={"libfoo.so"}
        )
        assert result.headers == {"libfoo.so": [Path("x")]}

    def test_two_manifest_keys_canonicalizing_to_the_same_library_are_rejected(
        self,
    ) -> None:
        # Two different raw spellings ("libfoo.so.1"/"libfoo.so.2") of the
        # SAME canonical library must not silently overwrite one another in
        # the output maps -- that would leave whichever key iterates last
        # in effect with no signal the other entry was ever discarded.
        with pytest.raises(
            BundleFactsLibraryOverridesError, match="both refer to the same library"
        ):
            parse_bundle_facts_library_overrides(
                {
                    "libfoo.so.1": {"headers": ["a"]},
                    "libfoo.so.2": {"headers": ["b"]},
                },
                known_libraries={"libfoo.so"},
            )

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

    @pytest.mark.parametrize("field_name", ["frontend", "frontend_context"])
    def test_explicit_null_is_rejected_cleanly_not_a_raw_assertionerror(
        self, field_name: str
    ) -> None:
        """Codex review: ``frontend``/``frontend_context`` are non-nullable
        fields with real defaults ("auto"/"host") -- an explicit YAML
        ``null`` (as opposed to the key being absent) used to sail past the
        ``value is not None and not isinstance(...)`` type check and only
        fail later at ``assert frontend is not None``, an ``AssertionError``
        instead of the promised ``BundleFactsLibraryOverridesError``."""
        with pytest.raises(BundleFactsLibraryOverridesError, match="must be a string"):
            parse_bundle_facts_library_overrides({"libfoo.so": {field_name: None}})

    @pytest.mark.parametrize(
        "field_name,bad_value,accepted_snippet",
        [
            ("frontend", "clnag", "recognized AST frontend"),
            ("frontend_context", "gpu", "not supported"),
        ],
    )
    def test_invalid_enum_value_is_rejected(
        self, field_name: str, bad_value: str, accepted_snippet: str
    ) -> None:
        """Codex review: a correctly-*typed* but unrecognized ``frontend``/
        ``frontend_context`` string (a typo, e.g. ``"clnag"``) used to reach
        ``CompileContext`` unchecked and only fail later, deep in extraction
        -- surfacing as a generic error instead of the clean, immediate
        ``BundleFactsLibraryOverridesError`` every other malformed field
        here raises."""
        with pytest.raises(BundleFactsLibraryOverridesError, match=accepted_snippet):
            parse_bundle_facts_library_overrides({"libfoo.so": {field_name: bad_value}})

    @pytest.mark.parametrize("field_name", ["headers", "includes"])
    def test_empty_path_string_in_list_is_rejected(self, field_name: str) -> None:
        """Codex review: an empty string is a valid ``str``, so it passed
        the list-of-strings type check cleanly -- but ``_resolve_path("",
        base_dir=...)`` resolves an empty *relative* path to ``base_dir``
        itself (``Path("/x") / Path("") == Path("/x")``), silently turning
        an accidentally blank ``headers: [""]`` entry into "scan the
        manifest's own directory" instead of a clean rejection. Regression
        checked with a real ``base_dir`` so the bug (a *resolved* Path
        equal to ``base_dir``, not just an unresolved empty string) would
        actually reproduce against the pre-fix code."""
        with pytest.raises(BundleFactsLibraryOverridesError, match="empty string"):
            parse_bundle_facts_library_overrides(
                {"libfoo.so": {field_name: [""]}},
                base_dir=Path("/some/manifest/dir"),
            )

    @pytest.mark.parametrize(
        "field_name,mixed_case_value,canonical_value",
        [
            ("frontend", "CLANG", "clang"),
            ("frontend_context", "DEVICE", "device"),
        ],
    )
    def test_enum_value_is_case_insensitive_like_the_cli(
        self, field_name: str, mixed_case_value: str, canonical_value: str
    ) -> None:
        """Codex review: ``--ast-frontend``'s own Click choice is case-
        insensitive and the typed API normalizes both fields via
        ``.lower()`` throughout -- this manifest's raw membership check
        used to reject an otherwise-valid CLI-equivalent spelling like
        ``frontend: CLANG``."""
        result = parse_bundle_facts_library_overrides(
            {"libfoo.so": {field_name: mixed_case_value}}
        )
        assert getattr(result.compile["libfoo.so"], field_name) == canonical_value

    @pytest.mark.parametrize("field_name", ["sysroot", "gcc_path", "gcc_prefix"])
    def test_empty_nullable_string_field_is_rejected_rather_than_silently_dropped(
        self, field_name: str
    ) -> None:
        """Codex review (originally found for ``sysroot``, fresh evidence
        extends it to ``gcc_path``/``gcc_prefix`` -- identical reasoning
        applies to all three nullable string fields): an empty string is
        falsy, so ``value=... if value else None`` would silently swallow
        it -- but the key is still present, so this library still gets its
        own per-library ``CompileContext``, which *replaces* the uniform one
        entirely (``bundle_side_input.py``'s ``.get(key, compile)``
        fallback). An accidentally blank value would therefore silently
        discard that library's uniform ``--compiler``/``--compiler-option``/
        toolchain selection rather than being rejected."""
        with pytest.raises(BundleFactsLibraryOverridesError, match="empty string"):
            parse_bundle_facts_library_overrides({"libfoo.so": {field_name: ""}})

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


class TestLoadBundleFactsLibraryOverrides:
    """Tests for the real-file-reading counterpart,
    :func:`load_bundle_facts_library_overrides` -- covers failure modes
    that only exist at the raw-YAML-text layer, before
    ``parse_bundle_facts_library_overrides`` ever sees a parsed ``dict``."""

    def test_unhashable_yaml_key_is_a_clean_error_not_a_raw_typeerror(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a syntactically valid YAML mapping
        can use a non-scalar (list) node as a key -- e.g. ``? [a, b]\\n:
        1``. ``dump_manifest._load_yaml_strict``'s own duplicate-key set
        (``key in seen``/``seen.add(key)``) raises a raw, untranslated
        ``TypeError: unhashable type`` for that, which the loader's
        previous ``except ManifestValidationError`` clause did not catch."""
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so:\n  ? [a, b]\n  : 1\n")

        with pytest.raises(BundleFactsLibraryOverridesError, match="invalid YAML"):
            load_bundle_facts_library_overrides(manifest)

    def test_invalid_utf8_manifest_is_a_clean_error(self, tmp_path: Path) -> None:
        """Codex review, fresh evidence: ``UnicodeDecodeError`` is a
        ``ValueError`` subclass, so an invalid-UTF-8 manifest was still
        caught somewhere -- but only by ``dispatch()``'s generic ``except
        (SnapshotError, ValueError, OSError)`` clause, exiting 1 instead of
        the exit-64 usage error every other malformed manifest input here
        produces."""
        manifest = tmp_path / "manifest.yaml"
        manifest.write_bytes(b"libreal.so:\n  headers:\n    - \xff\xfe invalid utf-8\n")

        with pytest.raises(BundleFactsLibraryOverridesError, match="cannot decode as UTF-8"):
            load_bundle_facts_library_overrides(manifest)

    def test_deeply_nested_manifest_is_a_clean_error_not_a_raw_recursionerror(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a well-formed but sufficiently
        deeply nested manifest exhausts Python's own recursion limit
        inside ``_load_yaml_strict``'s recursive-descent parsing --
        ``RecursionError`` is not a ``ValueError`` subclass (unlike
        ``UnicodeDecodeError``), so it escaped every existing translation
        clause here and dispatch()'s generic ``except (SnapshotError,
        ValueError, OSError)`` clause alike, leaking a raw traceback."""
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("[" * 2000 + "]" * 2000)

        with pytest.raises(BundleFactsLibraryOverridesError, match="too deeply nested"):
            load_bundle_facts_library_overrides(manifest)
