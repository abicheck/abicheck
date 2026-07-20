"""Tests for the whole-snapshot cache wiring (service_dump_cache.py)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.service_dump_cache import (
    _dump_cache_extra_key,
    _dump_is_cacheable,
    cached_run_dump,
)


def _sample_snap(name: str = "foo") -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name=name,
                mangled=f"_Z{len(name)}{name}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


def _cacheable_kwargs(**overrides):
    base = dict(
        pdb_path=None,
        dwarf_only=False,
        debug_roots=None,
        enable_debuginfod=False,
        debug_format=None,
        symbols_only=False,
        debug_presence_only=False,
        compile=None,
    )
    base.update(overrides)
    return base


class TestDumpIsCacheable:
    def test_plain_shape_is_cacheable(self):
        assert _dump_is_cacheable(**_cacheable_kwargs()) is True

    def test_pdb_path_not_cacheable(self, tmp_path):
        kwargs = _cacheable_kwargs(pdb_path=tmp_path / "x.pdb")
        assert _dump_is_cacheable(**kwargs) is False

    def test_dwarf_only_not_cacheable(self):
        assert _dump_is_cacheable(**_cacheable_kwargs(dwarf_only=True)) is False

    def test_debug_roots_not_cacheable(self, tmp_path):
        kwargs = _cacheable_kwargs(debug_roots=[tmp_path])
        assert _dump_is_cacheable(**kwargs) is False

    def test_enable_debuginfod_not_cacheable(self):
        kwargs = _cacheable_kwargs(enable_debuginfod=True)
        assert _dump_is_cacheable(**kwargs) is False

    def test_debug_format_not_cacheable(self):
        kwargs = _cacheable_kwargs(debug_format="dwarf")
        assert _dump_is_cacheable(**kwargs) is False

    def test_symbols_only_not_cacheable(self):
        kwargs = _cacheable_kwargs(symbols_only=True)
        assert _dump_is_cacheable(**kwargs) is False

    def test_debug_presence_only_not_cacheable(self):
        kwargs = _cacheable_kwargs(debug_presence_only=True)
        assert _dump_is_cacheable(**kwargs) is False

    def test_compile_context_not_cacheable(self):
        kwargs = _cacheable_kwargs(compile=object())
        assert _dump_is_cacheable(**kwargs) is False

    def test_header_graph_no_longer_a_cacheability_parameter(self):
        """G29 Phase A: header_graph/header_graph_includes are no longer
        run_dump parameters at all (the graph is unconditional), so
        _dump_is_cacheable no longer takes them — the plain shape stays
        cacheable, which is what actually matters here now."""
        assert _dump_is_cacheable(**_cacheable_kwargs()) is True


class TestDumpCacheExtraKey:
    def test_differs_by_binary_fmt(self):
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        k2 = _dump_cache_extra_key("pe", "auto", None, None)
        assert k1 != k2

    def test_differs_by_header_backend(self):
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        k2 = _dump_cache_extra_key("elf", "clang", None, None)
        assert k1 != k2

    def test_differs_by_public_headers(self, tmp_path):
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        k2 = _dump_cache_extra_key("elf", "auto", [tmp_path / "pub.h"], None)
        assert k1 != k2

    def test_order_independent_for_public_headers(self, tmp_path):
        a = tmp_path / "a.h"
        b = tmp_path / "b.h"
        k1 = _dump_cache_extra_key("elf", "auto", [a, b], None)
        k2 = _dump_cache_extra_key("elf", "auto", [b, a], None)
        assert k1 == k2

    def test_one_path_with_embedded_comma_does_not_collide_with_two_paths(self):
        # A regression guard for a real (if narrow) collision: joining with a
        # printable delimiter like "," means one path literally named "a,b"
        # and two separate paths "a"/"b" both stringify+sort+join to "a,b" —
        # indistinguishable. NUL can't appear in a filesystem path, so the two
        # inputs must produce different keys.
        one_path_with_comma = [Path("a,b")]
        two_paths = [Path("a"), Path("b")]
        k1 = _dump_cache_extra_key("elf", "auto", one_path_with_comma, None)
        k2 = _dump_cache_extra_key("elf", "auto", two_paths, None)
        assert k1 != k2

    def test_differs_by_env_pinned_frontend_even_though_raw_string_is_auto(
        self, monkeypatch
    ):
        # Codex review: "auto" consults ABICHECK_AST_FRONTEND at dump time, so
        # the raw header_backend string passed to this function is the same
        # "auto" literal regardless of what that env var resolves to. Hashing
        # the raw string let an env-pinned hybrid dump's key collide with an
        # unpinned (or differently-pinned) auto dump's key on this
        # PERSISTENT on-disk cache -- a later run with the env var in a
        # different state could silently reuse the wrong producer's snapshot.
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        k_unpinned = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "hybrid")
        k_hybrid_pinned = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
        k_clang_pinned = _dump_cache_extra_key("elf", "auto", None, None)
        assert len({k_unpinned, k_hybrid_pinned, k_clang_pinned}) == 3

    def test_auto_and_castxml_still_collide_when_env_unset(self, monkeypatch):
        # Both resolve to the identical effective backend ("castxml") when no
        # env pin is set, so they should still share a cache entry -- the fix
        # hashes the RESOLVED backend, not merely "make every raw string
        # distinct".
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        k_auto = _dump_cache_extra_key("elf", "auto", None, None)
        k_castxml = _dump_cache_extra_key("elf", "castxml", None, None)
        assert k_auto == k_castxml

    def test_differs_when_layout_tool_becomes_available(self, monkeypatch):
        # Codex review: service.run_dump calls attach_clang_layout for every
        # "clang"-backend dump, so the snapshot's layout fields depend on
        # ABICHECK_CLANG_LAYOUT_TOOL/PATH too -- a cache entry created before
        # enabling the tool must not be silently reused after enabling it.
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch(
            "abicheck.clang_layout_tool.shutil.which", return_value=None
        ):
            k_before = _dump_cache_extra_key("elf", "clang", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k_after = _dump_cache_extra_key("elf", "clang", None, None)
        assert k_before != k_after

    def test_layout_tool_identity_irrelevant_for_castxml(self, monkeypatch):
        # The tool never runs for a pure "castxml" backend -- its
        # availability/identity must not needlessly invalidate that cache
        # entry, which never involves it at all.
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        k1 = _dump_cache_extra_key("elf", "castxml", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k2 = _dump_cache_extra_key("elf", "castxml", None, None)
        assert k1 == k2

    def test_differs_when_layout_tool_becomes_available_for_hybrid(self, monkeypatch):
        # Codex review: run_dump's hybrid branch recurses into its own
        # header_backend="clang" sub-dump (which gets the SAME
        # attach_clang_layout enrichment as a pure "clang" dump) before
        # merge_snapshots folds any clang-only declarations -- carrying their
        # layout facts -- into the merged hybrid result. A hybrid cache entry
        # created before enabling/changing the tool must not be silently
        # reused afterward either.
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch(
            "abicheck.clang_layout_tool.shutil.which", return_value=None
        ):
            k_before = _dump_cache_extra_key("elf", "hybrid", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k_after = _dump_cache_extra_key("elf", "hybrid", None, None)
        assert k_before != k_after

    def test_differs_when_layout_tool_becomes_available_for_unpinned_auto(
        self, monkeypatch
    ):
        # Codex review: dumper._header_ast_parser's G16 logic can silently
        # runtime-fallback a genuinely-unpinned "auto" request from castxml to
        # clang (toolchain-version mismatch or a direct-include #error guard)
        # -- invisible to _resolve_header_backend's static, content-blind
        # resolution (which optimistically returns "castxml" for unpinned
        # auto). That fallback's snapshot is clang-sourced and gets the same
        # attach_clang_layout enrichment an explicit "clang" dump would, so
        # the layout tool's identity must be hashed for this case too, even
        # though resolved_backend here is "castxml".
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch(
            "abicheck.clang_layout_tool.shutil.which", return_value=None
        ):
            k_before = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k_after = _dump_cache_extra_key("elf", "auto", None, None)
        assert k_before != k_after

    def test_layout_tool_identity_irrelevant_for_explicit_castxml_pin(
        self, monkeypatch
    ):
        # An EXPLICIT castxml request -- either --ast-frontend castxml or an
        # ABICHECK_AST_FRONTEND=castxml pin with a raw "auto" request -- never
        # triggers the G16 runtime fallback (the castxml failure surfaces
        # verbatim instead), so the layout tool truly never runs for it and
        # must not needlessly invalidate its cache entry.
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxml")
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k2 = _dump_cache_extra_key("elf", "auto", None, None)
        assert k1 == k2

    def test_differs_by_header_graph_clang_availability_even_for_castxml(self):
        # Codex review: G29 Phase A's header-graph attach always runs its own
        # internal clang AST pass (service._attach_header_graph ->
        # dumper._clang_header_dump) regardless of `resolved_backend` -- a
        # plain "castxml" dump still gets one. A cache entry written while
        # clang was unavailable must not be replayed once clang becomes
        # available (or vice versa), or the header graph silently keeps
        # whatever degraded/absent coverage the first run saw.
        with patch("abicheck.dumper_clang._clang_available", return_value=False):
            k_missing = _dump_cache_extra_key("elf", "castxml", None, None)
        with patch("abicheck.dumper_clang._clang_available", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/clang++"):
                k_present = _dump_cache_extra_key("elf", "castxml", None, None)
        assert k_missing != k_present

    def test_differs_by_header_graph_clang_resolved_path(self):
        # Two different clang installs on PATH (e.g. a system upgrade) must
        # produce different keys even though both "are available".
        with patch("abicheck.dumper_clang._clang_available", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/clang++"):
                k1 = _dump_cache_extra_key("elf", "castxml", None, None)
            with patch("shutil.which", return_value="/opt/llvm-18/bin/clang++"):
                k2 = _dump_cache_extra_key("elf", "castxml", None, None)
        assert k1 != k2

    def test_differs_when_clang_binary_swapped_in_place_at_same_path(
        self, tmp_path
    ):
        # Codex review: the resolved PATH string alone survives an in-place
        # binary swap at the same path (a package upgrade, or a symlink
        # retargeted to a different clang install) -- an mtime+size
        # fingerprint of the resolved binary must be folded in too, or a
        # cache entry from before the swap gets replayed with stale
        # header-graph coverage from the old compiler.
        clang_path = tmp_path / "clang++"
        clang_path.write_bytes(b"fake clang v1")
        with (
            patch("abicheck.dumper_clang._clang_available", return_value=True),
            patch("shutil.which", return_value=str(clang_path)),
        ):
            k1 = _dump_cache_extra_key("elf", "castxml", None, None)

            clang_path.write_bytes(b"fake clang v2 - different size")
            import os
            import time

            os.utime(clang_path, (time.time() + 10, time.time() + 10))
            k2 = _dump_cache_extra_key("elf", "castxml", None, None)

        assert k1 != k2

    def test_header_graph_clang_key_respects_lang(self):
        # `cc`/`c++` resolve to different driver names -- fold `lang` in so a
        # C vs C++ dump (which can genuinely have only one of the two
        # installed) gets a distinct key.
        with patch("abicheck.dumper_clang._clang_available", return_value=True):
            with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
                k_cpp = _dump_cache_extra_key("elf", "castxml", None, None, "c++")
                k_c = _dump_cache_extra_key("elf", "castxml", None, None, "c")
        assert k_cpp != k_c


class TestCachedRunDump:
    def test_cache_hit_skips_run_dump(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        calls = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap()

        snap1 = cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
        snap2 = cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")

        assert len(calls) == 1
        assert snap1.functions[0].name == snap2.functions[0].name == "foo"

    def test_binary_content_change_invalidates_cache(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"content A")
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
        binary.write_bytes(b"content B - genuinely different")
        cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")

        assert len(calls) == 2

    def test_different_binary_fmt_is_a_different_cache_entry(self, tmp_path):
        # Same on-disk bytes, but resolve_input would only ever call
        # cached_run_dump with one binary_fmt per real file — this asserts
        # the `extra` key material (added specifically for binary_fmt) really
        # is folded in, guarding against a PE/Mach-O snapshot ever being
        # served back for an ELF request that happens to share a cache key.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"shared content")
        calls: list[str] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(binary_fmt)
            return _sample_snap(name=binary_fmt)

        snap_elf = cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
        snap_pe = cached_run_dump(fake_run_dump, binary, "pe", [], [], "1.0", "c++")

        assert calls == ["elf", "pe"]
        assert snap_elf.functions[0].name == "elf"
        assert snap_pe.functions[0].name == "pe"

    def test_inferred_header_root_sibling_edit_invalidates_cache(self, tmp_path):
        # Codex review: header_utils.resolve_inferred_header_roots() adds a
        # -H header's own parent directory to the search path even when no
        # explicit -I was given (dumper.dump()/service._dump_elf both call
        # it). A sibling header reached only through that inferred root
        # (never itself passed as an explicit `headers` entry, and not under
        # any explicit `includes` dir either since none was given) must still
        # invalidate the cache when it changes.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        api_h = tmp_path / "api.h"
        api_h.write_text("void f();\n")
        detail_h = tmp_path / "detail.h"
        detail_h.write_text("struct detail {};\n")
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(fake_run_dump, binary, "elf", [api_h], [], "1.0", "c++")

        import os
        import time

        os.utime(detail_h, (time.time() + 10, time.time() + 10))
        cached_run_dump(fake_run_dump, binary, "elf", [api_h], [], "1.0", "c++")

        assert len(calls) == 2

    def test_uncacheable_shape_always_calls_run_dump(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        calls = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap()

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dwarf_only=True
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dwarf_only=True
        )

        assert len(calls) == 2
