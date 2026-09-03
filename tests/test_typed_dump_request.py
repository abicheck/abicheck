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
from abicheck.elf_metadata import ElfMetadata
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

    def test_collect_mode_with_build_info_matches_the_dump_cli(
        self, tmp_path: Path, snap_path: Path
    ):
        """``depth`` omitted, ``build_info`` set → the ``dump`` CLI's own
        default (PR 3A blocker 5 sub-issue 2; see
        ``tests/test_dump_collect_mode_parity.py`` for the full grid and why
        the CLI's default is the canonical one)."""
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path, build_info=tmp_path))
        )
        assert evidence.collect_mode == "source-target"

    def test_collect_mode_without_any_evidence_matches_the_dump_cli(
        self, snap_path: Path
    ):
        """No inputs at all still resolves the ``dump`` CLI's fixed default
        (PR 3A blocker 5 sub-issue 2; unobservable either way, since nothing
        is embedded without ``sources``/``build_info``)."""
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path))
        )
        assert evidence.collect_mode == "source-target"

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

    def test_resolved_request_headers_property_reads_from_evidence(
        self, snap_path: Path
    ):
        from abicheck.service_dump_pipeline import resolve_dump_request

        resolved = resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert resolved.headers == tuple(resolved.evidence.headers)

    def test_execute_falls_back_to_conservative_label_on_gated_source_label_error(
        self, snap_path: Path, monkeypatch
    ):
        """The ``except (TypeError, ValueError)`` around ``gated_source_label``
        in ``execute_dump_request`` is a defensive backstop for any failure
        mode beyond the one ``l4_source_abi_was_attempted`` itself now
        handles -- still reachable if ``gated_source_label`` raises for a
        different reason."""
        from abicheck import evidence_depth
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        def _boom(*args, **kwargs):
            raise ValueError("simulated unexpected failure")

        # Patch the owning leaf (ADR-061 Phase 3), not the cli_dump_helpers
        # compat alias the engine no longer reads -- patching the alias makes
        # this vacuous: the un-raised path also satisfies the assertion.
        monkeypatch.setattr(evidence_depth, "gated_source_label", _boom)
        result = execute_dump_request(
            resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        )
        assert result.effective_depth in ("headers", "binary")

    def test_dump_result_effective_depth_matches_gated_source_label(
        self, snap_path: Path
    ):
        from abicheck.evidence_depth import gated_source_label
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path))
        result = execute_dump_request(resolve_dump_request(request))
        assert result.effective_depth == gated_source_label(
            result.snapshot.build_source, result.snapshot
        )

    def test_dump_result_has_no_storage_field(self, snap_path: Path):
        """Storage (writing to disk) stays CLI presentation layer, per this
        module's own docstring — not something a resolve/execute split adds."""
        from dataclasses import fields

        from abicheck.service_dump_pipeline import DumpResult

        assert "storage" not in {f.name for f in fields(DumpResult)}

    def test_dump_result_still_accepts_three_argument_construction(
        self, snap_path: Path
    ):
        """Codex review, fresh evidence: DumpResult is exported Tier-2 API,
        so appending effective_includes/effective_compile_context as
        *required* fields would TypeError on an external caller already
        constructing the previous three-field shape. Both must default."""
        from abicheck.service_dump_pipeline import DumpResult, resolve_dump_request

        resolved = resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        result = DumpResult(
            resolved=resolved, snapshot=_snapshot(), effective_depth="headers"
        )
        assert result.effective_includes == ()
        assert result.effective_compile_context is None

    def test_dump_result_carries_effective_includes_and_compile_context(
        self, snap_path: Path
    ):
        """PR 3A (dump/scan resolver convergence): DumpResult surfaces the
        P0.3 L3->L2 fold's own effective includes/compile-context, computed
        inside execute_dump_request's resolution call but previously
        discarded after use. No sources/build_info here, so the fold is a
        no-op -- includes is whatever the request supplied (empty), and the
        compile context is None (unchanged from the request's own None)."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(input=InputSpec(path=snap_path))
        result = execute_dump_request(resolve_dump_request(request))
        assert result.effective_includes == ()
        assert result.effective_compile_context is None

    def test_effective_header_backend_honors_compile_context_override(
        self, snap_path: Path
    ):
        """An explicit ``InputSpec.compile.frontend`` wins over the bare
        request-level ``frontend`` — the same precedence ``service.py``'s own
        ``eff_backend`` computation applies at execution time (Codex review,
        fresh evidence)."""
        from abicheck.compile_context import CompileContext
        from abicheck.service_dump_pipeline import resolve_dump_request

        request = DumpRequest(
            input=InputSpec(path=snap_path, compile=CompileContext(frontend="clang")),
            frontend="auto",
        )
        resolved = resolve_dump_request(request)
        assert resolved.header_backend == "auto"
        assert resolved.effective_header_backend == "clang"

    def test_effective_header_backend_resolves_auto_without_override(
        self, snap_path: Path
    ):
        from abicheck.service_dump_pipeline import resolve_dump_request

        resolved = resolve_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), frontend="auto")
        )
        assert resolved.header_backend == "auto"
        assert resolved.effective_header_backend in ("castxml", "clang", "hybrid")

    def test_effective_header_backend_reports_clang_for_non_host_frontend_context(
        self, snap_path: Path
    ):
        """An unpinned ``auto`` backend under a non-"host" ``frontend_context``
        is *always* routed to clang at execution
        (``dumper._header_ast_parser``: ``if resolved == "clang" or
        frontend_context != "host": return _run_clang()``) -- the reporting
        projection must reflect that instead of naming the backend "auto"
        would otherwise default to (Codex review, fresh evidence)."""
        from abicheck.service_dump_pipeline import resolve_dump_request

        resolved = resolve_dump_request(
            DumpRequest(
                input=InputSpec(path=snap_path),
                frontend="auto",
                frontend_context="device",
            )
        )
        assert resolved.header_backend == "auto"
        assert resolved.effective_header_backend == "clang"

    def test_effective_header_backend_preserves_pinned_castxml_error_path(
        self, snap_path: Path
    ):
        """An *explicit* ``--ast-frontend castxml`` combined with a non-"host"
        ``frontend_context`` doesn't route to clang -- it raises
        ``AstContextMissingError`` at execution
        (``dumper._resolve_single_ast_backend``). The reporting projection
        must not claim "clang" will run in that case (Codex review, two
        rounds -- the first fix applied the clang override unconditionally,
        missing this pinned-castxml case)."""
        from abicheck.service_dump_pipeline import resolve_dump_request

        resolved = resolve_dump_request(
            DumpRequest(
                input=InputSpec(path=snap_path),
                frontend="castxml",
                frontend_context="device",
            )
        )
        assert resolved.effective_header_backend == "castxml"

    def test_execute_forwards_the_bare_header_backend_not_the_reporting_projection(
        self, snap_path: Path, monkeypatch
    ):
        """Codex review, fresh evidence, two rounds -- the first round tried
        making execution consume ``effective_header_backend`` and that was
        itself a real regression, reverted here: ``dumper._header_ast_parser``'s
        own ``_auto_ast_fallback_eligible(backend)`` checks whether the
        backend it receives is *literally* the string ``"auto"`` to decide
        whether a CastXML failure may gracefully fall back to Clang --
        pre-resolving "auto" before it gets there silently disables that
        fallback (and a non-"host" ``frontend_context``'s own "auto"-specific
        routing). Execution must keep forwarding the bare, possibly-"auto"
        ``header_backend`` unchanged; ``effective_header_backend`` is a
        reporting-only projection, computed correctly, but never fed back
        into execution."""
        from abicheck import service_dump_pipeline
        from abicheck.compile_context import CompileContext
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        request = DumpRequest(
            input=InputSpec(path=snap_path, compile=CompileContext(frontend="clang")),
            frontend="auto",
        )
        resolved = resolve_dump_request(request)
        assert resolved.header_backend == "auto"
        # The reporting projection still resolves correctly...
        assert resolved.effective_header_backend == "clang"

        captured: dict[str, object] = {}
        original = service_dump_pipeline._resolve_side_snapshot_impl

        def _spy(*args, **kwargs):
            captured["header_backend"] = kwargs.get("header_backend")
            return original(*args, **kwargs)

        monkeypatch.setattr(service_dump_pipeline, "_resolve_side_snapshot_impl", _spy)
        execute_dump_request(resolved)
        # ...but execution still receives the bare, unresolved value -- the
        # same "auto" service.py's own eff_backend computation independently
        # (and correctly) resolves to "clang" via evidence.compile.frontend,
        # preserving every "auto"-specific behavior downstream of that.
        assert captured["header_backend"] == "auto"


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
                input=InputSpec(path=snap_path, headers=(header,), build_info=tmp_path)
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
                "seed_includes_and_fold_compile_context reached without build evidence"
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
                input=InputSpec(path=snap_path, headers=(header,), build_info=tmp_path)
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
        from abicheck import service
        from abicheck.buildsource import embed as embed_mod

        monkeypatch.setattr(
            embed_mod, "embed_build_source", lambda snap, **kwargs: None
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
        from abicheck.buildsource import embed as embed_mod

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            embed_mod,
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


def test_seeded_includes_never_forwards_build_query_without_explicit_permission(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, real reproduction, two rounds. First round: `collect_
    inline_pack`'s own `allow_build_query` parameter is a documented,
    deprecated no-op (see its docstring), so a caller supplying
    `build_config`/`build_query` without ALSO passing `allow_build_query=
    True` must never reach `seed_includes_and_fold_compile_context` with
    those values live -- confirmed to fail against the pre-fix code
    (build_config/build_query reached the fold unconditionally). Second
    round: `build_compile_db` is a bare data path/glob naming an existing
    compile database, not an executable command, and must NOT be gated the
    same way -- an earlier fix over-gated it, silently degrading a caller's
    real include paths/defines/dialect for supplying data that was never a
    permission question. Lives here rather than in test_header_compile_
    context.py (which sits at its 2000-line hard cap)."""
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import _seeded_includes_and_compile_context

    captured: dict[str, object] = {}

    def _fake_seed(*, pending_cleanups, **kwargs):
        captured.update(kwargs)
        return [], False, CompileContext(), ()

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        _fake_seed,
    )

    header = tmp_path / "h.h"
    header.write_text("void f();\n", encoding="utf-8")
    side = InputSpec(path=tmp_path / "lib.so", sources=tmp_path, headers=(header,))
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    trusted_config = tmp_path / "trusted.abicheck.yml"

    # allow_build_query omitted (defaults False) -- the operator-supplied
    # build_config/build_query (both potentially executable) must NOT reach
    # the fold, but build_compile_db (a bare data path) must still reach it.
    _seeded_includes_and_compile_context(
        side,
        evidence,
        build_config=trusted_config,
        build_query="cmake -S . -B build",
        build_compile_db="build/compile_commands.json",
    )
    assert captured["build_config"] is None
    assert captured["build_query"] is None
    assert captured["build_compile_db"] == "build/compile_commands.json"

    # allow_build_query=True -- now build_config/build_query reach the fold
    # as given too.
    captured.clear()
    _seeded_includes_and_compile_context(
        side,
        evidence,
        build_config=trusted_config,
        build_query="cmake -S . -B build",
        build_compile_db="build/compile_commands.json",
        allow_build_query=True,
    )
    assert captured["build_config"] == trusted_config
    assert captured["build_query"] == "cmake -S . -B build"
    assert captured["build_compile_db"] == "build/compile_commands.json"


