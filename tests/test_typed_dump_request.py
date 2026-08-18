# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""G33 Phase 5: ``dump``'s typed request.

Two things are pinned here:

* :class:`DumpRequest` validates by the same rules :class:`CompareRequest`
  does — same messages, so a mistake reads identically whichever command a
  caller reached for;
* :func:`run_dump_request` really applies the four steps that previously
  existed only inside ``cli.py``'s ``dump_cmd`` (collect-mode inference,
  inline build/source embedding, dependency walk, the depth floor).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, DumpRequest, InputSpec
from abicheck.compile_context import CompileContext
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import snapshot_to_json


def _snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        functions=[Function(name="foo", mangled="foo", return_type="void", params=[])],
    )


@pytest.fixture()
def snap_path(tmp_path: Path) -> Path:
    p = tmp_path / "lib.abi.json"
    p.write_text(snapshot_to_json(_snapshot()), encoding="utf-8")
    return p


def _write_yaml(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ===================================================================
# DumpRequest validation
# ===================================================================


class TestDumpRequestValidation:
    def test_defaults_are_valid(self, snap_path: Path):
        assert DumpRequest(input=InputSpec(path=snap_path)).validation_errors() == []

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"lang": "rust"}, "unsupported language 'rust'"),
            ({"frontend": "gcc"}, "unsupported AST frontend 'gcc'"),
            ({"debug_format": "stabs"}, "unsupported debug format 'stabs'"),
            ({"depth": "everything"}, "unsupported depth 'everything'"),
            ({"frontend_context": "gpu"}, "unsupported frontend context 'gpu'"),
        ],
    )
    def test_value_errors_match_compare_request(
        self, snap_path: Path, kwargs: dict, fragment: str
    ):
        """The same bad value must produce the same message on both requests.

        This is the point of routing both through one set of module-level
        helpers: front-end parity (ADR-037 D9) stopped at "CLI vs MCP" and now
        also covers "dump vs compare".
        """
        dump_errors = DumpRequest(
            input=InputSpec(path=snap_path), **kwargs
        ).validation_errors()
        compare_errors = CompareRequest(
            old=InputSpec(path=snap_path), new=InputSpec(path=snap_path), **kwargs
        ).validation_errors()
        assert any(fragment in e for e in dump_errors), dump_errors
        assert set(dump_errors) <= set(compare_errors)

    def test_android_frontend_needs_source_inputs(self, snap_path: Path):
        errors = DumpRequest(
            input=InputSpec(path=snap_path), frontend="android"
        ).validation_errors()
        assert any("source-ABI only" in e for e in errors)

    @pytest.mark.parametrize("field", ["sources", "build_info"])
    def test_android_frontend_satisfied_by_either_evidence_field(
        self, snap_path: Path, tmp_path: Path, field: str
    ):
        spec = InputSpec.of(snap_path, **{field: tmp_path})
        assert DumpRequest(input=spec, frontend="android").validation_errors() == []

    def test_android_frontend_satisfied_by_has_sources(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path), frontend="android", has_sources=True
        )
        assert request.validation_errors() == []

    def test_dump_manifest_and_headers_are_mutually_exclusive(
        self, snap_path: Path, tmp_path: Path
    ):
        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        spec = InputSpec(
            path=snap_path,
            headers=(hdr,),
            dump_manifest=object(),  # type: ignore[arg-type]
        )
        errors = DumpRequest(input=spec).validation_errors()
        assert any("mutually exclusive" in e and "input side" in e for e in errors)

    @pytest.mark.parametrize(
        ("field", "value_factory"),
        [
            ("public_header_dirs", lambda tmp: {"public_header_dirs": (tmp,)}),
            ("includes", lambda tmp: {"includes": (tmp,)}),
        ],
    )
    def test_dump_manifest_and_other_conflicting_fields_are_caught_pre_flight(
        self, snap_path: Path, tmp_path: Path, field: str, value_factory
    ):
        """`dumper.dump()` itself rejects `dump_manifest` alongside
        `extra_includes`/`public_header_dirs` (its names for this dataclass's
        `includes`/`public_header_dirs`) -- but only at extraction time. Before
        this fix, `validate()` missed both, so a caller relying on
        `validation_errors()`/`validate()` alone got a late, generic
        `SnapshotError` instead of the fast usage error `headers` already gets
        (Codex review named `public_header_dirs`; `includes` has the identical
        gap, confirmed independently against `dumper.dump()`'s own check).
        """
        spec = InputSpec(
            path=snap_path,
            dump_manifest=object(),  # type: ignore[arg-type]
            **value_factory(tmp_path),
        )
        errors = DumpRequest(input=spec).validation_errors()
        assert any(
            "mutually exclusive" in e and "input side" in e and field in e
            for e in errors
        ), errors

    def test_a_clean_manifest_only_request_is_unaffected(
        self, snap_path: Path, tmp_path: Path
    ):
        # The combined check must not fire when nothing actually conflicts.
        spec = InputSpec(path=snap_path, dump_manifest=object())  # type: ignore[arg-type]
        assert DumpRequest(input=spec).validation_errors() == []

    def test_per_input_compile_frontend_context_is_validated(self, snap_path: Path):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend_context="gpu"))
        errors = DumpRequest(input=spec).validation_errors()
        assert any("unsupported input frontend context 'gpu'" in e for e in errors)

    def test_validate_raises_with_every_problem_joined(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path), lang="rust", depth="deep"
        )
        with pytest.raises(ValidationError) as exc:
            request.validate()
        assert "unsupported language" in str(exc.value)
        assert "unsupported depth" in str(exc.value)

    def test_validate_returns_self_for_inline_use(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        assert request.validate() is request

    def test_replace_is_additive(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        assert request.replace(depth="headers").depth == "headers"
        assert request.depth is None

    def test_case_insensitive_values_accepted_like_compare(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path),
            lang="C++",
            frontend="CastXML",
            debug_format="DWARF",
            depth="HEADERS",
            frontend_context="DEVICE",
        )
        assert request.validation_errors() == []


class TestRunDumpRequest:
    def test_resolves_a_snapshot_input(self, snap_path: Path):
        from abicheck.service import run_dump_request

        snap = run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert snap.library == "libfoo.so.1"
        assert [f.name for f in snap.functions] == ["foo"]

    def test_validation_runs_before_any_resolution(self, snap_path: Path, monkeypatch):
        from abicheck import service

        def _boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("resolve_input reached despite an invalid request")

        monkeypatch.setattr(service, "resolve_input", _boom)
        with pytest.raises(ValidationError):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), lang="rust")
            )

    def test_explicit_depth_is_a_floor_not_a_hint(self, snap_path: Path):
        """A symbols-only snapshot cannot satisfy ``depth='build'``.

        The Tier-2 twin of ``dump --depth``'s ``DumpDepthNotSatisfiedError``:
        silently returning weaker evidence is a lie a downstream baseline
        consumer cannot detect.
        """
        from abicheck.service import run_dump_request

        with pytest.raises(ValidationError, match="only reached 'binary'"):
            run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), depth="build")
            )

    def test_binary_depth_is_always_satisfied(self, snap_path: Path):
        from abicheck.service import run_dump_request

        snap = run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), depth="binary")
        )
        assert snap.library == "libfoo.so.1"

    def test_binary_depth_clears_headers_before_resolving(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """``depth='binary'`` must not still run L2 on carried-over headers."""
        from abicheck import service

        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["headers"] = list(headers or [])
            captured["public_headers"] = list(kwargs.get("public_headers") or [])
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, headers=(hdr,)), depth="binary")
        )
        assert captured["headers"] == []
        # The public-header sets are cleared too: a headerless dump still
        # fingerprints them, so leaving them populated would make two otherwise
        # identical snapshots disagree on scope.
        assert captured["public_headers"] == []

    def test_headers_double_as_the_public_header_set(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service

        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        pub_dir = tmp_path / "include"
        pub_dir.mkdir()
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["public_headers"] = list(kwargs.get("public_headers") or [])
            captured["public_header_dirs"] = list(
                kwargs.get("public_header_dirs") or []
            )
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, headers=(hdr,), public_header_dirs=(pub_dir,)
                )
            )
        )
        assert captured["public_headers"] == [hdr]
        assert captured["public_header_dirs"] == [pub_dir]

    def test_compile_context_and_frontend_reach_resolve_input(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["compile"] = kwargs.get("compile")
            captured["header_backend"] = kwargs.get("header_backend")
            captured["dwarf_only"] = kwargs.get("dwarf_only")
            captured["include_dependencies"] = kwargs.get("include_dependencies")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path,
                    compile=CompileContext(sysroot=Path("/opt/sysroot")),
                    include_dependencies=False,
                ),
                frontend="clang",
                dwarf_only=True,
                frontend_context="device",
            )
        )
        compile_ctx = captured["compile"]
        assert isinstance(compile_ctx, CompileContext)
        assert compile_ctx.sysroot == Path("/opt/sysroot")
        # The request-level frontend_context is merged onto an input whose own
        # value reads as the class default.
        assert compile_ctx.frontend_context == "device"
        assert captured["header_backend"] == "clang"
        assert captured["dwarf_only"] is True
        assert captured["include_dependencies"] is False

    def test_debug_format_auto_becomes_none(self, snap_path: Path, monkeypatch):
        """ "auto" *is* "no format forced" at the extraction layer."""
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["debug_format"] = kwargs.get("debug_format")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), debug_format="AUTO")
        )
        assert captured["debug_format"] is None

    def test_debug_format_rejected_for_non_elf_input(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import service

        monkeypatch.setattr(service, "detect_binary_format", lambda _p: "pe")
        with pytest.raises(ValidationError, match="only supported for ELF"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), debug_format="dwarf")
            )

    def test_collect_mode_inferred_from_build_info(
        self, tmp_path: Path, snap_path: Path
    ):
        """``depth`` omitted, ``build_info`` set → the "build" collect mode.

        Asserted against the shared resolver rather than through a real
        extraction: the inference is the behaviour, the extractor is not.
        """
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path, build_info=tmp_path))
        )
        assert evidence.collect_mode != "off"

    def test_collect_mode_off_without_any_evidence(self, snap_path: Path):
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path))
        )
        assert evidence.collect_mode == "off"

    def test_no_pair_wide_dialect_override_for_a_lone_dump(
        self, snap_path: Path, tmp_path: Path
    ):
        """A single dump has no second side to agree with on a C++ standard.

        ``pair_wide_cxx20_std_override`` exists so two *compared* sides cannot
        silently diverge; applying it to one dump would change what ``dump``
        produces today for no benefit, so the resolved compile context stays
        whatever the input asked for.
        """
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        hdr = tmp_path / "api.h"
        hdr.write_text("template <class T> concept Any = true;\n", encoding="utf-8")
        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path, headers=(hdr,)))
        )
        assert evidence.compile is None

    def test_follow_dependencies_is_opt_in(self, snap_path: Path, monkeypatch):
        from abicheck import dependency_info, service

        calls: list[object] = []
        monkeypatch.setattr(
            dependency_info,
            "populate_side_dependency_info",
            lambda *a, **k: calls.append(a),
        )
        service.run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert calls == []

    def test_follow_dependencies_forwards_search_paths(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import dependency_info, service

        captured: dict[str, object] = {}

        def _fake(snap, side, fmt, search_paths, ld_library_path):
            captured["search_paths"] = list(search_paths)
            captured["ld_library_path"] = ld_library_path

        monkeypatch.setattr(dependency_info, "populate_side_dependency_info", _fake)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(path=snap_path),
                follow_dependencies=True,
                dependency_search_paths=(Path("/opt/lib"),),
                ld_library_path="/usr/lib",
            )
        )
        assert captured["search_paths"] == [Path("/opt/lib")]
        assert captured["ld_library_path"] == "/usr/lib"


