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

    # ── ADR-068 §3 #19: budget_s finite/non-negative floor (Codex review,
    #    fresh evidence, PR #1178) ──────────────────────────────────────────

    @pytest.mark.parametrize("budget_s", [0, 0.0, 1, 900.5, None])
    def test_valid_budget_s_accepted(self, budget_s):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), budget_s=budget_s
        )
        assert req.validation_errors() == []

    @pytest.mark.parametrize(
        "budget_s", [float("nan"), float("inf"), float("-inf"), -1.0, -0.5]
    )
    def test_non_finite_or_negative_budget_s_rejected(self, budget_s):
        # The typed API reaches the identical deadline_scope(request.budget_s)
        # call `run_compare_request` enters with no CLI in between, so it
        # needs the same math.isfinite floor the CLI's own --budget parser
        # (_parse_budget) already applies -- an infinite deadline never trips
        # deadline.check()'s `left <= 0` test, and nan never compares true
        # either way, so both would otherwise silently disable the guard.
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), budget_s=budget_s
        )
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "budget_s" in errors[0]
        with pytest.raises(ValidationError, match="budget_s"):
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
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), depth="graph"
        )
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


class TestCompareRequestHashableWithEnvMatrix:
    """Codex review, P2: ``CompareRequest`` is a frozen dataclass with a
    dataclass-generated ``__hash__``, so every field it carries must itself
    be hashable for that generated method to work at all -- and
    ``env_matrix``, an :class:`~abicheck.environment_matrix.EnvironmentMatrix`,
    used to be unhashable (a plain, non-frozen dataclass with ``eq=True``
    gets ``__hash__`` set to ``None`` by default), which made *every*
    ``CompareRequest`` supplying real deployment configuration unhashable
    too -- breaking a typed-API caller using requests as set members or
    cache keys. Fixed by giving ``EnvironmentMatrix`` (and its ``sycl``/
    ``cuda`` sub-dataclasses) an explicit ``__hash__`` over a hashable
    projection of their fields; see ``EnvironmentMatrix.__hash__``'s own
    docstring for why the fields themselves stay ``list``/``dict``.
    """

    def _request(self, floor: str = "2.28") -> CompareRequest:
        from abicheck.environment_matrix import EnvironmentMatrix

        return CompareRequest(
            old=InputSpec.of("a"),
            new=InputSpec.of("b"),
            env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": floor}),
        )

    def test_hash_does_not_raise(self):
        # This is the reported defect verbatim: it used to raise
        # `TypeError: unhashable type: 'EnvironmentMatrix'`.
        assert isinstance(hash(self._request()), int)

    def test_two_requests_with_equal_env_matrix_hash_and_compare_equal(self):
        a = self._request()
        b = self._request()
        assert a == b
        assert hash(a) == hash(b)
        # And a real set/dict use, the exact scenario the finding names.
        assert {a, b} == {a}
        assert {a: "cached"}[b] == "cached"

    def test_two_requests_with_different_env_matrix_are_distinguishable(self):
        a = self._request("2.28")
        b = self._request("2.34")
        assert a != b
        # Hash collision is legal in general, but these two floors must not
        # be treated as the same cache key/set member.
        assert {a, b} == {a, b}

    def test_request_with_no_env_matrix_stays_hashable(self):
        # The plain default (env_matrix=None) must be unaffected.
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert isinstance(hash(req), int)


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


class TestCompareRequestRemovedFieldDoesNotShiftPositionalArgs:
    """Codex review, PR #1180 ("Prevent positional CompareRequest arguments
    from shifting"): removing ``reconcile_build_context`` from the middle of
    this dataclass used to silently rebind every field after it for a
    positional caller (a later ``bool`` landing in the next field's own
    slot, a later ``Path`` in ``diagnostic_comparison``'s). A ``KW_ONLY``
    sentinel now sits exactly where that field used to be, so such a call
    fails loudly at construction instead. The dummy trailing value below
    (``"env-matrix.yaml"``) is just an arbitrary string past the boundary --
    not a reference to the since-removed ``env_matrix_path`` field, which
    would have sat there before ADR-068 D5's ``compare --env-matrix``
    demotion (``CompareRequest.env_matrix`` now carries an already-resolved
    ``EnvironmentMatrix``, not a path).
    """

    def test_documented_short_positional_shape_still_works(self):
        old = InputSpec(path=Path("old"))
        new = InputSpec(path=Path("new"))
        req = CompareRequest(old, new, "c++", "clang")
        assert req.lang == "c++"
        assert req.frontend == "clang"

    def test_reaching_past_the_removed_fields_old_slot_fails_at_construction(self):
        old = InputSpec(path=Path("old"))
        new = InputSpec(path=Path("new"))
        with pytest.raises(TypeError):
            CompareRequest(
                old,
                new,
                "c++",
                "clang",
                False,
                "strict_abi",
                None,
                None,
                True,
                None,
                None,
                False,
                False,
                None,
                "env-matrix.yaml",
            )


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
        return (
            CompareResult(diff=diff, old_snapshot=old, new_snapshot=new),
            diff,
            old,
            new,
        )

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


