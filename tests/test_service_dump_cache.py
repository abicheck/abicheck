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
        header_graph=False,
        header_graph_includes=False,
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

    def test_header_graph_not_cacheable(self):
        kwargs = _cacheable_kwargs(header_graph=True)
        assert _dump_is_cacheable(**kwargs) is False

    def test_header_graph_includes_not_cacheable(self):
        kwargs = _cacheable_kwargs(header_graph_includes=True)
        assert _dump_is_cacheable(**kwargs) is False


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