class TestResolveExecuteDumpRequestSplit:
    """CLI cleanup phase two, PR C / PR 3A: ``run_dump_request`` is now a
    thin adapter over :func:`resolve_dump_request` + :func:`execute_dump_request`.

    Pins the split's actual point: ``resolve_dump_request`` never invokes
    ``resolve_input`` (no castxml/clang, no write), and the two-step path
    produces the identical snapshot ``run_dump_request`` itself returns.
    """

    def test_resolve_never_invokes_resolve_input(self, snap_path: Path, monkeypatch):
        from abicheck import service
        from abicheck.service_dump_pipeline import resolve_dump_request

        def _boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("resolve_input reached during resolve-only step")

        monkeypatch.setattr(service, "resolve_input", _boom)
        resolved = resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert resolved.request.input.path == snap_path

    def test_resolve_validates_before_anything_else(self, snap_path: Path):
        from abicheck.service_dump_pipeline import resolve_dump_request

        with pytest.raises(ValidationError):
            resolve_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), lang="rust")
            )

    def test_execute_produces_the_same_snapshot_as_run_dump_request(
        self, snap_path: Path
    ):
        from abicheck.service import run_dump_request
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path))
        via_adapter = run_dump_request(request)
        result = execute_dump_request(resolve_dump_request(request))
        assert result.snapshot.library == via_adapter.library == "libfoo.so.1"
        assert [f.name for f in result.snapshot.functions] == [
            f.name for f in via_adapter.functions
        ]

    def test_run_dump_request_is_literally_the_composition(self, snap_path: Path):
        """``run_dump_request`` cannot silently diverge from the two-step path."""
        from abicheck import service
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path))
        expected = execute_dump_request(resolve_dump_request(request)).snapshot
        actual = service.run_dump_request(request)
        assert actual.library == expected.library
        assert [f.mangled for f in actual.functions] == [
            f.mangled for f in expected.functions
        ]

    def test_depth_floor_raises_only_at_execute_time(self, snap_path: Path):
        """A ``depth`` requested but not reached is an execution-time failure —
        the resolve step has no snapshot yet to check it against."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path), depth="build")
        resolved = resolve_dump_request(request)  # must not raise
        assert resolved.requested_depth == "build"
        with pytest.raises(ValidationError, match="only reached 'binary'"):
            execute_dump_request(resolved)

    def test_resolved_request_reports_requested_depth_and_collect_mode(
        self, snap_path: Path
    ):
        from abicheck.service_dump_pipeline import resolve_dump_request

        resolved = resolve_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), depth="binary")
        )
        assert resolved.requested_depth == "binary"
        assert resolved.collect_mode == "off"

    def test_dump_result_effective_depth_matches_gated_source_label(
        self, snap_path: Path
    ):
        from abicheck.cli_dump_helpers import _gated_source_label
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path))
        result = execute_dump_request(resolve_dump_request(request))
        assert result.effective_depth == _gated_source_label(
            result.snapshot.build_source, result.snapshot
        )

    def test_dump_result_has_no_storage_field(self, snap_path: Path):
        """Storage (writing to disk) stays CLI presentation layer, per this
        module's own docstring — not something a resolve/execute split adds."""
        from dataclasses import fields

        from abicheck.service_dump_pipeline import DumpResult

        assert "storage" not in {f.name for f in fields(DumpResult)}


