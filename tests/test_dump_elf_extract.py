"""Unit tests for ELF dump extraction through run_dump."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot

# ── extract_elf() ─────────────────────────────────────────────────────────────


class TestDumpElf:
    def test_implicit_header_root_passed_to_dumper(self, tmp_path):
        # P3 regression: a -H umbrella nested under include/ must reach the
        # frontend with the include root on extra_includes, with no explicit -I.
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            extract_elf(p, [umb], [], "1.0", "c++")
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
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        build_inc = tmp_path / "buildinc"
        build_inc.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        cc = CompileContext(gcc_option_tokens=("-I", str(build_inc)))
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            extract_elf(p, [header], [], "1.0", "c++", compile=cc)
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
        from abicheck.workflows.dump.native import extract_elf

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
            extract_elf(
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
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "pub.h"
        header.write_text("int f(void);\n")
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            extract_elf(p, [header], [inc], "1.0", "c++")
        assert mock.call_args.kwargs["public_include_search_dirs"] == [inc]

    def test_implicit_root_defers_to_isystem_build_context(self, tmp_path):
        # Codex: when the caller's CompileContext supplies includes via -isystem,
        # the inferred -H root must defer — emitted as its own -isystem token
        # *after* the build's (build's is emitted first, so it wins), not jumping
        # ahead as -I. -isystem also keeps it above the standard system dirs.
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.workflows.dump.native import extract_elf

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
            extract_elf(p, [umb], [], "1.0", "c++", compile=cc)
        kwargs = mock.call_args.kwargs
        assert root not in kwargs["extra_includes"]  # not promoted to -I
        toks = list(kwargs["gcc_option_tokens"])
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        # the build's -isystem dir stays ahead of the inferred root (wins)
        assert toks.index(gen) < toks.index(str(root))

    def test_no_headers_warning(self, tmp_path):
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch(
            "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
        ):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = extract_elf(p, [], [], "1.0", "c++")
        assert result is snap

    def test_invalid_include_dir_raises(self, tmp_path):
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        h = tmp_path / "foo.h"
        h.write_text("")
        bad_inc = tmp_path / "nonexistent"
        with patch(
            "abicheck.workflows.dump.native.expand_header_inputs", return_value=[h]
        ):
            with pytest.raises(ValidationError, match="Include directory"):
                extract_elf(p, [h], [bad_inc], "1.0", "c++")

    def test_dump_error_wraps(self, tmp_path):
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x00" * 10)
        with patch(
            "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
        ):
            with patch("abicheck.dumper.dump", side_effect=RuntimeError("bad elf")):
                with pytest.raises(SnapshotError, match="Failed to dump"):
                    extract_elf(p, [], [], "1.0", "c++")

    def test_includes_without_headers_warns(self, tmp_path):
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="test", version="1.0")
        with patch(
            "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
        ):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = extract_elf(p, [], [inc], "1.0", "c++")
        assert result is snap

    def test_lang_c_sets_compiler(self, tmp_path):
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch(
            "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
        ):
            with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
                extract_elf(p, [], [], "1.0", "c")
        call_kwargs = mock_dump.call_args
        assert (
            call_kwargs.kwargs.get("compiler") == "cc"
            or call_kwargs[1].get("compiler") == "cc"
        )

    def test_debuginfod_url_reaches_resolve_debug_info(self, tmp_path):
        # Codex (PR #551): a custom --debuginfod-url must reach the resolver's
        # debuginfod_urls kwarg, not just gate enable_debuginfod on/off.
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch(
                "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
            ),
            patch(
                "abicheck.debug_resolver.resolve_debug_info", return_value=None
            ) as mock_resolve,
            patch("abicheck.dumper.dump", return_value=snap),
        ):
            extract_elf(
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
        from abicheck.workflows.dump.native import extract_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with (
            patch(
                "abicheck.workflows.dump.native.expand_header_inputs", return_value=[]
            ),
            patch(
                "abicheck.debug_resolver.resolve_debug_info", return_value=None
            ) as mock_resolve,
            patch("abicheck.dumper.dump", return_value=snap),
        ):
            extract_elf(p, [], [], "1.0", "c++", enable_debuginfod=True)
        assert mock_resolve.call_args.kwargs["debuginfod_urls"] is None
