"""Unit tests for run_dump's header-graph attachment and the _attach_header_graph wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot, Function
from abicheck.workflows.dump.native import (
    run_dump,
)
from tests._dump_format_fakes import fake_format_adapter
from tests._header_graph_coverage import coverage_for as _coverage_for


class TestRunDumpHeaderGraph:
    """The header-only (L2) semantic graph (ADR-041 addendum) is always
    embedded uniformly across all three binary formats when headers are
    parsed (G29 Phase A: no longer flag-gated)."""

    def test_embeds_by_default_when_headers_given(self, tmp_path):
        # G29 Phase A regression: no flag needed any more — a plain run_dump
        # call with headers present attempts the graph attach by default.
        # api.h is a relative, nonexistent path here (this test only cares
        # about the no-op-vs-attempted gate, not clang-parse success), so the
        # attach degrades gracefully to a declaration-only graph rather than
        # crashing — same contract as test_degrades_gracefully_when_clang_unavailable.
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with fake_format_adapter("pe", result=snap):
            result = run_dump(p, "pe", [Path("api.h")], [], "1.0", "c++")
        assert result.surface_graph is not None

    def test_noop_when_no_headers_parsed(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with fake_format_adapter("pe", result=snap):
            result = run_dump(p, "pe", [], [], "1.0", "c++")
        assert result.surface_graph is None

    def test_embeds_graph_from_clang_ast(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(
            library="lib",
            version="1.0",
            platform="pe",
            functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
        )
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        mock_ast.assert_called_once()
        # The resolved (existing, expanded) header must reach the clang pass —
        # not the raw, unexpanded argument (Codex review).
        assert mock_ast.call_args.args[0] == [header]
        assert result.surface_graph is not None
        node_ids = {n.id for n in result.surface_graph.nodes}
        assert "decl://_Z1fv" in node_ids
        # The manifest coverage row must be populated too (Codex review) — an
        # empty default manifest would read as "L5 not collected" to
        # cli_buildsource_helpers._layer_presence/_optional_coverage even
        # though source_graph is populated.
        from abicheck.buildsource.model import CoverageStatus, DataLayer

        l5 = _coverage_for(result, DataLayer.L5_SOURCE_GRAPH)
        assert l5 is not None
        assert l5.status == CoverageStatus.PARTIAL  # no edges in this empty AST
        l3 = _coverage_for(result, DataLayer.L3_BUILD)
        assert l3 is not None
        assert l3.status == CoverageStatus.NOT_COLLECTED

    def test_header_graph_uses_same_frontend_context_as_primary_snapshot(
        self, tmp_path
    ):
        """ADR-050 D5 (Codex review): the internal semantic header graph
        (G29 Phase A) must be built with the SAME frontend_context as the
        primary snapshot -- a device-context dump's embedded graph built
        from an unrequested host parse would combine device declarations
        with host-only call/type/include edges, feeding crosschecks a graph
        incoherent with what it's describing."""
        from abicheck.dry_run_estimate import CompileContext

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(
            library="lib",
            version="1.0",
            platform="pe",
            functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
        )
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        cc = CompileContext(frontend_context="device")
        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ) as mock_ast,
        ):
            run_dump(p, "pe", [header], [], "1.0", "c++", compile=cc)
        mock_ast.assert_called_once()
        assert mock_ast.call_args.kwargs["frontend_context"] == "device"

    def test_degrades_gracefully_when_clang_unavailable(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")

        def _raise(*a, **k):
            raise SnapshotError("clang not found")

        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                side_effect=_raise,
            ) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        mock_ast.assert_called_once()
        # Never aborts the dump (ADR-028 D3); the graph is embedded but inert.
        assert result.surface_graph is not None
        assert result.surface_graph.edges == []

    def test_expands_header_directory_before_clang_pass(self, tmp_path):
        # Codex review: a `headers` entry may be a directory (a supported
        # run_dump input the main dump path already expands) — the header
        # graph's own clang pass must see the expanded file list, not the
        # raw directory (which would otherwise get written into an invalid
        # `#include "<dir>"` line and silently degrade to the seed-only
        # graph).
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        header = hdr_dir / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ) as mock_ast,
        ):
            result = run_dump(p, "pe", [hdr_dir], [], "1.0", "c++")
        mock_ast.assert_called_once()
        assert mock_ast.call_args.args[0] == [header]
        assert result.surface_graph is not None

    def test_header_graph_includes_folds_include_edges(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pub = tmp_path / "pub.h"
        pub.write_text('#include "detail/impl.h"\n')
        impl_dir = tmp_path / "detail"
        impl_dir.mkdir()
        impl = impl_dir / "impl.h"
        impl.write_text("struct Impl {};\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}

        class _Proc:
            stdout = f"pub.o: {pub} {impl}"
            stderr = ""

        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph_workers.deadline.run_bounded",
                lambda *a, **k: _Proc(),
            ),
        ):
            result = run_dump(
                p,
                "pe",
                [pub],
                [],
                "1.0",
                "c++",
            )
        graph = result.surface_graph
        pub_id = f"header://{pub}"
        assert any(
            e.kind == "COMPILE_UNIT_INCLUDES_FILE" and e.src == pub_id
            for e in graph.edges
        )
        assert graph.coverage["include_edges"]["collected"] is True

    def test_header_graph_includes_marks_pass_covered_when_map_is_empty(self, tmp_path):
        """A leaf header with no #includes of its own is a genuine zero, not
        an uncollected pass — `header_include_graph` must still be stamped so
        `_include_graph_covered` doesn't mistake it for "never ran"."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}

        class _Proc:
            stdout = f"api.o: {header}"
            stderr = ""
            returncode = 0

        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph_workers.deadline.run_bounded",
                lambda *a, **k: _Proc(),
            ),
        ):
            result = run_dump(
                p,
                "pe",
                [header],
                [],
                "1.0",
                "c++",
            )
        graph = result.surface_graph
        assert not any(e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in graph.edges)
        assert graph.extractor_passes.get("header_include_graph") is True
        assert graph.coverage["include_edges"]["collected"] is True
        assert graph.coverage["include_edges"]["count"] == 0

    def test_header_graph_includes_marks_pass_degraded_on_partial_failure(
        self, tmp_path
    ):
        """One header's `clang -M` succeeding while another's fails is a real,
        partial result -- it must fold the successful header's edges but
        must NOT be confirmed as a clean full pass (`extractor_passes`), only
        `degraded_passes`, so `_include_graph_fully_covered` never trusts the
        failed header's portion as evidence of genuine absence."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        good = tmp_path / "good.h"
        good.write_text('#include "good_impl.h"\n')
        impl = tmp_path / "good_impl.h"
        impl.write_text("struct Impl {};\n")
        bad = tmp_path / "bad.h"
        bad.write_text("void g();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}

        class _OkProc:
            stdout = f"good.o: {good} {impl}"
            stderr = ""
            returncode = 0

        class _FailProc:
            stdout = ""
            stderr = "fatal error: something broke"
            returncode = 1

        def _fake_run(cmd, *a, **k):
            return _OkProc() if str(good) in cmd else _FailProc()

        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph_workers.deadline.run_bounded",
                _fake_run,
            ),
        ):
            result = run_dump(
                p,
                "pe",
                [good, bad],
                [],
                "1.0",
                "c++",
            )
        graph = result.surface_graph
        good_id = f"header://{good}"
        assert any(
            e.kind == "COMPILE_UNIT_INCLUDES_FILE" and e.src == good_id
            for e in graph.edges
        )
        assert graph.extractor_passes.get("header_include_graph") is not True
        assert graph.degraded_passes.get("header_include_graph") is True

    def test_header_graph_present_by_default_no_flags(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        # G29 Phase A: header_graph_includes is no longer a separate opt-in
        # gated behind header_graph — both are always attempted together
        # once headers are parsed, so this now asserts the graph IS present
        # by default (the "ignored without --header-graph" invariant this
        # test used to check is gone; see
        # test_header_graph_includes_folds_include_edges above for the
        # include-edge content check).
        with fake_format_adapter("pe", result=snap):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        assert result.surface_graph is not None


class TestRunDumpHeaderGraphSkippedForDwarfOnly:
    """Codex review: dwarf_only=True means "ignore headers entirely" --
    _dump_elf already honors that (it skips header-root inference/include
    validation and warns if headers are supplied alongside it). Before this
    fix, the header-graph attach ran unconditionally regardless of
    dwarf_only, so a `--dwarf-only -H ...` request could still silently
    re-parse those headers via clang and attach L2 build_source evidence to
    what the caller explicitly asked to be a DWARF-only snapshot."""

    def test_elf_dwarf_only_does_not_attach_header_graph(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        calls: list[tuple[bool, bool]] = []

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            result = run_dump(p, "elf", [header], [], "1.0", "c++", dwarf_only=True)

        assert calls == [(False, False)]
        assert result.build_source is None

    def test_elf_without_dwarf_only_still_attaches_header_graph(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=True)
        calls: list[tuple[bool, bool]] = []

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            run_dump(p, "elf", [header], [], "1.0", "c++", dwarf_only=False)

        assert calls == [(True, True)]

    def test_hybrid_dwarf_only_does_not_attach_header_graph(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        castxml_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=False, ast_producer="castxml"
        )
        clang_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=False, ast_producer="clang"
        )
        calls: list[tuple[bool, bool]] = []

        def _fake_dump_elf(request):
            compile_ctx = request.compile
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("elf", side_effect=_fake_dump_elf),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
        ):
            run_dump(p, "elf", header_backend="hybrid", dwarf_only=True)

        # Both recursive sub-dumps attach with the graph forced off
        # (_skip_header_graph_attach) regardless of dwarf_only, plus the
        # final merged-snapshot attach must also be off since dwarf_only=True.
        assert all(not h and not i for h, i in calls)

    def test_elf_symbols_only_does_not_attach_header_graph(self, tmp_path):
        """Codex review: symbols_only=True also means "ignore headers
        entirely" -- dumper.dump()'s own `if symbols_only or not headers:`
        gate skips header-based type expansion for that case, same as
        dwarf_only skips header parsing. The header-graph attach must not
        silently re-parse headers into L2 build_source evidence for a
        snapshot the caller explicitly requested as symbols-only."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        calls: list[tuple[bool, bool]] = []

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            run_dump(p, "elf", [header], [], "1.0", "c++", symbols_only=True)

        assert calls == [(False, False)]

    def test_elf_empty_headers_does_not_open_ast_memoize_scope(self, tmp_path):
        """Codex review, PR #840: a manifest-driven dump (`dump_manifest`,
        mutually exclusive with `headers` per api_types.py) reaches
        `_dump_elf` with an empty `_headers`, so the header-graph attach
        immediately below always no-ops. Opening `ast_memoize_scope()`
        unconditionally around this call would protect a memo nothing will
        ever consume, while silently vetoing the opt-in streaming pruner
        (gated on `ast_memoize_active()`) for a manifest dump's own TU
        parses too whenever they share this thread (a single TU, or
        `ABICHECK_TU_JOBS=1`)."""
        from abicheck.storage import header_ast_cache as dumper_cache

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        seen_active: dict[str, bool] = {}

        def _fake_dump_elf(_request):
            seen_active["active"] = dumper_cache.ast_memoize_active()
            return snap

        with (
            fake_format_adapter("elf", side_effect=_fake_dump_elf),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            assert not dumper_cache.ast_memoize_active()
            run_dump(p, "elf", [], [], "1.0", "c++")
            assert not dumper_cache.ast_memoize_active()

        assert seen_active["active"] is False

    def test_pe_symbols_only_does_not_attach_header_graph(self, tmp_path):
        """Codex review, fresh evidence: the ELF fix above added
        `not symbols_only` to the ELF/hybrid attach predicates, but the PE
        branch omitted it -- a symbols-only PE dump could still grow
        header-graph findings."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        calls: list[tuple[bool, bool]] = []

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            run_dump(p, "pe", [header], [], "1.0", "c++", symbols_only=True)

        assert calls == [(False, False)]

    def test_macho_symbols_only_does_not_attach_header_graph(self, tmp_path):
        """Codex review, fresh evidence: same gap as the PE case above, on
        the Mach-O branch."""
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        calls: list[tuple[bool, bool]] = []

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            fake_format_adapter("macho", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=lambda s, *a, **k: s,
            ),
        ):
            run_dump(p, "macho", [header], [], "1.0", "c++", symbols_only=True)

        assert calls == [(False, False)]