# ===================================================================
# The Phase 5 gate, as an executable check
# ===================================================================


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


#: Concepts ``abi_compare`` names differently because it has two inputs, or
#: that exist only because it renders and gates a comparison. Everything else
#: in its signature is a concept ``dump``/``scan`` share and must therefore be
#: reachable on those tools too.
_COMPARE_ONLY_PARAMS = {
    # two-sided input naming
    "old_input",
    "new_input",
    "old_headers",
    "new_headers",
    # report rendering — a dump emits a snapshot, a scan its own report
    "output_format",
    "show_only",
    "report_mode",
    "show_impact",
    "stat",
    # gating a comparison's exit code
    "severity_preset",
    "severity_abi_breaking",
    "severity_potential_breaking",
    "severity_quality_issues",
    "severity_addition",
    # ADR-043 app scoping and ADR-050's comparability escape hatch: both are
    # properties of comparing two things
    "used_by",
    "required_symbols",
    "diagnostic_comparison",
}


class TestAndroidFrontendIsNotAHeaderBackend:
    """``android`` must never reach ``resolve_input`` as the compile context's
    frontend (Codex review, P2).

    It is in ``SUPPORTED_FRONTENDS`` but not ``HEADER_AST_FRONTENDS``: source-ABI
    only, no header-AST path. Both pipelines already map the bare
    ``header_backend`` to ``"auto"`` for it — but ``service._run_dump_uncached``
    gives an explicit ``compile.frontend`` *precedence* over that argument, and
    ``dumper._resolve_header_backend`` raises for anything outside
    castxml/clang/hybrid/auto. So a resolved context still carrying "android"
    failed the whole extraction with "Unknown AST frontend 'android'", before
    any build/source evidence was embedded.
    """

    def _spy_on_compile_context(self, monkeypatch) -> dict[str, object]:
        """Record the ``compile.frontend`` that actually reaches ``resolve_input``."""
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            ctx = kwargs.get("compile")
            captured["frontend"] = None if ctx is None else ctx.frontend
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        return captured

    def test_dump_request_downgrades_android_to_auto(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service

        captured = self._spy_on_compile_context(monkeypatch)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path,
                    build_info=tmp_path,
                    compile=CompileContext(frontend="android"),
                ),
                frontend="android",
            )
        )
        assert captured["frontend"] == "auto"

    def test_compare_request_downgrades_android_to_auto(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """The same defect existed for a typed ``compare`` caller, which is why
        the fix lives in the shared resolution rather than at either caller."""
        from abicheck import service
        from abicheck.api_types import CompareRequest

        captured = self._spy_on_compile_context(monkeypatch)
        side = InputSpec(
            path=snap_path,
            build_info=tmp_path,
            compile=CompileContext(frontend="android"),
        )
        service.run_compare_request(
            CompareRequest(old=side, new=side, frontend="android")
        )
        assert captured["frontend"] == "auto"

    def test_a_real_header_frontend_is_left_alone(self, snap_path: Path, monkeypatch):
        from abicheck import service

        captured = self._spy_on_compile_context(monkeypatch)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, compile=CompileContext(frontend="clang")
                ),
                frontend="clang",
            )
        )
        assert captured["frontend"] == "clang"


