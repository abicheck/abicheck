"""Unit tests for abicheck.service — targeting ≥80% coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.api_types import CompareRequest, CompareResult, InputSpec
from abicheck.checker_types import DiffResult
from abicheck.comparability import compute_extraction_contract
from abicheck.errors import ScopeMismatchError, SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, DependencyInfo, Function, Visibility
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.service import (
    _render_deps_section_md,
    collect_metadata,
    compare_snapshots,
    dedup_policy_override_warnings,
    detect_binary_format,
    expand_header_inputs,
    load_suppression_and_policy,
    render_output,
    resolve_input,
    run_compare,
    run_compare_request,
    run_dump,
    sniff_text_format,
)

# ── detect_binary_format() ──────────────────────────────────────────────────


class TestDetectBinaryFormat:
    def test_delegates_to_binary_utils(self, tmp_path):
        p = tmp_path / "test.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        result = detect_binary_format(p)
        assert result == "elf"

    def test_non_binary_returns_none(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello world")
        result = detect_binary_format(p)
        assert result is None


# ── sniff_text_format() ─────────────────────────────────────────────────────


class TestSniffTextFormat:
    def test_json_format(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        assert sniff_text_format(p) == "json"

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = { 'Headers' => {} };")
        assert sniff_text_format(p) == "perl"

    def test_unknown_format(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("Some random text content")
        assert sniff_text_format(p) == "unknown"

    def test_oserror_returns_unknown(self, tmp_path):
        p = tmp_path / "nonexistent"
        assert sniff_text_format(p) == "unknown"

    def test_json_with_whitespace(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text("   \n  {}")
        assert sniff_text_format(p) == "json"

    def test_zstd_compressed_json_with_realistic_window(self, tmp_path):
        """End-to-end repro of the reported bug: a `.json.zst` baseline
        whose frame records a realistic multi-megabyte window (matching the
        writer's real baseline level -- content large enough to keep zstd
        out of the single-segment mode that would otherwise collapse the
        recorded window down to the content size) must still sniff as
        `json` rather than falling through to `unknown` -- which is what
        surfaced as the user-facing "Cannot detect format" error when
        `snapshot_io._ZSTD_MAX_WINDOW_SIZE_BYTES` was computed in KiB
        instead of bytes."""
        zstandard = pytest.importorskip("zstandard")

        blob = "a" * (9 * 1024 * 1024)
        text = json.dumps({"library": "x", "version": "1", "blob": blob})

        params = zstandard.ZstdCompressionParameters.from_level(19, window_log=23)
        cctx = zstandard.ZstdCompressor(compression_params=params)
        compressed = cctx.compress(text.encode())
        frame = zstandard.get_frame_parameters(compressed)
        assert frame.window_size == 8 * 1024 * 1024

        p = tmp_path / "baseline.json.zst"
        p.write_bytes(compressed)
        assert sniff_text_format(p) == "json"


# ── expand_header_inputs() ──────────────────────────────────────────────────


class TestExpandHeaderInputs:
    def test_single_file(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("#pragma once")
        result = expand_header_inputs([h])
        assert result == [h]

    def test_directory_expansion(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        (d / "a.h").write_text("")
        (d / "b.hpp").write_text("")
        (d / "c.txt").write_text("")  # not a header
        result = expand_header_inputs([d])
        names = {p.name for p in result}
        assert "a.h" in names
        assert "b.hpp" in names
        assert "c.txt" not in names

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            expand_header_inputs([tmp_path / "missing.h"])

    def test_empty_directory_raises(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ValidationError, match="no supported header"):
            expand_header_inputs([d])

    def test_deduplication(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("")
        result = expand_header_inputs([h, h])
        assert len(result) == 1

    def test_directory_with_subdirs(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        sub = d / "sub"
        sub.mkdir()
        (sub / "deep.h").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 1
        assert result[0].name == "deep.h"

    @pytest.mark.parametrize("noise_dir", [".abicheck-build", ".git"])
    def test_prunes_abicheck_build_and_vcs_dirs(self, tmp_path, noise_dir):
        # Generated headers under abicheck's own cmake build dir (and VCS dirs)
        # must never inflate the L2 header surface (CodeRabbit).
        d = tmp_path / "include"
        d.mkdir()
        (d / "public.h").write_text("int api(void);\n")
        sub = d / noise_dir
        sub.mkdir()
        (sub / "config.h").write_text("#define GENERATED 1\n")
        result = expand_header_inputs([d])
        names = {p.name for p in result}
        assert names == {"public.h"}  # generated config.h pruned

    def test_various_extensions(self, tmp_path):
        d = tmp_path / "hdrs"
        d.mkdir()
        for ext in (".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc"):
            (d / f"test{ext}").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 8


# ── resolve_input() ─────────────────────────────────────────────────────────


class TestResolveInput:
    def test_is_elf_true_calls_run_dump(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            result = resolve_input(p, is_elf=True)
        assert result is snap
        mock.assert_called_once()

    def test_include_dependencies_threads_to_run_dump(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(p, is_elf=True, include_dependencies=False)
        assert mock.call_args.kwargs["include_dependencies"] is False

    def test_include_dependencies_defaults_true(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(p, is_elf=True)
        assert mock.call_args.kwargs["include_dependencies"] is True

    def test_resolve_input_no_longer_accepts_header_graph_kwargs(self, tmp_path):
        # G29 Phase A: header_graph/header_graph_includes are no longer
        # public parameters of resolve_input at all — the L2 graph attach is
        # unconditional inside run_dump now (module constants, not a
        # threaded flag), so passing either kwarg is a genuine TypeError,
        # not a silently-accepted opt-in.
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with pytest.raises(TypeError):
            resolve_input(p, is_elf=True, header_graph=True)
        with pytest.raises(TypeError):
            resolve_input(p, is_elf=True, header_graph_includes=True)

    def test_header_graph_not_forwarded_as_run_dump_kwarg_elf_fast_path(self, tmp_path):
        # G29 Phase A: run_dump no longer takes header_graph/
        # header_graph_includes kwargs at all on the is_elf=True fast path
        # either — the graph attach is unconditional inside run_dump.
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(p, is_elf=True)
        _, kwargs = mock.call_args
        assert "header_graph" not in kwargs
        assert "header_graph_includes" not in kwargs

    def test_header_graph_not_forwarded_as_run_dump_kwarg_detected_format_path(
        self, tmp_path
    ):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(p)
        _, kwargs = mock.call_args
        assert "header_graph" not in kwargs
        assert "header_graph_includes" not in kwargs

    def test_header_graph_attach_reached_through_linker_script(self, tmp_path):
        # A caller resolving `libfoo.so` (the dev-symlink-shaped GNU ld
        # script) must still get the L2 graph attach on the real target it
        # follows to — the recursive resolve_input() call used to need
        # explicit header_graph/header_graph_includes forwarding to avoid
        # dropping them back to False (Codex review); now that the attach is
        # unconditional inside run_dump, there is nothing to drop.
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 100)
        script = tmp_path / "libfoo.so"
        script.write_text("INPUT(libfoo.so.1)\n", encoding="utf-8")
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(script)
        assert mock.call_count == 1
        _, kwargs = mock.call_args
        assert "header_graph" not in kwargs
        assert "header_graph_includes" not in kwargs

    def test_include_dependencies_reaches_target_through_linker_script(self, tmp_path):
        """Codex review: the recursive resolve_input() call following a GNU
        ld linker script to its real target used to drop include_dependencies
        back to its default (True), so `compare --include-system-declarations`
        (filtered by default) silently stopped filtering for any operand
        that happened to be a linker script instead of the DSO directly."""
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 100)
        script = tmp_path / "libfoo.so"
        script.write_text("INPUT(libfoo.so.1)\n", encoding="utf-8")
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap) as mock:
            resolve_input(script, include_dependencies=False)
        assert mock.call_count == 1
        _, kwargs = mock.call_args
        assert kwargs["include_dependencies"] is False

    def test_header_graph_lang_matches_elf_main_pass_normalization(self, tmp_path):
        """Codex review: ``_dump_elf`` normalizes ``lang`` to only ever force
        "c" explicitly (letting auto-detection run for the default "c++"),
        before calling ``dumper.dump()`` -- ``_attach_header_graph``'s own
        ``_clang_header_dump`` call must be given that identical normalized
        value, or it hashes a different cache key than the main pass just
        used and permanently misses the new AST reuse memo for the default,
        by far the most common, ELF dump shape."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch(
                "abicheck.service_dump_native._attach_header_graph", return_value=snap
            ) as mock_attach,
        ):
            run_dump(p, "elf", headers=[header], includes=[], lang="c++")
        args, _ = mock_attach.call_args
        assert args[5] is None  # not the raw "c++" default

    def test_header_graph_lang_forces_c_like_elf_main_pass(self, tmp_path):
        """The other half of the normalization: an explicit ``lang="c"``
        request must still reach ``_attach_header_graph`` as "c", matching
        what ``_dump_elf`` itself forwards to ``dumper.dump()`` for that
        explicit case."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n")
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch(
                "abicheck.service_dump_native._attach_header_graph", return_value=snap
            ) as mock_attach,
        ):
            run_dump(p, "elf", headers=[header], includes=[], lang="c")
        args, _ = mock_attach.call_args
        assert args[5] == "c"

    def test_elf_forwards_explicit_public_include_search_dirs_to_dump_elf(
        self, tmp_path
    ):
        """Codex review, fresh evidence: `_run_dump_uncached()` computed
        `_public_include_search_dirs` (falling back to the possibly-widened
        `_includes` only when the caller didn't distinguish the two) but
        never forwarded it into its own `_dump_elf()` call, which
        independently re-derived provenance widening from its own
        `includes` parameter -- silently reintroducing the exact
        already-widened-includes regression this whole parameter exists to
        prevent, just one call layer up."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        widened_dir = tmp_path / "widened"
        explicit_dir = tmp_path / "explicit"
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_elf", return_value=snap) as mock_dump_elf,
            patch("abicheck.service_dump_native._attach_header_graph", return_value=snap),
        ):
            run_dump(
                p,
                "elf",
                headers=[header],
                includes=[widened_dir],
                lang="c++",
                public_include_search_dirs=[explicit_dir],
            )
        passed = mock_dump_elf.call_args.kwargs["public_include_search_dirs"]
        assert passed == [explicit_dir]
        assert widened_dir not in passed

    def test_elf_forwards_explicit_public_include_search_dirs_to_header_graph(
        self, tmp_path
    ):
        """Same finding, the header-graph-attach half: the ELF branch's own
        `_attach_header_graph` call used `_includes` (possibly widened)
        for `include_search_dirs` instead of `_public_include_search_dirs`
        -- disagreeing with the primary parse's own declaration-provenance
        classification just fixed above (Codex review, fresh evidence)."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        widened_dir = tmp_path / "widened"
        explicit_dir = tmp_path / "explicit"
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch(
                "abicheck.service_dump_native._attach_header_graph", return_value=snap
            ) as mock_attach,
        ):
            run_dump(
                p,
                "elf",
                headers=[header],
                includes=[widened_dir],
                lang="c++",
                public_include_search_dirs=[explicit_dir],
            )
        passed = mock_attach.call_args.kwargs["include_search_dirs"]
        assert passed == [explicit_dir]
        assert widened_dir not in passed

    def test_header_graph_lang_matches_pe_main_pass_normalization(self, tmp_path):
        """Codex review (second pass): PE/Mach-O's own main pass, reached via
        ``service_header_scoped._try_header_scoped_dump`` whenever headers
        are given (the only case ``_attach_header_graph`` does anything at
        all), normalizes ``lang`` the same way ELF's ``_dump_elf`` does
        (case-insensitively) -- the earlier assumption that PE/Mach-O never
        normalize was wrong; ``_attach_header_graph`` must match that
        normalized value too, not the raw default "c++"."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch(
                "abicheck.service_dump_native._attach_header_graph", return_value=snap
            ) as mock_attach,
        ):
            run_dump(p, "pe", headers=[header], includes=[], lang="c++")
        args, _ = mock_attach.call_args
        assert args[5] is None  # not the raw "c++" default

    def test_header_graph_lang_forces_c_for_pe(self, tmp_path):
        """The other half: an explicit (case-insensitive) ``lang="C"``
        request must still reach ``_attach_header_graph`` as "C", matching
        what ``_try_header_scoped_dump`` forwards for that explicit case."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n")
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch(
                "abicheck.service_dump_native._attach_header_graph", return_value=snap
            ) as mock_attach,
        ):
            run_dump(p, "pe", headers=[header], includes=[], lang="C")
        args, _ = mock_attach.call_args
        assert args[5] == "C"

    def test_binary_detection_elf(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.run_dump", return_value=snap):
            result = resolve_input(p)
        assert result is snap

    def test_elf_forwards_provenance_to_dumper(self, tmp_path):
        # P1 regression: the ELF service path (used by `scan`) must thread
        # public_headers / public_header_dirs into dumper.dump, which runs
        # apply_provenance. Without this the ELF origins stay UNKNOWN and the
        # provenance-gated cross-checks silently skip — even with
        # public-header set given. The `dump` CLI always forwarded them; this
        # path did not.
        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 100)
        hdr = tmp_path / "pub.h"
        hdr.write_text("int f();")
        pubdir = tmp_path / "include"
        pubdir.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            resolve_input(
                so,
                headers=[hdr],
                includes=[],
                is_elf=True,
                public_headers=[hdr],
                public_header_dirs=[pubdir],
            )
        kwargs = mock.call_args.kwargs
        assert kwargs["public_headers"] == [hdr]
        assert kwargs["public_header_dirs"] == [pubdir]

    def test_symvers_by_filename(self, tmp_path):
        p = tmp_path / "Module.symvers"
        p.write_text("0x1\tkmalloc\tvmlinux\tEXPORT_SYMBOL_GPL\tCORE\n")
        result = resolve_input(p, is_elf=False)
        assert result.kabi is not None
        assert result.kabi.entries["kmalloc"].namespace == "CORE"

    def test_symvers_by_content_generic_name(self, tmp_path):
        # A generically-named file still resolves as kABI via content sniffing.
        p = tmp_path / "syms.txt"
        p.write_text("0x2\tkfree\tvmlinux\tEXPORT_SYMBOL\t\n")
        result = resolve_input(p, is_elf=False)
        assert result.kabi is not None
        assert "kfree" in result.kabi.entries

    def test_symvers_empty_falls_through(self, tmp_path):
        # A .symvers file with no valid records is not treated as kABI.
        from abicheck.service import _resolve_symvers

        p = tmp_path / "empty.symvers"
        p.write_text("# only a comment\n")
        assert _resolve_symvers(p, "1.0") is None

    def test_symvers_unreadable_returns_none(self, tmp_path):
        from abicheck.service import _resolve_symvers

        # A directory named like a manifest cannot be read as text → None.
        d = tmp_path / "Module.symvers"
        d.mkdir()
        assert _resolve_symvers(d, "1.0") is None

    def test_json_text_format(self, tmp_path):
        p = tmp_path / "snap.json"
        snap = AbiSnapshot(library="test", version="1.0")
        p.write_text('{"library": "test"}')
        with patch("abicheck.workflows.input_resolution.load_snapshot", return_value=snap):
            result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_json_load_error_wraps_in_snapshot_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with patch("abicheck.workflows.input_resolution.load_snapshot", side_effect=ValueError("bad")):
            with pytest.raises(SnapshotError, match="Failed to load JSON"):
                resolve_input(p, is_elf=False)

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.workflows.input_resolution.detect_binary_format", return_value=None):
            with patch("abicheck.workflows.input_resolution.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    return_value=snap,
                ):
                    result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_perl_import_error(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        with patch("abicheck.workflows.input_resolution.detect_binary_format", return_value=None):
            with patch("abicheck.workflows.input_resolution.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    side_effect=ValueError("parse fail"),
                ):
                    with pytest.raises(SnapshotError, match="ABICC Perl"):
                        resolve_input(p, is_elf=False)

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "mystery"
        p.write_text("???")
        with patch("abicheck.workflows.input_resolution.detect_binary_format", return_value=None):
            with patch("abicheck.workflows.input_resolution.sniff_text_format", return_value="unknown"):
                with pytest.raises(ValidationError, match="Cannot detect format"):
                    resolve_input(p, is_elf=False)

    def test_static_archive_raises_with_guidance(self, tmp_path):
        """A `.a`/`.lib` ar archive fails deliberately with actionable guidance
        (G8 — static libraries are a by-design non-goal), not a generic
        'Cannot detect format' error."""
        p = tmp_path / "libfoo.a"
        # Minimal ar archive: magic + an (empty) member header is not required —
        # the magic alone is what resolve_input branches on.
        p.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        with pytest.raises(ValidationError, match="static/import library archive"):
            resolve_input(p)


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
        with patch("abicheck.service_dump_native._dump_elf", return_value=snap):
            result = run_dump(p, "elf")
        assert result is snap

    def test_pe_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap):
            result = run_dump(p, "pe")
        assert result is snap

    def test_macho_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service_dump_native._dump_macho", return_value=snap):
            result = run_dump(p, "macho")
        assert result is snap


class TestRunDumpHybridNormalization:
    """G28 Phase 3 (Codex review): eff_backend == "hybrid" was a raw string
    comparison, so an indirectly-selected hybrid request (case-insensitive
    value, or the documented ABICHECK_AST_FRONTEND=hybrid pin with auto)
    fell through to the single-backend path instead of triggering the merge
    recursion -- fixed by resolving through dumper._resolve_header_backend.
    """

    def _fake_dump_elf(self, castxml_snap, clang_snap):
        def _fake(*args, **kwargs):
            compile_ctx = kwargs.get("compile")
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
        with patch(
            "abicheck.service_dump_native._dump_elf",
            side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
        ):
            result = run_dump(p, "elf", header_backend="HYBRID")
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
        with patch(
            "abicheck.service_dump_native._dump_elf",
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
        def _fake(*args, **kwargs):
            compile_ctx = kwargs.get("compile")
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
            patch(
                "abicheck.service_dump_native._dump_elf",
                side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
            ),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
        ):
            result = run_dump(p, "elf", header_backend="hybrid")

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
        def _fake(*args, **kwargs):
            compile_ctx = kwargs.get("compile")
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
            patch(
                "abicheck.service_dump_native._dump_elf",
                side_effect=self._fake_dump_elf(castxml_snap, clang_snap),
            ),
            patch("abicheck.service_dump_native.attach_clang_layout", side_effect=_fake_attach),
        ):
            result = run_dump(p, "elf", header_backend="hybrid")

        # attach_clang_layout is called once per recursive sub-dump's OWN
        # elf-branch tail (castxml_snap's and clang_snap's) -- never a third
        # time on the merged (ast_producer="hybrid") snapshot itself.
        assert len(calls) == 2
        assert all(c.ast_producer != "hybrid" for c in calls)
        assert result.ast_producer == "hybrid"


# ── _implicit_header_includes() (P3: -H umbrella resolves without -I) ────────


class TestImplicitHeaderIncludes:
    def test_directory_input_is_its_own_root(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        assert _implicit_header_includes([inc]) == [inc]

    def test_file_at_root_adds_parent(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        umb = inc / "dnnl.hpp"
        umb.write_text("// umbrella")
        assert _implicit_header_includes([umb]) == [inc]

    def test_nested_umbrella_adds_include_root_ancestor(self, tmp_path):
        # include/oneapi/tbb.h → both its parent (include/oneapi) and the
        # conventional include root (include/) must be on the search path so
        # `#include "oneapi/tbb/..."` resolves.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        umb = nested / "tbb.h"
        umb.write_text("// umbrella")
        dirs = _implicit_header_includes([umb])
        assert nested in dirs
        assert root in dirs

    def test_namespace_directory_adds_include_root_ancestor(self, tmp_path):
        # A -H *directory* nested under a conventional root, e.g.
        # `-H include/oneapi`, must add BOTH itself and the include root —
        # headers inside still `#include "oneapi/..."` relative to include/.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        dirs = _implicit_header_includes([nested])
        assert nested in dirs
        assert root in dirs

    def test_deduplicates(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "a.h").write_text("")
        (inc / "b.h").write_text("")
        # Two files in the same dir → the root appears once.
        assert _implicit_header_includes([inc / "a.h", inc / "b.h"]) == [inc]

    def test_skips_nonexistent_parent(self, tmp_path):
        # A -H file whose parent dir does not exist contributes nothing.
        from abicheck.header_utils import _implicit_header_includes

        ghost = tmp_path / "absent" / "x.h"
        assert _implicit_header_includes([ghost]) == []


class TestSplitPublicHeaderInputs:
    """A `-H`/`--header`/`--devel-pkg` input may name a file or a whole
    directory; the two must route to resolve_input's public_headers vs.
    public_header_dirs separately (real CI incident, comparability.py's
    scope_fingerprint common-root leak when a directory entry was
    fingerprinted as an individual file identity)."""

    def test_all_files(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        a = tmp_path / "a.h"
        a.write_text("// a")
        b = tmp_path / "b.h"
        b.write_text("// b")
        files, dirs = split_public_header_inputs([a, b])
        assert files == [a, b]
        assert dirs == []

    def test_all_directories(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        d1 = tmp_path / "include"
        d1.mkdir()
        d2 = tmp_path / "usr" / "include"
        d2.mkdir(parents=True)
        files, dirs = split_public_header_inputs([d1, d2])
        assert files == []
        assert dirs == [d1, d2]

    def test_mixed_files_and_directories_preserve_relative_order(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        f = tmp_path / "a.h"
        f.write_text("// a")
        d = tmp_path / "include"
        d.mkdir()
        files, dirs = split_public_header_inputs([f, d])
        assert files == [f]
        assert dirs == [d]

    def test_empty_input(self):
        from abicheck.header_utils import split_public_header_inputs

        assert split_public_header_inputs([]) == ([], [])


class TestResolveInferredHeaderRoots:
    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        return root, umb

    def test_no_build_context_uses_plain_I(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [])
        assert root in inc and toks == []

    def test_isystem_context_defers_via_isystem(self, tmp_path):
        # A build-context -isystem makes the inferred root defer — emitted as
        # -isystem (below build context, above standard system dirs), not -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-isystem", "/gen")
        )
        assert inc == []
        # every inferred root is emitted as -isystem (not -I, not -idirafter)
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in toks and "-I" not in toks

    def test_gcc_options_include_string_detected(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/include -O2"
        )
        assert inc == [] and str(root) in toks

    @pytest.mark.parametrize(
        ("tok", "want"),
        [
            ("/Ibuild\\generated", "/I"),
            ("/external:Igen", "/external:I"),
            ("/imsvc", "/imsvc"),
        ],
    )
    def test_msvc_slash_I_context_detected(self, tmp_path, tok, want):
        # An MSVC/clang-cl build context (/I, /external:I, /imsvc) must also count
        # as build context so the inferred root defers instead of shadowing it,
        # and in the MSVC dialect (never GNU -isystem, which cl.exe/clang-cl
        # would ignore). The deferred bucket mirrors the context's own lowest
        # bucket so the root can't shadow /external:I//imsvc system dirs (#454):
        # a plain /I context stays /I; a system-bucket context echoes it.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=(tok,))
        assert inc == []  # detected as build context → deferred
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == want
        assert "-isystem" not in toks

    def test_msvc_system_bucket_root_does_not_shadow(self, tmp_path):
        # #454 item 3: when the MSVC context uses a system bucket, the deferred
        # root must echo that bucket (not collapse to /I, which clang-cl lowers
        # to -I and searches *above* the /external:I//imsvc system dirs). With
        # both a plain /I and a system bucket present, the system bucket wins so
        # the root sits below every build-context include dir.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, ext = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /external:I third_party"
        )
        assert ext[ext.index(str(root)) - 1] == "/external:I"
        _, imsvc = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /imsvc clang_sys"
        )
        assert imsvc[imsvc.index(str(root)) - 1] == "/imsvc"
        # When both appear, the *lowest-searched* bucket wins: clang-cl searches
        # /imsvc (%INCLUDE%-style) dirs after /external:I, so deferring into
        # /imsvc keeps the root below the build's /imsvc dirs too (Codex review).
        # A context using /imsvc is necessarily clang-cl, so /imsvc is supported.
        _, both = resolve_inferred_header_roots(
            [umb], [], gcc_options="/imsvc a /external:I b"
        )
        assert both[both.index(str(root)) - 1] == "/imsvc"

    def test_msvc_bucket_not_fooled_by_include_operand(self, tmp_path):
        # A spaced /I operand that merely *starts with* a bucket name (a dir
        # literally called /imsvc-sdk) must NOT be read as an /imsvc flag — the
        # only real flag here is /I, so the deferred root stays /I (CodeRabbit
        # review). Picking /imsvc would emit a flag cl.exe rejects.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("/I", "/imsvc-sdk")
        )
        assert toks[toks.index(str(root)) - 1] == "/I"

        # The mirror case: a *GNU* -I context whose operand dir starts with a
        # slash spelling must not be misclassified as MSVC (dialect detection
        # filters operands too) — it stays the GNU -isystem bucket.
        _, gnu_toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-I", "/imsvc-sdk")
        )
        assert gnu_toks[gnu_toks.index(str(root)) - 1] == "-isystem"

    def test_deferred_flag_dialect_matches_build_context(self, tmp_path):
        # The deferred flag matches the build context's lowest include bucket:
        # above-system GNU (-I/-isystem) → -isystem; MSVC /I → /I; an
        # -idirafter-only context → -idirafter (so its below-system fallback
        # keeps priority instead of being shadowed by an -isystem root).
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, gnu = resolve_inferred_header_roots([umb], [], gcc_options="-I /build/gen")
        assert gnu[gnu.index(str(root)) - 1] == "-isystem"
        _, msvc = resolve_inferred_header_roots([umb], [], gcc_options="/I build\\gen")
        assert msvc[msvc.index(str(root)) - 1] == "/I"
        _, after = resolve_inferred_header_roots(
            [umb], [], gcc_options="-idirafter /build/gen"
        )
        assert after[after.index(str(root)) - 1] == "-idirafter"
        assert "-isystem" not in after

    def test_mixed_above_system_and_idirafter_defaults_to_isystem(self, tmp_path):
        # #454 item 2: a mixed GNU context (-I + -idirafter) is unsatisfiable
        # with a single flag — the root can't be both above the system dirs
        # (to win the -I-context basename collision) and below -idirafter.
        # -isystem is the documented default (favors the common collision
        # case; compile DBs essentially never emit -idirafter). This locks
        # that choice in so a refactor can't silently flip it to -idirafter.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, mixed = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/primary -idirafter /build/generated"
        )
        assert mixed[mixed.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in mixed

    def test_root_already_in_build_context_is_skipped(self, tmp_path):
        # A root the build context already supplies as -I must NOT be re-added as
        # -isystem (GCC would then ignore the build's -I). Here the build provides
        # the include root; only the *other* inferred ancestor (oneapi) defers.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)  # include/, umbrella at include/oneapi
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options=f"-I {root}")
        assert inc == []
        # the include root is in the build context → not re-emitted at all
        assert str(root) not in toks
        # the nested ancestor is not in the build context → deferred via -isystem
        assert str(nested) in toks
        assert toks[toks.index(str(nested)) - 1] == "-isystem"

    def test_build_context_dir_attached_form_deduped(self, tmp_path):
        # The attached spelling (-I<dir>) is parsed too, so the root is skipped.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=(f"-I{root}",)
        )
        assert str(root) not in toks

    def test_deferred_token_dirs_extracts_isystem_paths(self):
        from pathlib import Path

        from abicheck.header_utils import deferred_token_dirs

        toks = ["-isystem", "/a/include", "-isystem", "/b/oneapi"]
        assert deferred_token_dirs(toks) == [Path("/a/include"), Path("/b/oneapi")]
        assert deferred_token_dirs([]) == []

    def test_dangling_include_flag_no_operand(self, tmp_path):
        # A bare -I with no following dir (build context present but supplies no
        # parsable dir) still defers the inferred roots via -isystem, no crash.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=("-I",))
        assert inc == []
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"

    def test_non_include_options_are_not_build_context(self, tmp_path):
        # -O2/-DNDEBUG add no include dir, so the inferred root stays a plain -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-O2 -DNDEBUG", gcc_option_tokens=("-Wall",)
        )
        assert root in inc and toks == []

    def test_user_include_deduped(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [nested])
        # nested came from the user -I; only the include root is inferred-added.
        assert nested not in inc and root in inc

    def test_no_inferred_roots_returns_empty(self, tmp_path):
        # A -H file with a nonexistent parent yields no inferred roots → no flags
        # of either kind (and no spurious -isystem even with a build context).
        from abicheck.header_utils import resolve_inferred_header_roots

        ghost = tmp_path / "absent" / "x.h"
        assert resolve_inferred_header_roots(
            [ghost], [], gcc_option_tokens=("-isystem", "/x")
        ) == ([], [])

    def test_malformed_gcc_options_falls_back_to_plain_split(self, tmp_path):
        # An unbalanced quote makes shlex.split raise; we fall back to str.split
        # so an -I in a malformed --gcc-options string is still detected.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options='-I "/broken')
        assert inc == [] and str(root) in toks


# ── _dump_elf() ─────────────────────────────────────────────────────────────


class TestDumpElf:
    def test_implicit_header_root_passed_to_dumper(self, tmp_path):
        # P3 regression: a -H umbrella nested under include/ must reach the
        # frontend with the include root on extra_includes, with no explicit -I.
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [umb], [], "1.0", "c++")
        passed = mock.call_args.kwargs["extra_includes"]
        assert root in passed  # the include root was auto-added (plain -I)

    def test_gcc_option_tokens_include_dir_is_hashed(self, tmp_path):
        """Codex review, PR #782: this primary ELF dump pass's own
        deferred_dirs (extra_hash_dirs) computation only covered
        resolve_inferred_header_roots's own deferred roots -- never any
        include-search directory riding in compile.gcc_option_tokens itself
        (an explicit --gcc-options/--compiler-option -I, or -- since the
        P0.3 L3->L2 fold -- a compile-DB-derived one). service._attach_
        header_graph's own independent second parse already hashes this
        identical set into its own cache key, so leaving it out of THIS
        primary parse's cache key let the two passes disagree on staleness
        -- reusing a stale cached AST here while the header-graph pass
        correctly reparsed."""
        from abicheck.service import _dump_elf
        from abicheck.service_scan import CompileContext

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        build_inc = tmp_path / "buildinc"
        build_inc.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        cc = CompileContext(gcc_option_tokens=("-I", str(build_inc)))
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [header], [], "1.0", "c++", compile=cc)
        assert build_inc in mock.call_args.kwargs["extra_hash_dirs"]

    def test_public_include_search_dirs_used_over_widened_includes(self, tmp_path):
        """Codex review, fresh evidence: this function's own internal
        dump() call previously derived provenance widening from its own
        `includes` parameter directly -- but by the time `includes` reaches
        this function, it can already be build/source-evidence-widened by
        an upstream caller (`_run_dump_uncached`'s own `_seeded_includes_
        and_compile_context`-style seeding), so using it directly risked
        promoting a private sibling header under a build-derived directory
        to PUBLIC_HEADER. A caller that threads the genuinely explicit list
        separately via `public_include_search_dirs` must have THAT list
        reach dump(), not the (possibly wider) `includes`."""
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        widened_dir = tmp_path / "widened"
        widened_dir.mkdir()
        explicit_dir = tmp_path / "explicit"
        explicit_dir.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(
                p,
                [header],
                [widened_dir],
                "1.0",
                "c++",
                public_include_search_dirs=[explicit_dir],
            )
        passed = mock.call_args.kwargs["public_include_search_dirs"]
        assert passed == [explicit_dir]
        assert widened_dir not in passed

    def test_public_include_search_dirs_falls_back_to_includes_when_omitted(
        self, tmp_path
    ):
        """Backward-compatible default (Codex review): a caller that hasn't
        been updated to distinguish the two still gets today's unchanged
        behavior -- `includes` itself is used."""
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [header], [inc], "1.0", "c++")
        assert mock.call_args.kwargs["public_include_search_dirs"] == [inc]

    def test_implicit_root_defers_to_isystem_build_context(self, tmp_path):
        # Codex: when the caller's CompileContext supplies includes via -isystem,
        # the inferred -H root must defer — emitted as its own -isystem token
        # *after* the build's (build's is emitted first, so it wins), not jumping
        # ahead as -I. -isystem also keeps it above the standard system dirs.
        from abicheck.service import _dump_elf
        from abicheck.service_scan import CompileContext

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        gen = str(tmp_path / "gen")
        snap = AbiSnapshot(library="t", version="1.0")
        cc = CompileContext(gcc_option_tokens=("-isystem", gen))
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [umb], [], "1.0", "c++", compile=cc)
        kwargs = mock.call_args.kwargs
        assert root not in kwargs["extra_includes"]  # not promoted to -I
        toks = list(kwargs["gcc_option_tokens"])
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        # the build's -isystem dir stays ahead of the inferred root (wins)
        assert toks.index(gen) < toks.index(str(root))

    def test_no_headers_warning(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [], "1.0", "c++")
        assert result is snap

    def test_invalid_include_dir_raises(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        h = tmp_path / "foo.h"
        h.write_text("")
        bad_inc = tmp_path / "nonexistent"
        with patch("abicheck.service_dump_native.expand_header_inputs", return_value=[h]):
            with pytest.raises(ValidationError, match="Include directory"):
                _dump_elf(p, [h], [bad_inc], "1.0", "c++")

    def test_dump_error_wraps(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x00" * 10)
        with patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", side_effect=RuntimeError("bad elf")):
                with pytest.raises(SnapshotError, match="Failed to dump"):
                    _dump_elf(p, [], [], "1.0", "c++")

    def test_includes_without_headers_warns(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [inc], "1.0", "c++")
        assert result is snap

    def test_lang_c_sets_compiler(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
                _dump_elf(p, [], [], "1.0", "c")
        call_kwargs = mock_dump.call_args
        assert (
            call_kwargs.kwargs.get("compiler") == "cc"
            or call_kwargs[1].get("compiler") == "cc"
        )

    def test_debuginfod_url_reaches_resolve_debug_info(self, tmp_path):
        # Codex (PR #551): a custom --debuginfod-url must reach the resolver's
        # debuginfod_urls kwarg, not just gate enable_debuginfod on/off.
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]),
            patch(
                "abicheck.debug_resolver.resolve_debug_info", return_value=None
            ) as mock_resolve,
            patch("abicheck.dumper.dump", return_value=snap),
        ):
            _dump_elf(
                p,
                [],
                [],
                "1.0",
                "c++",
                enable_debuginfod=True,
                debuginfod_url="https://debuginfod.example.test/",
            )
        assert mock_resolve.call_args.kwargs["debuginfod_urls"] == [
            "https://debuginfod.example.test/"
        ]

    def test_no_debuginfod_url_passes_none(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch("abicheck.service_dump_native.expand_header_inputs", return_value=[]),
            patch(
                "abicheck.debug_resolver.resolve_debug_info", return_value=None
            ) as mock_resolve,
            patch("abicheck.dumper.dump", return_value=snap),
        ):
            _dump_elf(p, [], [], "1.0", "c++", enable_debuginfod=True)
        assert mock_resolve.call_args.kwargs["debuginfod_urls"] is None


# ── _dump_pe() ──────────────────────────────────────────────────────────────


class TestHeaderScopedInferredRoots:
    """P3 parity: the PE/Mach-O header-scoped path also adds inferred -H roots."""

    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("int f(void);\n", encoding="utf-8")
        return root, umb

    def test_pe_no_build_context_adds_root_as_I(self, tmp_path):
        from abicheck.service import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)
        captured = {}

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        with patch("abicheck.dumper._dump_pe", fake_pe):
            _try_header_scoped_dump("pe", tmp_path / "x.dll", [umb], [], "1.0", "c++")
        # no build context → inferred include root rides in extra_includes
        assert root in captured["extra_includes"]

    def test_no_headers_skips_inferred_derivation(self, tmp_path):
        # With no -H headers the derivation is skipped: the original includes pass
        # through unchanged and nothing is deferred/hashed.
        from abicheck.service import _try_header_scoped_dump

        captured = {}

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        inc = [tmp_path / "inc"]
        with patch("abicheck.dumper._dump_pe", fake_pe):
            _try_header_scoped_dump("pe", tmp_path / "x.dll", [], inc, "1.0", "c++")
        assert captured["extra_includes"] == inc  # unchanged, no inferred roots
        assert captured["extra_hash_dirs"] == ()

    def test_macho_build_context_defers_and_hashes(self, tmp_path):
        from abicheck.service import _try_header_scoped_dump
        from abicheck.service_scan import CompileContext

        root, umb = self._umbrella(tmp_path)
        captured = {}

        def fake_macho(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        cc = CompileContext(gcc_option_tokens=("-isystem", str(tmp_path / "gen")))
        with patch("abicheck.dumper._dump_macho", fake_macho):
            _try_header_scoped_dump(
                "macho", tmp_path / "x.dylib", [umb], [], "1.0", "c++", compile=cc
            )
        # build context → root defers to -isystem (gcc_option_tokens), not -I,
        # and its dir is hashed into the cache key (extra_hash_dirs)
        assert root not in captured["extra_includes"]
        toks = list(captured["gcc_option_tokens"])
        assert str(root) in toks and toks[toks.index(str(root)) - 1] == "-isystem"
        assert root in captured["extra_hash_dirs"]

    def test_pe_header_scoped_dump_attaches_extraction_contract(self, tmp_path):
        """ADR-050 D1 (Codex review, PR #624 follow-up): this path calls
        dumper._dump_pe directly, bypassing dumper.dump() entirely -- without
        the _attach_extraction_contract call, contract would silently stay
        None for every real PE header-scoped dump, unlike the ELF service
        path (_dump_elf), which already routes through dumper.dump()."""
        from abicheck.service import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            return AbiSnapshot(
                library="x",
                version="1.0",
                from_headers=True,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )

        with patch("abicheck.dumper._dump_pe", fake_pe):
            snap, reason = _try_header_scoped_dump(
                "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
            )
        assert reason is None
        assert snap is not None
        assert snap.contract is not None
        assert snap.contract.profile_fingerprint is not None

    def test_pe_header_scoped_dump_threads_public_headers_into_contract(self, tmp_path):
        """Codex review, PR #624 follow-up: run_dump applies public_headers/
        public_header_dirs to a PE/Mach-O snapshot separately via
        _apply_native_provenance *after* this call returns, but that same
        provenance input must also reach the scope_fingerprint here -- else
        two saved snapshots differing only in declared public-header
        provenance could share a scope_fingerprint."""
        from abicheck.service import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            return AbiSnapshot(
                library="x",
                version="1.0",
                from_headers=True,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )

        with patch("abicheck.dumper._dump_pe", fake_pe):
            snap_no_public, _ = _try_header_scoped_dump(
                "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
            )
            snap_with_public, _ = _try_header_scoped_dump(
                "pe",
                tmp_path / "x.dll",
                [umb],
                [],
                "1.0",
                "c++",
                public_header_dirs=[root],
            )

        assert snap_no_public.contract is not None
        assert snap_with_public.contract is not None
        assert (
            snap_no_public.contract.scope_fingerprint
            != snap_with_public.contract.scope_fingerprint
        )

    def test_deadline_exceeded_propagates_not_swallowed_as_fallback(self, tmp_path):
        # Codex review on the P0 fix: the broad `except Exception` below (which
        # exists to fall back to export-table mode when a header backend is
        # merely unavailable) must NOT also swallow an active --budget's
        # deadline.DeadlineExceeded. Falling back on that would silently mask
        # the overflow (a degraded-but-"successful" scan instead of the
        # dedicated budget-overflow exit code) and let the scan keep doing
        # work past the point it should have aborted. It must propagate so
        # run_scan_core's except deadline.DeadlineExceeded -> _BudgetOverflow
        # mapping applies to PE/Mach-O the same way it already does for ELF.
        from abicheck import deadline
        from abicheck.service import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def raises_deadline_exceeded(
            path, headers, extra_includes, version, compiler, **k
        ):
            raise deadline.DeadlineExceeded(-1.0)

        with patch("abicheck.dumper._dump_pe", raises_deadline_exceeded):
            with pytest.raises(deadline.DeadlineExceeded):
                _try_header_scoped_dump(
                    "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
                )

        with patch("abicheck.dumper._dump_macho", raises_deadline_exceeded):
            with pytest.raises(deadline.DeadlineExceeded):
                _try_header_scoped_dump(
                    "macho", tmp_path / "x.dylib", [umb], [], "1.0", "c++"
                )

    def test_explicit_device_context_failure_propagates_not_swallowed(self, tmp_path):
        # Codex review: AstContextMissingError/AstContextAmbiguousError only
        # ever come from a NON-"host" --frontend-context request (ADR-050
        # D5) -- there is no "device" default, so seeing either here always
        # means the user's explicit device-context request failed. The
        # broad `except Exception` below (which exists to fall back to
        # export-table mode when a header backend is merely unavailable)
        # must not also swallow this and silently succeed with --header/
        # --include ignored, exactly the same reasoning as the
        # DeadlineExceeded test above.
        from abicheck.errors import AstContextMissingError
        from abicheck.service import _try_header_scoped_dump
        from abicheck.service_scan import CompileContext

        _root, umb = self._umbrella(tmp_path)

        def raises_ast_context_missing(
            path, headers, extra_includes, version, compiler, **k
        ):
            raise AstContextMissingError("no AST context with kind='device'")

        cc = CompileContext(frontend_context="device")

        with patch("abicheck.dumper._dump_pe", raises_ast_context_missing):
            with pytest.raises(AstContextMissingError):
                _try_header_scoped_dump(
                    "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++", compile=cc
                )

        with patch("abicheck.dumper._dump_macho", raises_ast_context_missing):
            with pytest.raises(AstContextMissingError):
                _try_header_scoped_dump(
                    "macho",
                    tmp_path / "x.dylib",
                    [umb],
                    [],
                    "1.0",
                    "c++",
                    compile=cc,
                )


class TestDumpPe:
    def test_no_machine_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = None
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(SnapshotError, match="Failed to extract PE metadata"):
                _dump_pe(p, "1.0")

    def test_no_exports_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(ValidationError, match="no exports"):
                _dump_pe(p, "1.0")

    def test_successful_pe_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "MyFunc"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.platform == "pe"
        assert len(result.functions) == 1
        assert result.functions[0].name == "MyFunc"

    def test_pe_import_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=ImportError("no pefile"),
        ):
            with pytest.raises(SnapshotError, match="no pefile"):
                _dump_pe(p, "1.0")

    def test_pe_runtime_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=RuntimeError("corrupt"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse PE"):
                _dump_pe(p, "1.0")

    def test_ordinal_export(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = None
        export.ordinal = 42
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].name == "ordinal:42"

    def test_pdb_found_and_parsed(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        mock_dwarf = MagicMock()
        mock_adv = MagicMock()
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=Path("/fake.pdb")):
                with patch(
                    "abicheck.pdb_metadata.parse_pdb_debug_info",
                    return_value=(mock_dwarf, mock_adv),
                ):
                    result = _dump_pe(p, "1.0")
        assert result.dwarf is mock_dwarf
        assert result.dwarf_advanced is mock_adv

    def test_pdb_parsing_exception_handled(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch(
                "abicheck.pdb_utils.locate_pdb", side_effect=RuntimeError("pdb error")
            ):
                result = _dump_pe(p, "1.0")
        assert result.dwarf is None

    def test_cpp_name_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "?MyFunc@@YAXXZ"  # MSVC mangled
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── _dump_macho() ───────────────────────────────────────────────────────────


class TestDumpMacho:
    def test_successful_macho_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_myFunc"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert result.platform == "macho"
        assert len(result.functions) == 1

    def test_no_exports_no_metadata_raises(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        macho_meta = MagicMock()
        macho_meta.exports = []
        macho_meta.install_name = None
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            with pytest.raises(SnapshotError, match="no exports"):
                _dump_macho(p, "1.0")

    def test_parse_error(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata",
            side_effect=RuntimeError("bad macho"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse Mach-O"):
                _dump_macho(p, "1.0")

    def test_export_without_name_skipped(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        exp_named = MagicMock()
        exp_named.name = "_func"
        exp_empty = MagicMock()
        exp_empty.name = ""
        macho_meta = MagicMock()
        macho_meta.exports = [exp_named, exp_empty]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert len(result.functions) == 1

    def test_cpp_symbol_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        export = MagicMock()
        export.name = "_ZN3foo3barEv"  # C++ mangled
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── collect_metadata() ──────────────────────────────────────────────────────


class TestCollectMetadata:
    def test_binary_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with patch("abicheck.workflows.input_resolution.sniff_text_format", return_value="unknown"):
            meta = collect_metadata(p)
        assert meta is not None
        assert meta.path == str(p)
        assert len(meta.sha256) == 64
        assert meta.size_bytes == 104

    def test_json_snapshot_returns_none(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        meta = collect_metadata(p)
        assert meta is None

    def test_perl_dump_returns_none(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        meta = collect_metadata(p)
        assert meta is None


# ── load_suppression_and_policy() ───────────────────────────────────────────


class TestLoadSuppressionAndPolicy:
    def test_no_suppress_no_policy(self):
        s, p = load_suppression_and_policy(None)
        assert s is None
        assert p is None

    def test_invalid_suppression_file(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("not: [valid: suppression")
        with pytest.raises(ValidationError, match="Invalid suppression"):
            load_suppression_and_policy(f)

    def test_valid_suppression_file(self, tmp_path):
        f = tmp_path / "suppress.yaml"
        f.write_text(
            "version: 1\nsuppressions:\n  - symbol: 'foo'\n    change_kind: func_removed\n"
        )
        s, p = load_suppression_and_policy(f)
        assert s is not None
        assert p is None

    def test_policy_file_with_non_default_policy_warns(self, tmp_path, caplog):
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("overrides: {}\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(
                None, policy="permissive", policy_file_path=pf
            )
        assert p is not None
        assert "ignored" in caplog.text.lower()

    def test_invalid_policy_file(self, tmp_path):
        pf = tmp_path / "bad_policy.yaml"
        pf.write_text("- this is a list not a mapping\n")
        with pytest.raises(ValidationError):
            load_suppression_and_policy(None, policy_file_path=pf)

    def test_risky_override_warns(self, tmp_path, caplog):
        """A risky override must warn even through this Tier-2 chokepoint --
        `compare-release`'s real per-library fan-out loads its policy here,
        not through the CLI's own `_load_suppression_and_policy` (Codex
        review: the latter's warning never reached that path)."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(None, policy_file_path=pf)
        assert p is not None
        assert "HIGH RISK" in caplog.text
        assert "func_removed" in caplog.text

    def test_risky_override_warns_every_call_outside_dedup_scope(
        self, tmp_path, caplog
    ):
        """Without `dedup_policy_override_warnings()`, every call still warns
        -- e.g. a plain `compare`/`scan --against` invocation, which only
        ever loads a given policy file once, must see no behaviour change."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            load_suppression_and_policy(None, policy_file_path=pf)
            load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 2

    def test_dedup_policy_override_warnings_warns_once_per_scope(
        self, tmp_path, caplog
    ):
        """Inside `dedup_policy_override_warnings()`, reloading the identical
        policy file warns once -- the fix for `compare-release`'s per-library
        fan-out flooding stderr with the same warning (Codex review)."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
                load_suppression_and_policy(None, policy_file_path=pf)
                load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 1

    def test_dedup_scope_does_not_leak_across_calls(self, tmp_path, caplog):
        """A fresh `dedup_policy_override_warnings()` scope must not remember
        warnings already emitted by a previous, already-exited scope."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 2

    def test_dedup_scope_shared_with_cli_params_loader(self, tmp_path, capsys, caplog):
        """`dedup_policy_override_warnings()` must dedupe across *both*
        loaders, not just repeated `service.load_suppression_and_policy()`
        calls -- `compare-release` also loads through `cli_params.
        _load_suppression_and_policy` (its early strict-suppression
        validation and probe-matrix paths), and a scope covering only one
        loader would still let the same warning through twice (Codex
        review: fresh evidence on the follow-up commit)."""
        import logging

        from abicheck.cli_params import _load_suppression_and_policy

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                _load_suppression_and_policy(None, "strict_abi", pf)  # click.echo
                load_suppression_and_policy(None, policy_file_path=pf)  # logger
                load_suppression_and_policy(None, policy_file_path=pf)  # logger
        # The CLI-level loader's warning is the first one seen in the shared
        # scope, so it "wins" -- both later service-level loads are deduped
        # against it and log nothing further.
        assert "HIGH RISK" in capsys.readouterr().err
        assert "HIGH RISK" not in caplog.text


# ── run_compare() ───────────────────────────────────────────────────────────


class TestRunCompare:
    def _make_snap_file(self, tmp_path, name, version="1.0"):
        """Create a minimal JSON snapshot file."""
        snap = AbiSnapshot(
            library=name,
            version=version,
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    is_extern_c=True,
                ),
            ],
        )
        from abicheck.serialization import save_snapshot

        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def test_compare_two_snapshots(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        result, old, new = run_compare(old_p, new_p).as_tuple()
        assert isinstance(result, DiffResult)
        assert isinstance(old, AbiSnapshot)
        assert isinstance(new, AbiSnapshot)

    def test_compare_with_suppression(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        sf = tmp_path / "suppress.yaml"
        sf.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    change_kind: func_removed\n"
        )
        result, _, _ = run_compare(old_p, new_p, suppress=sf).as_tuple()
        assert isinstance(result, DiffResult)

    def test_headers_passed_as_public_headers(self, tmp_path, monkeypatch):
        """run_compare_request (the CompareRequest chokepoint used by the
        compare-release/directory-package fan-out) must thread each side's
        headers through as its public-header set for provenance tagging —
        same rule as the single-pair CLI's compare --header fix. Regression:
        this was silently dropped, unlike the single-pair path."""
        from abicheck import service as service_mod

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        new_h = tmp_path / "new.h"

        calls: list[dict] = []
        original_resolve = service_mod.resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "version": version, **kwargs})
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _spy)

        run_compare(
            old_p,
            new_p,
            old_headers=[old_h],
            new_headers=[new_h],
        )
        assert len(calls) == 2
        # Matched by path rather than unpacked positionally: old/new resolution
        # runs concurrently on two threads (service.run_compare_request), so
        # the order the spy observes calls in is not guaranteed.
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["public_headers"] == [old_h]
        assert calls_by_path[new_p]["public_headers"] == [new_h]

    def test_debuginfod_url_appended_last_preserves_positional_order(self):
        # Codex review (PR #551): debuginfod_url was originally inserted right
        # after enable_debuginfod, ahead of scope_to_public_surface — any
        # caller invoking run_compare positionally that far would have every
        # later positional argument silently shift by one slot. It was the
        # LAST parameter at the time; diagnostic_comparison (ADR-050 D2) was
        # later appended after it by the same rule, so "last" itself moved on
        # — see test_diagnostic_comparison_appended_last_preserves_positional_order
        # for that -- but debuginfod_url's own position relative to its
        # pre-existing neighbors must still be unchanged.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        assert params.index("scope_to_public_surface") == (
            params.index("enable_debuginfod") + 1
        )
        assert (
            params.index("debuginfod_url")
            == params.index("public_surface_allowlist") + 1
        )


class TestCompareRequestAdr055Evidence:
    """ADR-055 D1: CompareRequest/InputSpec's depth/sources/build_info/compile/
    frontend_context/dump_manifest/public_header_dirs fields, wired into
    run_compare_request via service_compare_evidence.py."""

    def _make_snap_file(self, tmp_path, name, version):
        snap = AbiSnapshot(library=name, version=version)
        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def _spy_resolve_input(self, monkeypatch):
        from abicheck import service as service_mod

        calls: list[dict] = []
        original_resolve = service_mod.resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "headers": headers, **kwargs})
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _spy)
        return calls

    def test_depth_binary_clears_headers(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h]),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["headers"] == []

    def test_public_header_dirs_unioned_into_resolve_input(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        extra_dir = tmp_path / "extra_public"
        extra_dir.mkdir()
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, public_header_dirs=[extra_dir]),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert extra_dir in calls_by_path[old_p]["public_header_dirs"]

    def test_dump_manifest_forwarded_to_resolve_input(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        sentinel = object()

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=sentinel),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["dump_manifest"] is sentinel

    def test_depth_binary_also_clears_dump_manifest(self, tmp_path, monkeypatch):
        """Codex review (P2): depth="binary" clears headers but must also
        clear dump_manifest -- otherwise the manifest still drives its own
        multi-TU L2 header extraction despite the caller explicitly
        requesting binary-only evidence."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        sentinel = object()

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=sentinel),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["dump_manifest"] is None

    def test_per_side_compile_override_wins_over_pair_compile(
        self, tmp_path, monkeypatch
    ):
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        side_compile = CompileContext(sysroot=Path("/custom-sysroot"))

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=side_compile),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"] is side_compile

    def test_frontend_context_default_fills_missing_side_compile(
        self, tmp_path, monkeypatch
    ):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            frontend_context="device",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"
        assert calls_by_path[new_p]["compile"].frontend_context == "device"

    def test_frontend_context_case_is_normalized(self, tmp_path, monkeypatch):
        """Codex review (P2): validate() accepts frontend_context case-
        insensitively, but every real consumer compares against the
        lowercase "host"/"device" literals -- an accepted "DEVICE" must
        still normalize to "device", not silently behave as neither."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            frontend_context="DEVICE",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"

    def test_unrelated_side_override_still_picks_up_device_default(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2, second round): CompileContext.frontend_context
        has no "unset" representation, so an unrelated per-side override
        (e.g. only a sysroot) that never touches frontend_context must still
        pick up the request-level frontend_context default -- it must not be
        silently mistaken for an explicit "host" pin just because "host" is
        also the field's own default value."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(sysroot=Path("/sysroot"))),
            new=InputSpec.of(new_p),
            frontend_context="device",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].sysroot == Path("/sysroot")
        assert calls_by_path[old_p]["compile"].frontend_context == "device"
        assert calls_by_path[new_p]["compile"].frontend_context == "device"

    def test_sources_triggers_embed_build_source(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []

        def _fake_embed(snap, **kwargs):
            embed_calls.append(kwargs)

        monkeypatch.setattr("abicheck.buildsource.embed.embed_build_source", _fake_embed)
        # The real diffing/pack-loading (prepare_embedded_build_source) is
        # exercised by the CLI-path tests already; here we're only asserting
        # that run_compare_request wires sources/collect_mode into
        # embed_build_source, so stub the diff step to a no-op.
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert len(embed_calls) == 1
        assert embed_calls[0]["sources"] == src_dir
        assert embed_calls[0]["collect_mode"] != "off"

    def test_no_evidence_fields_skips_embed_build_source(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )

        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        run_compare_request(request)
        assert embed_calls == []

    def test_embedded_evidence_is_diffed_into_extra_changes(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): embed_build_source alone only writes
        snap.build_source -- the checker never reads it directly, so it must
        also be diffed (prepare_embedded_build_source) and forwarded to
        compare_snapshots as extra_changes, or a source-only ABI change would
        silently produce an ordinary artifact-only compatible verdict."""
        from abicheck import service as service_mod
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        sentinel_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="source_only_fn",
            description="source-only ABI break injected by test",
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: ([sentinel_change], [], {}, [sentinel_change]),
        )

        captured = {}
        original_compare_snapshots = service_mod.compare_snapshots

        def _spy_compare_snapshots(old, new, *a, **kw):
            captured["extra_changes"] = kw.get("extra_changes")
            return original_compare_snapshots(old, new, *a, **kw)

        monkeypatch.setattr(service_mod, "compare_snapshots", _spy_compare_snapshots)

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert captured["extra_changes"] == [sentinel_change]
        assert sentinel_change in result.changes

    def test_embedded_evidence_not_reloaded_as_out_of_band_pack(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): request.old/new.sources/build_info were already
        collected inline into old/new.build_source by embed_build_source, so
        prepare_embedded_build_source's own *_build_info/*_sources params
        (its out-of-band pack-directory override, distinct from the already-
        embedded facts) must be None here -- passing the same raw path again
        would make _resolve_side_pack try to reload it as an evidence pack
        (expecting a manifest.json) and raise ClickException."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        captured_args = {}

        def _fake_prepare(old, new, collect_mode, extra_changes, *rest, **kwargs):
            captured_args["rest"] = rest
            return extra_changes, [], {}, []

        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source", _fake_prepare
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert captured_args["rest"] == (None, None, None, None)

    def test_extractor_matches_effective_frontend(self, tmp_path, monkeypatch):
        """Codex review (P2): embed_build_source's extractor must match the
        same eff_backend resolve_input/run_dump use internally (an explicit
        compile.frontend wins over the bare header_backend), not silently
        default to "auto" while L2 used a different frontend."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="castxml")
            ),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "castxml"

    def test_default_auto_frontend_resolves_to_castxml_for_extractor(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2, second round): the default frontend="auto" must
        not be forwarded to embed_build_source's extractor as the literal
        string "auto" -- _make_source_extractor doesn't special-case "auto"
        and falls back to Clang, while L2's own "auto" resolves to castxml by
        default (dumper._resolve_header_backend), so the common default-
        frontend case would otherwise silently run L2/L4 through different
        tools."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "castxml"

    def test_uppercase_auto_compile_frontend_is_not_treated_as_explicit(
        self, monkeypatch
    ):
        """Codex review, fresh evidence: `compile.frontend="AUTO"` is an
        accepted spelling (validated case-insensitively,
        `api_types.frontend_value_errors`), but `effective_frontend`'s own
        "is this an explicit override" check used to be case-sensitive
        (`compile.frontend != "auto"`) -- so "AUTO" read as an explicit
        override instead of the no-op it means, and its own `_resolve_header_
        backend` re-resolution then re-read `ABICHECK_AST_FRONTEND` live
        instead of honoring the already-resolved `header_backend` argument
        (the exact mechanism `service_dump_pipeline.ResolvedDumpRequest.
        effective_header_backend`'s pin relies on not happening)."""
        from abicheck.compile_context import CompileContext
        from abicheck.service_compare_evidence import effective_frontend

        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        assert (
            effective_frontend(CompileContext(frontend="AUTO"), "clang") == "clang"
        )
        assert (
            effective_frontend(CompileContext(frontend="Auto"), "clang") == "clang"
        )
        # An explicit non-auto override still wins, case as given.
        assert (
            effective_frontend(CompileContext(frontend="castxml"), "clang")
            == "castxml"
        )

    def test_public_headers_forwarded_to_embed_build_source(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): omitting the side's public-header roots from
        embed_build_source leaves source replay's own public-header set
        empty, so the L4 extractor can classify every declaration as non-API
        and emit an empty surface, silently hiding source-only breaks."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        header = tmp_path / "api.h"
        header.write_text("")
        header_dir = tmp_path / "include"
        header_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p,
                sources=src_dir,
                headers=[header],
                public_header_dirs=[header_dir],
            ),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert str(header) in embed_calls[0]["public_headers"]
        assert str(header_dir) in embed_calls[0]["public_header_dirs"]

    def test_dump_manifest_public_roots_forwarded_to_embed_build_source(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1, second round): a dump_manifest replaces
        `headers` entirely, so public_headers/public_header_dirs (both
        derived from `headers`) stay empty for a manifest-driven request --
        the manifest's own roots (public_header_paths/public_header_dirs)
        must still reach source replay, or it can classify the manifest's
        API declarations as non-public and omit source-only breaks."""
        from types import SimpleNamespace

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        manifest = SimpleNamespace(
            roots=[],
            public_header_paths=["/proj/api.h"],
            public_header_dirs=["/proj/include"],
            translation_units=[],
        )

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir, dump_manifest=manifest),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert "/proj/api.h" in embed_calls[0]["public_header_dirs"]
        assert "/proj/include" in embed_calls[0]["public_header_dirs"]

    def test_dump_manifest_project_owned_includes_not_forwarded_to_replay(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1, third round): a TU's project_owned include
        directories are private sibling/support roots used only to keep
        resolve_dependency_scope from misclassifying them as a toolchain
        dependency -- not declared public API surface. Forwarding them into
        L4 source replay's own public-header set would make the extractors
        treat every declaration under a private support dir as API-relevant,
        false-flagging private-header churn as a source break."""
        from types import SimpleNamespace

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        private_inc = SimpleNamespace(path="/proj/private_support", project_owned=True)
        tu = SimpleNamespace(includes=[private_inc], forced_includes=())
        manifest = SimpleNamespace(
            roots=[],
            public_header_paths=[],
            public_header_dirs=[],
            translation_units=[tu],
        )

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir, dump_manifest=manifest),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert "/proj/private_support" not in embed_calls[0]["public_header_dirs"]

    def test_hybrid_frontend_rejected_for_explicit_source_depth(self, tmp_path):
        """Codex review (P1): mirror cli.py's own --depth source +
        --ast-frontend hybrid UsageError -- L4 source-ABI replay has no
        dual-backend hybrid extractor, so this combination must be rejected
        rather than silently reach an artifact-only verdict."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="hybrid")
            ),
            new=InputSpec.of(new_p),
            depth="source",
        )
        with pytest.raises(ValidationError, match="hybrid"):
            run_compare_request(request)

    def test_android_frontend_rejected_for_raw_source_tree(self, tmp_path):
        """Codex review (P2): frontend="android" combined with a genuine raw
        source tree must be rejected -- embed_build_source's inline
        collection pipeline has no real Android extractor and would
        otherwise silently substitute Clang."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
            frontend="android",
            has_sources=True,
        )
        with pytest.raises(ValidationError, match="android"):
            run_compare_request(request)

    def test_android_frontend_allowed_for_prebuilt_evidence_pack(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2, second round): a prebuilt BuildSourcePack/inputs
        pack is loaded as pre-captured facts by embed_build_source -- no
        extractor ever runs for it, so frontend="android" combined with a
        genuine pack directory must be allowed, not rejected."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        # Real manifest, not a patched predicate: is_pack_dir only reads it.
        (pack_dir / "manifest.json").write_text('{"build_source_pack_version": 1}')

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=pack_dir),
            new=InputSpec.of(new_p),
            frontend="android",
            has_sources=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_android_frontend_allowed_with_build_info_only(self, tmp_path, monkeypatch):
        """build_info alone never drives L4 extraction (only L3 compile-DB
        resolution), so it plays no part in the android/hybrid extractor
        rejection -- must be allowed even as a raw (non-pack) directory."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        build_dir = tmp_path / "build"
        build_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, build_info=build_dir),
            new=InputSpec.of(new_p),
            frontend="android",
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_hybrid_frontend_allowed_without_explicit_source_depth(
        self, tmp_path, monkeypatch
    ):
        """The CLI's own implicit-default case (no explicit --depth) is
        allowed to honestly degrade -- only an *explicit* depth="source"
        request is rejected for hybrid."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="hybrid")
            ),
            new=InputSpec.of(new_p),
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_manifest_forced_includes_feed_pair_wide_cxx20_detection(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): a dump_manifest replaces `headers` (empty
        tuple), so its own translation_units[].forced_includes must feed the
        same pair-wide C++20 heuristic or a manifest-only side's real C++20
        signal goes undetected, letting the pair disagree on dialect."""
        from types import SimpleNamespace

        from abicheck import service_scan

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        forced = tmp_path / "concepts.h"
        tu = SimpleNamespace(forced_includes=(forced,))
        manifest = SimpleNamespace(translation_units=[tu])

        captured = {}

        def _fake_override(lang, old_headers, new_headers, *a, **k):
            captured["old_headers"] = list(old_headers)
            return None

        # ADR-055 D1: the pair-wide scan runs in the shared
        # `service_compare_pipeline`, which imports this helper from the module
        # that defines it rather than through `service`'s re-export -- so the
        # spy belongs on `service_scan`, where it lives.
        monkeypatch.setattr(
            service_scan, "pair_wide_cxx20_std_override", _fake_override
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=manifest), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert forced in captured["old_headers"]

    def test_binary_depth_clears_public_headers_for_scope_fingerprint(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): depth="binary" clears the actual header parse
        but a headerless dump still fingerprints public_headers/
        public_header_dirs for scope_fingerprint -- old/new sides with
        differing header lists must not spuriously ScopeMismatchError
        despite the request explicitly selecting binary-only evidence."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h]),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["public_headers"] == []
        assert calls_by_path[old_p]["public_header_dirs"] == []

    def test_request_frontend_case_is_normalized_for_extractor(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): request.frontend="CASTXML" validates case-
        insensitively, but the extractor forwarded to source replay must be
        lowercase -- _make_source_extractor only recognizes lowercase
        frontend names and silently falls back to Clang otherwise, making L2
        and L4 use different frontends."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
            frontend="CASTXML",
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "castxml"

    def test_depth_is_case_insensitive_end_to_end(self, tmp_path, monkeypatch):
        """Codex review (P2): CompareRequest.validate() accepts a case-
        insensitive depth (e.g. "BUILD"), so the internal EvidenceDepth
        construction must not raise ValueError for it. The depth-satisfaction
        gate (a later Codex review) is mocked to "satisfied" here so this
        test stays scoped to its own original concern (case handling), not
        the separate depth-reached check covered by
        TestCompareRequestDepthSatisfaction below."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label", lambda *a, **k: "build"
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="BUILD"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_pair_wide_dialect_merges_into_unrelated_side_compile(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): a side_compile override unrelated to the C++
        dialect (e.g. only a sysroot) must not silently discard the pair-wide
        C++20 heuristic's override for that side -- it should be merged in
        unless the side already pins its own explicit standard."""
        from abicheck import service_scan
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        # ADR-055 D1: spied on `service_scan` (where it is defined) rather than
        # `service` (which only re-exports it) -- the shared compare pipeline
        # imports it from the defining module.
        monkeypatch.setattr(
            service_scan,
            "pair_wide_cxx20_std_override",
            lambda *a, **k: ("-std=gnu++20",),
        )
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(sysroot=Path("/sysroot"))),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        old_compile = calls_by_path[old_p]["compile"]
        assert old_compile.sysroot == Path("/sysroot")
        assert "-std=gnu++20" in old_compile.gcc_option_tokens

    def test_headers_alongside_dump_manifest_rejected(self, tmp_path):
        """Codex review: dump_manifest replaces `headers` for the primary
        AST -- dumper_manifest.resolve_header_ast_result ignores `headers`
        entirely once a manifest is given -- but this method still forwarded
        a non-empty `headers` into provenance tagging and dialect detection
        alongside it, mixing two declared surfaces. Mirrors the CLI's own
        --dump-manifest/-H UsageError (cli_compare_helpers.py)."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        old_h.write_text("void f();\n")
        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h], dump_manifest=dm),
            new=InputSpec.of(new_p),
        )
        with pytest.raises(ValidationError, match="mutually exclusive"):
            run_compare_request(request)

    def test_dump_manifest_alone_is_not_rejected(self, tmp_path, monkeypatch):
        """A dump_manifest with no ordinary headers on that side must not be
        caught by the new mutual-exclusivity guard above.

        CodeRabbit review: the previous try/except let this test pass while
        asserting nothing if run_compare_request raised no exception at all
        -- capture any ValidationError message unconditionally instead, so
        the guard-specific claim is always actually checked."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )
        self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=dm),
            new=InputSpec.of(new_p),
        )
        # No ValidationError raised for the mutual-exclusivity guard itself;
        # it may still fail later trying to actually resolve the manifest's
        # (nonexistent) header, which is unrelated to this guard.
        messages: list[str] = []
        try:
            run_compare_request(request)
        except ValidationError as exc:
            messages.append(str(exc))
        assert all("mutually exclusive" not in m for m in messages)

    def test_per_side_frontend_context_case_is_normalized(self, tmp_path, monkeypatch):
        """Codex review: request.frontend_context is normalized to lowercase,
        but a per-side InputSpec.compile.frontend_context passed straight
        through unchanged -- an accepted case-insensitive spelling like
        "DEVICE" then compared unequal to the lowercase literal every real
        consumer (e.g. sycl_context.py) checks against."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(frontend_context="DEVICE")),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"

    def test_per_side_invalid_frontend_context_rejected(self, tmp_path):
        """A per-side compile.frontend_context bypassed api_types.py's enum
        check entirely (only the request-level default was validated) --
        validate() must now catch it too."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(frontend_context="bogus")),
            new=InputSpec.of(new_p),
        )
        errors = request.validation_errors()
        assert any("bogus" in e and "old" in e for e in errors)
        with pytest.raises(ValidationError, match="bogus"):
            run_compare_request(request)

    def test_malformed_evidence_pack_raises_snapshot_error_not_click_exception(
        self, tmp_path
    ):
        """Codex review: a malformed evidence pack (InputSpec.sources/build_info)
        raised click.ClickException deep inside embed_build_source's
        _load_pack_or_raise -- a CLI-framework exception this Tier-2 API's
        documented ValidationError/SnapshotError contract has no place for."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        bad_pack = tmp_path / "bad_pack"
        bad_pack.mkdir()
        # is_pack_dir() treats an unparseable manifest.json as a (corrupt) pack
        # so downstream loading raises loudly instead of silently collecting.
        (bad_pack / "manifest.json").write_text("{not valid json")

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=bad_pack), new=InputSpec.of(new_p)
        )
        with pytest.raises(SnapshotError, match="Invalid evidence pack"):
            run_compare_request(request)

    def test_embedded_evidence_diffing_is_quiet(self, tmp_path, monkeypatch, capsys):
        """Codex review: prepare_embedded_build_source's coverage-table echoes
        and attach_evidence_metrics' timing-summary echo previously printed
        CLI tables to stderr unconditionally -- polluting a non-CLI caller's
        stream with output it has no way to suppress. run_compare_request must
        pass quiet=True through both."""
        from abicheck import cli_buildsource_helpers

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        def fake_diff_embedded_build_source(*args, **kwargs):
            assert kwargs.get("quiet") is True
            return [], [], {"extractor.duration_seconds": 0.01}

        monkeypatch.setattr(
            cli_buildsource_helpers,
            "diff_embedded_build_source",
            fake_diff_embedded_build_source,
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, build_info=tmp_path), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""


class TestCompareRequestDepthSatisfaction:
    """Codex review, P1: an explicitly requested `depth` (e.g. "source")
    that a raw input actually failed to reach (no usable compile database/
    extractor/linkable declarations) previously diffed whatever weaker
    evidence embed_build_source produced with no signal that the requested
    depth wasn't met -- mirrors dump's own check_requested_depth_satisfied
    hard-fail, monkeypatching `_gated_source_label` (the shared "what depth
    did this snapshot actually reach" recompute) to simulate reached vs.
    not-reached without needing a real compile database/source tree."""

    def _make_snap_file(self, tmp_path, name, version):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import save_snapshot

        path = tmp_path / f"{name}_{version}.json"
        save_snapshot(AbiSnapshot(library=name, version=version), path)
        return path

    def test_depth_not_reached_rejected(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "build",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        with pytest.raises(ValidationError, match="only reached 'build'"):
            run_compare_request(request)

    def test_depth_reached_passes(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "source",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_reports_the_failing_side(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        def _by_version(build_source, snap):
            # The old side's snapshot loads with version "1.0" -- distinguish
            # by that so only the new side fails the gate.
            return "source" if snap.version == "1.0" else "binary"

        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label", _by_version
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        with pytest.raises(ValidationError, match="new side"):
            run_compare_request(request)

    def test_depth_binary_always_satisfied(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="binary"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_no_depth_skips_the_gate(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)


class TestDiagnosticComparisonThreading:
    """ADR-050 D2's escape hatch, threaded through every service.py
    chokepoint (rollout-risk follow-up, PR #624): checker.compare()'s
    comparability gate was already live from Tier-1, but unreachable from
    every real front-end (CLI/MCP/scan/appcompat all route through
    service.py, never checker.compare directly -- ADR-037 D1/D10.1), since
    none of compare_snapshots/run_compare_request/run_compare forwarded
    diagnostic_comparison. These tests build a genuinely scope-mismatched
    contract pair (by hand, since dumper.py wiring is separate work) and
    confirm the escape hatch reaches checker.compare from each entry
    point."""

    def _mismatched_pair(self, tmp_path):
        # 2-header declared set: a single header's own name is no longer
        # load-bearing scope identity (Codex review, PR #624 follow-up —
        # the CI-red incident once the gate went live on real dumps at
        # scale), so this needs a genuine multi-header declared-surface
        # difference to still trigger the gate.
        a_old = tmp_path / "old" / "a.h"
        a_new = tmp_path / "new" / "a.h"
        old_h = tmp_path / "old" / "foo.h"
        new_h = tmp_path / "new" / "bar.h"
        old_h.parent.mkdir(parents=True)
        new_h.parent.mkdir(parents=True)
        a_old.write_text("int g(void);\n")
        a_new.write_text("int g(void);\n")
        old_h.write_text("int f(void);\n")
        new_h.write_text("int f(void);\n")

        def _snap(version, headers):
            return AbiSnapshot(
                library="libtest.so",
                version=version,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
                contract=compute_extraction_contract(declared_headers=headers),
            )

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(_snap("1.0", [a_old, old_h]), old_p)
        save_snapshot(_snap("2.0", [a_new, new_h]), new_p)
        return old_p, new_p

    def test_compare_snapshots_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        with pytest.raises(ScopeMismatchError):
            compare_snapshots(load_snapshot(old_p), load_snapshot(new_p))

    def test_compare_snapshots_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        result = compare_snapshots(
            load_snapshot(old_p), load_snapshot(new_p), diagnostic_comparison=True
        )
        assert result.assurance == "none"

    def test_run_compare_request_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        request = CompareRequest(old=InputSpec(path=old_p), new=InputSpec(path=new_p))
        with pytest.raises(ScopeMismatchError):
            run_compare_request(request)

    def test_run_compare_request_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        request = CompareRequest(
            old=InputSpec(path=old_p),
            new=InputSpec(path=new_p),
            diagnostic_comparison=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert result.assurance == "none"

    def test_run_compare_shim_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        with pytest.raises(ScopeMismatchError):
            run_compare(old_p, new_p)

    def test_run_compare_shim_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        result, _, _ = run_compare(old_p, new_p, diagnostic_comparison=True).as_tuple()
        assert result.assurance == "none"

    def test_diagnostic_comparison_appended_last_preserves_positional_order(self):
        # Same rule as debuginfod_url (Codex review, PR #551): appended after
        # every pre-existing parameter so a positional caller's existing
        # bindings don't shift. contract_evaluation (ADR-049 Phase 3) was
        # later appended after it by the same rule, so "last" itself moved
        # on again -- see
        # TestContractEvaluationThreading.test_contract_evaluation_appended_last_preserves_positional_order
        # for that -- but diagnostic_comparison's own position relative to
        # its pre-existing neighbor must still be unchanged.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        assert params.index("diagnostic_comparison") == (
            params.index("debuginfod_url") + 1
        )


class TestContractEvaluationThreading:
    """ADR-049 Phase 3's shadow contract evaluator (contract_evaluation.py)
    was wired into checker.compare() (stamping Change.contract_relevance/
    contract_reason_code/contract_assurance) but unreachable from every real
    front-end: none of compare_snapshots/run_compare_request/run_compare
    forwarded a contract_evaluation flag, and no front-end may call
    checker.compare directly (cli-contract AI-readiness gate, ADR-037
    D10.1). These tests build a genuine function-signature change and
    confirm the flag reaches checker.compare from each Tier-2 entry point
    and actually stamps the finding, mirroring
    TestDiagnosticComparisonThreading's pattern for the sibling
    diagnostic_comparison escape hatch."""

    def _changed_pair(self, tmp_path):
        def _snap(version, return_type):
            return AbiSnapshot(
                library="libtest.so",
                version=version,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type=return_type,
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
            )

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(_snap("1.0", "int"), old_p)
        save_snapshot(_snap("2.0", "long"), new_p)
        return old_p, new_p

    def test_compare_snapshots_default_leaves_contract_relevance_unset(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result = compare_snapshots(load_snapshot(old_p), load_snapshot(new_p))
        assert result.changes
        assert all(c.contract_relevance is None for c in result.changes)

    def test_compare_snapshots_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result = compare_snapshots(
            load_snapshot(old_p), load_snapshot(new_p), contract_evaluation=True
        )
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_run_compare_request_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        request = CompareRequest(
            old=InputSpec(path=old_p),
            new=InputSpec(path=new_p),
            contract_evaluation=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_run_compare_shim_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result, _, _ = run_compare(old_p, new_p, contract_evaluation=True).as_tuple()
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_contract_evaluation_appended_last_preserves_positional_order(self):
        # Same rule as debuginfod_url/diagnostic_comparison (Codex review,
        # PR #551 and ADR-050 D2 follow-up): appended after every
        # pre-existing parameter so a positional caller's existing bindings
        # don't shift. include_dependencies (dependency-scope default-
        # filtering parity fix) was appended after this one in turn.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        # pack_policy_overrides/pack_internal_namespaces (CLI cleanup phase
        # two, "PR B" slice 1) were appended after contract_mode in turn,
        # following the same rule; compile_context (both-sides L2 compile
        # context for the directory/package release fan-out) was appended
        # after those in turn.
        assert params[-1] == "compile_context"
        assert params[-2] == "pack_internal_namespaces"
        assert params[-3] == "pack_policy_overrides"
        assert params[-4] == "contract_mode"
        assert params[-5] == "include_dependencies"
        assert params[-6] == "contract_evaluation"
        assert params[-7] == "diagnostic_comparison"

    def test_get_type_hints_resolves_without_nameerror(self):
        """Codex review: `run_compare` moved from `service.py` into
        `service_compare_pipeline.py` in this same PR, and `Path` was only
        imported there under `TYPE_CHECKING` -- with `from __future__ import
        annotations` (PEP 563), `typing.get_type_hints(run_compare)` (a
        schema generator or docs tool introspecting this public function)
        raised `NameError: name 'Path' is not defined`. Fixed by importing
        `pathlib.Path` unconditionally in `service_compare_pipeline.py`."""
        import typing

        hints = typing.get_type_hints(run_compare)
        assert hints["old_input"] is Path
        assert hints["new_input"] is Path


class TestParallelOldNewExtraction:
    """run_compare_request resolves old/new concurrently (two threads) since
    neither side depends on the other until compare_snapshots(). These tests
    guard the concurrency itself, exception propagation, and the
    ABICHECK_PARALLEL_EXTRACTION=0 escape hatch."""

    def _snap_files(self, tmp_path):
        from abicheck.serialization import save_snapshot

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="l", version="1.0"), old_p)
        save_snapshot(AbiSnapshot(library="l", version="2.0"), new_p)
        return old_p, new_p

    def test_both_sides_extracted_concurrently(self, tmp_path, monkeypatch):
        import threading

        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input
        # A 2-party barrier: this call only returns once BOTH old and new
        # resolution have reached it. If run_compare_request serialized them
        # (regressing to the old sequential behavior) this would deadlock —
        # the second call can never start until the first returns, but the
        # first is stuck waiting on the barrier — surfacing as a
        # BrokenBarrierError after the timeout rather than a flaky race.
        barrier = threading.Barrier(2, timeout=5)

        def _synced_resolve(path, headers, includes, version, lang, **kwargs):
            barrier.wait()
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _synced_resolve)

        result, old, new = run_compare(old_p, new_p).as_tuple()
        assert isinstance(result, DiffResult)

    def test_exception_from_one_side_propagates(self, tmp_path, monkeypatch):
        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input

        def _boom(path, headers, includes, version, lang, **kwargs):
            if Path(path) == Path(old_p):
                raise SnapshotError("boom - old side failed")
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _boom)

        with pytest.raises(SnapshotError, match="boom - old side failed"):
            run_compare(old_p, new_p)

    def test_env_var_disables_parallel_extraction(self, tmp_path, monkeypatch):
        import threading

        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input
        threads_seen: set[int] = set()

        def _spy(path, headers, includes, version, lang, **kwargs):
            threads_seen.add(threading.get_ident())
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _spy)
        monkeypatch.setenv("ABICHECK_PARALLEL_EXTRACTION", "0")

        run_compare(old_p, new_p)

        # Both calls ran on the current (main) thread — no pool was used.
        assert threads_seen == {threading.get_ident()}


# ── render_output() ─────────────────────────────────────────────────────────


class TestRenderOutput:
    @pytest.fixture
    def snap(self):
        return AbiSnapshot(
            library="libtest",
            version="1.0",
            functions=[Function(name="foo", mangled="foo", return_type="int")],
        )

    @pytest.fixture
    def diff_result(self):
        return DiffResult(old_version="1.0", new_version="2.0", library="libtest")

    def test_json_format(self, diff_result, snap):
        out = render_output("json", diff_result, snap)
        d = json.loads(out)
        assert "library" in d or "verdict" in d or isinstance(d, dict)

    def test_markdown_format(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap)
        assert isinstance(out, str)

    def test_md_format(self, diff_result, snap):
        out = render_output("md", diff_result, snap)
        assert isinstance(out, str)

    def test_sarif_format(self, diff_result, snap):
        out = render_output("sarif", diff_result, snap)
        d = json.loads(out)
        assert "$schema" in d or "runs" in d

    def test_html_format(self, diff_result, snap):
        out = render_output("html", diff_result, snap)
        assert (
            "<html" in out.lower()
            or "<!doctype" in out.lower()
            or "<div" in out.lower()
        )

    def test_unsupported_format_raises(self, diff_result, snap):
        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("xml", diff_result, snap)

    def test_oneline_format(self, diff_result, snap):
        # CLI cleanup phase two, PR 1: --stat's boolean parameter is gone --
        # the one-line summary is now its own fmt value
        # (service_render.ONELINE_FORMAT), reached only via the built-in
        # `quick` --profile at the CLI layer, but directly callable here as
        # a plain fmt string like any other format.
        from abicheck.service_render import ONELINE_FORMAT

        out = render_output(ONELINE_FORMAT, diff_result, snap)
        assert isinstance(out, str)
        assert "\n" not in out.strip()

    def test_stat_kwarg_is_a_compatibility_shim_for_oneline(self, diff_result, snap):
        """CodeRabbit review: `render_output` is exported via
        `abicheck.service.__all__` (Tier-2 typed API), so an existing
        caller spelling the pre-PR-1 `render_output(..., stat=True)` must
        not get a bare `TypeError` -- only the CLI's own `--stat` flag was
        announced as removed, not this function's signature. For a
        non-``json`` *fmt*, `stat=True` is equivalent to
        `fmt=ONELINE_FORMAT` (the human one-line renderer)."""
        from abicheck.service_render import ONELINE_FORMAT

        assert render_output(
            "markdown", diff_result, snap, stat=True
        ) == render_output(ONELINE_FORMAT, diff_result, snap)

    def test_stat_kwarg_with_json_fmt_preserves_the_old_stat_json_shape(
        self, diff_result, snap
    ):
        """Codex review, fresh evidence: an earlier revision of this shim
        collapsed `render_output("json", ..., stat=True)` onto the human
        one-line renderer too, silently breaking a Tier-2 caller that fed
        the pre-PR-1 `--stat --format json` shape to `json.loads()`. The
        JSON case must keep returning `to_stat_json`'s summary-only JSON
        object (no `changes` array), not human text."""
        from abicheck.reporter import to_stat_json

        out = render_output("json", diff_result, snap, stat=True)
        assert json.loads(out) == json.loads(to_stat_json(diff_result))
        d = json.loads(out)
        assert "changes" not in d
        assert d["verdict"] == diff_result.verdict.value

    def test_stat_kwarg_with_junit_fmt_is_never_short_circuited(
        self, diff_result, snap
    ):
        """Codex review, fresh evidence: the pre-PR-1 `--stat` boolean's own
        guard was `if stat and fmt != "junit": ...` -- JUnit was *never*
        replaced by the one-line summary, since an XML consumer needs the
        real `<testsuite>` document regardless of `--stat`. A revision of
        this shim that routed every non-JSON `fmt` (JUnit included) to the
        human one-line renderer silently broke that XML consumer."""
        assert render_output(
            "junit", diff_result, snap, stat=True
        ) == render_output("junit", diff_result, snap, stat=False)

    def test_show_recommendation_false_still_suppresses_the_section(
        self, diff_result, snap
    ):
        """Codex review, fresh evidence: `show_recommendation` is a real,
        effective toggle, not an inert compatibility shim -- an earlier
        revision hard-coded `True` into the `to_markdown` call regardless
        of what the caller passed, silently reintroducing the
        recommendation section for a direct Tier-2 caller that explicitly
        asked it be suppressed (only the CLI's own `--recommend` flag was
        announced removed, not this keyword's effect)."""
        with_rec = render_output(
            "markdown", diff_result, snap, show_recommendation=True
        )
        without_rec = render_output(
            "markdown", diff_result, snap, show_recommendation=False
        )
        assert with_rec != without_rec

    def test_show_recommendation_default_matches_pre_removal_api(
        self, diff_result, snap
    ):
        """Codex review, fresh evidence, second round: the default must stay
        `False` -- the exact pre-removal Tier-2 Python API default -- not
        `True`. An earlier revision changed the default to match the CLI's
        own unconditional-inclusion behaviour, which silently changed what
        an existing direct caller gets when it omits this keyword entirely
        (a public-API default change this PR's docs never announced). The
        CLI achieves its own unconditional inclusion by having its wrapper
        (`cli._render_output`) pass `show_recommendation=True` explicitly,
        not by changing this function's default -- see
        `test_cli_recommendation_is_unconditional_despite_the_false_default`
        below for that half of the contract."""
        assert render_output(
            "markdown", diff_result, snap
        ) == render_output("markdown", diff_result, snap, show_recommendation=False)

    def test_cli_recommendation_is_unconditional_despite_the_false_default(
        self, diff_result, snap
    ):
        """The CLI's own `cli._render_output` wrapper explicitly passes
        `show_recommendation=True` to `render_output` (it never relies on
        the library default), so its own markdown output stays
        unconditional even though `render_output`'s own default flipped
        back to `False` in this round's fix."""
        from abicheck.cli import _render_output

        cli_markdown = _render_output("markdown", diff_result, snap)
        default_markdown = render_output("markdown", diff_result, snap)
        explicit_true_markdown = render_output(
            "markdown", diff_result, snap, show_recommendation=True
        )
        assert cli_markdown == explicit_true_markdown
        assert cli_markdown != default_markdown

    def test_stat_json_forwards_require_complete_analysis(self, diff_result, snap):
        """Codex/CodeRabbit review: `render_output`'s own `stat`
        short-circuit (before format dispatch) bypassed
        `require_complete_analysis` entirely, independent of the identical
        `to_json`-level gap already covered in test_reporter.py -- this is
        the service-level entry point `compare --stat --format json
        --require-complete-analysis` actually goes through."""
        from abicheck.analysis_assurance import AnalysisAssurance

        diff_result.analysis_assurance = AnalysisAssurance(status="partial")
        out = render_output(
            "json", diff_result, snap, stat=True, require_complete_analysis=True
        )
        d = json.loads(out)
        assert d["analysis_assurance_exit_contribution"] == 1

    def test_json_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("json", diff_result, snap, follow_deps=True)
        d = json.loads(out)
        assert "old_dependency_info" in d

    def test_markdown_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("markdown", diff_result, snap, follow_deps=True)
        assert "Dependency" in out

    def test_html_with_new_snap(self, snap):
        new_snap = AbiSnapshot(library="libtest", version="2.0")
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("html", diff_result, snap, new=new_snap)
        assert isinstance(out, str)


# ── _render_deps_section_md() ──────────────────────────────────────────────


class TestRenderDepsSection:
    def test_basic_deps(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0, "resolution_reason": "system"}],
            bindings_summary={"GLOBAL": 5},
            unresolved=[{"soname": "libmissing.so", "consumer": "lib.so"}],
            missing_symbols=[
                {"symbol": "foo", "version": "GLIBC_2.17"},
                {"symbol": "bar"},
            ],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result
        assert "GLOBAL" in result
        assert "libmissing.so" in result
        assert "foo" in result
        assert "bar" in result

    def test_no_dep_info(self):
        old = AbiSnapshot(library="lib", version="1.0")
        result = _render_deps_section_md(old, None)
        assert "Dependency" in result
        # Should still have the header

    def test_missing_symbols_truncated(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            missing_symbols=[{"symbol": f"sym{i}"} for i in range(15)],
        )
        result = _render_deps_section_md(old, None)
        assert "+5 more" in result

    def test_non_int_depth(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": "invalid"}],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result

    def test_both_old_and_new(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(nodes=[{"soname": "old.so", "depth": 0}])
        new = AbiSnapshot(library="lib", version="2.0")
        new.dependency_info = DependencyInfo(nodes=[{"soname": "new.so", "depth": 0}])
        result = _render_deps_section_md(old, new)
        assert "old.so" in result
        assert "new.so" in result


# ── Header-scoped PE/Mach-O dumps (issue #235) ───────────────────────────────


def _scoped_snapshot(platform: str, *funcs: tuple[str, Visibility]) -> AbiSnapshot:
    """Build a fake header-scoped snapshot as ``dumper._dump_*`` would return."""
    from abicheck.model import RecordType

    snap = AbiSnapshot(library="lib", version="1.0", platform=platform)
    snap.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=v) for n, v in funcs
    ]
    # A header-scoped dump carries real type info (so layout diffs still fire).
    snap.types = [RecordType(name="PublicStruct", kind="struct")]
    return snap


def _pe_meta(*export_names: str) -> MagicMock:
    meta = MagicMock()
    meta.machine = "AMD64"
    exports = []
    for i, name in enumerate(export_names, start=1):
        exp = MagicMock()
        exp.name = name
        exp.ordinal = i
        exports.append(exp)
    meta.exports = exports
    return meta


def _mk_header(tmp_path: Path, name: str = "api.h") -> Path:
    """Create a real public header file so expand_header_inputs accepts it."""
    h = tmp_path / name
    h.write_text("int PublicApiFunc(void);\n")
    return h


class TestPeHeaderScoping:
    """Issue #235: --header/--include must scope the PE ABI surface."""

    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        # Export table has a public API symbol AND a private/internal export.
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        # Header-scoped dump only sees the symbol declared in the public header.
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump,
        ):
            result = _dump_pe(
                p, "1.0", headers=[_mk_header(tmp_path)], includes=[Path("inc")]
            )

        # The header-aware dumper was actually invoked with the (expanded) headers.
        assert mock_dump.called
        called_headers = mock_dump.call_args.args[1]
        assert called_headers == [tmp_path / "api.h"]
        # Surface is scoped: private export absent, public symbol present.
        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names
        assert "InternalPrivateFunc" not in names
        # Type info preserved so reachable layout changes still diff.
        assert any(t.name == "PublicStruct" for t in result.types)

    def test_private_export_absent_from_headers_not_compared(self, tmp_path):
        """An exported-but-private symbol removed in 'new' must not surface."""
        from abicheck.checker import compare
        from abicheck.service import _dump_pe

        old_p = tmp_path / "old.dll"
        new_p = tmp_path / "new.dll"
        old_p.write_bytes(b"MZ" + b"\x00" * 100)
        new_p.write_bytes(b"MZ" + b"\x00" * 100)

        old_pe = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        new_pe = _pe_meta("PublicApiFunc")  # private export dropped in new
        old_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        new_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
            with (
                patch("abicheck.pe_metadata.parse_pe_metadata", return_value=old_pe),
                patch("abicheck.dumper._dump_pe", return_value=old_scoped),
            ):
                old_snap = _dump_pe(old_p, "1.0", headers=[_mk_header(tmp_path)])
            with (
                patch("abicheck.pe_metadata.parse_pe_metadata", return_value=new_pe),
                patch("abicheck.dumper._dump_pe", return_value=new_scoped),
            ):
                new_snap = _dump_pe(new_p, "2.0", headers=[_mk_header(tmp_path)])

        result = compare(old_snap, new_snap)
        removed = [
            c for c in result.changes if "InternalPrivateFunc" in (c.symbol or "")
        ]
        assert removed == [], f"private export must not be reported: {removed}"

    def test_fallback_when_no_header_match(self, tmp_path):
        """MSVC-mangled C++ exports won't match Itanium names → warn + fallback."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("?realFunc@@YAHXZ")
        # castxml parsed headers but nothing matched the export table.
        scoped = _scoped_snapshot("pe", ("someDecl", Visibility.HIDDEN))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped),
        ):
            with pytest.warns(
                UserWarning, match="None of the provided headers matched"
            ):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        # Fell back to the full export table.
        names = [f.name for f in result.functions]
        assert "?realFunc@@YAHXZ" in names

    def test_fallback_when_castxml_unavailable(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch(
                "abicheck.dumper._dump_pe",
                side_effect=RuntimeError("castxml not found"),
            ),
        ):
            with pytest.warns(
                UserWarning, match="Header-based ABI scoping unavailable"
            ):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names

    def test_no_headers_uses_export_table(self, tmp_path):
        """Without headers, behaviour is unchanged: full export table, PUBLIC."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe") as mock_dump,
        ):
            result = _dump_pe(p, "1.0")

        assert not mock_dump.called  # castxml path never taken
        names = {f.name for f in result.functions}
        assert names == {"PublicApiFunc", "InternalPrivateFunc"}
        assert all(f.visibility == Visibility.PUBLIC for f in result.functions)

    def test_pdb_debug_preserved_on_scoped_snapshot(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        dwarf_meta = MagicMock()
        dwarf_adv = MagicMock()

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.dumper._dump_pe", return_value=scoped),
            patch(
                "abicheck.service_dump_native_pe._extract_pdb_debug",
                return_value=(dwarf_meta, dwarf_adv),
            ),
        ):
            result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        assert result.dwarf is dwarf_meta
        assert result.dwarf_advanced is dwarf_adv

    def test_header_directory_is_expanded(self, tmp_path):
        """`--header <dir>` must expand to files, not feed a dir to castxml."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        (hdr_dir / "a.h").write_text("int PublicApiFunc(void);\n")
        (hdr_dir / "b.hpp").write_text("int Other(void);\n")
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump,
        ):
            _dump_pe(p, "1.0", headers=[hdr_dir])

        # The dumper received the individual header files, not the directory.
        called_headers = mock_dump.call_args.args[1]
        assert hdr_dir not in called_headers
        assert {h.name for h in called_headers} == {"a.h", "b.hpp"}

    def test_bad_header_path_raises_not_silent_fallback(self, tmp_path):
        """A nonexistent header must raise, not silently fall back to exports."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
        ):
            with pytest.raises(ValidationError, match="not found"):
                _dump_pe(p, "1.0", headers=[tmp_path / "missing.h"])


class TestMachoHeaderScoping:
    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("publicFn", Visibility.PUBLIC))

        with (
            patch(
                "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
            ),
            patch("abicheck.dumper._dump_macho", return_value=scoped) as mock_dump,
        ):
            result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert mock_dump.called
        assert [f.name for f in result.functions] == ["publicFn"]

    def test_fallback_when_no_header_match(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("other", Visibility.HIDDEN))

        with (
            patch(
                "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
            ),
            patch("abicheck.dumper._dump_macho", return_value=scoped),
        ):
            with pytest.warns(
                UserWarning, match="None of the provided headers matched"
            ):
                result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert [f.name for f in result.functions] == ["_publicFn"]


class TestRunDumpHeaderWiring:
    """run_dump must forward headers/includes to the PE/Mach-O dumpers."""

    def test_run_dump_pe_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap) as mock_pe:
            run_dump(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_run_dump_macho_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service_dump_native._dump_macho", return_value=snap) as mock_macho:
            run_dump(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]


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
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap):
            result = run_dump(p, "pe", [Path("api.h")], [], "1.0", "c++")
        assert result.build_source is not None

    def test_noop_when_no_headers_parsed(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap):
            result = run_dump(p, "pe", [], [], "1.0", "c++")
        assert result.build_source is None

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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch(
                "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
            ) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        mock_ast.assert_called_once()
        # The resolved (existing, expanded) header must reach the clang pass —
        # not the raw, unexpanded argument (Codex review).
        assert mock_ast.call_args.args[0] == [header]
        assert result.build_source is not None
        assert result.build_source.source_graph is not None
        node_ids = {n.id for n in result.build_source.source_graph.nodes}
        assert "decl://_Z1fv" in node_ids
        # The manifest coverage row must be populated too (Codex review) — an
        # empty default manifest would read as "L5 not collected" to
        # cli_buildsource_helpers._layer_presence/_optional_coverage even
        # though source_graph is populated.
        from abicheck.buildsource.model import CoverageStatus, DataLayer

        l5 = result.build_source.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
        assert l5 is not None
        assert l5.status == CoverageStatus.PARTIAL  # no edges in this empty AST
        l3 = result.build_source.manifest.coverage_for(DataLayer.L3_BUILD)
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
        from abicheck.service_scan import CompileContext

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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch(
                "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", side_effect=_raise) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        mock_ast.assert_called_once()
        # Never aborts the dump (ADR-028 D3); the graph is embedded but inert.
        assert result.build_source is not None
        assert result.build_source.source_graph is not None
        assert result.build_source.source_graph.edges == []

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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch(
                "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
            ) as mock_ast,
        ):
            result = run_dump(p, "pe", [hdr_dir], [], "1.0", "c++")
        mock_ast.assert_called_once()
        assert mock_ast.call_args.args[0] == [header]
        assert result.build_source is not None
        assert result.build_source.source_graph is not None

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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=(ast, None, False)),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph.deadline.run_bounded",
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
        graph = result.build_source.source_graph
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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=(ast, None, False)),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph.deadline.run_bounded",
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
        graph = result.build_source.source_graph
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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=(ast, None, False)),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph.deadline.run_bounded",
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
        graph = result.build_source.source_graph
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
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap):
            result = run_dump(p, "pe", [header], [], "1.0", "c++")
        assert result.build_source is not None


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
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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

        def _fake_dump_elf(*args, **kwargs):
            compile_ctx = kwargs.get("compile")
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        def _fake_attach(_snap, header_graph, header_graph_includes, *_args, **_kwargs):
            calls.append((header_graph, header_graph_includes))
            return _snap

        with (
            patch("abicheck.service_dump_native._dump_elf", side_effect=_fake_dump_elf),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
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
            patch("abicheck.service_dump_native._dump_elf", return_value=snap),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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
        from abicheck import dumper_cache

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", from_headers=False)
        seen_active: dict[str, bool] = {}

        def _fake_dump_elf(*_a, **_k):
            seen_active["active"] = dumper_cache.ast_memoize_active()
            return snap

        with (
            patch("abicheck.service_dump_native._dump_elf", side_effect=_fake_dump_elf),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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
            patch("abicheck.service_dump_native._dump_pe", return_value=snap),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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
            patch("abicheck.service_dump_native._dump_macho", return_value=snap),
            patch("abicheck.service_dump_native._attach_header_graph", side_effect=_fake_attach),
            patch(
                "abicheck.service_dump_native.attach_clang_layout", side_effect=lambda s, *a, **k: s
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
        from abicheck.service import _attach_header_graph
        from abicheck.service_scan import CompileContext

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
        from abicheck.service import _attach_header_graph
        from abicheck.service_scan import CompileContext

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
        `_clang_header_dump` call is a real downstream consumer of the raw
        AST dict (`buildsource.call_graph.parse_clang_ast_calls` walks it
        directly for call-graph edges), so the opt-in streaming pruner must
        be force-disabled for this call regardless of the env var --
        otherwise a pruned dependency function/variable node would degrade
        or drop call-graph edges the graph is built to capture."""
        from abicheck.dumper_clang_streaming import streaming_prune_suppressed
        from abicheck.service import _attach_header_graph

        monkeypatch.setenv("ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS", "1")
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")

        seen_suppressed: dict[str, bool] = {}

        def _stub_clang_header_dump(*a, **k):
            seen_suppressed["suppressed"] = streaming_prune_suppressed()
            return {"kind": "TranslationUnitDecl", "inner": []}, None, False

        monkeypatch.setattr(
            "abicheck.dumper._clang_header_dump", _stub_clang_header_dump
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
        from abicheck.service import _attach_header_graph

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
    its _clang_header_dump call must match whichever main pass it's paired
    with -- case-insensitively, since PE/Mach-O's own main pass
    (service_header_scoped._try_header_scoped_dump) treats "C" the same as
    "c". The compiler string is part of the AST cache key, so a mismatch
    here silently misses the memo on top of picking the wrong driver."""

    def test_uppercase_c_selects_cc_compiler(self, tmp_path: Path):
        from abicheck.service import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
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
        from abicheck.service import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
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
        (_resolve_clang_bin/fallback) and its `language=` argument -- these
        three were fixed alongside the compiler selection above but are
        independent code paths, each capable of drifting back to "C++" on
        its own."""
        from abicheck.service import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            patch("abicheck.dumper._clang_header_dump", return_value=(ast, None, False)),
            patch(
                "abicheck.dumper._resolve_clang_bin", return_value="/opt/llvm/clang"
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
    _clang_header_dump call has its own AST cache key, but its extra_hash_dirs
    computation only covered resolve_inferred_header_roots's own deferred
    roots -- never any include-search directory riding in
    compile.gcc_option_tokens itself (an explicit --gcc-options/
    --compiler-option -I, or -- since the P0.3 L3->L2 fold -- a compile-DB-
    derived one). A directory the primary snapshot pass already hashes into
    its own cache key must be hashed here too, or an edit under it would
    silently reuse a stale cached graph even though the primary snapshot
    re-parsed correctly."""

    def test_gcc_option_tokens_include_dir_is_hashed(self, tmp_path: Path):
        from abicheck.service import _attach_header_graph
        from abicheck.service_scan import CompileContext

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        build_inc = tmp_path / "buildinc"
        build_inc.mkdir()
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
        ) as mock_ast:
            _attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[header],
                includes=[],
                lang="c++",
                compile=CompileContext(
                    gcc_option_tokens=("-I", str(build_inc))
                ),
                public_headers=None,
                public_header_dirs=None,
            )
        assert build_inc in mock_ast.call_args.kwargs["extra_hash_dirs"]

    def test_no_include_tokens_hashes_nothing_extra(self, tmp_path: Path):
        from abicheck.service import _attach_header_graph

        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        snap = AbiSnapshot(library="lib", version="1.0")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with patch(
            "abicheck.dumper._clang_header_dump", return_value=(ast, None, False)
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


class TestCliNativeBinaryHeaderWiring:
    """CLI _dump_native_binary must forward headers to service._dump_pe/_dump_macho."""

    def test_cli_pe_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service_dump_native._dump_pe", return_value=snap) as mock_pe:
            _dump_native_binary(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_cli_macho_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service_dump_native._dump_macho", return_value=snap) as mock_macho:
            _dump_native_binary(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]

    def test_cli_pe_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.service_dump_native._dump_pe", side_effect=SnapshotError("boom")):
            with pytest.raises(click.ClickException, match="boom"):
                _dump_native_binary(p, "pe", [], [], "1.0", "c++")

    def test_cli_macho_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        with patch("abicheck.service_dump_native._dump_macho", side_effect=SnapshotError("nope")):
            with pytest.raises(click.ClickException, match="nope"):
                _dump_native_binary(p, "macho", [], [], "1.0", "c++")


def test_run_scan_runs_deferred_build_dir_cleanup(monkeypatch):
    # Fast-lane guard for the scan orchestrator's ownership of the inferred
    # build-dir cleanup (the real end-to-end check is the integration suite):
    # service_scan.run_scan must run the deferred cleanup thunks in its finally —
    # both on success and when run_scan_core raises — so the temp cmake build dir
    # never outlives the scan. Mirrors the same contract in cli_scan.run_scan.
    from types import SimpleNamespace

    from abicheck import service_scan as _ss

    ran = {"n": 0}

    def fake_core(**kw):
        # The orchestrator hands us the cleanup list; register a sentinel thunk the
        # way collect_inline_pack would for an inferred cmake build dir.
        kw["defer_cleanup"].append(lambda: ran.__setitem__("n", ran["n"] + 1))
        outcome = SimpleNamespace(
            verdict="COMPATIBLE",
            exit_code=0,
            coverage=[],
            crosscheck={},
            to_dict=lambda: {},
        )
        return SimpleNamespace(outcome=outcome, findings=[])

    monkeypatch.setattr(_ss, "estimate_scan", lambda req: [])
    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", fake_core)

    req = _ss.ScanRequest(binaries=[Path("libfoo.so")], depth="binary")
    res = _ss.run_scan(req)
    assert res.verdict == "COMPATIBLE"
    assert ran["n"] == 1  # the finally ran the deferred cleanup on success

    # And it still runs when the core raises a budget overflow mid-scan.
    from abicheck.scan_engine import _BudgetOverflow

    ran["n"] = 0

    def raising_core(**kw):
        kw["defer_cleanup"].append(lambda: ran.__setitem__("n", ran["n"] + 1))
        raise _BudgetOverflow("over budget")

    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", raising_core)
    res = _ss.run_scan(req)
    assert res.exit_code == 5  # budget overflow surfaced
    assert ran["n"] == 1  # finally still ran the cleanup on the raise path


def test_run_scan_rejects_comparison_only_fields_without_baseline():
    # Codex review on PR #657: ScanRequest's policy/suppression/scope/
    # force-public/pattern-verdict/env-matrix fields only mean anything for
    # a baseline comparison (run_scan_core only calls _run_baseline_compare
    # when baseline is set and mode isn't "audit"). Without a baseline they
    # must be rejected loudly (mirrors the CLI's identical scan_cmd guard),
    # not silently accepted and discarded.
    from abicheck.errors import ValidationError
    from abicheck.service_scan import ScanRequest, run_scan

    req = ScanRequest(binaries=[Path("libfoo.so")], depth="binary", policy="sdk_vendor")
    with pytest.raises(ValidationError, match="only take effect with a baseline"):
        run_scan(req)


def test_run_scan_rejects_comparison_only_fields_with_audit_mode_despite_baseline():
    # Even with a baseline set, an explicit mode="audit" means run_scan_core
    # never calls _run_baseline_compare either -- the guard must catch that
    # combination too, not just baseline=None.
    from abicheck.errors import ValidationError
    from abicheck.service_scan import ScanRequest, run_scan

    req = ScanRequest(
        binaries=[Path("libfoo.so")],
        depth="binary",
        baseline=Path("old.abi.json"),
        mode="audit",
        pattern_verdicts=True,
    )
    with pytest.raises(ValidationError, match="only take effect with a baseline"):
        run_scan(req)


def test_run_scan_allows_comparison_fields_with_a_real_baseline(monkeypatch):
    # The new guard must not fire for the case it's meant to allow: a real
    # baseline comparison actually using the config surface.
    from types import SimpleNamespace

    from abicheck import service_scan as _ss

    def fake_core(**kw):
        outcome = SimpleNamespace(
            verdict="COMPATIBLE",
            exit_code=0,
            coverage=[],
            crosscheck={},
            to_dict=lambda: {},
        )
        return SimpleNamespace(outcome=outcome, findings=[])

    monkeypatch.setattr(_ss, "estimate_scan", lambda req: [])
    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", fake_core)

    req = _ss.ScanRequest(
        binaries=[Path("libfoo.so")],
        depth="binary",
        baseline=Path("old.abi.json"),
        policy="sdk_vendor",
    )
    res = _ss.run_scan(req)
    assert res.verdict == "COMPATIBLE"


def test_run_scan_rejects_collapse_versioned_symbols_without_baseline():
    # Codex review on PR #657: ScanRequest gained collapse_versioned_symbols
    # (an ICU-style version-suffix transition needs it to demote a rename to
    # COMPATIBLE_WITH_RISK the same way `compare`'s config-resolved
    # equivalent does) -- it must be rejected without a baseline like every
    # other comparison-only field.
    from abicheck.errors import ValidationError
    from abicheck.service_scan import ScanRequest, run_scan

    req = ScanRequest(
        binaries=[Path("libfoo.so")], depth="binary", collapse_versioned_symbols=True
    )
    with pytest.raises(ValidationError, match="only take effect with a baseline"):
        run_scan(req)


def test_run_scan_forwards_collapse_versioned_symbols_to_core(monkeypatch):
    # And, with a real baseline, the value must actually reach
    # run_scan_core (the Python API's own config-surface parity gap Codex
    # found -- the CLI threads this but ScanRequest never exposed it).
    from types import SimpleNamespace

    from abicheck import service_scan as _ss

    captured = {}

    def fake_core(**kw):
        captured["collapse_versioned_symbols"] = kw.get("collapse_versioned_symbols")
        outcome = SimpleNamespace(
            verdict="COMPATIBLE",
            exit_code=0,
            coverage=[],
            crosscheck={},
            to_dict=lambda: {},
        )
        return SimpleNamespace(outcome=outcome, findings=[])

    monkeypatch.setattr(_ss, "estimate_scan", lambda req: [])
    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", fake_core)

    req = _ss.ScanRequest(
        binaries=[Path("libfoo.so")],
        depth="binary",
        baseline=Path("old.abi.json"),
        collapse_versioned_symbols=True,
    )
    _ss.run_scan(req)

    assert captured["collapse_versioned_symbols"] is True


# ── _try_attach_numpy_capi_surface() ────────────────────────────────────────


class TestTryAttachNumpyCapiSurface:
    def test_logs_only_when_consumption_detected(self, tmp_path, monkeypatch, caplog):
        # extract_numpy_capi_surface returns a real (non-None) surface with
        # False flags for an ordinary, successfully-scanned non-NumPy
        # library -- the INFO log must not fire for every such library
        # (CodeRabbit review).
        from abicheck.numpy_capi import NumPyCapiSurface
        from abicheck.service import _try_attach_numpy_capi_surface

        snap = AbiSnapshot(library="lib.so", version="1.0")
        not_consuming = NumPyCapiSurface(
            consumes_array_api=False, consumes_ufunc_api=False
        )
        monkeypatch.setattr(
            "abicheck.numpy_capi.extract_numpy_capi_surface", lambda _p: not_consuming
        )
        with caplog.at_level("INFO", logger="abicheck.service"):
            _try_attach_numpy_capi_surface(snap, tmp_path / "lib.so")
        assert snap.numpy_capi is not_consuming
        assert "NumPy C-API consumption detected" not in caplog.text

    @pytest.mark.parametrize(
        "consumes_array_api,consumes_ufunc_api",
        [(True, False), (False, True)],
    )
    def test_logs_when_consumption_detected(
        self, tmp_path, monkeypatch, caplog, consumes_array_api, consumes_ufunc_api
    ):
        # Parametrized over each side of the production OR condition, so
        # removing either one from _try_attach_numpy_capi_surface's guard
        # would fail this test (CodeRabbit review).
        from abicheck.numpy_capi import NumPyCapiSurface
        from abicheck.service import _try_attach_numpy_capi_surface

        snap = AbiSnapshot(library="lib.so", version="1.0")
        consuming = NumPyCapiSurface(
            consumes_array_api=consumes_array_api,
            consumes_ufunc_api=consumes_ufunc_api,
        )
        monkeypatch.setattr(
            "abicheck.numpy_capi.extract_numpy_capi_surface", lambda _p: consuming
        )
        with caplog.at_level("INFO", logger="abicheck.service"):
            _try_attach_numpy_capi_surface(snap, tmp_path / "lib.so")
        assert snap.numpy_capi is consuming
        assert "NumPy C-API consumption detected" in caplog.text


class TestRunDumpDependencyScope:
    """``run_dump`` is built from ``_run_dump_uncached`` via
    ``dumper_scoping.wrap_run_dump_with_dependency_scope`` -- default
    ``include_dependencies=True`` preserves every existing caller's
    (scan/MCP/dump's own inline calls) unfiltered behavior, tagged
    explicitly "full"; passing ``include_dependencies=False`` (what
    ``compare`` now defaults to) filters the same way ``dump`` does by
    default. See tests/test_dumper_scoping.py for direct coverage of the
    wrapping function itself."""

    def test_run_dump_defaults_to_full(self, tmp_path):
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"\x7fELF" + b"\x00" * 100)
        fake_snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        with patch("abicheck.service_dump_native._run_dump_uncached", return_value=fake_snap):
            result = run_dump(elf_path, "elf")
        assert result.dependency_scope == "full"

    def test_run_dump_include_dependencies_false_filters(self, tmp_path):
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"\x7fELF" + b"\x00" * 100)
        fake_snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
        with patch("abicheck.service_dump_native._run_dump_uncached", return_value=fake_snap):
            result = run_dump(elf_path, "elf", include_dependencies=False)
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
        assert "include_dependencies" in sig.parameters
        assert sig.parameters["include_dependencies"].default is True


class TestMetadataAttachFailuresAreSwallowed:
    """Each enrichment step must never fail a dump (ADR-037).

    The three ``except Exception`` handlers went untested while the block
    lived in ``service.py``; extracting it to ``service_metadata_attach``
    surfaced that as uncovered new lines, so they are pinned here.
    """

    @staticmethod
    def _snap():
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(library="libfoo.so", version="1")

    def test_an_unresolvable_library_path_is_swallowed(self, caplog) -> None:
        # `resolve()` used to run *above* the handler, so a path it cannot
        # resolve aborted the whole dump instead of skipping metadata
        # extraction (CodeRabbit review).
        #
        # The failure is injected rather than staged from a real filesystem
        # shape, because every concrete trigger is either interpreter- or
        # environment-dependent: a symlink loop raises `RuntimeError` only
        # on 3.10-3.12 (3.13 switched to non-strict `realpath`; CI's 3.14
        # lane caught an earlier version of this test asserting otherwise),
        # and a deleted cwd raises everywhere but requires mutating process
        # state. What the fix is actually about is *where the call sits*, so
        # that is what this pins.
        import logging

        from abicheck.service_metadata_attach import _try_attach_sycl_metadata

        class _UnresolvablePath:
            def resolve(self):
                raise OSError("cannot resolve")

        snap = self._snap()
        with caplog.at_level(logging.DEBUG, logger="abicheck.service"):
            _try_attach_sycl_metadata(snap, _UnresolvablePath())
        assert snap.sycl is None
        assert "SYCL metadata extraction skipped" in caplog.text

    def test_python_extension_detection_failure_attaches_nothing(
        self, monkeypatch, caplog
    ) -> None:
        import abicheck.python_ext as python_ext_mod
        from abicheck.service_metadata_attach import _try_attach_python_ext_metadata

        monkeypatch.setattr(
            python_ext_mod,
            "detect_python_extension",
            lambda _snap: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            _try_attach_python_ext_metadata(snap)
        assert snap.python_ext is None
        assert "Python extension detection skipped" in caplog.text

    def test_numpy_capi_extraction_failure_attaches_nothing(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.numpy_capi as numpy_capi_mod
        from abicheck.service_metadata_attach import _try_attach_numpy_capi_surface

        monkeypatch.setattr(
            numpy_capi_mod,
            "extract_numpy_capi_surface",
            lambda _path: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            _try_attach_numpy_capi_surface(snap, tmp_path / "libfoo.so")
        assert snap.numpy_capi is None
        assert "NumPy C-API surface extraction skipped" in caplog.text

    def test_python_api_recovery_failure_attaches_nothing(
        self, monkeypatch, caplog
    ) -> None:
        import abicheck.python_api as python_api_mod
        from abicheck.service_metadata_attach import _try_attach_python_api_surface

        monkeypatch.setattr(
            python_api_mod,
            "detect_python_api",
            lambda _snap: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            _try_attach_python_api_surface(snap)
        assert snap.python_api is None
        assert "Python API surface recovery skipped" in caplog.text

    def test_sycl_metadata_is_attached_when_detected(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.service_metadata_attach import _try_attach_sycl_metadata

        detected = sycl_mod.SyclMetadata(implementation="dpcpp")
        monkeypatch.setattr(sycl_mod, "parse_sycl_metadata", lambda _dir: detected)
        snap = self._snap()
        with caplog.at_level("INFO", logger="abicheck.service"):
            _try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is detected
        assert "SYCL metadata attached" in caplog.text

    def test_sycl_detection_failure_attaches_nothing(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.service_metadata_attach import _try_attach_sycl_metadata

        monkeypatch.setattr(
            sycl_mod,
            "parse_sycl_metadata",
            lambda _dir: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            _try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is None
        assert "SYCL metadata extraction skipped" in caplog.text

    def test_a_non_sycl_library_attaches_nothing_quietly(
        self, tmp_path, monkeypatch
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.service_metadata_attach import _try_attach_sycl_metadata

        monkeypatch.setattr(sycl_mod, "parse_sycl_metadata", lambda _dir: None)
        snap = self._snap()
        _try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is None

    def test_a_library_with_no_numpy_capi_attaches_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        import abicheck.numpy_capi as numpy_capi_mod
        from abicheck.service_metadata_attach import _try_attach_numpy_capi_surface

        monkeypatch.setattr(
            numpy_capi_mod, "extract_numpy_capi_surface", lambda _path: None
        )
        snap = self._snap()
        _try_attach_numpy_capi_surface(snap, tmp_path / "libfoo.so")
        assert snap.numpy_capi is None


class TestRunCompareRequestTypedResult:
    """ADR-055 D2: the one typed entry point and what it returns.

    ``run_compare_request`` returned a bare 3-tuple until 0.6; it and the
    ``run_compare`` shim both return the typed result now.
    """

    def _pair(self, tmp_path):
        old = AbiSnapshot(library="libtest", version="1.0")
        new = AbiSnapshot(library="libtest", version="2.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(old, old_p)
        save_snapshot(new, new_p)
        return CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))

    def test_returns_a_compare_result(self, tmp_path):
        result = run_compare_request(self._pair(tmp_path))
        assert isinstance(result, CompareResult)
        assert isinstance(result.diff, DiffResult)
        assert result.old_snapshot.version == "1.0"
        assert result.new_snapshot.version == "2.0"

    def test_as_tuple_reproduces_the_pre_0_6_shape(self, tmp_path):
        """The documented one-line migration for a positional caller."""
        result = run_compare_request(self._pair(tmp_path))
        diff, old, new = result.as_tuple()
        assert diff is result.diff
        assert old is result.old_snapshot
        assert new is result.new_snapshot

    def test_the_kwargs_shim_returns_the_same_type(self, tmp_path):
        # run_compare is the documented one-call entry point; it must not be
        # the one place still handing back a tuple.
        request = self._pair(tmp_path)
        assert isinstance(
            run_compare(request.old.path, request.new.path), CompareResult
        )

    def test_suppression_is_none_when_the_request_names_no_file(self, tmp_path):
        assert run_compare_request(self._pair(tmp_path)).suppression is None

    def test_carries_the_resolved_suppression_list(self, tmp_path):
        """What ADR-055 D4 needs: the resolved list, without a second load.

        ``DiffResult`` carries the resolved policy file but never the
        suppression list, so before this a front end applying post-
        classification scoping (``appcompat.scope_diff_to_app``) had to load
        the same file again itself.
        """
        supp = tmp_path / "suppress.yaml"
        supp.write_text(
            "version: 1\nsuppressions:\n  - symbol: _Z1gone\n    reason: test\n",
            encoding="utf-8",
        )
        request = self._pair(tmp_path).replace(suppress=supp)
        result = run_compare_request(request)
        assert result.suppression is not None
        assert len(result.suppression.rule_identities()) == 1

    def test_follow_linker_scripts_is_forwarded_per_side(self, tmp_path, monkeypatch):
        """ADR-055 D4: the MCP server's resource guard is request surface.

        ``resolve_input`` follows a GNU ld linker script by default; the MCP
        tools must not, because they size-check only the caller-supplied path.
        """
        import abicheck.service as service_mod

        captured: dict[str, object] = {}
        original = service_mod.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured[version] = kwargs.get("follow_linker_scripts")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _spy)
        import dataclasses

        base = self._pair(tmp_path)
        run_compare_request(
            base.replace(
                old=dataclasses.replace(
                    base.old, version="old", follow_linker_scripts=False
                ),
                new=dataclasses.replace(base.new, version="new"),
            )
        )
        assert captured["old"] is False
        # Unset stays the historical default rather than inheriting the other side.
        assert captured["new"] is True


class TestRunCompareRequestResolutionParity:
    """ADR-055 D1, second slice: the last concepts the CLI's own resolution
    (``cli_resolve._resolve_compare_snapshots``) could express and the typed
    request could not — ``--dwarf-only``, ``--debug-format``, ADR-050 D1's
    include labels, and ``--follow-deps``."""

    def _elf_pair(self, tmp_path) -> tuple[Path, Path]:
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        for p in (old_p, new_p):
            p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return old_p, new_p

    def _stub_dump(self, monkeypatch) -> dict[str, dict[str, object]]:
        """Record resolve_input's debug kwargs per side, without a real parse."""
        import abicheck.workflows.input_resolution as input_resolution_mod

        seen: dict[str, dict] = {}

        def _fake(path, headers=None, *args, **kwargs):
            seen[Path(path).name] = {
                k: kwargs.get(k)
                for k in ("dwarf_only", "debug_format", "include_labels")
            }
            return AbiSnapshot(library=Path(path).name, version="x")

        monkeypatch.setattr(input_resolution_mod, "run_dump", _fake)
        return seen

    def _request(self, tmp_path, **kwargs) -> CompareRequest:
        old_p, new_p = self._elf_pair(tmp_path)
        return CompareRequest(
            old=InputSpec(path=old_p, version="old"),
            new=InputSpec(path=new_p, version="new"),
            **kwargs,
        )

    def test_debug_parse_fields_reach_both_sides(self, tmp_path, monkeypatch):
        seen = self._stub_dump(monkeypatch)
        run_compare_request(
            self._request(tmp_path, dwarf_only=True, debug_format="dwarf")
        )
        assert set(seen) == {"old.so", "new.so"}
        for side in seen.values():
            assert side["dwarf_only"] is True
            assert side["debug_format"] == "dwarf"

    def test_include_labels_are_converted_back_to_a_mapping(
        self, tmp_path, monkeypatch
    ):
        # Carried as a tuple of pairs so the request stays hashable, but
        # `resolve_input` takes the mapping — the conversion must happen.
        seen = self._stub_dump(monkeypatch)
        run_compare_request(
            self._request(tmp_path, include_labels=((Path("/inc"), "proj"),))
        )
        assert seen["old.so"]["include_labels"] == {Path("/inc"): "proj"}

    def test_include_labels_default_passes_none_not_an_empty_mapping(
        self, tmp_path, monkeypatch
    ):
        seen = self._stub_dump(monkeypatch)
        run_compare_request(self._request(tmp_path))
        assert seen["old.so"]["include_labels"] is None

    def test_request_stays_hashable_with_include_labels_set(self, tmp_path):
        # The property InputSpec's own docstring claims for the whole request.
        request = self._request(tmp_path, include_labels=((Path("/inc"), "proj"),))
        assert hash(request) == hash(request.replace())

    def _stub_dependency_population(
        self, monkeypatch
    ) -> list[tuple[str, list[Path], str]]:
        import abicheck.dependency_info as dep_mod

        calls: list[tuple] = []
        monkeypatch.setattr(
            dep_mod,
            "populate_dependency_info",
            lambda snap, so_path, search_paths, sysroot, ld_library_path: calls.append(
                (Path(so_path).name, list(search_paths), ld_library_path)
            ),
        )
        return calls

    def test_follow_dependencies_populates_both_elf_sides(self, tmp_path, monkeypatch):
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        run_compare_request(
            self._request(
                tmp_path,
                follow_dependencies=True,
                dependency_search_paths=(Path("/opt/lib"),),
                ld_library_path="/usr/lib",
            )
        )
        assert [c[0] for c in calls] == ["old.so", "new.so"]
        assert calls[0][1] == [Path("/opt/lib")]
        assert calls[0][2] == "/usr/lib"

    def test_follow_dependencies_is_opt_in(self, tmp_path, monkeypatch):
        # It costs a full dependency-graph resolution per side, so an
        # unrelated caller must not start paying for it silently.
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        run_compare_request(self._request(tmp_path))
        assert calls == []

    def test_non_elf_sides_are_skipped(self, tmp_path, monkeypatch):
        # resolve_dependencies reads ELF DT_NEEDED entries; a PE/Mach-O side
        # has nothing for it to do.
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="lib", version="1.0"), old_p)
        save_snapshot(AbiSnapshot(library="lib", version="2.0"), new_p)
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p),
                new=InputSpec(path=new_p),
                follow_dependencies=True,
            )
        )
        assert calls == []


class TestDebugFormatResolution:
    """ADR-055 D1 second slice, Codex review round 2: what `debug_format`
    actually reaches (and doesn't reach) the extraction layer as."""

    def _request(self, tmp_path, **kwargs) -> CompareRequest:
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        for p in (old_p, new_p):
            p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return CompareRequest(
            old=InputSpec(path=old_p), new=InputSpec(path=new_p), **kwargs
        )

    def _spy(self, monkeypatch) -> dict[str, object]:
        import abicheck.workflows.input_resolution as input_resolution_mod

        seen: dict[str, object] = {}

        def _fake(path, headers=None, *args, **kwargs):
            seen["debug_format"] = kwargs.get("debug_format")
            return AbiSnapshot(library=Path(path).name, version="x")

        monkeypatch.setattr(input_resolution_mod, "run_dump", _fake)
        return seen

    def test_auto_becomes_none_not_the_literal_string(self, tmp_path, monkeypatch):
        # `_resolve_debug_metadata` raises ValueError for anything outside
        # dwarf/btf/ctf and treats None as auto-detect, so forwarding the
        # accepted "auto" verbatim crashed during extraction. The CLI does the
        # same translation (cli_compare_helpers/cli_dump_helpers).
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="auto"))
        assert seen["debug_format"] is None

    def test_auto_is_case_insensitive_too(self, tmp_path, monkeypatch):
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="AUTO"))
        assert seen["debug_format"] is None

    def test_an_explicit_format_is_lowercased_and_forwarded(
        self, tmp_path, monkeypatch
    ):
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="BTF"))
        assert seen["debug_format"] == "btf"

    def test_forced_elf_format_is_rejected_for_a_non_elf_side(self, tmp_path):
        # The PE/Mach-O dump paths take no debug-format argument, so it would
        # be silently dropped and the run would report success having ignored
        # what was asked. The CLI rejects this up front; so does this now.
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        pe = tmp_path / "new.dll"
        pe.write_bytes(b"MZ" + b"\x00" * 200)
        request = CompareRequest(
            old=InputSpec(path=old_p), new=InputSpec(path=pe), debug_format="dwarf"
        )
        with pytest.raises(ValidationError, match="only supported for ELF"):
            run_compare_request(request)

    def test_auto_is_not_rejected_for_a_non_elf_side(self, tmp_path, monkeypatch):
        # "auto" forces nothing, so there is nothing for a PE side to ignore.
        self._spy(monkeypatch)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        pe = tmp_path / "new.dll"
        pe.write_bytes(b"MZ" + b"\x00" * 200)
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p), new=InputSpec(path=pe), debug_format="auto"
            )
        )

    def test_snapshot_inputs_are_unaffected(self, tmp_path, monkeypatch):
        # A JSON snapshot has no detected binary format; same as the CLI, that
        # is not a rejection case.
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="l", version="1"), old_p)
        save_snapshot(AbiSnapshot(library="l", version="2"), new_p)
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p),
                new=InputSpec(path=new_p),
                debug_format="dwarf",
            )
        )


class TestComparePipelinePhases:
    """ADR-055 D1: ``run_compare_request`` is the composition of the two
    phases in ``service_compare_pipeline``, and those phases are what the
    native ``compare`` CLI now shares instead of its own resolution copy."""

    def _snap_file(self, tmp_path, name, version):
        from abicheck.serialization import save_snapshot

        path = tmp_path / f"{name}-{version}.json"
        save_snapshot(
            AbiSnapshot(
                library=name,
                version=version,
                functions=[
                    Function(
                        name="foo",
                        mangled="foo",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
            ),
            path,
        )
        return path

    def test_run_compare_request_equals_resolve_then_classify(self, tmp_path):
        from abicheck.service import (
            classify_compare_pair,
            resolve_compare_request,
            run_compare_request,
        )

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))

        composed = classify_compare_pair(request, resolve_compare_request(request))
        one_call = run_compare_request(request)

        assert [c.kind for c in composed.diff.changes] == [
            c.kind for c in one_call.diff.changes
        ]
        assert composed.diff.verdict == one_call.diff.verdict
        assert composed.old_snapshot.version == one_call.old_snapshot.version
        assert composed.new_snapshot.version == one_call.new_snapshot.version

    def test_resolve_phase_returns_both_sides_and_their_evidence(self, tmp_path):
        from abicheck.service import resolve_compare_request

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        pair = resolve_compare_request(
            CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        )
        assert pair.old.version == "1.0"
        assert pair.new.version == "2.0"
        # A JSON snapshot has no detected binary format, same as on the CLI.
        assert pair.old_fmt is None and pair.new_fmt is None
        assert pair.old_evidence.collect_mode == pair.new_evidence.collect_mode == "off"

    def test_layer_coverage_rows_reach_the_result(self, tmp_path, monkeypatch):
        """The one branch in `classify_compare_pair` nothing else exercises.

        ``prepare_embedded_build_source`` only returns layer-coverage rows when
        the snapshots actually carry embedded L3-L5 evidence, so a plain
        snapshot pair leaves this assignment unexecuted. Stubbing that helper
        checks the wiring itself: rows it produces are attached to the
        ``DiffResult``, and the metrics call still receives the extra changes.
        """
        from abicheck.buildsource import evidence_report
        from abicheck.service import classify_compare_pair, resolve_compare_request

        rows = [{"layer": "L3", "covered": 1}]
        attached: list[object] = []

        def _fake_prepare(*_args, **_kwargs):
            return [], rows, {"elapsed": 0.0}, []

        def _fake_attach(result, metrics, extra, **_kwargs):
            attached.append((result, metrics, extra))

        # ADR-061 Phase 3: patch the owner -- these moved to the engine, and
        # `classify_compare_pair` imports them from there.
        monkeypatch.setattr(
            evidence_report, "prepare_embedded_build_source", _fake_prepare
        )
        monkeypatch.setattr(evidence_report, "attach_evidence_metrics", _fake_attach)

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result = classify_compare_pair(request, resolve_compare_request(request))

        assert result.diff.layer_coverage == rows
        assert len(attached) == 1
        assert attached[0][0] is result.diff