class TestAttachHeaderGraphDeviceContext:
    """Codex review: ClangHeaderIncludeExtractor drives a plain `clang -M`
    per header with no frontend_context/-fsycl concept at all (unlike the
    AST pass in the same function, which threads frontend_context through
    and is validated against a real DPC++ capture) -- for a device-context
    request it would silently resolve `__SYCL_DEVICE_ONLY__`-style guards as
    host and attach host-only include edges to a device snapshot's graph.
    Must be skipped entirely, not just given the wrong flags, so the
    include-graph pass stays honestly "not collected" rather than
    confidently wrong."""

    def test_device_context_skips_include_extractor(self, tmp_path):
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        with patch(
            "abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor"
        ) as mock_extractor:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=True,
                headers=[header],
                includes=[],
                lang="c",
                compile=CompileContext(frontend_context="device"),
                public_headers=None,
                public_header_dirs=None,
            )
        mock_extractor.assert_not_called()

    def test_host_context_still_uses_include_extractor(self, tmp_path):
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        with patch(
            "abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor"
        ) as mock_extractor:
            mock_extractor.return_value.extract.return_value = ({}, [])
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=True,
                headers=[header],
                includes=[],
                lang="c",
                compile=CompileContext(frontend_context="host"),
                public_headers=None,
                public_header_dirs=None,
            )
        mock_extractor.return_value.extract.assert_called_once()

    def test_streaming_prune_is_suppressed_for_this_call(self, tmp_path, monkeypatch):
        """Codex review, PR #840: `_attach_header_graph`'s own
        `clang_header_dump` call is a real downstream consumer of the raw
        AST dict (`buildsource.call_graph.parse_clang_ast_calls` walks it
        directly for call-graph edges), so the opt-in streaming pruner must
        be force-disabled for this call regardless of the env var --
        otherwise a pruned dependency function/variable node would degrade
        or drop call-graph edges the graph is built to capture."""
        from abicheck.extract.headers.clang.streaming import streaming_prune_suppressed
        from abicheck.service_header_graph_attach import _attach_header_graph

        monkeypatch.setenv("ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS", "1")
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")

        seen_suppressed: dict[str, bool] = {}

        def _stub_clang_header_dump(*a, **k):
            seen_suppressed["suppressed"] = streaming_prune_suppressed()
            return {"kind": "TranslationUnitDecl", "inner": []}, None, False

        monkeypatch.setattr(
            "abicheck.extract.headers.clang.backend.clang_header_dump",
            _stub_clang_header_dump,
        )
        assert not streaming_prune_suppressed()  # not leaked before the call
        _attach_header_graph(
            snap,
            header_graph=True,
            header_graph_includes=False,
            headers=[header],
            includes=[],
            lang="c",
            compile=None,
            public_headers=None,
            public_header_dirs=None,
        )
        assert seen_suppressed["suppressed"] is True
        assert not streaming_prune_suppressed()  # not leaked after the call

    def test_no_compile_context_defaults_to_host_and_still_uses_extractor(
        self, tmp_path
    ):
        """compile=None -- the default CompileContext() -- must behave the
        same as an explicit host request, not be silently skipped."""
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        with patch(
            "abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor"
        ) as mock_extractor:
            mock_extractor.return_value.extract.return_value = ({}, [])
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=True,
                headers=[header],
                includes=[],
                lang="c",
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        mock_extractor.return_value.extract.assert_called_once()