class TestBuildDerivedIncludeSeeding:
    """A typed request seeds the build's include dirs, as the CLI does.

    With headers plus ``sources``/``build_info`` but no explicit ``includes``,
    the L2 public-header parse cannot see the include dirs the build already
    knows (public headers reaching into a dependency SDK). The CLI has seeded
    these from the build since ADR-033; the typed path did not, so an identical
    request parsed less than the equivalent CLI invocation (Codex review).
    """

    def test_seeds_when_evidence_is_present_and_includes_are_empty(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """PR C (typed dump/scan convergence) merged the typed API's own
        two-call include-seed/compile-context-fold path into the one
        combined `seed_includes_and_fold_compile_context` primitive the
        three CLI-side resolvers already used -- see
        `service_input_resolution._seeded_includes_and_compile_context`'s
        own docstring. This test now mocks that combined function instead
        of the removed `seed_l2_includes`.
        """
        from abicheck import service

        seen: dict[str, object] = {}
        seeded = tmp_path / "generated-include"
        seeded.mkdir()
        # The combined primitive is a no-op with no headers to match against
        # (mirrors seed_l2_includes'/derive_l2_compile_context's own real
        # no-op-without-headers contract) -- a header is required to reach it.
        header = tmp_path / "api.h"
        header.write_text("void f();\n", encoding="utf-8")

        def _fake_seed(**kwargs):
            seen["headers"] = list(kwargs["headers"])
            seen["collect_mode"] = kwargs["collect_mode"]
            return [seeded], False, None, ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["includes"] = list(includes or [])
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, headers=(header,), build_info=tmp_path
                )
            )
        )
        assert captured["includes"] == [seeded]
        # A Tier-2 call must never *execute* a build system as a side effect of
        # resolving an input — passive discovery only ("off" maps to
        # allow_inferred_build_query=False inside the combined primitive).
        assert seen["collect_mode"] == "off"

    def test_no_evidence_means_no_seeding_call(self, snap_path: Path, monkeypatch):
        """Without sources/build_info the seed is skipped entirely."""
        from abicheck import service

        def _boom(**kwargs):  # pragma: no cover - must never run
            raise AssertionError(
                "seed_includes_and_fold_compile_context reached without "
                "build evidence"
            )

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _boom,
        )
        snap = service.run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert snap.library == "libfoo.so.1"

    def test_cleanups_run_after_resolution(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """Seeded temp dirs are drained only after the parse consumed them."""
        from abicheck import service
        from abicheck.compile_context import CompileContext

        header = tmp_path / "api.h"
        header.write_text("void f();\n", encoding="utf-8")
        order: list[str] = []

        def _fake_seed(*, pending_cleanups, **kwargs):
            pending_cleanups.append(lambda: order.append("cleanup"))
            return [], False, CompileContext(), ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            order.append("resolve")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, headers=(header,), build_info=tmp_path
                )
            )
        )
        assert order == ["resolve", "cleanup"]