def test_resolve_side_snapshot_impl_forwards_gated_build_inputs_to_embed(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence: the L2 seed and the L3-L5 embed step
    must agree on the same build_config/build_query/build_compile_db, or the
    snapshot's own evidence layers could silently describe two different
    builds. _resolve_side_snapshot_impl computes the allow_build_query gate
    once and forwards the identical (gated) values to both."""
    # ADR-061 Phase 3: patch the implementation owner
    # (`workflows.artifact.execute`), not the `service_input_resolution`
    # facade -- the caller reads the owner's reference, never the facade's.
    from abicheck import service
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.workflows.artifact import execute as sir

    header = tmp_path / "h.h"
    header.write_text("void f();\n", encoding="utf-8")
    side = InputSpec(path=tmp_path / "lib.so", sources=tmp_path, headers=(header,))
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="build", dump_manifest=None
    )
    trusted_config = tmp_path / "trusted.abicheck.yml"

    monkeypatch.setattr(
        sir,
        "_seeded_includes_and_compile_context",
        lambda *a, **k: ([], None, False, []),
    )
    monkeypatch.setattr(
        service, "resolve_input", lambda *a, **k: AbiSnapshot(library="x", version="1")
    )

    embed_captured: dict[str, object] = {}

    def _fake_embed(*args, **kwargs):
        embed_captured.update(kwargs)

    monkeypatch.setattr(sir, "embed_side_build_source", _fake_embed)

    sir._resolve_side_snapshot_impl(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
        build_config=trusted_config,
        build_query="cmake -S . -B build",
        build_compile_db="build/compile_commands.json",
        allow_build_query=True,
    )
    assert embed_captured["build_config"] == trusted_config
    assert embed_captured["build_query"] == "cmake -S . -B build"
    assert embed_captured["build_compile_db"] == "build/compile_commands.json"

    embed_captured.clear()
    sir._resolve_side_snapshot_impl(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
        build_config=trusted_config,
        build_query="cmake -S . -B build",
        build_compile_db="build/compile_commands.json",
        allow_build_query=False,
    )
    assert embed_captured["build_config"] is None
    assert embed_captured["build_query"] is None
    assert embed_captured["build_compile_db"] == "build/compile_commands.json"


class TestSharedPipelineReachesADR039BuildContextCollector:
    """PR C (CLI cleanup phase two, typed dump/scan convergence): the ADR-039
    build-context collector (``header_conditionals.attach_build_context``)
    used to be reachable only from the ELF ``dump`` CLI's own
    ``perform_elf_dump`` (one call site). ``_resolve_side_snapshot_impl`` --
    shared by ``compare``'s implicit-dump operand and ``dump``'s typed
    ``run_dump_request`` API -- now reaches the identical collector. These
    tests exercise the real collector (not mocked), only ``service.
    resolve_input`` is stubbed to avoid a real castxml/clang invocation."""

    def _compile_db(self, tmp_path: Path, src: Path, *extra_flags: str) -> Path:
        import json

        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "file": str(src),
                        "arguments": [
                            "c++",
                            "-c",
                            str(src),
                            "-o",
                            "out.o",
                            *extra_flags,
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        return db

    def test_collector_populates_defines_and_conditional_fields(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from abicheck import (
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text(
            "struct Widget {\n  int x;\n#ifdef GUARD\n  int guarded;\n#endif\n};\n",
            encoding="utf-8",
        )
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,), build_info=db)
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert "GUARD" in resolution.snapshot.build_context_defines
        assert "Widget" in resolution.snapshot.conditional_fields
        assert "guarded" in resolution.snapshot.conditional_fields["Widget"]

    def test_collector_runs_for_a_followed_linker_script_even_when_fmt_is_none(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Codex review, PR #809, fresh evidence: `fmt` is computed by the
        caller from `service.detect_binary_format(side.path)` *before*
        `service.resolve_input()` runs -- a GNU ld linker script (the
        conventional development `libfoo.so` symlink, e.g.
        `INPUT(libfoo.so.1)`) reads as `None` there, but `resolve_input`'s
        own `follow_linker_scripts=True` default follows it to a real ELF
        target and returns a genuine ELF snapshot. Gating on the stale
        `fmt` (rather than the post-resolution `snap.elf`) would silently
        skip the collector for this common ELF input form -- reproduced
        here by passing `fmt=None` (what the caller would have computed
        for the raw linker-script text) while the resolved snapshot is a
        real ELF one."""
        from abicheck import (
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text(
            "struct Widget {\n  int x;\n#ifdef GUARD\n  int guarded;\n#endif\n};\n",
            encoding="utf-8",
        )
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,), build_info=db)
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt=None,
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert "GUARD" in resolution.snapshot.build_context_defines
        assert "Widget" in resolution.snapshot.conditional_fields

    def test_collector_expands_a_header_directory_before_scanning(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Codex review, PR #809: ``InputSpec.headers`` may name a directory
        (``service.resolve_input``/``_dump_elf`` expand it internally via
        ``expand_header_inputs`` before parsing) -- the collector must scan
        the same expanded file set, not the raw, unexpanded directory entry
        (``Path(dir).read_text()`` raises ``OSError``, silently caught and
        skipped by ``collect_build_context``, leaving ``conditional_fields``
        empty for the common directory-``-H`` case)."""
        from abicheck import (
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        include_dir = tmp_path / "include"
        include_dir.mkdir()
        hdr = include_dir / "widget.h"
        hdr.write_text(
            "struct Widget {\n  int x;\n#ifdef GUARD\n  int guarded;\n#endif\n};\n",
            encoding="utf-8",
        )
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )

        side = InputSpec(
            path=tmp_path / "lib.so", headers=(include_dir,), build_info=db
        )
        evidence = SideEvidence(
            headers=[include_dir], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[include_dir],
            public_header_dirs=[],
        )
        assert "GUARD" in resolution.snapshot.build_context_defines
        assert "Widget" in resolution.snapshot.conditional_fields
        assert "guarded" in resolution.snapshot.conditional_fields["Widget"]

    def test_collector_never_sees_folded_derived_tokens_only_pre_fold_explicit(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Mirrors ``test_perform_elf_dump_keeps_l3_derived_flags_out_of_
        build_context_collector`` (tests/test_non_elf_dump_l2_seed.py) for
        the shared pipeline: the ADR-039 collector's ``extra_flags`` must be
        this side's own pre-fold ``InputSpec.compile`` tokens only, never the
        P0.3 L3->L2-folded ``CompileContext`` this function also resolves
        internally -- else a build-derived define would be unioned
        snapshot-wide (the ninth finding in AGENTS.md's L3->L2-fold entry)."""
        from abicheck import (
            header_conditionals as _hc,
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DL3ONLY=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )
        # Simulate the L3->L2 fold resolving a CompileContext carrying the
        # database's own -DL3ONLY=1 -- this must never reach the collector.
        monkeypatch.setattr(
            sir,
            "_seeded_includes_and_compile_context",
            lambda *a, **k: (
                [],
                CompileContext(gcc_option_tokens=("-DL3ONLY=1",)),
                True,
                [],
            ),
        )

        captured: dict[str, object] = {}
        real_attach = _hc.attach_build_context

        def _spy_attach(snap, compile_db_arg, headers, extra_flags, **kw):
            captured["extra_flags"] = list(extra_flags)
            return real_attach(snap, compile_db_arg, headers, extra_flags, **kw)

        monkeypatch.setattr(_hc, "attach_build_context", _spy_attach)

        side = InputSpec(
            path=tmp_path / "lib.so",
            headers=(hdr,),
            build_info=db,
            compile=CompileContext(gcc_option_tokens=("-DUSERONLY=1",)),
        )
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert captured["extra_flags"] == ["-DUSERONLY=1"]
        assert "-DL3ONLY=1" not in captured["extra_flags"]

    def test_collector_skipped_when_no_compile_db_resolvable(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """No ``build_info`` at all: the collector must not run (and must not
        raise) -- a plain context-free dump is still the common case."""
        from abicheck import (
            header_conditionals as _hc,
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )
        called = {"attach": False}

        def _spy_attach(*a, **k):
            called["attach"] = True

        monkeypatch.setattr(_hc, "attach_build_context", _spy_attach)

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,))
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert called["attach"] is False
        assert resolution.snapshot.build_context_defines == set()

    def test_collector_skipped_for_a_non_elf_snapshot(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Codex review, PR #809: the ADR-039 collector is an established
        ELF-only mechanism (``handle_non_elf_dump`` never calls it) -- the
        shared pipeline must not attach build-context evidence to a PE/
        Mach-O snapshot, or it would silently disagree with the native
        PE/Mach-O dump path."""
        from abicheck import (
            header_conditionals as _hc,
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(library="lib", version="1", from_headers=True),
        )
        called = {"attach": False}

        def _spy_attach(*a, **k):
            called["attach"] = True

        monkeypatch.setattr(_hc, "attach_build_context", _spy_attach)

        side = InputSpec(path=tmp_path / "lib.dll", headers=(hdr,), build_info=db)
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="pe",
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert called["attach"] is False
        assert resolution.snapshot.build_context_defines == set()

    def test_collector_never_receives_a_source_filter(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """``InputSpec`` deliberately has no ``compile_db_filter`` field
        (Codex review, PR #809, fresh evidence: an earlier version of this
        field always raised whenever combined with a resolvable compile
        database, so it had no successful execution path where it narrowed
        anything -- see ``InputSpec``'s own comment). Pin that the shared
        pipeline always calls the ADR-039 collector with the default,
        unfiltered ``source_filter=None`` for a real compile database +
        headers, rather than raising or narrowing."""
        from abicheck import (
            header_conditionals as _hc,
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )

        captured: dict[str, object] = {}
        real_attach = _hc.attach_build_context

        def _spy_attach(snap, compile_db_arg, headers, extra_flags, **kw):
            captured["source_filter"] = kw.get("source_filter")
            return real_attach(snap, compile_db_arg, headers, extra_flags, **kw)

        monkeypatch.setattr(_hc, "attach_build_context", _spy_attach)

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,), build_info=db)
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert captured["source_filter"] is None

    def test_collector_skipped_for_a_loaded_json_snapshot(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Codex review, PR #809, fresh evidence: ``snap.elf is not None``
        alone doesn't prove a live ELF parse happened -- a *loaded* JSON
        snapshot (``resolve_input``'s own ``fmt == "json"`` branch, which
        returns ``load_snapshot(path)`` verbatim with no parse at all) round
        -trips its original ``elf``/``from_headers`` fields exactly as saved,
        so the identical signal fires. Running the collector there would
        scan the *current* filesystem's headers/compile-db and overwrite the
        loaded snapshot's own recorded build-context evidence with
        unrelated data -- ``side.path`` here is a JSON file, not an ELF
        binary or a followed linker script, so the collector must not run."""
        from abicheck import (
            header_conditionals as _hc,
            service,
            service_input_resolution as sir,
        )
        from abicheck.service_compare_evidence import SideEvidence

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        db = self._compile_db(tmp_path, src, "-DGUARD=1")

        snapshot_path = tmp_path / "baseline.abicheck.json"
        snapshot_path.write_text('{"schema_version": 25}\n', encoding="utf-8")

        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *a, **k: AbiSnapshot(
                library="lib", version="1", from_headers=True, elf=ElfMetadata()
            ),
        )
        called = {"attach": False}

        def _spy_attach(*a, **k):
            called["attach"] = True

        monkeypatch.setattr(_hc, "attach_build_context", _spy_attach)

        side = InputSpec(path=snapshot_path, headers=(hdr,), build_info=db)
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        resolution = sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt=None,
            public_headers=[hdr],
            public_header_dirs=[],
        )
        assert called["attach"] is False
        assert resolution.snapshot.build_context_defines == set()