class TestAttachHeaderGraphCompilerSelection:
    """Codex review (P2): _attach_header_graph's own compiler selection for
    its clang_header_dump call must match whichever main pass it's paired
    with -- case-insensitively, since PE/Mach-O's own main pass
    (service_header_scoped._try_header_scoped_dump) treats "C" the same as
    "c". The compiler string is part of the AST cache key, so a mismatch
    here silently misses the memo on top of picking the wrong driver."""

    def test_uppercase_c_selects_cc_compiler(self, tmp_path: Path):
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.extract.headers.clang.backend.clang_header_dump",
            return_value=(ast, None, False),
        ) as mock_ast:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[header],
                includes=[],
                lang="C",
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        assert mock_ast.call_args.kwargs["compiler"] == "cc"

    def test_lang_none_selects_cpp_compiler(self, tmp_path: Path):
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.extract.headers.clang.backend.clang_header_dump",
            return_value=(ast, None, False),
        ) as mock_ast:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[header],
                includes=[],
                lang=None,
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        assert mock_ast.call_args.kwargs["compiler"] == "c++"

    def test_uppercase_c_selects_c_include_extractor_driver_and_language(
        self, tmp_path: Path
    ):
        """CodeRabbit review: the same case-insensitive normalization must
        also apply to the include-graph pass's own driver resolution
        (resolve_clang_bin/fallback) and its `language=` argument -- these
        three were fixed alongside the compiler selection above but are
        independent code paths, each capable of drifting back to "C++" on
        its own."""
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            patch(
                "abicheck.extract.headers.clang.backend.clang_header_dump",
                return_value=(ast, None, False),
            ),
            patch(
                "abicheck.extract.compiler_driver.resolve_clang_bin",
                return_value="/opt/llvm/clang",
            ) as mock_resolve,
            patch(
                "abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor"
            ) as mock_extractor,
        ):
            mock_extractor.return_value.extract.return_value = ({}, [])
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=True,
                headers=[header],
                includes=[],
                lang="C",
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        assert mock_resolve.call_args.args[0] == "cc"
        mock_extractor.assert_called_once_with(clang_bin="/opt/llvm/clang")
        assert mock_extractor.return_value.extract.call_args.kwargs["language"] == "C"