class TestRawSourceUnderAndroidIsRejected:
    """`android` source-ABI replay is not wired into inline collection.

    `DumpRequest.frontend` accepts `android` (unlike the per-input
    `compile.frontend`, which is header-AST only), but a raw source tree
    under it has no extractor — so it is a usage error rather than a
    silently weaker snapshot. The sibling of the `hybrid` rule below.
    """

    def test_a_raw_source_tree_under_android_raises(
        self, snap_path: Path, tmp_path: Path
    ):
        from abicheck import service

        sources = tmp_path / "raw"
        sources.mkdir()
        (sources / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="'android' AST frontend"):
            service.run_dump_request(
                DumpRequest(
                    input=InputSpec(path=snap_path, sources=sources),
                    frontend="android",
                )
            )

    def test_has_sources_without_inline_sources_is_allowed(
        self, snap_path: Path, monkeypatch
    ):
        # The error message names this as the way through; if it stopped
        # working the message would be actively misleading.
        from abicheck import service

        snap = service.run_dump_request(
            DumpRequest(
                input=InputSpec(path=snap_path),
                frontend="android",
                has_sources=True,
            )
        )
        assert snap.library == "libfoo.so.1"


class TestRawSourceUnderHybridIsRejected:
    """`depth="source"` + the `hybrid` frontend is a usage error, not a
    silently weaker result — `hybrid` has no real L4 extractor. Mirrors
    `cli.py`'s own `UsageError` for the same combination.
    """

    def _request(self, snap_path: Path, sources: Path, frontend: str) -> DumpRequest:
        return DumpRequest(
            input=InputSpec(path=snap_path, sources=sources),
            frontend=frontend,
            depth="source",
        )

    def test_a_raw_source_tree_under_hybrid_raises(
        self, snap_path: Path, tmp_path: Path
    ):
        from abicheck import service

        sources = tmp_path / "raw"
        sources.mkdir()
        (sources / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="incompatible with the 'hybrid'"):
            service.run_dump_request(self._request(snap_path, sources, "hybrid"))

    def test_a_prebuilt_pack_under_hybrid_is_allowed(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        # Only a *raw* tree needs real extraction; a prebuilt pack never feeds
        # L4, so it must not be swept up by the same guard.
        from abicheck import cli_buildsource, service

        monkeypatch.setattr(
            cli_buildsource, "embed_build_source", lambda snap, **kwargs: None
        )
        # A real pack is identified by manifest *content* -- the
        # BuildSourcePack version marker -- not by the file merely existing.
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            '{"build_source_pack_version": 1}', encoding="utf-8"
        )
        # The stub embeds nothing, so the run still fails -- but on the depth
        # floor, *not* the hybrid guard. That distinction is the assertion:
        # reaching the floor at all proves the pack got past the guard.
        with pytest.raises(ValidationError) as exc:
            service.run_dump_request(self._request(snap_path, pack, "hybrid"))
        assert "only reached 'binary'" in str(exc.value)
        assert "hybrid" not in str(exc.value)