class TestSeedCleanupsDrainBeforeTheEmbedStep:
    """PR 3A (CLI cleanup phase two, dump/scan resolver convergence).

    ``_resolve_side_snapshot_impl`` used to drain the L2 seed's own cleanups
    only at the very end of the function -- after ``embed_side_build_source``.
    An inferred build query holds its deterministic per-source-tree build dir
    under an exclusive ``flock`` until its cleanup runs, and the embed step
    runs its *own* inferred query in the same call, so that ordering makes the
    second query contend, in the same process, on a lock the first still holds
    -- blocking for up to ``INFERRED_QUERY_TIMEOUT_S`` (600s) before falling
    back to a throwaway dir. That is the identical self-contention shape
    ``scan_engine._build_new_snapshot`` already fixed (the fifth finding on the
    root ``AGENTS.md``'s L3->L2-fold entry); this pins the same ordering here.

    Latent rather than live for the callers that exist today (the seed's own
    ``collect_mode`` is pinned ``"off"``, so no caller can currently run an
    inferred query in the seed at all and the cleanup list is always empty) --
    which is exactly why it needs a test rather than a bug report: the trap
    springs on whichever future PR relaxes that pin.
    """

    def test_cleanups_run_after_the_parse_and_before_the_embed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from abicheck import service
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.workflows.artifact import execute as sir

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
        sources = tmp_path / "src"
        sources.mkdir()

        events: list[str] = []

        def _fake_seed(*_a, **_k):
            events.append("seed")
            return [], None, False, [lambda: events.append("cleanup")]

        monkeypatch.setattr(sir, "_seeded_includes_and_compile_context", _fake_seed)

        def _fake_resolve(*_a, **_k):
            events.append("parse")
            return AbiSnapshot(library="lib", version="1", from_headers=True)

        monkeypatch.setattr(service, "resolve_input", _fake_resolve)
        monkeypatch.setattr(
            sir,
            "embed_side_build_source",
            lambda *_a, **_k: events.append("embed"),
        )

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,), sources=sources)
        evidence = SideEvidence(
            headers=[hdr],
            compile=None,
            collect_mode="source-target",
            dump_manifest=None,
        )
        sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
        )

        assert events == ["seed", "parse", "cleanup", "embed"]
        assert events.index("cleanup") < events.index("embed")

    def test_cleanups_still_run_exactly_once_when_the_parse_raises(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The drain moved into a nested ``finally``; the outer one is kept as a
        backstop. Neither may run the same already-handed-off thunk twice."""
        import pytest

        from abicheck import service
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.workflows.artifact import execute as sir

        hdr = tmp_path / "widget.h"
        hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")

        runs: list[str] = []
        monkeypatch.setattr(
            sir,
            "_seeded_includes_and_compile_context",
            lambda *_a, **_k: ([], None, False, [lambda: runs.append("cleanup")]),
        )

        def _boom(*_a, **_k):
            raise SnapshotError("nope")

        monkeypatch.setattr(service, "resolve_input", _boom)

        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,))
        evidence = SideEvidence(
            headers=[hdr], compile=None, collect_mode="off", dump_manifest=None
        )
        with pytest.raises(SnapshotError):
            sir._resolve_side_snapshot_impl(
                side,
                evidence,
                lang="c++",
                header_backend="auto",
                fmt="elf",
                public_headers=[hdr],
                public_header_dirs=[],
            )

        assert runs == ["cleanup"]