class TestAttachHeaderGraphHashesIncludeSearchTokens:
    """Codex review, PR #782: _attach_header_graph's own independent second
    clang_header_dump call has its own AST cache key, but its extra_hash_dirs
    computation only covered resolve_inferred_header_roots's own deferred
    roots -- never any include-search directory riding in
    compile.gcc_option_tokens itself (an explicit --gcc-options/
    --compiler-option -I, or -- since the P0.3 L3->L2 fold -- a compile-DB-
    derived one). A directory the primary snapshot pass already hashes into
    its own cache key must be hashed here too, or an edit under it would
    silently reuse a stale cached graph even though the primary snapshot
    re-parsed correctly."""

    def test_gcc_option_tokens_include_dir_is_hashed(self, tmp_path: Path):
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        build_inc = tmp_path / "buildinc"
        build_inc.mkdir()
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.extract.headers.clang.backend.clang_header_dump",
            return_value=(ast, None, False),
        ) as mock_ast:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[header],
                includes=[],
                lang="c++",
                compile=CompileContext(gcc_option_tokens=("-I", str(build_inc))),
                public_headers=None,
                public_header_dirs=None,
            )
        assert build_inc in mock_ast.call_args.kwargs["extra_hash_dirs"]

    def test_no_include_tokens_hashes_nothing_extra(self, tmp_path: Path):
        from abicheck.service_header_graph_attach import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.extract.headers.clang.backend.clang_header_dump",
            return_value=(ast, None, False),
        ) as mock_ast:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[header],
                includes=[],
                lang="c++",
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        assert mock_ast.call_args.kwargs["extra_hash_dirs"] == ()