class TestMalformedPackIsTranslated:
    """`embed_build_source` raises `click.ClickException` on a malformed pack —
    a CLI concept with no place in this Tier-2 API's contract, so the
    resolver translates it to `SnapshotError`.
    """

    def test_click_exception_becomes_snapshot_error(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        import click

        from abicheck import cli_buildsource, service

        def _boom(snap, **kwargs):
            raise click.ClickException("build pack is malformed")

        monkeypatch.setattr(cli_buildsource, "embed_build_source", _boom)
        sources = tmp_path / "src"
        sources.mkdir()
        with pytest.raises(SnapshotError, match="build pack is malformed"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path, sources=sources))
            )


class TestSourceReplayUsesTheSelectedCompiler:
    """L4 source-ABI replay invokes the compiler the request selected.

    `embed_build_source` defaults `clang_bin` to a bare "clang"; the `dump`
    CLI and `scan_engine` both override it from `--compiler`/`--compiler-prefix`,
    and the typed path did not. On a hermetic or cross-toolchain host where
    only the requested compiler works, that made an omitted `depth` return a
    weaker snapshot and an explicit `depth="source"` fail, even though the
    caller supplied the right compiler (Codex review).
    """

    @pytest.fixture
    def replayed_with(self, monkeypatch):
        from abicheck import cli_buildsource

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            cli_buildsource,
            "embed_build_source",
            lambda snap, **kwargs: captured.update(kwargs),
        )
        return captured

    def _run(self, snap_path: Path, tmp_path: Path, ctx) -> None:
        from abicheck import service

        sources = tmp_path / "src"
        sources.mkdir(exist_ok=True)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, sources=sources, compile=ctx))
        )

    def test_gcc_path_reaches_source_replay(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        self._run(snap_path, tmp_path, CompileContext(gcc_path="/opt/oneapi/icpx"))
        assert replayed_with["clang_bin"] == "/opt/oneapi/icpx"

    def test_no_compile_context_keeps_the_default(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        self._run(snap_path, tmp_path, None)
        assert replayed_with["clang_bin"] == "clang"

    def test_a_cl_style_driver_is_not_excluded(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        # L4 re-drives a CL compile unit with `--driver-mode=cl` itself, so
        # unlike the S2 pre-scan it must NOT fall back off a CL-mode driver
        # (`exclude_cl_style=False`) -- doing so would replay an Intel DPC++
        # build through a plain clang that cannot parse it.
        self._run(snap_path, tmp_path, CompileContext(gcc_path="/opt/dpcpp-cl"))
        assert replayed_with["clang_bin"] == "/opt/dpcpp-cl"


class TestDependencySysrootIsForwarded:
    """`--follow-deps` under a sysroot searches the target, not the host.

    The only sysroot a typed request can carry is the input's own compile
    context; passing `None` made a cross/sysrooted extraction search host
    defaults and report the target's dependencies unresolved, where the CLI
    (which forwards `--sysroot`) resolved them (Codex review).
    """

    @pytest.fixture
    def resolved_sysroot(self, monkeypatch):
        """Capture the sysroot the dependency resolver is actually given.

        Records it out of `**kwargs` as well as the positional slot, so this
        keeps asserting on the real value rather than raising `TypeError`
        if `populate_side_dependency_info` ever switches to keyword
        arguments (CodeRabbit review).
        """
        from abicheck import dependency_info

        captured: dict[str, object] = {}

        def _record(snap, path, search=None, sysroot=None, ldpath=None, **kwargs):
            captured["sysroot"] = kwargs.get("sysroot", sysroot)

        monkeypatch.setattr(dependency_info, "populate_dependency_info", _record)
        monkeypatch.setattr(
            dependency_info, "_dependency_source", lambda side, fmt: side.path
        )
        return captured

    def test_sysroot_comes_from_the_input_compile_context(
        self, snap_path: Path, resolved_sysroot: dict
    ):
        from abicheck import service

        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, compile=CompileContext(sysroot=Path("/opt/sysroot"))
                ),
                follow_dependencies=True,
            )
        )
        assert resolved_sysroot["sysroot"] == Path("/opt/sysroot")

    def test_no_compile_context_means_no_sysroot(
        self, snap_path: Path, resolved_sysroot: dict
    ):
        from abicheck import service

        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), follow_dependencies=True)
        )
        assert resolved_sysroot["sysroot"] is None


