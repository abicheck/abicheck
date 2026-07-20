"""Tests for snapshot caching layer (5c)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.snapshot_cache import (
    _cache_key,
    _get_cache_dir,
    _hash_include_dir_headers,
    _safe_mtime,
    lookup,
    store,
)


def _sample_snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="foo_init",
                mangled="_Z8foo_initv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


class TestCacheKey:
    def test_deterministic(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        hdr = tmp_path / "foo.h"
        hdr.write_text("#pragma once\n")

        key1 = _cache_key(binary, [hdr], [], "1.0", "c++")
        key2 = _cache_key(binary, [hdr], [], "1.0", "c++")
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex

    def test_different_version_different_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        key1 = _cache_key(binary, [], [], "1.0", "c++")
        key2 = _cache_key(binary, [], [], "2.0", "c++")
        assert key1 != key2

    def test_different_content_different_key(self, tmp_path):
        b1 = tmp_path / "lib1.so"
        b1.write_bytes(b"content A")
        b2 = tmp_path / "lib2.so"
        b2.write_bytes(b"content B")

        key1 = _cache_key(b1, [], [], "1.0", "c++")
        key2 = _cache_key(b2, [], [], "1.0", "c++")
        assert key1 != key2

    def test_missing_binary_returns_empty(self, tmp_path):
        key = _cache_key(tmp_path / "nonexistent.so", [], [], "1.0", "c++")
        assert key == ""

    def test_stale_pre_header_graph_cache_version_invalidated(
        self, tmp_path, monkeypatch
    ):
        """A snapshot cached under the pre-G31 cache version ("2", before the
        L2 header graph became unconditional) must not be served as a hit for
        the identical binary/headers/includes/version/lang/extra under the
        current version — otherwise a warm cache from before this upgrade
        would silently replay a no-graph snapshot forever (Codex review on
        PR #612, service_dump_cache._dump_is_cacheable now allows the plain
        cacheable shape onto this same cache)."""
        import abicheck.snapshot_cache as snapshot_cache_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        monkeypatch.setattr(snapshot_cache_mod, "_SNAPSHOT_CACHE_VERSION", "2")
        old_key = _cache_key(binary, [], [], "1.0", "c++")

        monkeypatch.undo()
        current_key = _cache_key(binary, [], [], "1.0", "c++")

        assert old_key != current_key

    def test_different_lang_different_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        key_cpp = _cache_key(binary, [], [], "1.0", "c++")
        key_c = _cache_key(binary, [], [], "1.0", "c")
        assert key_cpp != key_c

    def test_different_includes_different_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        inc1 = tmp_path / "inc1"
        inc1.mkdir()
        inc2 = tmp_path / "inc2"
        inc2.mkdir()
        key1 = _cache_key(binary, [], [inc1], "1.0", "c++")
        key2 = _cache_key(binary, [], [inc2], "1.0", "c++")
        assert key1 != key2

    def test_transitive_header_under_include_dir_changes_key(self, tmp_path):
        """A header only reachable transitively through an ``-I`` directory
        (never itself passed as an explicit ``headers`` entry) must still
        invalidate the cache when it changes -- otherwise a change to e.g.
        ``inc/detail.h`` pulled in by a public header would silently serve
        a stale snapshot (and stale header-graph data) forever (Codex review
        on PR #612: before G31 Phase A this was moot because a header-graph
        dump was always uncacheable; it is not moot now that it's cacheable
        by default)."""
        import os
        import time

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        inc = tmp_path / "inc"
        inc.mkdir()
        transitive_hdr = inc / "detail.h"
        transitive_hdr.write_text("struct detail { int x; };\n")

        key1 = _cache_key(binary, [], [inc], "1.0", "c++")

        os.utime(transitive_hdr, (time.time() + 10, time.time() + 10))
        key2 = _cache_key(binary, [], [inc], "1.0", "c++")

        assert key1 != key2

    def test_new_transitive_header_under_include_dir_changes_key(self, tmp_path):
        """Adding a new header under an include directory also invalidates
        the cache, not just editing an existing one."""
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        inc = tmp_path / "inc"
        inc.mkdir()

        key1 = _cache_key(binary, [], [inc], "1.0", "c++")
        (inc / "new.h").write_text("struct added {};\n")
        key2 = _cache_key(binary, [], [inc], "1.0", "c++")

        assert key1 != key2

    @pytest.mark.parametrize("suffix", [".tpp", ".inc", ".inl", ".tcc"])
    def test_template_implementation_suffixes_bust_the_key(self, tmp_path, suffix):
        """Codex review: an earlier ad hoc suffix set here was missing
        ``.tpp``/``.inc`` (present in the shared ``header_utils.
        CACHE_HEADER_SUFFIXES`` other cache-invalidation code already uses).
        Editing a template-implementation file pulled in by a parsed header
        must invalidate the cache just like editing a plain ``.h`` does."""
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        inc = tmp_path / "inc"
        inc.mkdir()
        impl = inc / f"detail{suffix}"
        impl.write_text("// template body\n")

        key1 = _cache_key(binary, [], [inc], "1.0", "c++")

        import os
        import time

        os.utime(impl, (time.time() + 10, time.time() + 10))
        key2 = _cache_key(binary, [], [inc], "1.0", "c++")

        assert key1 != key2

    def test_edit_inside_header_directory_input_changes_key(self, tmp_path):
        """A `headers` entry can itself be a directory (`-H include/`); the
        header-AST parse expands it to every header file underneath, so
        editing one of those files must invalidate the cache even though the
        directory's own mtime doesn't necessarily change on a same-size
        in-place edit (Codex review on PR #612 -- the same transitive-header
        staleness class already fixed for `includes` directories above)."""
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        nested = hdr_dir / "api.h"
        nested.write_text("struct api { int x; };\n")

        key1 = _cache_key(binary, [hdr_dir], [], "1.0", "c++")

        import os
        import time

        os.utime(nested, (time.time() + 10, time.time() + 10))
        key2 = _cache_key(binary, [hdr_dir], [], "1.0", "c++")

        assert key1 != key2

    def test_hash_include_dir_headers_unreadable_dir_is_a_noop(
        self, tmp_path, monkeypatch
    ):
        """An include directory that raises OSError while being walked (e.g.
        a permission error) degrades to hashing nothing extra, matching this
        module's "any read problem is cache-safe, never a crash" stance --
        it must not propagate and abort cache-key computation."""
        import hashlib

        inc = tmp_path / "inc"
        inc.mkdir()

        def _boom(self, pattern):
            raise OSError("permission denied")
            yield  # pragma: no cover - makes this a generator function

        monkeypatch.setattr(Path, "rglob", _boom)

        h = hashlib.sha256()
        _hash_include_dir_headers(h, inc)  # must not raise
        assert h.digest() == hashlib.sha256().digest()  # nothing was hashed

    def test_hash_include_dir_headers_stat_failure_hashes_missing_marker(
        self, tmp_path, monkeypatch
    ):
        """A header that disappears (or otherwise fails to stat) between being
        listed and being hashed still contributes a deterministic MISSING
        marker rather than crashing or silently skipping the entry. Only the
        specific file's ``stat()`` is made to fail -- ``Path.rglob``'s own
        internal directory traversal also calls ``stat()`` under the hood, so
        patching it unconditionally would break listing itself rather than
        exercising the explicit per-entry ``except OSError`` branch this test
        targets."""
        import hashlib

        inc = tmp_path / "inc"
        inc.mkdir()
        gone = inc / "gone.h"
        gone.write_text("struct gone {};\n")

        real_stat = Path.stat

        def _selective_boom(self, *args, **kwargs):
            if self == gone:
                raise OSError("vanished")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _selective_boom)

        h1 = hashlib.sha256()
        _hash_include_dir_headers(h1, inc)
        h2 = hashlib.sha256()
        _hash_include_dir_headers(h2, inc)
        assert h1.digest() == h2.digest()  # deterministic MISSING marker
        assert h1.digest() != hashlib.sha256().digest()  # path was still hashed

    def test_header_mtime_change_different_key(self, tmp_path):
        import time

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        hdr = tmp_path / "foo.h"
        hdr.write_text("#pragma once\n")
        key1 = _cache_key(binary, [hdr], [], "1.0", "c++")
        # Change header mtime
        import os

        os.utime(hdr, (time.time() + 10, time.time() + 10))
        key2 = _cache_key(binary, [hdr], [], "1.0", "c++")
        assert key1 != key2


class TestLookupStore:
    def test_miss_returns_none(self, tmp_path, monkeypatch):
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", tmp_path / "empty_cache")
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"content")
        result = lookup(binary, [], [], "1.0", "c++")
        assert result is None

    def test_store_and_lookup_roundtrip(self, tmp_path, monkeypatch):
        # Use a temp cache dir
        cache_dir = tmp_path / "cache"
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        snap = _sample_snap()
        store(snap, binary, [], [], "1.0", "c++")
        assert cache_dir.exists()
        assert len(list(cache_dir.glob("*.json"))) == 1

        result = lookup(binary, [], [], "1.0", "c++")
        assert result is not None
        assert result.library == "libfoo.so.1"
        assert len(result.functions) == 1

    def test_invalidation_on_content_change(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"version 1")

        snap = _sample_snap()
        store(snap, binary, [], [], "1.0", "c++")

        # Modify binary content
        binary.write_bytes(b"version 2")
        result = lookup(binary, [], [], "1.0", "c++")
        assert result is None  # cache miss — binary changed

    def test_missing_binary_no_op(self, tmp_path, monkeypatch):
        """store/lookup with missing binary should be no-ops, not crash."""
        cache_dir = tmp_path / "cache"
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        snap = _sample_snap()
        store(snap, tmp_path / "gone.so", [], [], "1.0", "c++")
        assert not cache_dir.exists()  # nothing stored
        result = lookup(tmp_path / "gone.so", [], [], "1.0", "c++")
        assert result is None

    def test_corrupted_cache_returns_none(self, tmp_path, monkeypatch):
        """Corrupted JSON in cache should be treated as a miss."""
        cache_dir = tmp_path / "cache"
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        # Store valid entry first
        snap = _sample_snap()
        store(snap, binary, [], [], "1.0", "c++")

        # Corrupt the cached file
        for f in cache_dir.glob("*.json"):
            f.write_text("{ invalid json")

        result = lookup(binary, [], [], "1.0", "c++")
        assert result is None


class TestEviction:
    def test_evicts_oldest_entries(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        import abicheck.snapshot_cache as sc

        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)
        monkeypatch.setattr(sc, "MAX_ENTRIES", 3)

        snap = _sample_snap()
        # Create 5 cache entries
        for i in range(5):
            binary = tmp_path / f"lib{i}.so"
            binary.write_bytes(f"content {i}".encode())
            store(snap, binary, [], [], "1.0", "c++")

        # Should have at most MAX_ENTRIES files
        entries = list(cache_dir.glob("*.json"))
        assert len(entries) <= 3


class TestGetCacheDir:
    def test_fallback_on_runtime_error(self, monkeypatch):
        """When Path.home() raises RuntimeError, fall back to tempdir."""
        import tempfile

        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            result = _get_cache_dir()
        assert str(result).startswith(tempfile.gettempdir())
        assert result.name == "snapshots"

    def test_xdg_cache_home_used(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        result = _get_cache_dir()
        assert str(result).startswith(str(tmp_path / "xdg"))


class TestSafeMtime:
    def test_returns_mtime_for_existing_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text("{}")
        mtime = _safe_mtime(f)
        assert mtime > 0

    def test_returns_zero_for_missing_file(self, tmp_path):
        mtime = _safe_mtime(tmp_path / "nonexistent.json")
        assert mtime == 0.0


class TestStoreErrorPaths:
    def test_store_oserror_on_mkdir(self, tmp_path, monkeypatch):
        """Store gracefully handles OSError on mkdir."""
        import abicheck.snapshot_cache as sc

        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        def _raise_oserror(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "mkdir", _raise_oserror)
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        snap = _sample_snap()
        store(snap, binary, [], [], "1.0", "c++")  # should not raise
        # mkdir failed, so nothing should have been written to the cache dir.
        assert not cache_dir.exists()

    def test_store_serialization_error_does_not_raise(self, tmp_path, monkeypatch):
        """A non-OSError failure while serializing (e.g. an unexpected
        non-JSON-serializable field) must not propagate: caching sits on top
        of a dump that already succeeded, so a write-time failure here can
        only ever cost a cache miss next time, never break the caller."""
        import abicheck.snapshot_cache as sc

        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        def _raise_type_error(*args, **kwargs):
            raise TypeError("Object of type MagicMock is not JSON serializable")

        monkeypatch.setattr(
            "abicheck.serialization.snapshot_to_json", _raise_type_error
        )
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        snap = _sample_snap()
        store(snap, binary, [], [], "1.0", "c++")  # should not raise
        # Serialization failed before the temp file could be renamed into
        # place, so no (partial/corrupt) cache entry should be left behind.
        assert list(cache_dir.glob("*.json")) == []


class TestExtraKeyMaterial:
    def test_different_extra_different_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        key1 = _cache_key(binary, [], [], "1.0", "c++", extra="elf|auto")
        key2 = _cache_key(binary, [], [], "1.0", "c++", extra="pe|auto")
        assert key1 != key2

    def test_same_extra_same_key(self, tmp_path):
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")

        key1 = _cache_key(binary, [], [], "1.0", "c++", extra="elf|auto")
        key2 = _cache_key(binary, [], [], "1.0", "c++", extra="elf|auto")
        assert key1 == key2

    def test_store_lookup_roundtrip_with_extra(self, tmp_path, monkeypatch):
        import abicheck.snapshot_cache as sc

        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(sc, "_CACHE_DIR", cache_dir)

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"ELF content")
        snap = _sample_snap()

        store(snap, binary, [], [], "1.0", "c++", extra="elf|auto")
        # A lookup with different `extra` material is a distinct cache entry
        # (e.g. a different header-AST backend or binary format) and misses.
        assert lookup(binary, [], [], "1.0", "c++", extra="pe|auto") is None
        result = lookup(binary, [], [], "1.0", "c++", extra="elf|auto")
        assert result is not None
        assert result.library == "libfoo.so.1"
