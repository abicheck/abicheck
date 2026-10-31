"""Unit tests for abicheck.workflows.input_resolution (input detection, resolution, metadata) and dry_run_estimate.expand_header_inputs."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from abicheck.dry_run_estimate import (
    expand_header_inputs,
)
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot
from abicheck.workflows.dump.native import (
    run_dump,
)
from abicheck.workflows.input_resolution import (
    collect_metadata,
    detect_binary_format,
    resolve_input,
    sniff_text_format,
)
from tests._dump_format_fakes import fake_format_adapter

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

    def test_abicc_perl_dump_is_unknown(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = { 'Headers' => {} };")
        assert sniff_text_format(p) == "unknown"

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
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
            result = resolve_input(p, is_elf=True)
        assert result is snap
        mock.assert_called_once()

    def test_include_dependencies_threads_to_run_dump(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
            resolve_input(p, is_elf=True, include_dependencies=False)
        assert mock.call_args.kwargs["include_system_declarations"] is False

    def test_include_dependencies_defaults_to_the_filtered_surface(self, tmp_path):
        # PR #1258: every typed entry point agrees with the CLI's own
        # dependency-scoping default, so an omitted option cannot yield a
        # `full` snapshot here and a `filtered` one from `abicheck dump`
        # (which is a ScopeMismatchError with no verdict at compare time).
        # The cross-surface invariant lives in
        # tests/test_front_end_default_parity.py; this pins the one call.
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
            resolve_input(p, is_elf=True)
        assert mock.call_args.kwargs["include_system_declarations"] is False

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
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
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
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
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
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
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
        with patch(
            "abicheck.workflows.input_resolution.run_dump", return_value=snap
        ) as mock:
            resolve_input(script, include_dependencies=False)
        assert mock.call_count == 1
        _, kwargs = mock.call_args
        assert kwargs["include_system_declarations"] is False

    def test_header_graph_lang_matches_elf_main_pass_normalization(self, tmp_path):
        """Codex review: ``_dump_elf`` normalizes ``lang`` to only ever force
        "c" explicitly (letting auto-detection run for the default "c++"),
        before calling ``dumper.dump()`` -- ``_attach_header_graph``'s own
        ``extract.headers.clang.backend.clang_header_dump`` call must be given that identical normalized
        value, or it hashes a different cache key than the main pass just
        used and permanently misses the new AST reuse memo for the default,
        by far the most common, ELF dump shape."""
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
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
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
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
        never forwarded it into its own `extract_elf()` call, which
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
            fake_format_adapter("elf", result=snap) as mock_dump_elf,
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
            ),
        ):
            run_dump(
                p,
                "elf",
                headers=[header],
                includes=[widened_dir],
                lang="c++",
                public_include_search_dirs=[explicit_dir],
            )
        passed = mock_dump_elf.last.public_include_search_dirs
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
            fake_format_adapter("elf", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
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
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
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
            fake_format_adapter("pe", result=snap),
            patch(
                "abicheck.workflows.dump.native._attach_header_graph", return_value=snap
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
        from abicheck.workflows.input_resolution import _resolve_symvers

        p = tmp_path / "empty.symvers"
        p.write_text("# only a comment\n")
        assert _resolve_symvers(p, "1.0") is None

    def test_symvers_unreadable_returns_none(self, tmp_path):
        from abicheck.workflows.input_resolution import _resolve_symvers

        # A directory named like a manifest cannot be read as text → None.
        d = tmp_path / "Module.symvers"
        d.mkdir()
        assert _resolve_symvers(d, "1.0") is None

    def test_json_text_format(self, tmp_path):
        p = tmp_path / "snap.json"
        snap = AbiSnapshot(library="test", version="1.0")
        p.write_text('{"library": "test"}')
        with patch(
            "abicheck.workflows.input_resolution.load_snapshot", return_value=snap
        ):
            result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_json_load_error_wraps_in_snapshot_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with patch(
            "abicheck.workflows.input_resolution.load_snapshot",
            side_effect=ValueError("bad"),
        ):
            with pytest.raises(SnapshotError, match="Failed to load JSON"):
                resolve_input(p, is_elf=False)

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "mystery"
        p.write_text("???")
        with patch(
            "abicheck.workflows.input_resolution.detect_binary_format",
            return_value=None,
        ):
            with patch(
                "abicheck.workflows.input_resolution.sniff_text_format",
                return_value="unknown",
            ):
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


# ── collect_metadata() ──────────────────────────────────────────────────────


class TestCollectMetadata:
    def test_binary_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with patch(
            "abicheck.workflows.input_resolution.sniff_text_format",
            return_value="unknown",
        ):
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