class TestPerInputFrontendIsValidated:
    """A typo in `InputSpec.compile.frontend` is a usage error, not a default run.

    The source-ABI-only downgrade (`android` → `auto`) originally rewrote
    *every* non-header frontend, so `CompileContext(frontend="clnag")` turned
    a typo that used to raise `Unknown AST frontend` into a successful
    default-backend run — trading one bug for a worse one (Codex review).
    Fixed on both halves: the value is validated up front, and the downgrade
    is narrowed to frontends that are actually known-but-header-less.
    """

    @pytest.mark.parametrize("label", ["dump", "compare"])
    def test_typo_is_rejected_by_validate(self, snap_path: Path, label: str):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend="clnag"))
        request = (
            DumpRequest(input=spec)
            if label == "dump"
            else CompareRequest(old=spec, new=spec)
        )
        errors = request.validation_errors()
        assert any("unsupported" in e and "'clnag'" in e for e in errors), errors

    def test_typo_raises_rather_than_running(self, snap_path: Path):
        from abicheck.service import run_dump_request

        with pytest.raises(ValidationError, match="clnag"):
            run_dump_request(
                DumpRequest(
                    input=InputSpec(
                        path=snap_path, compile=CompileContext(frontend="clnag")
                    )
                )
            )

    def test_downgrade_only_applies_to_a_known_headerless_frontend(self):
        """`android` is downgraded; an unknown value is left for the extraction
        layer to reject, so the narrowing cannot silently resurrect the bug."""
        from abicheck.service_compare_evidence import _header_ast_frontend_only

        assert (
            _header_ast_frontend_only(CompileContext(frontend="android")).frontend
            == "auto"
        )
        assert (
            _header_ast_frontend_only(CompileContext(frontend="clnag")).frontend
            == "clnag"
        )
        assert (
            _header_ast_frontend_only(CompileContext(frontend="clang")).frontend
            == "clang"
        )

    def test_a_valid_per_input_frontend_still_passes(self, snap_path: Path):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend="castxml"))
        assert DumpRequest(input=spec).validation_errors() == []
