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

"""Unit tests for the typed Tier-2 request structs (ADR-037 D2 / G22 Phase 1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, CompareResult, InputSpec, OutputSpec
from abicheck.errors import ValidationError


class TestInputSpec:
    def test_of_coerces_str_to_path(self):
        spec = InputSpec.of("lib.so", headers=["a.h", "b.h"], includes=["inc"])
        assert spec.path == Path("lib.so")
        assert spec.headers == (Path("a.h"), Path("b.h"))
        assert spec.includes == (Path("inc"),)

    def test_of_single_string_is_one_path_not_per_character(self):
        # A bare string must be one path, not a tuple of per-character paths.
        spec = InputSpec.of("lib.so", headers="include/api.h")
        assert spec.headers == (Path("include/api.h"),)

    def test_of_single_path_is_one_path(self):
        spec = InputSpec.of("lib.so", includes=Path("inc"))
        assert spec.includes == (Path("inc"),)

    def test_of_defaults_are_empty_tuples(self):
        spec = InputSpec.of("lib.so")
        assert spec.headers == ()
        assert spec.includes == ()
        assert spec.debug_roots == ()
        assert spec.pdb is None
        assert spec.version == ""

    def test_of_pdb_coerced(self):
        spec = InputSpec.of("lib.so", pdb="lib.pdb", debug_roots=["dbg"])
        assert spec.pdb == Path("lib.pdb")
        assert spec.debug_roots == (Path("dbg"),)

    def test_is_frozen(self):
        spec = InputSpec.of("lib.so")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.path = Path("other.so")  # type: ignore[misc]

    # ── ADR-055 D1: sources/build_info/dump_manifest/compile/public_header_dirs ──

    def test_adr055_fields_default_to_none_or_empty(self):
        spec = InputSpec.of("lib.so")
        assert spec.sources is None
        assert spec.build_info is None
        assert spec.dump_manifest is None
        assert spec.compile is None
        assert spec.public_header_dirs == ()

    def test_of_coerces_sources_and_build_info_to_path(self):
        spec = InputSpec.of("lib.so", sources="src", build_info="build")
        assert spec.sources == Path("src")
        assert spec.build_info == Path("build")

    def test_of_coerces_public_header_dirs(self):
        spec = InputSpec.of("lib.so", public_header_dirs=["a", "b"])
        assert spec.public_header_dirs == (Path("a"), Path("b"))

    # ── ADR-055 D4: follow_linker_scripts ─────────────────────────────────────

    def test_follow_linker_scripts_defaults_true(self):
        # Matches `resolve_input`'s own default, so no pre-existing caller's
        # behaviour changes just because the field appeared.
        assert InputSpec.of("lib.so").follow_linker_scripts is True
        assert InputSpec(path=Path("lib.so")).follow_linker_scripts is True

    def test_of_passes_through_follow_linker_scripts(self):
        spec = InputSpec.of("lib.so", follow_linker_scripts=False)
        assert spec.follow_linker_scripts is False

    def test_of_passes_through_compile_and_dump_manifest(self):
        from abicheck.compile_context import CompileContext

        compile_ctx = CompileContext(sysroot=Path("/sysroot"))
        spec = InputSpec.of("lib.so", compile=compile_ctx)
        assert spec.compile is compile_ctx


class TestCompareRequestDefaults:
    def test_scope_public_defaults_true(self):
        # The headline drift fix: one default for every front-end (ADR-037 §Context #1).
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.scope_public is True

    def test_other_defaults(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.lang == "c++"
        assert req.frontend == "auto"
        assert req.has_sources is False
        assert req.policy == "strict_abi"
        assert req.policy_file_path is None
        assert req.suppress is None
        assert req.force_public_symbols is None
        assert req.pattern_verdicts is False
        assert req.enable_debuginfod is False
        # ADR-055 D1
        assert req.depth is None
        assert req.frontend_context == "host"

    def test_adr055_resolution_parity_defaults(self):
        # ADR-055 D1's second slice: additive, so an existing caller's
        # behaviour is unchanged until it opts in.
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.dwarf_only is False
        assert req.debug_format is None
        assert req.include_labels == ()
        assert req.follow_dependencies is False
        assert req.dependency_search_paths == ()
        assert req.ld_library_path == ""

    def test_is_frozen(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.scope_public = False  # type: ignore[misc]

    def test_distinct_instances_do_not_share_defaults(self):
        # A frozen dataclass with a bare mutable default would share it across
        # instances; the struct fields here are immutable, so this must hold.
        a = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        c = CompareRequest(old=InputSpec.of("c"), new=InputSpec.of("d"))
        assert a.old is not c.old
        assert a.old.headers == c.old.headers == ()


class TestCompareRequestValidate:
    def test_valid_request_has_no_errors(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.validation_errors() == []
        assert req.validate() is req

    @pytest.mark.parametrize("lang", ["c", "c++", "C", "C++"])
    def test_supported_langs_accepted(self, lang):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), lang=lang)
        assert req.validation_errors() == []

    def test_unsupported_lang_rejected(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), lang="rust")
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "rust" in errors[0]
        with pytest.raises(ValidationError, match="rust"):
            req.validate()

    def test_empty_policy_rejected(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), policy="")
        assert any("policy" in e for e in req.validation_errors())

    def test_multiple_errors_collected(self):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), lang="go", policy=""
        )
        assert len(req.validation_errors()) == 2

    # ── D8/D9: --ast-frontend value + feasibility validation ─────────────────

    @pytest.mark.parametrize("frontend", ["auto", "castxml", "clang", "AUTO", "Clang"])
    def test_supported_frontends_accepted(self, frontend):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), frontend=frontend
        )
        assert req.validation_errors() == []

    def test_unsupported_frontend_rejected(self):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), frontend="gccxml"
        )
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "gccxml" in errors[0]
        # Allowed set is surfaced so the user can self-correct.
        assert "castxml" in errors[0] and "clang" in errors[0]
        with pytest.raises(ValidationError, match="gccxml"):
            req.validate()

    def test_android_frontend_without_sources_rejected(self):
        # 'android' is source-ABI only (no header-AST path) — a header-only run
        # can't use it (ADR-037 D8/D9).
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), frontend="android"
        )
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "android" in errors[0] and "--sources" in errors[0]

    def test_android_frontend_with_sources_accepted(self):
        req = CompareRequest(
            old=InputSpec.of("a"),
            new=InputSpec.of("b"),
            frontend="android",
            has_sources=True,
        )
        assert req.validation_errors() == []

    def test_android_frontend_with_legacy_has_sources_accepted(self):
        # ADR-055 D1: has_sources=True (no inline InputSpec.sources/build_info)
        # is the one combination that's actually reachable -- it reuses a
        # pre-captured header-abi dump outside run_compare_request's own
        # inline evidence collection.
        req = CompareRequest(
            old=InputSpec.of("a"),
            new=InputSpec.of("b"),
            frontend="android",
            has_sources=True,
        )
        assert req.validation_errors() == []

    def test_android_frontend_with_inputspec_sources_accepted_at_validation_time(
        self, tmp_path
    ):
        """ADR-055 D1 (Codex review, second round): whether InputSpec.sources
        is compatible with frontend="android" depends on whether it's a raw
        tree (rejected) or a prebuilt evidence pack (valid) -- that
        filesystem-dependent distinction is checked at runtime in
        service.run_compare_request, not here (this leaf module has no
        cluster-only pack-detection helpers available)."""
        req = CompareRequest(
            old=InputSpec.of("a", sources=tmp_path),
            new=InputSpec.of("b"),
            frontend="android",
        )
        assert req.validation_errors() == []

    def test_android_frontend_with_inputspec_build_info_accepted(self, tmp_path):
        """Codex review (third round): InputSpec.build_info alone must also
        satisfy the android feasibility rule, not just sources/has_sources --
        embed_build_source auto-detects a pack directory in either
        build_info or sources, so a prebuilt evidence pack passed via
        build_info is exactly the same "already have a pre-captured
        header-abi dump" case this rule exists to allow."""
        req = CompareRequest(
            old=InputSpec.of("a", build_info=tmp_path),
            new=InputSpec.of("b"),
            frontend="android",
        )
        assert req.validation_errors() == []

    def test_missing_policy_file_rejected(self, tmp_path):
        # D9 pre-flight: a --policy path that doesn't exist errors identically
        # from CLI and MCP (one Tier-2 rule).
        missing = tmp_path / "nope.yml"
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), policy_file_path=missing
        )
        errors = req.validation_errors()
        assert any("policy file not found" in e for e in errors)

    def test_existing_policy_file_accepted(self, tmp_path):
        present = tmp_path / "policy.yml"
        present.write_text("base_policy: strict_abi\n")
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), policy_file_path=present
        )
        assert req.validation_errors() == []

    # ── ADR-055 D1: --depth validation ────────────────────────────────────────

    @pytest.mark.parametrize("depth", ["binary", "headers", "build", "source", "BUILD"])
    def test_supported_depths_accepted(self, depth):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), depth=depth)
        assert req.validation_errors() == []

    def test_unsupported_depth_rejected(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), depth="graph")
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "graph" in errors[0]
        with pytest.raises(ValidationError, match="graph"):
            req.validate()

    def test_depth_none_is_not_validated(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), depth=None)
        assert req.validation_errors() == []

    # ── ADR-055 D1: frontend_context validation ───────────────────────────────

    @pytest.mark.parametrize("value", ["host", "device", "HOST", "Device"])
    def test_supported_frontend_contexts_accepted(self, value):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), frontend_context=value
        )
        assert req.validation_errors() == []

    def test_unsupported_frontend_context_rejected(self):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), frontend_context="DEVICE2"
        )
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "DEVICE2" in errors[0]
        with pytest.raises(ValidationError, match="DEVICE2"):
            req.validate()

    def test_headers_alongside_dump_manifest_rejected(self, tmp_path):
        """CodeRabbit review: dump_manifest replaces headers for the primary
        AST -- forwarding both mixes two declared surfaces into one
        snapshot's provenance/dialect detection (mirrors the CLI's own
        --dump-manifest/-H UsageError). Previously only checked at runtime in
        service.run_compare_request; moved into this Tier-2 pre-flight
        validate() so a caller using validation_errors()/validate() alone
        also catches it, not only one that goes on to call
        run_compare_request."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )
        req = CompareRequest(
            old=InputSpec.of("a", headers=["old.h"], dump_manifest=dm),
            new=InputSpec.of("b"),
        )
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "mutually exclusive" in errors[0] and "old" in errors[0]
        with pytest.raises(ValidationError, match="mutually exclusive"):
            req.validate()

    def test_dump_manifest_alone_is_not_rejected_by_validate(self, tmp_path):
        """A dump_manifest with no ordinary headers on that side must not be
        caught by the mutual-exclusivity guard above."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )
        req = CompareRequest(
            old=InputSpec.of("a", dump_manifest=dm), new=InputSpec.of("b")
        )
        assert req.validation_errors() == []


