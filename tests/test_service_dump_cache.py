"""Tests for the whole-snapshot cache wiring (service_dump_cache.py)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.service_dump_cache import (
    _dump_cache_extra_key,
    _dump_is_cacheable,
    _manifest_cache_paths,
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


class _FakeManifest:
    """Minimal ``.translation_units``-shaped stand-in for a real
    ``DumpManifest`` -- just enough for ``_manifest_all_tus_required`` to
    read, without needing a full ``parse_manifest`` round-trip."""

    def __init__(self, *, all_required: bool) -> None:
        class _Tu:
            def __init__(self, required: bool) -> None:
                self.required = required

        self.translation_units = [_Tu(True), _Tu(all_required)]


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

    def test_dump_manifest_is_cacheable(self):
        # ADR-050 D3/D6 (G32 Phase E): a manifest-driven dump used to be
        # unconditionally excluded; it is now cacheable like any other plain
        # shape once the cache key covers the manifest's own scope.
        kwargs = _cacheable_kwargs(dump_manifest=_FakeManifest(all_required=True))
        assert _dump_is_cacheable(**kwargs) is True

    def test_dump_manifest_with_optional_tu_not_cacheable(self):
        # Codex review, PR #636: a required=False TU's failure degrades to a
        # logged diagnostic rather than failing the dump, so a transient
        # failure could otherwise get permanently cached as a "successful"
        # but silently-degraded snapshot.
        kwargs = _cacheable_kwargs(dump_manifest=_FakeManifest(all_required=False))
        assert _dump_is_cacheable(**kwargs) is False


class TestDumpCacheExtraKey:
    def test_differs_by_binary_fmt(self):
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        k2 = _dump_cache_extra_key("pe", "auto", None, None)
        assert k1 != k2

    def test_binary_only_key_does_not_fingerprint_path_tools(self):
        with patch("abicheck.dumper._tool_identity") as identity:
            key = _dump_cache_extra_key(
                "elf", "auto", None, None, uses_ast=False
            )
        assert "\x00no-ast\x00" in key
        identity.assert_not_called()

    def test_binary_only_key_includes_public_scope(self, tmp_path):
        first = _dump_cache_extra_key(
            "elf", "auto", [tmp_path / "public" / "api.h"], None,
            uses_ast=False,
        )
        second = _dump_cache_extra_key(
            "elf", "auto", [tmp_path / "private" / "api.h"], None,
            uses_ast=False,
        )
        assert first != second

    def test_differs_by_header_backend(self):
        k1 = _dump_cache_extra_key("elf", "auto", None, None)
        k2 = _dump_cache_extra_key("elf", "clang", None, None)
        assert k1 != k2

    def test_c_and_cpp_fingerprint_their_actual_compilers(self):
        def compiler_bin(name, _path, _prefix):  # noqa: ANN001
            return ("/toolchain/gcc" if name == "cc" else "/toolchain/g++", name)

        with (
            patch("abicheck.dumper._resolve_compiler_binary", side_effect=compiler_bin),
            patch("abicheck.dumper._tool_identity", side_effect=lambda value: value),
        ):
            c_key = _dump_cache_extra_key("elf", "castxml", None, None, "c")
            cpp_key = _dump_cache_extra_key("elf", "castxml", None, None, "c++")
        assert c_key != cpp_key
        assert "/toolchain/gcc" in c_key
        assert "/toolchain/g++" in cpp_key

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
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
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
        monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch(
            "abicheck.clang_layout_tool.shutil.which", return_value=None
        ):
            k_before = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv("ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool")
        k_after = _dump_cache_extra_key("elf", "auto", None, None)
        assert k_before != k_after

    def test_layout_tool_irrelevant_when_auto_fallback_is_disabled(
        self, monkeypatch
    ):
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        k_before = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv(
            "ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool"
        )
        k_after = _dump_cache_extra_key("elf", "auto", None, None)
        assert k_before == k_after

    def test_invalid_frontend_pin_uses_same_fallback_cache_identity(
        self, monkeypatch
    ):
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "invalid-value")
        monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch("abicheck.clang_layout_tool.shutil.which", return_value=None):
            k_before = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv(
            "ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool"
        )
        k_after = _dump_cache_extra_key("elf", "auto", None, None)
        assert k_before != k_after

    def test_auto_frontend_env_remains_fallback_eligible(self, monkeypatch):
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "auto")
        monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")
        monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
        with patch("abicheck.clang_layout_tool.shutil.which", return_value=None):
            k_before = _dump_cache_extra_key("elf", "auto", None, None)
        monkeypatch.setenv(
            "ABICHECK_CLANG_LAYOUT_TOOL", "/opt/abicheck-clang-layout-tool"
        )
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

    def test_bumped_cache_version_invalidates_pre_dependency_scope_entries(
        self, tmp_path, monkeypatch
    ):
        """Regression for the PR #651 follow-up (Codex review): a cache
        entry written under the OLD ``_SNAPSHOT_CACHE_VERSION`` (before
        ``service.run_dump`` started tagging its result
        ``dependency_scope="full"``) must not be replayed verbatim once the
        version is bumped -- ``cached_run_dump`` returns a cache hit
        directly, without ever calling the passed-in ``run_dump`` (and
        therefore without ever tagging it), so a stale untagged entry would
        otherwise bypass the dependency-scope comparability gate forever."""
        from abicheck import snapshot_cache as snapshot_cache_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        calls = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap()

        monkeypatch.setattr(snapshot_cache_mod, "_SNAPSHOT_CACHE_VERSION", "OLD")
        cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
        assert len(calls) == 1

        monkeypatch.setattr(snapshot_cache_mod, "_SNAPSHOT_CACHE_VERSION", "NEW")
        cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
        assert len(calls) == 2  # re-run, not served from the old-version entry

    def test_input_change_during_dump_is_not_cached_under_new_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        header = tmp_path / "api.h"
        header.write_text("OLD", encoding="utf-8")
        calls: list[str] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            value = header.read_text(encoding="utf-8")
            calls.append(value)
            if value == "OLD":
                header.write_text("NEW", encoding="utf-8")
            return _sample_snap(name=value)

        first = cached_run_dump(
            fake_run_dump, binary, "elf", [header], [], "1.0", "c++"
        )
        second = cached_run_dump(
            fake_run_dump, binary, "elf", [header], [], "1.0", "c++"
        )
        assert first.functions[0].name == "OLD"
        assert second.functions[0].name == "NEW"
        assert calls == ["OLD", "NEW"]

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


class TestManifestCachePaths:
    """Direct unit tests for _manifest_cache_paths's dedup + public-dir
    folding, mirroring test_dumper_manifest.py's own manifest-construction
    style (parse_manifest over a YAML body, not a hand-built DumpManifest)."""

    def _manifest(self, tmp_path: Path, body: str):
        from abicheck.dump_manifest import parse_manifest

        base = tmp_path / "manifest_dir"
        base.mkdir(exist_ok=True)
        return parse_manifest(body, base_dir=base, source="<test>")

    def test_dedups_a_repeated_root(self, tmp_path):
        # dump_manifest.py's parser does not reject a literally-repeated
        # roots entry.
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h, a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        headers, _includes = _manifest_cache_paths(manifest)
        assert headers == [manifest.roots[0]]

    def test_dedups_a_root_repeated_as_a_forced_include(self, tmp_path):
        # The documented real-world shape: a TU's forced_includes commonly
        # names the same header a manifest's roots also declares.
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        headers, _includes = _manifest_cache_paths(manifest)
        assert headers == list(manifest.roots)

    def test_dedups_a_forced_include_repeated_across_tus(self, tmp_path):
        # b.h is never a root, so its first occurrence (tu_a) genuinely adds
        # it; its second occurrence (tu_b) must be the one deduplicated --
        # unlike a.h, which (being a root) is already seen before either TU's
        # forced_includes loop even runs and so never exercises this branch.
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h, b.h]\n"
            "  - name: tu_b\n    forced_includes: [b.h]\n",
        )
        headers, _includes = _manifest_cache_paths(manifest)
        assert [str(h.name) for h in headers] == ["a.h", "b.h"]

    def test_dedups_an_include_dir_repeated_across_tus(self, tmp_path):
        # roots: [a.h]'s own implicit parent-directory root (manifest_dir --
        # a real, existing directory, since the _manifest() helper creates
        # it) also lands in `includes` alongside `vendor` (see
        # test_implicit_header_parent_dir_folds_into_includes below), so this
        # asserts the repeated `vendor` entry specifically dedups to one
        # occurrence rather than asserting the list's total length.
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n    includes: [vendor]\n"
            "  - name: tu_b\n    forced_includes: [a.h]\n    includes: [vendor]\n",
        )
        _headers, includes = _manifest_cache_paths(manifest)
        vendor = manifest.translation_units[0].includes[0].path
        assert includes.count(vendor) == 1

    def test_public_header_dirs_fold_into_includes(self, tmp_path):
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\npublic_header_dirs: [pub]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        _headers, includes = _manifest_cache_paths(manifest)
        assert manifest.public_header_dirs[0] in includes

    def test_public_header_dir_already_an_include_is_not_duplicated(self, tmp_path):
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\npublic_header_dirs: [vendor]\n"
            "translation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n    includes: [vendor]\n",
        )
        _headers, includes = _manifest_cache_paths(manifest)
        vendor = manifest.public_header_dirs[0]
        assert includes.count(vendor) == 1

    def test_implicit_header_parent_dir_folds_into_includes(self, tmp_path):
        # Codex review, PR #636: a header force-included with no matching
        # TU `includes`/manifest `public_header_dirs` entry can still
        # quote-include an unnamed sibling from its own directory (ADR-050
        # D1's "a declared header's own parent directory is implicitly
        # project-owned, no -I needed" rule) -- that directory must still be
        # walked for content changes.
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        _headers, includes = _manifest_cache_paths(manifest)
        assert manifest.base_dir in includes

    def test_implicit_header_parent_dir_already_a_tu_include_is_not_duplicated(
        self, tmp_path
    ):
        # The implicit parent-dir root is only found for a directory that
        # actually exists on disk (_implicit_header_includes's own
        # .is_dir() guard), so this test needs the real directory present,
        # unlike the sibling tests above that only reference bare filenames
        # under the always-created manifest_dir root itself.
        (tmp_path / "manifest_dir" / "include").mkdir(parents=True)
        manifest = self._manifest(
            tmp_path,
            "roots: [include/foo.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [include/foo.h]\n"
            "    includes: [include]\n",
        )
        _headers, includes = _manifest_cache_paths(manifest)
        include_dir = manifest.translation_units[0].includes[0].path
        assert includes.count(include_dir) == 1

    def test_public_header_paths_fold_into_headers(self, tmp_path):
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\npublic_header_paths: [pub.h]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        headers, _includes = _manifest_cache_paths(manifest)
        assert [h.name for h in headers] == ["a.h", "pub.h"]

    def test_public_header_path_already_a_root_is_not_duplicated(self, tmp_path):
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\npublic_header_paths: [a.h]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        headers, _includes = _manifest_cache_paths(manifest)
        assert headers == [manifest.roots[0]]


class TestCachedRunDumpManifest:
    """ADR-050 D3/D6 (G32 Phase E) — a manifest-driven dump is now cacheable,
    keyed on the manifest's own content + structural identity (TU names,
    ordered includes/forced_includes, required/contributes_to_abi,
    project_owned)."""

    def _manifest(self, tmp_path: Path, body: str):
        from abicheck.dump_manifest import parse_manifest

        base = tmp_path / "manifest_dir"
        base.mkdir(exist_ok=True)
        return parse_manifest(body, base_dir=base, source="<test>")

    def _write_header(self, tmp_path: Path, name: str, content: str) -> Path:
        p = tmp_path / "manifest_dir" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_cache_hit_skips_run_dump_and_threads_manifest_on_miss(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        calls: list[object] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            # The dormant bug this test guards: cached_run_dump's live-dump
            # call used to omit dump_manifest entirely once manifest dumps
            # became reachable past the (former) unconditional exclusion --
            # silently falling back to a legacy single-TU dump on every
            # cache miss instead of the real manifest-driven one.
            calls.append(kwargs.get("dump_manifest"))
            return _sample_snap()

        snap1 = cached_run_dump(
            fake_run_dump,
            binary,
            "elf",
            [],
            [],
            "1.0",
            "c++",
            dump_manifest=manifest,
        )
        snap2 = cached_run_dump(
            fake_run_dump,
            binary,
            "elf",
            [],
            [],
            "1.0",
            "c++",
            dump_manifest=manifest,
        )
        assert len(calls) == 1
        assert calls[0] is manifest
        assert snap1.functions[0].name == snap2.functions[0].name == "foo"

    def test_forced_include_content_edit_invalidates_manifest_cache(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        header = self._write_header(tmp_path, "a.h", "int f(void);\n")
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        header.write_text("int f(void);\nint g(void);\n", encoding="utf-8")
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        assert len(calls) == 2

    def test_implicit_sibling_header_edit_invalidates_manifest_cache(self, tmp_path):
        # Codex review, PR #636: a header force-included with no matching TU
        # `includes` entry or manifest `public_header_dirs` can still
        # quote-include an unnamed sibling support header from its own
        # directory -- the same "a declared header's own parent directory is
        # implicitly project-owned, no -I needed" rule ADR-050 D1 documents
        # for profile_fingerprint, needed here too so the cache key doesn't
        # under-count exactly the same way. The legacy CLI path already
        # covers this via header_utils.resolve_inferred_header_roots.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        include_dir = tmp_path / "manifest_dir" / "include"
        include_dir.mkdir(parents=True)
        (include_dir / "foo.h").write_text('#include "detail.h"\nint f(void);\n')
        detail = include_dir / "detail.h"
        detail.write_text("struct detail { int x; };\n")
        manifest = self._manifest(
            tmp_path,
            "roots: [include/foo.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [include/foo.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        detail.write_text("struct detail { int x; int y; };\n")
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        assert len(calls) == 2

    def test_public_header_path_content_edit_invalidates_manifest_cache(self, tmp_path):
        # Codex review, PR #636: a `public_header_paths` entry that names
        # neither a declared root nor any TU's forced_includes (a
        # provenance-only header) must still be content-hashed, or an edit
        # to it is silently invisible to the cache key.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        pub_header = self._write_header(tmp_path, "pub.h", "int g(void);\n")
        manifest = self._manifest(
            tmp_path,
            "roots: [a.h]\npublic_header_paths: [pub.h]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        pub_header.write_text("int g(void);\nint h(void);\n", encoding="utf-8")
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=manifest
        )
        assert len(calls) == 2

    def test_tu_rename_invalidates_manifest_cache(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        old = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n  - name: tu_b\n    forced_includes: [a.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2

    def test_frontend_context_change_invalidates_manifest_cache(self, tmp_path):
        # ADR-050 D5 (G32 Phase D, Codex review): a manifest's own
        # frontend_context selects a genuinely different AST (host vs.
        # device) -- manifest_tu_scope_field only covers per-TU fields, so
        # without folding this manifest-level field into the cache key too,
        # two manifests differing only here would share a cached snapshot.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        old = self._manifest(
            tmp_path,
            "frontend_context: host\nroots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        new = self._manifest(
            tmp_path,
            "frontend_context: device\nroots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2

    def test_roots_membership_change_invalidates_manifest_cache(self, tmp_path):
        # Codex review, PR #636: a header already reachable via a TU's
        # forced_includes produces an identical _manifest_cache_paths()
        # flattened header list whether or not it is ALSO declared a root
        # -- but `roots` is what dumper.dump() uses as the manifest's own
        # declared-surface `headers` (dumper.py's `headers=list(dump_manifest
        # .roots)`), driving provenance/public classification independently
        # of file content. Both manifests here have an identical flattened
        # header set (forced_includes already names both a.h and b.h) and
        # identical TU structure, so only a dedicated roots key component
        # can tell them apart.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        self._write_header(tmp_path, "b.h", "int g(void);\n")
        old = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h, b.h]\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h, b.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h, b.h]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2

    def test_tu_includes_reorder_invalidates_manifest_cache(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        (tmp_path / "manifest_dir" / "vendor").mkdir(parents=True, exist_ok=True)
        (tmp_path / "manifest_dir" / "extra").mkdir(parents=True, exist_ok=True)
        old = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "    includes: [vendor, extra]\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "    includes: [extra, vendor]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2

    def test_contributes_to_abi_flip_invalidates_manifest_cache(self, tmp_path):
        # Both variants keep every TU required=True (contributes_to_abi=False
        # doesn't force required=False, only the reverse implication holds)
        # so BOTH manifests stay cacheable -- otherwise a required=False TU
        # would make `old` uncacheable on its own and this would stop
        # actually exercising cache-key sensitivity to the flag flip at all.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        old = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "  - name: tu_b\n    forced_includes: [a.h]\n"
            "    contributes_to_abi: false\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "  - name: tu_b\n    forced_includes: [a.h]\n"
            "    contributes_to_abi: true\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2

    def test_public_header_paths_change_invalidates_manifest_cache(self, tmp_path):
        # Codex review, PR #636: dumper.dump() replaces the caller-supplied
        # public_headers/public_header_dirs with the manifest's own
        # public_header_paths/public_header_dirs for provenance/contract
        # purposes whenever a manifest is given -- the cache key must
        # classify the same way, or two manifests differing only in
        # public_header_paths (a real ScopeOrigin-affecting difference) would
        # hash identically and silently share a cached snapshot whose
        # declarations were classified public/private under the wrong set.
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        self._write_header(tmp_path, "b.h", "int g(void);\n")
        old = self._manifest(
            tmp_path,
            "roots: [a.h, b.h]\npublic_header_paths: [a.h]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h, b.h]\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h, b.h]\npublic_header_paths: [a.h, b.h]\n"
            "translation_units:\n  - name: tu_a\n    forced_includes: [a.h, b.h]\n",
        )
        calls: list[list[str]] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(sorted(str(p) for p in (kwargs.get("public_headers") or [])))
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        # Two live dumps (cache miss both times), not a spurious hit.
        assert len(calls) == 2

    def test_project_owned_flip_invalidates_manifest_cache(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF fake content")
        self._write_header(tmp_path, "a.h", "int f(void);\n")
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        old = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "    includes: [{path: ../src, project_owned: false}]\n",
        )
        new = self._manifest(
            tmp_path,
            "roots: [a.h]\ntranslation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "    includes: [{path: ../src, project_owned: true}]\n",
        )
        calls: list[int] = []

        def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
            calls.append(1)
            return _sample_snap(name=f"foo{len(calls)}")

        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=old
        )
        cached_run_dump(
            fake_run_dump, binary, "elf", [], [], "1.0", "c++", dump_manifest=new
        )
        assert len(calls) == 2