class TestCompareRequestEnvMatrixPathCompat:
    """Codex review, fresh evidence on PR #1221 ("compare --env-matrix
    demotion to deployment: config key"): that PR's `CompareRequest`
    demotion (ADR-068 D5) replaced the documented, released 0.4.0 field
    `env_matrix_path: Path` outright with `env_matrix: EnvironmentMatrix`,
    breaking a Tier-2 caller constructed exactly per the previously-
    published shape (``CompareRequest(..., env_matrix_path=Path(...))``,
    credited in ``CHANGELOG.md``'s 0.4.0 entry) with an immediate
    ``TypeError`` at construction, not a graceful fallback -- despite that
    PR's own ADR-068 amendment claiming the typed Python API was
    unaffected. ``env_matrix_path`` is kept as a genuine, still-accepted
    constructor parameter that stays pure request *intent* --
    :meth:`CompareRequest.effective_env_matrix` resolves it (via
    ``workflows.input_resolution.load_env_matrix``, the same loader the
    retired CLI flag itself used) lazily, at the point a workflow actually
    classifies the request, **not** in ``__post_init__``: a follow-up Codex
    review on this same shim found that resolving eagerly at construction
    broke a caller that builds the request before the matrix file exists,
    and would let a queued request classify against contents cached before
    the file was later edited. See ``tests/test_environment_drift.py::
    TestLoadEnvMatrix::test_compare_request_carries_a_resolved_matrix_not_a_path``
    for the sibling ``env_matrix=`` coverage this mirrors.
    """

    def test_env_matrix_path_resolves_to_the_same_matrix_env_matrix_would(
        self, tmp_path
    ) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        p = tmp_path / "env.yaml"
        p.write_text('runtime_floors:\n  GLIBC: "2.28"\n')

        via_path = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=p,
        )
        via_value = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"}),
        )
        # The raw field stays unresolved until something actually asks for
        # the effective matrix -- construction alone must do no file I/O.
        assert via_path.env_matrix is None
        assert via_path.effective_env_matrix() == via_value.effective_env_matrix()
        # Fresh Codex review, follow-up: `env_matrix_path` must keep
        # reflecting what the caller actually passed -- a typed caller that
        # wants to inspect or log the original path it gave must still be
        # able to (see `TestCompareRequestEnvMatrixPathReplace` below for
        # the `.replace()` half of this same contract).
        assert via_path.env_matrix_path == p

    def test_env_matrix_path_load_is_deferred_past_construction(self, tmp_path) -> None:
        """Codex review (P1, fresh evidence on PR #1221): a `CompareRequest`
        built before its matrix file exists must not fail at construction --
        only when the effective matrix is actually resolved, at the
        classify boundary. This is the load-bearing behavioral difference
        from the previous round's eager-``__post_init__`` design."""
        missing = tmp_path / "not-yet-written.yaml"
        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=missing,
        )
        assert req.env_matrix_path == missing

        with pytest.raises(ValidationError, match="Cannot read environment matrix"):
            req.effective_env_matrix()

        # Writing the file *after* construction and resolving again must
        # see it -- proving resolution genuinely happens at call time, not
        # from a value cached somewhere on the request.
        missing.write_text('runtime_floors:\n  GLIBC: "2.28"\n')
        resolved = req.effective_env_matrix()
        assert resolved is not None
        assert resolved.runtime_floors == {"GLIBC": "2.28"}

    def test_env_matrix_path_missing_file_raises_validation_error(
        self, tmp_path
    ) -> None:
        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=tmp_path / "nope.yaml",
        )
        with pytest.raises(ValidationError, match="Cannot read environment matrix"):
            req.effective_env_matrix()

    def test_env_matrix_path_malformed_yaml_raises_validation_error(
        self, tmp_path
    ) -> None:
        p = tmp_path / "env.yaml"
        p.write_text("runtime_floors: [unclosed\n  GLIBC: {")
        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=p,
        )
        with pytest.raises(ValidationError, match="Invalid environment matrix"):
            req.effective_env_matrix()

    def test_passing_both_env_matrix_and_env_matrix_path_is_a_usage_error(
        self, tmp_path
    ) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        p = tmp_path / "env.yaml"
        p.write_text('runtime_floors:\n  GLIBC: "2.28"\n')

        with pytest.raises(ValidationError, match="not both"):
            CompareRequest(
                old=InputSpec(path=tmp_path / "old.so"),
                new=InputSpec(path=tmp_path / "new.so"),
                env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"}),
                env_matrix_path=p,
            )

    def test_replace_with_a_new_env_matrix_path_re_resolves_from_it(
        self, tmp_path
    ) -> None:
        """Codex review, fresh gap on this same shim (PR #1221): the earlier
        fix reset `env_matrix_path` to `None` right after resolving it, so
        (a) a caller could no longer inspect the path it originally gave,
        and (b) `.replace(env_matrix_path=new_path)` on an already-resolved
        request combined the *inherited* resolved `env_matrix` with the
        *new* path and incorrectly raised the "not both" error -- even
        though the caller's clear intent was "re-resolve from this new
        path," not "give me both a path and a matrix at once." Now that
        resolution never happens eagerly, there is no "inherited resolved
        value" to collide with in the first place: `env_matrix` stays
        `None` for a path-only request at every step, so `.replace()`
        simply updates which path a later `effective_env_matrix()` call
        will read.
        """
        p1 = tmp_path / "env1.yaml"
        p1.write_text('runtime_floors:\n  GLIBC: "2.28"\n')
        p2 = tmp_path / "env2.yaml"
        p2.write_text('runtime_floors:\n  GLIBC: "2.31"\n')

        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=p1,
        )
        assert req.env_matrix_path == p1
        assert req.env_matrix is None
        resolved1 = req.effective_env_matrix()
        assert resolved1 is not None
        assert resolved1.runtime_floors == {"GLIBC": "2.28"}

        replaced = req.replace(env_matrix_path=p2)
        assert replaced.env_matrix_path == p2
        assert replaced.env_matrix is None
        resolved2 = replaced.effective_env_matrix()
        assert resolved2 is not None
        assert resolved2.runtime_floors == {"GLIBC": "2.31"}

    def test_replace_with_env_matrix_path_over_an_explicit_env_matrix_still_raises(
        self, tmp_path
    ) -> None:
        """The finding's own third scenario: when `env_matrix` was given
        *explicitly* by the original caller (never derived from a path
        here), `.replace(env_matrix_path=...)` must still raise the "not
        both" error -- that really is an ambiguous combination, unlike the
        path-only case in the test above."""
        from abicheck.environment_matrix import EnvironmentMatrix

        p = tmp_path / "env.yaml"
        p.write_text('runtime_floors:\n  GLIBC: "2.28"\n')

        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.31"}),
        )
        assert req.env_matrix_path is None

        with pytest.raises(ValidationError, match="not both"):
            req.replace(env_matrix_path=p)

    def test_request_stays_hashable_with_env_matrix_path_set(self, tmp_path) -> None:
        p = tmp_path / "env.yaml"
        p.write_text('runtime_floors:\n  GLIBC: "2.28"\n')

        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=p,
        )
        assert isinstance(hash(req), int)
        # `.replace()` must not re-trigger the "not both" usage error, and
        # must leave `env_matrix_path` (and the still-unresolved
        # `env_matrix`) alone when it only changes an unrelated field.
        replaced = req.replace(lang="c")
        assert replaced.lang == "c"
        assert replaced.env_matrix_path == p
        assert replaced.env_matrix == req.env_matrix

    def test_no_env_matrix_path_or_value_is_a_pure_no_op(self, tmp_path) -> None:
        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
        )
        assert req.env_matrix is None
        assert req.env_matrix_path is None
        assert req.effective_env_matrix() is None