class TestCompareRequestReplace:
    def test_replace_round_trips_fields(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        changed = req.replace(scope_public=False, lang="c")
        assert changed.scope_public is False
        assert changed.lang == "c"
        # Original is untouched (frozen + copy semantics).
        assert req.scope_public is True
        assert req.lang == "c++"
        # Unchanged fields are carried over verbatim.
        assert changed.old is req.old
        assert changed.policy == req.policy


class TestCompareRequestRuntimeResolvableAnnotations:
    def test_get_type_hints_resolves_without_nameerror(self):
        """Codex review, PR B slice 1 follow-up: with `from __future__ import
        annotations` (PEP 563), every annotation is a string evaluated lazily
        -- `typing.get_type_hints()` (used by schema generators, docs tools,
        or any other API consumer introspecting this public dataclass) needs
        every name it references to be resolvable in the *module's runtime
        globals*, not merely under `TYPE_CHECKING`. `pack_policy_overrides`/
        `pack_internal_namespaces` referenced `ChangeKind`/`Verdict`, which
        were only imported under `TYPE_CHECKING` -- `get_type_hints()` raised
        `NameError: name 'ChangeKind' is not defined`. Fixed by importing
        both unconditionally (verified no import cycle: neither
        `checker_policy` nor `change_registry_types` imports `api_types`,
        directly or transitively)."""
        import typing

        hints = typing.get_type_hints(CompareRequest)
        assert "pack_policy_overrides" in hints
        assert "pack_internal_namespaces" in hints


class TestOutputSpec:
    def test_defaults(self):
        out = OutputSpec()
        assert out.fmt == "text"
        assert out.path is None

    def test_is_frozen(self):
        out = OutputSpec(fmt="json")
        with pytest.raises(dataclasses.FrozenInstanceError):
            out.fmt = "sarif"  # type: ignore[misc]


class TestCompareResult:
    """ADR-055 D2: the typed result wrapper."""

    def _result(self):
        from abicheck.checker_types import DiffResult
        from abicheck.model import AbiSnapshot

        diff = DiffResult(old_version="1.0", new_version="2.0", library="lib")
        old = AbiSnapshot(library="lib", version="1.0")
        new = AbiSnapshot(library="lib", version="2.0")
        return CompareResult(diff=diff, old_snapshot=old, new_snapshot=new), diff, old, new

    def test_as_tuple_matches_the_legacy_return_shape(self):
        # The whole point of the wrapper is that it is a *rename* of the tuple,
        # not a reordering — a caller unpacking either must see the same three
        # objects in the same order.
        result, diff, old, new = self._result()
        assert result.as_tuple() == (diff, old, new)

    def test_suppression_defaults_to_none(self):
        result, _diff, _old, _new = self._result()
        assert result.suppression is None

    def test_carries_the_resolved_suppression_list(self):
        from abicheck.suppression import SuppressionList

        _result, diff, old, new = self._result()
        suppression = SuppressionList([])  # empty rule set is enough here
        result = CompareResult(
            diff=diff, old_snapshot=old, new_snapshot=new, suppression=suppression
        )
        assert result.suppression is suppression
        # ...and it stays out of the tuple view, which is the legacy shape.
        assert result.as_tuple() == (diff, old, new)

    def test_is_frozen(self):
        result, _diff, _old, new = self._result()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.old_snapshot = new  # type: ignore[misc]

    def test_get_type_hints_resolves_without_nameerror(self):
        """CodeRabbit review, fresh evidence, PR #1032:
        `TestCompareRequestRuntimeResolvableAnnotations` above already
        establishes the pattern (PEP 563 -- every annotation is a lazy
        string that `typing.get_type_hints()` must resolve against the
        *module's runtime globals*, not merely under `TYPE_CHECKING`) for
        `CompareRequest`; `CompareResult` had the identical bug for six of
        `api_types.py`'s then-seven `TYPE_CHECKING`-only names, all
        referenced directly by its own fields: `diff: DiffResult`,
        `old_snapshot`/`new_snapshot: AbiSnapshot`, `suppression:
        SuppressionList | None`, `exit_decision: ExitDecision | None`,
        `severity_config: SeverityConfig | None`. The review comment's own
        suggestion (import only `DiffResult`) would have left
        `get_type_hints()` failing on the very next name (`AbiSnapshot`) --
        fixed by importing all five unconditionally (verified no import
        cycle: none of `checker_types`/`model`/`suppression`/
        `policy.exit_decision`/`policy.severity` imports `api_types`,
        directly or transitively). `CompileContext`/`DumpManifest` stay
        `TYPE_CHECKING`-only since `CompareResult` never references them."""
        import typing

        hints = typing.get_type_hints(CompareResult)
        assert hints["diff"] is not None
        assert "old_snapshot" in hints
        assert "new_snapshot" in hints
        assert "suppression" in hints
        assert "exit_decision" in hints
        assert "severity_config" in hints


class TestDebugFormatValidation:
    """ADR-055 D1 second slice (Codex review): the newly-exposed
    ``debug_format`` must fail through this module's ``ValidationError``
    contract, and accept the same spellings the CLI's case-insensitive
    ``--debug-format`` choice does."""

    def _request(self, debug_format):
        return CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), debug_format=debug_format
        )

    @pytest.mark.parametrize("value", ["auto", "dwarf", "btf", "ctf"])
    def test_cli_choice_values_are_accepted(self, value):
        assert self._request(value).validation_errors() == []

    @pytest.mark.parametrize("value", ["DWARF", "Btf", "CTF"])
    def test_accepted_case_insensitively_like_the_cli_choice(self, value):
        # click.Choice(..., case_sensitive=False) — an API caller typing
        # "DWARF" must not behave differently from the CLI caller who did.
        assert self._request(value).validation_errors() == []

    def test_a_typo_is_a_validation_error_not_a_raw_valueerror(self):
        # Without this it reached dumper_debug._resolve_debug_metadata, whose
        # comparisons are lowercase-only, and surfaced as a bare ValueError.
        with pytest.raises(ValidationError, match="unsupported debug format"):
            self._request("dwraf").validate()

    def test_none_stays_valid(self):
        assert self._request(None).validation_errors() == []
