"""Unit tests for abicheck.workflows.dump.native.run_dump: hybrid normalization, header wiring and dependency scope."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.errors import ValidationError
from abicheck.model import AbiSnapshot
from abicheck.workflows.dump.native import (
    run_dump,
)
from tests._dump_format_fakes import fake_format_adapter

# ── run_dump() ──────────────────────────────────────────────────────────────


class TestRunDump:
    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "lib.xyz"
        p.write_bytes(b"\x00" * 100)
        with pytest.raises(ValidationError, match="Unsupported binary format"):
            run_dump(p, "webasm")

    def test_elf_format_delegates(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with fake_format_adapter("elf", result=snap):
            result = run_dump(p, "elf")
        assert result is snap

    def test_pe_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with fake_format_adapter("pe", result=snap):
            result = run_dump(p, "pe")
        assert result is snap

    def test_macho_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with fake_format_adapter("macho", result=snap):
            result = run_dump(p, "macho")
        assert result is snap


class TestRunDumpHybridNormalization:
    """G28 Phase 3 (Codex review): eff_backend == "hybrid" was a raw string
    comparison, so an indirectly-selected hybrid request (case-insensitive
    value, or the documented ABICHECK_AST_FRONTEND=hybrid pin with auto)
    fell through to the single-backend path instead of triggering the merge
    recursion -- fixed by resolving through header_ast_backend._resolve_header_backend.
    """

    def _fake_dump_elf(self, castxml_snap, clang_snap):
        def _fake(request):
            compile_ctx = request.compile
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        return _fake

    def test_case_insensitive_header_backend_triggers_hybrid(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        castxml_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="castxml"
        )
        clang_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="clang"
        )
        with fake_format_adapter(
            "elf",
            side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
        ):
            result = run_dump(p, "elf", frontend="HYBRID")
        assert result.ast_producer == "hybrid"

    def test_env_var_pin_with_auto_triggers_hybrid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "hybrid")
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        castxml_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="castxml"
        )
        clang_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="clang"
        )
        with fake_format_adapter(
            "elf",
            side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
        ):
            result = run_dump(p, "elf")  # header_backend defaults to "auto"
        assert result.ast_producer == "hybrid"


class TestRunDumpHybridHeaderGraphAttachedOnce:
    """Codex review / G29 Phase A: the header-graph attach must not run on
    either hybrid sub-dump -- each would independently attach its OWN graph
    seeded from only that one backend's declarations, so the FINAL merged
    snapshot's embedded graph would miss any clang-only declaration the
    merge appended (castxml never produced), and it would waste a whole
    extra clang AST pass per sub-dump for a graph immediately thrown away.
    Now that the attach is unconditional (no longer flag-gated), this is
    enforced via ``run_dump``'s private ``_skip_header_graph_attach`` knob on
    the recursive hybrid sub-calls, verified here by asserting it is only
    ever invoked with the graph *enabled* once, on the merged snapshot."""

    def _fake_dump_elf(self, castxml_snap, clang_snap):
        def _fake(request):
            compile_ctx = request.compile
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        return _fake

    def test_header_graph_attached_once_to_merged_snapshot(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        castxml_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="castxml"
        )
        clang_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="clang"
        )
        calls: list[tuple[AbiSnapshot, bool | None]] = []

        def _fake_attach(snap, header_graph, *_args, **_kwargs):
            calls.append((snap, header_graph))
            return snap

        with (
            fake_format_adapter(
                "elf",
                side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
            ),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph",
                side_effect=_fake_attach,
            ),
        ):
            result = run_dump(p, "elf", frontend="hybrid")

        # _attach_header_graph is invoked on each recursive sub-dump's OWN
        # elf path too, but with the graph disabled (header_graph=False, via
        # _skip_header_graph_attach) -- only the call with header_graph=True
        # matters here, and it must run exactly once, on the already-merged
        # (ast_producer="hybrid") snapshot, not on either single-backend
        # sub-dump.
        true_calls = [c for c in calls if c[1] is True]
        assert len(true_calls) == 1
        assert true_calls[0][0].ast_producer == "hybrid"
        assert result.ast_producer == "hybrid"
        # And the two sub-dump attaches ran with the graph explicitly
        # disabled, not simply omitted.
        false_calls = [c for c in calls if c[1] is False]
        assert len(false_calls) == 2


class TestRunDumpHybridDoesNotDoubleEnrichLayout:
    """general-purpose review finding: run_dump's hybrid branch used to call
    attach_clang_layout a SECOND time on the already-merged snapshot, even
    though the recursive header_backend="clang" sub-dump already got it from
    that same function's own ELF/PE/Mach-O tail before the merge -- a
    provably redundant extra invocation of the compiled external tool
    (apply_layout_facts backfills nothing new the second time)."""

    def _fake_dump_elf(self, castxml_snap, clang_snap):
        def _fake(request):
            compile_ctx = request.compile
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        return _fake

    def test_attach_clang_layout_not_called_on_merged_snapshot(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        castxml_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="castxml"
        )
        clang_snap = AbiSnapshot(
            library="test", version="1.0", from_headers=True, ast_producer="clang"
        )
        calls: list[AbiSnapshot] = []

        def _fake_attach(snap, *_args, **_kwargs):
            calls.append(snap)
            return snap

        with (
            fake_format_adapter(
                "elf",
                side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
            ),
            patch(
                "abicheck.workflows.dump.native.attach_clang_layout",
                side_effect=_fake_attach,
            ),
        ):
            result = run_dump(p, "elf", frontend="hybrid")

        # attach_clang_layout is called once per recursive sub-dump's OWN
        # elf-branch tail (castxml_snap's and clang_snap's) -- never a third
        # time on the merged (ast_producer="hybrid") snapshot itself.
        assert len(calls) == 2
        assert all(c.ast_producer != "hybrid" for c in calls)
        assert result.ast_producer == "hybrid"


class TestRunDumpHeaderWiring:
    """run_dump must forward headers/includes to the PE/Mach-O dumpers."""

    def test_run_dump_pe_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with fake_format_adapter("pe", result=snap) as mock_pe:
            run_dump(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.last.headers == [Path("api.h")]
        assert mock_pe.last.includes == [Path("inc")]

    def test_run_dump_macho_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with fake_format_adapter("macho", result=snap) as mock_macho:
            run_dump(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.last.headers == [Path("api.h")]


class TestRunDumpDependencyScope:
    """``run_dump`` is built from ``_run_dump_uncached`` via
    ``workflows.run_dump_scope.wrap_run_dump_with_dependency_scope``. Its
    ``include_dependencies`` default is ``False`` -- the same value
    ``dump --include-system-declarations`` and
    ``InputSpec.include_dependencies`` carry -- so a Tier-2 caller that omits
    the keyword gets the same ``"filtered"`` surface the CLI gives for the
    same inputs. Passing ``True`` restores the unfiltered ``"full"`` surface.
    See tests/test_dumper_scoping.py for direct coverage of the wrapping
    function itself."""

    def test_run_dump_defaults_to_filtered_matching_the_cli(self, tmp_path):
        """The default used to be ``True`` ("full"), which is what made the
        typed API and the CLI produce different surfaces from identical
        inputs -- and, because
        ``comparability.check_contracts_comparable`` refuses a ``filtered``
        vs ``full`` pair, made a CLI baseline uncomparable against a
        typed-API candidate (``scope_mismatch``, no verdict). Measured on a
        one-header C++ library: 10 functions via the CLI, 5,597 via
        ``DumpRequest``."""
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"\x7fELF" + b"\x00" * 100)
        fake_snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        with patch(
            "abicheck.workflows.dump.native._run_dump_uncached", return_value=fake_snap
        ):
            result = run_dump(elf_path, "elf")
        assert result.dependency_scope == "filtered"

    def test_run_dump_include_dependencies_true_restores_full(self, tmp_path):
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"\x7fELF" + b"\x00" * 100)
        fake_snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        with patch(
            "abicheck.workflows.dump.native._run_dump_uncached", return_value=fake_snap
        ):
            result = run_dump(elf_path, "elf", include_system_declarations=True)
        assert result.dependency_scope == "full"

    def test_run_dump_include_dependencies_false_filters(self, tmp_path):
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"\x7fELF" + b"\x00" * 100)
        fake_snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        with patch(
            "abicheck.workflows.dump.native._run_dump_uncached", return_value=fake_snap
        ):
            result = run_dump(elf_path, "elf", include_system_declarations=False)
        assert result.dependency_scope == "filtered"

    def test_run_dump_preserves_own_name_and_signature(self):
        """CodeRabbit review: the double functools.wraps() chain
        (_call_run_dump_uncached wraps _run_dump_uncached, then
        wrap_run_dump_with_dependency_scope wraps that) copied __name__ all
        the way down to "_run_dump_uncached" instead of "run_dump" -- wrong
        for any caller that introspects it. The extended __signature__
        (include_dependencies added) must stay correct too."""
        import inspect

        assert run_dump.__name__ == "run_dump"
        assert run_dump.__qualname__ == "run_dump"
        sig = inspect.signature(run_dump)
        assert "include_system_declarations" in sig.parameters
        assert sig.parameters["include_system_declarations"].default is False
