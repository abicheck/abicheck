"""Unit tests for dumper.py internals — mock external tools.

Covers _CastxmlParser methods, _castxml_available, _cache_key,
_parse_vtable_index, _vt_sort_key, _pyelftools_exported_symbols,
and _castxml_dump error paths.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.dumper import (
    _cache_key,
    _cache_path,
    _castxml_available,
    _castxml_dump,
    _CastxmlParser,
    _is_kernel_binary,
    _parse_vtable_index,
    _pyelftools_exported_symbols,
    _resolve_debug_metadata,
    _safe_mtime,
    _safe_size,
    _vt_sort_key,
)
from abicheck.model import Visibility
from abicheck.name_classification import canonicalize_type_name

# ── _castxml_available ──────────────────────────────────────────────────

class TestCastxmlAvailable:
    def test_returns_true_when_castxml_on_path(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        assert _castxml_available() is True

    def test_returns_false_when_castxml_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        assert _castxml_available() is False


# ── _safe_mtime ──────────────────────────────────────────────────────────

class TestSafeMtime:
    def test_returns_mtime_for_existing_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"")
        mtime, is_epoch = _safe_mtime(p)
        assert mtime == p.stat().st_mtime
        assert is_epoch is False

    def test_returns_none_when_path_missing(self, tmp_path):
        mtime, is_epoch = _safe_mtime(tmp_path / "does_not_exist.so")
        assert mtime is None
        assert is_epoch is False

    def test_honours_source_date_epoch_over_real_mtime(self, tmp_path, monkeypatch):
        # Two dumps of identical binary content must stay byte-identical
        # under SOURCE_DATE_EPOCH (reproducible-builds spec) — the real,
        # varying filesystem mtime must not leak into the snapshot.
        p = tmp_path / "lib.so"
        p.write_bytes(b"")
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1000000000")
        mtime, is_epoch = _safe_mtime(p)
        assert mtime == 1000000000.0
        assert mtime != p.stat().st_mtime
        assert is_epoch is True

    def test_zero_source_date_epoch_is_honoured(self, tmp_path, monkeypatch):
        # "0" is a valid (if unusual) SOURCE_DATE_EPOCH — a non-empty string
        # is truthy regardless of the numeric value it parses to, so this
        # must not fall through to the real mtime (CodeRabbit review).
        p = tmp_path / "lib.so"
        p.write_bytes(b"")
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
        mtime, is_epoch = _safe_mtime(p)
        assert mtime == 0.0
        assert is_epoch is True

    def test_invalid_source_date_epoch_falls_back_to_real_mtime(self, tmp_path, monkeypatch):
        p = tmp_path / "lib.so"
        p.write_bytes(b"")
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-number")
        mtime, is_epoch = _safe_mtime(p)
        assert mtime == p.stat().st_mtime
        assert is_epoch is False


class TestSafeSize:
    def test_returns_size_for_existing_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"abicheck")
        assert _safe_size(p) == 8

    def test_returns_none_when_path_missing(self, tmp_path):
        assert _safe_size(tmp_path / "does_not_exist.so") is None

    def test_not_gated_by_source_date_epoch(self, tmp_path, monkeypatch):
        # Unlike _safe_mtime, size is a property of content, not timestamps
        # — two reproducible builds of identical content have identical
        # size by definition, so it needs no SOURCE_DATE_EPOCH gating.
        p = tmp_path / "lib.so"
        p.write_bytes(b"abicheck")
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1000000000")
        assert _safe_size(p) == 8


# ── _parse_vtable_index ─────────────────────────────────────────────────

class TestParseVtableIndex:
    @pytest.mark.parametrize("input_val,expected", [
        (None, None),
        ("3", 3),
        ("-1", -1),
        ("abc", None),
        ("", None),
        ("0", 0),
    ])
    def test_parse_vtable_index(self, input_val, expected):
        assert _parse_vtable_index(input_val) == expected


# ── _vt_sort_key ────────────────────────────────────────────────────────

class TestVtSortKey:
    def test_with_index(self):
        assert _vt_sort_key((5, "foo")) == (0, 5)

    def test_without_index(self):
        assert _vt_sort_key((None, "bar")) == (1, 0)

    def test_ordering(self):
        items = [(None, "z"), (2, "b"), (0, "a")]
        items.sort(key=_vt_sort_key)
        assert [name for _, name in items] == ["a", "b", "z"]


# ── _cache_key ──────────────────────────────────────────────────────────

class TestCacheKey:
    def test_deterministic(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("int x;", encoding="utf-8")
        k1 = _cache_key([h], [], "c++")
        k2 = _cache_key([h], [], "c++")
        assert k1 == k2

    def test_different_compiler_different_key(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("int x;", encoding="utf-8")
        k1 = _cache_key([h], [], "c++")
        k2 = _cache_key([h], [], "cc")
        assert k1 != k2

    def test_with_include_dirs(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("int x;", encoding="utf-8")
        inc = tmp_path / "inc"
        inc.mkdir()
        (inc / "bar.h").write_text("int y;", encoding="utf-8")
        k1 = _cache_key([h], [inc], "c++")
        k2 = _cache_key([h], [], "c++")
        assert k1 != k2

    def test_nonexistent_header_no_crash(self):
        k = _cache_key([Path("/nonexistent/x.h")], [], "c++")
        assert isinstance(k, str) and len(k) == 64

    def test_extra_hash_dirs_fold_in_contents(self, tmp_path):
        # A deferred inferred root rides in gcc_option_tokens, not extra_includes,
        # so its contents are hashed only via extra_hash_dirs. Editing a header
        # under it (umbrella unchanged) must change the key — else a stale AST is
        # reused (Codex review).
        import os
        import time

        umb = tmp_path / "umbrella.h"
        umb.write_text("int a(void);\n", encoding="utf-8")
        root = tmp_path / "pkg"
        root.mkdir()
        detail = root / "detail.h"
        detail.write_text("int b(void);\n", encoding="utf-8")

        k1 = _cache_key([umb], [], "c++", extra_hash_dirs=(root,))
        detail.write_text("int b(int);\n", encoding="utf-8")
        future = time.time() + 10
        os.utime(detail, (future, future))
        k2 = _cache_key([umb], [], "c++", extra_hash_dirs=(root,))
        assert k1 != k2  # the deferred root's contents are folded into the key

    def test_hash_dirs_cover_all_header_suffixes(self, tmp_path):
        # Not just .h/.hpp — an edit to any recognised header suffix under a
        # hashed dir must bust the key (Codex review).
        import os
        import time

        umb = tmp_path / "umbrella.h"
        umb.write_text("int a(void);\n", encoding="utf-8")
        root = tmp_path / "pkg"
        root.mkdir()
        for ext in (".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc", ".inl", ".tcc"):
            detail = root / f"detail{ext}"
            detail.write_text("int b(void);\n", encoding="utf-8")
            k1 = _cache_key([umb], [], "c++", extra_hash_dirs=(root,))
            detail.write_text("int b(int);\n", encoding="utf-8")
            future = time.time() + 10
            os.utime(detail, (future, future))
            k2 = _cache_key([umb], [], "c++", extra_hash_dirs=(root,))
            assert k1 != k2, ext
            detail.unlink()

    def test_without_hash_dir_transitive_edit_is_missed(self, tmp_path):
        # The complementary gap the fix closes: with the root *not* hashed, an
        # edit under it leaves the umbrella-only key unchanged.
        import os
        import time

        umb = tmp_path / "umbrella.h"
        umb.write_text("int a(void);\n", encoding="utf-8")
        root = tmp_path / "pkg"
        root.mkdir()
        detail = root / "detail.h"
        detail.write_text("int b(void);\n", encoding="utf-8")

        k1 = _cache_key([umb], [], "c++")
        detail.write_text("int b(int);\n", encoding="utf-8")
        future = time.time() + 10
        os.utime(detail, (future, future))
        k2 = _cache_key([umb], [], "c++")
        assert k1 == k2  # detail.h not hashed → why extra_hash_dirs is needed


# ── _cache_path ─────────────────────────────────────────────────────────

class TestCachePath:
    def test_returns_path(self):
        p = _cache_path("abc123")
        assert p.name == "abc123.xml"
        assert "abi_check" in str(p)

    def test_posix_honors_xdg_cache_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

        p = _cache_path("abc123", backend="clang")

        assert p == tmp_path / "xdg-cache" / "abi_check" / "clang" / "abc123.json"

    def test_posix_readonly_home_falls_back_to_temp_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        blocked_home = tmp_path / "blocked-home"
        blocked_cache = blocked_home / ".cache" / "abi_check" / "castxml"
        real_mkdir = Path.mkdir

        def fail_blocked_cache(self, *args, **kwargs):
            if self == blocked_cache:
                raise OSError("read-only home")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "home", lambda: blocked_home)
        monkeypatch.setattr(Path, "mkdir", fail_blocked_cache)
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        p = _cache_path("abc123")

        assert p == tmp_path / "abi_check" / "castxml" / "abc123.xml"


# ── _resolve_debug_metadata ─────────────────────────────────────────────

class TestResolveDebugMetadata:
    def test_forced_btf_uses_btf_parser(self, tmp_path, monkeypatch):
        from abicheck.btf_metadata import BtfMetadata

        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _path: BtfMetadata(has_btf=True),
        )

        dwarf_meta, dwarf_adv = _resolve_debug_metadata(tmp_path / "lib.so", "btf")

        assert dwarf_meta.has_dwarf
        assert dwarf_adv is not None

    def test_forced_ctf_uses_ctf_parser(self, tmp_path, monkeypatch):
        from abicheck.ctf_metadata import CtfMetadata

        monkeypatch.setattr(
            "abicheck.ctf_metadata.parse_ctf_metadata",
            lambda _path: CtfMetadata(has_ctf=True),
        )

        dwarf_meta, dwarf_adv = _resolve_debug_metadata(tmp_path / "lib.so", "ctf")

        assert dwarf_meta.has_dwarf
        assert dwarf_adv is not None

    def test_forced_dwarf_uses_dwarf_parser(self, tmp_path, monkeypatch):
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        expected = (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata())
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf", lambda _path, **_kw: expected
        )

        assert _resolve_debug_metadata(tmp_path / "lib.so", "dwarf") is expected

    def test_invalid_debug_format_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid debug_format"):
            _resolve_debug_metadata(tmp_path / "lib.so", "invalid")

    def test_forced_btf_missing_section_still_returns(self, tmp_path, monkeypatch):
        """Forced BTF with no .BTF section logs a warning and returns empty."""
        from abicheck.btf_metadata import BtfMetadata

        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _p: BtfMetadata(has_btf=False),
        )
        dwarf_meta, _ = _resolve_debug_metadata(tmp_path / "lib.so", "btf")
        assert not dwarf_meta.has_dwarf

    def test_forced_ctf_missing_section_still_returns(self, tmp_path, monkeypatch):
        """Forced CTF with no .ctf section logs a warning and returns empty."""
        from abicheck.ctf_metadata import CtfMetadata

        monkeypatch.setattr(
            "abicheck.ctf_metadata.parse_ctf_metadata",
            lambda _p: CtfMetadata(has_ctf=False),
        )
        dwarf_meta, _ = _resolve_debug_metadata(tmp_path / "lib.so", "ctf")
        assert not dwarf_meta.has_dwarf

    def test_auto_kernel_prefers_btf(self, tmp_path, monkeypatch):
        """Auto-detect on a kernel binary with a .BTF section prefers BTF.

        _format_out must report "btf" here even though the *requested*
        debug_format was None (Codex review): a caller checking has_dwarf
        alone, or the raw requested format, can't tell BTF was actually
        used and would otherwise open a second, direct DWARF walk of
        whatever real .debug_info the binary happens to also carry.
        """
        from abicheck.btf_metadata import BtfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: True)
        monkeypatch.setattr("abicheck.btf_metadata.has_btf_section", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _p: BtfMetadata(has_btf=True),
        )
        format_out: list[str | None] = []
        dwarf_meta, _ = _resolve_debug_metadata(
            tmp_path / "vmlinux", None, _format_out=format_out,
        )
        assert dwarf_meta.has_dwarf  # BTF converted to DwarfMetadata
        assert format_out == ["btf"]

    def test_auto_falls_back_to_btf_when_no_dwarf(self, tmp_path, monkeypatch):
        """Userspace binary with no DWARF but a .BTF section falls back to BTF."""
        from abicheck.btf_metadata import BtfMetadata
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: False)
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(), AdvancedDwarfMetadata()),
        )
        monkeypatch.setattr("abicheck.btf_metadata.has_btf_section", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _p: BtfMetadata(has_btf=True),
        )
        format_out: list[str | None] = []
        dwarf_meta, _ = _resolve_debug_metadata(
            tmp_path / "lib.so", None, _format_out=format_out,
        )
        assert dwarf_meta.has_dwarf
        assert format_out == ["btf"]

    def test_auto_falls_back_to_ctf_when_no_dwarf_or_btf(self, tmp_path, monkeypatch):
        """No DWARF and no BTF but a .ctf section falls back to CTF."""
        from abicheck.ctf_metadata import CtfMetadata
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: False)
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(), AdvancedDwarfMetadata()),
        )
        monkeypatch.setattr("abicheck.btf_metadata.has_btf_section", lambda _p: False)
        monkeypatch.setattr("abicheck.ctf_metadata.has_ctf_section", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.ctf_metadata.parse_ctf_metadata",
            lambda _p: CtfMetadata(has_ctf=True),
        )
        format_out: list[str | None] = []
        dwarf_meta, _ = _resolve_debug_metadata(
            tmp_path / "lib.so", None, _format_out=format_out,
        )
        assert dwarf_meta.has_dwarf
        assert format_out == ["ctf"]

    def test_format_out_reports_dwarf_and_none(self, tmp_path, monkeypatch):
        """_format_out reports "dwarf" when real DWARF is found on the
        auto-detect path, and None when no debug info exists at all."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: False)
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata()),
        )
        format_out: list[str | None] = []
        _resolve_debug_metadata(tmp_path / "lib.so", None, _format_out=format_out)
        assert format_out == ["dwarf"]

        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(has_dwarf=False), AdvancedDwarfMetadata()),
        )
        monkeypatch.setattr("abicheck.btf_metadata.has_btf_section", lambda _p: False)
        monkeypatch.setattr("abicheck.ctf_metadata.has_ctf_section", lambda _p: False)
        format_out2: list[str | None] = []
        _resolve_debug_metadata(tmp_path / "lib.so", None, _format_out=format_out2)
        assert format_out2 == [None]

    @pytest.mark.parametrize("btf_section_present", [False, True])
    def test_auto_kernel_btf_absent_falls_through_to_dwarf(
        self, tmp_path, monkeypatch, btf_section_present
    ):
        """Kernel binary with no usable BTF falls through to DWARF — covers both
        the section-absent and the section-present-but-empty fall-through edges."""
        from abicheck.btf_metadata import BtfMetadata
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.btf_metadata.has_btf_section", lambda _p: btf_section_present
        )
        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _p: BtfMetadata(has_btf=False),
        )
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata()),
        )
        dwarf_meta, _ = _resolve_debug_metadata(tmp_path / "vmlinux", None)
        assert dwarf_meta.has_dwarf  # came from DWARF, not BTF

    def test_auto_returns_empty_when_no_debug_info(self, tmp_path, monkeypatch):
        """No usable DWARF/BTF/CTF → empty DwarfMetadata (has_dwarf False).

        BTF and CTF sections are *present but empty* so the ``has_btf``/``has_ctf``
        fall-through edges are exercised, not just the section-absent ones."""
        from abicheck.btf_metadata import BtfMetadata
        from abicheck.ctf_metadata import CtfMetadata
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: False)
        monkeypatch.setattr(
            "abicheck.dwarf_unified.parse_dwarf",
            lambda _p, **_k: (DwarfMetadata(), AdvancedDwarfMetadata()),
        )
        monkeypatch.setattr("abicheck.btf_metadata.has_btf_section", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.btf_metadata.parse_btf_metadata",
            lambda _p: BtfMetadata(has_btf=False),
        )
        monkeypatch.setattr("abicheck.ctf_metadata.has_ctf_section", lambda _p: True)
        monkeypatch.setattr(
            "abicheck.ctf_metadata.parse_ctf_metadata",
            lambda _p: CtfMetadata(has_ctf=False),
        )
        dwarf_meta, _ = _resolve_debug_metadata(tmp_path / "lib.so", None)
        assert not dwarf_meta.has_dwarf


class TestIsKernelBinary:
    def test_vmlinux_name(self, tmp_path):
        assert _is_kernel_binary(tmp_path / "vmlinux") is True

    @pytest.mark.parametrize("name", ["mod.ko", "mod.ko.xz", "mod.ko.zst", "mod.ko.gz"])
    def test_ko_suffixes(self, tmp_path, name):
        assert _is_kernel_binary(tmp_path / name) is True

    def test_plain_so_is_not_kernel(self, tmp_path):
        """A regular .so with no .modinfo section is not a kernel binary."""
        so = tmp_path / "libfoo.so"
        so.write_bytes(b"not an elf")
        assert _is_kernel_binary(so) is False


# ── _pyelftools_exported_symbols ────────────────────────────────────────

class TestPyelftoolsExportedSymbols:
    def test_raises_on_invalid_file(self, tmp_path):
        f = tmp_path / "bad.so"
        f.write_text("not elf", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Failed to parse ELF"):
            _pyelftools_exported_symbols(f)

    def test_raises_on_nonexistent_file(self):
        with pytest.raises(RuntimeError):
            _pyelftools_exported_symbols(Path("/nonexistent/lib.so"))


# ── _castxml_dump ───────────────────────────────────────────────────────

class TestCastxmlDump:
    def test_raises_when_castxml_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(RuntimeError, match="castxml not found"):
            _castxml_dump([Path("test.h")], [])

    def test_cache_hit_returns_cached(self, tmp_path, monkeypatch):
        """When cache file exists, castxml is not invoked."""
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        # Create a valid XML cache file
        cache_xml = tmp_path / "cached.xml"
        root = Element("GCC_XML")
        from xml.etree.ElementTree import ElementTree
        ElementTree(root).write(str(cache_xml))

        # Patch _cache_key/_cache_path to return our cached file
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: cache_xml)

        result = _castxml_dump([Path("h.h")], [])
        assert result.tag == "GCC_XML"

    def test_corrupt_cache_is_discarded(self, tmp_path, monkeypatch):
        """Corrupt cache entry is removed before castxml is re-invoked."""
        import subprocess
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        # Write an unparseable (empty) XML cache file
        cache_xml = tmp_path / "cached.xml"
        cache_xml.write_text("")

        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: cache_xml)

        # Track whether cache was already gone when subprocess.run was called
        cache_existed_at_run = []

        def fake_run(*args, **kwargs):
            cache_existed_at_run.append(cache_xml.exists())
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="castxml stub error"
            )

        monkeypatch.setattr("abicheck.dumper.subprocess.run", fake_run)

        with pytest.raises(RuntimeError, match="castxml failed"):
            _castxml_dump([Path("h.h")], [])

        # subprocess.run must have been called (cache didn't short-circuit)
        assert cache_existed_at_run, "subprocess.run was never called"
        # The corrupt cache must have been deleted BEFORE the re-run
        assert not cache_existed_at_run[0], "Cache was not deleted before castxml re-run"
        # And must still be gone after
        assert not cache_xml.exists()

    def test_castxml_empty_output_file_raises(self, tmp_path, monkeypatch):
        """castxml exits 0 but writes no output file → RuntimeError."""
        import subprocess
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: tmp_path / "c.xml")

        def fake_run(*args, **kwargs):
            # Do NOT write out_xml — simulate castxml exiting 0 with no output
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("abicheck.dumper.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="no output file"):
            _castxml_dump([Path("h.h")], [])

    def test_castxml_invalid_xml_raises(self, tmp_path, monkeypatch):
        """castxml exits 0 but writes invalid XML → RuntimeError."""
        import subprocess
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: tmp_path / "c.xml")

        def fake_run(*args, **kwargs):
            # Write the output file with garbage XML
            for a in args:
                if isinstance(a, list):
                    for part in a:
                        if str(part).endswith(".xml") and "castxml" not in str(part):
                            Path(part).write_text("<<<not xml>>>")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("abicheck.dumper.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="invalid XML|no output file"):
            _castxml_dump([Path("h.h")], [])

    def test_castxml_empty_root_raises(self, tmp_path, monkeypatch):
        """castxml exits 0 but writes XML with empty root → RuntimeError."""
        import subprocess
        from xml.etree.ElementTree import ElementTree
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: tmp_path / "c.xml")

        def fake_run(*args, **kwargs):
            # Write valid XML with empty root (no declarations)
            for a in args:
                if isinstance(a, list):
                    for part in a:
                        if str(part).endswith(".xml") and "castxml" not in str(part):
                            root = Element("CastXML")
                            ElementTree(root).write(str(part))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("abicheck.dumper.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="empty XML|no output file"):
            _castxml_dump([Path("h.h")], [])


# ── _CastxmlParser ─────────────────────────────────────────────────────

def _xml_root(*children: Element) -> Element:
    """Build a GCC_XML root with child elements."""
    root = Element("GCC_XML")
    for c in children:
        root.append(c)
    return root


def _fund_type(id_: str, name: str) -> Element:
    el = Element("FundamentalType", id=id_, name=name)
    return el


class TestCastxmlParserTypeName:
    def test_fundamental_type(self):
        ft = _fund_type("t1", "int")
        root = _xml_root(ft)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t1") == "int"

    def test_pointer_type(self):
        ft = _fund_type("t1", "int")
        ptr = Element("PointerType", id="t2", type="t1")
        root = _xml_root(ft, ptr)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "int*"

    def test_reference_type(self):
        ft = _fund_type("t1", "int")
        ref = Element("ReferenceType", id="t2", type="t1")
        root = _xml_root(ft, ref)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "int&"

    def test_rvalue_reference_type(self):
        ft = _fund_type("t1", "int")
        rref = Element("RValueReferenceType", id="t2", type="t1")
        root = _xml_root(ft, rref)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "int&&"

    def test_cv_qualified_const(self):
        ft = _fund_type("t1", "int")
        cv = Element("CvQualifiedType", id="t2", type="t1")
        cv.set("const", "1")
        root = _xml_root(ft, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "const int"

    def test_cv_qualified_pointee_const_is_prefix(self):
        # `const int *` — PointerType wrapping a CvQualifiedType: the
        # POINTEE is const, not the pointer value. Prefix form is correct.
        ft = _fund_type("t1", "int")
        cv = Element("CvQualifiedType", id="t2", type="t1", const="1")
        ptr = Element("PointerType", id="t3", type="t2")
        root = _xml_root(ft, cv, ptr)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "const int*"

    def test_cv_qualified_pointer_value_const_is_suffix(self):
        # `int * const` — CvQualifiedType directly wrapping a PointerType:
        # the pointer VALUE is const, not what it points to. Must render as
        # a suffix so it's distinguishable from the pointee-const case above
        # (G28 "known, deferred limitation" — both used to collapse to the
        # identical string "const int*").
        ft = _fund_type("t1", "int")
        ptr = Element("PointerType", id="t2", type="t1")
        cv = Element("CvQualifiedType", id="t3", type="t2", const="1")
        root = _xml_root(ft, ptr, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "int* const"

    def test_cv_qualified_pointer_value_volatile_is_suffix(self):
        # Same distinction with `volatile`, the exact ambiguous case from
        # the deferred-limitation writeup: `int * volatile` vs.
        # `volatile int *` both used to render as "volatile int*".
        ft = _fund_type("t1", "int")
        ptr = Element("PointerType", id="t2", type="t1")
        cv = Element("CvQualifiedType", id="t3", type="t2", volatile="1")
        root = _xml_root(ft, ptr, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "int* volatile"

    def test_cv_qualified_pointee_volatile_is_prefix(self):
        ft = _fund_type("t1", "int")
        cv = Element("CvQualifiedType", id="t2", type="t1", volatile="1")
        ptr = Element("PointerType", id="t3", type="t2")
        root = _xml_root(ft, cv, ptr)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "volatile int*"

    def test_cv_qualified_pointee_and_value_both_const_matches_clang(self):
        # `const int * const p` — a const pointer to const int: BOTH the
        # prefix (pointee) and suffix (pointer-value) branches fire on the
        # same declarator. Verified against real clang (`-ast-dump=json`
        # on `const int * const g;`, clang 18): clang spells this
        # "const int *const". canonicalize_type_name (already used to
        # compare cross-producer/cross-backend spellings before any
        # equality check) normalizes both castxml's and clang's spelling
        # to the identical string, confirming this combined case doesn't
        # newly diverge the way the Codex-caught typedef case did.
        ft = _fund_type("t1", "int")
        pointee_cv = Element("CvQualifiedType", id="t2", type="t1", const="1")
        ptr = Element("PointerType", id="t3", type="t2")
        value_cv = Element("CvQualifiedType", id="t4", type="t3", const="1")
        root = _xml_root(ft, pointee_cv, ptr, value_cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t4") == "const int* const"
        assert canonicalize_type_name(p._type_name("t4")) == canonicalize_type_name(
            "const int *const"
        )

    def test_cv_qualified_reference_value_const_is_suffix(self):
        # A CvQualifiedType directly wrapping a ReferenceType is likewise a
        # value-position qualifier (rare/ill-formed to declare directly, but
        # can arise via a reference typedef), not a pointee one.
        ft = _fund_type("t1", "int")
        ref = Element("ReferenceType", id="t2", type="t1")
        cv = Element("CvQualifiedType", id="t3", type="t2", const="1")
        root = _xml_root(ft, ref, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "int& const"

    def test_cv_qualified_through_typedef_stays_prefix(self):
        # `typedef int *IntPtr; IntPtr const p;` — deliberately NOT treated
        # as a suffix-position qualifier, even though IntPtr aliases a
        # pointer: the clang backend takes clang's own `qualType` spelling
        # verbatim, and clang's printer does not relocate a qualifier
        # through a typedef to an implicit, textually-absent `*` either
        # (it spells this "const IntPtr", never "IntPtr const") — following
        # the alias here would newly diverge from clang on this exact case
        # (Codex review). Since "IntPtr" itself carries no visible sigil,
        # there is no real prefix-vs-suffix ambiguity to resolve for it.
        ft = _fund_type("t1", "int")
        ptr = Element("PointerType", id="t2", type="t1")
        td = Element("Typedef", id="t3", name="IntPtr", type="t2")
        cv = Element("CvQualifiedType", id="t4", type="t3", const="1")
        root = _xml_root(ft, ptr, td, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t4") == "const IntPtr"

    def test_cv_qualified_restrict_only_has_no_spelling_effect(self):
        # `int * restrict` — CvQualifiedType with ONLY `restrict` set (no
        # const/volatile): restrict is deliberately excluded from the
        # rendered spelling (zero ABI/mangling effect, tracked separately
        # via Param.is_restrict), so the name is unchanged from the base
        # pointer type — neither prefixed nor suffixed.
        ft = _fund_type("t1", "int")
        ptr = Element("PointerType", id="t2", type="t1")
        cv = Element("CvQualifiedType", id="t3", type="t2", restrict="1")
        root = _xml_root(ft, ptr, cv)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t3") == "int*"

    def test_cv_qualifies_pointer_value_empty_id_is_false(self):
        ft = _fund_type("t1", "int")
        root = _xml_root(ft)
        p = _CastxmlParser(root, set(), set())
        assert p._cv_qualifies_pointer_value("") is False

    def test_cv_qualifies_pointer_value_unresolvable_id_is_false(self):
        ft = _fund_type("t1", "int")
        root = _xml_root(ft)
        p = _CastxmlParser(root, set(), set())
        assert p._cv_qualifies_pointer_value("does-not-exist") is False

    def test_struct_type(self):
        s = Element("Struct", id="t1", name="Point")
        root = _xml_root(s)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t1") == "Point"

    def test_class_type(self):
        c = Element("Class", id="t1", name="Widget")
        root = _xml_root(c)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t1") == "Widget"

    def test_union_type(self):
        u = Element("Union", id="t1", name="Data")
        root = _xml_root(u)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t1") == "Data"

    def test_typedef(self):
        ft = _fund_type("t1", "unsigned long")
        td = Element("Typedef", id="t2", name="size_t", type="t1")
        root = _xml_root(ft, td)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "size_t"

    def test_array_type(self):
        ft = _fund_type("t1", "int")
        arr = Element("ArrayType", id="t2", type="t1", max="9")
        root = _xml_root(ft, arr)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "int[9]"

    def test_array_type_no_max(self):
        ft = _fund_type("t1", "char")
        arr = Element("ArrayType", id="t2", type="t1")
        root = _xml_root(ft, arr)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t2") == "char[]"

    def test_enum_type(self):
        e = Element("Enumeration", id="t1", name="Color")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("t1") == "Color"

    def test_unknown_id_returns_question(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), set())
        assert p._type_name("missing") == "?"

    def test_depth_limit(self):
        # Create a deeply nested pointer chain
        elements = [_fund_type("t0", "int")]
        for i in range(12):
            elements.append(Element("PointerType", id=f"t{i+1}", type=f"t{i}"))
        root = _xml_root(*elements)
        p = _CastxmlParser(root, set(), set())
        result = p._type_name("t12")
        assert "?" in result


class TestCastxmlParserVisibility:
    def test_public_from_dynamic(self):
        root = _xml_root()
        p = _CastxmlParser(root, {"_Z3foov"}, set())
        assert p._visibility("_Z3foov") == Visibility.PUBLIC

    def test_public_from_name(self):
        root = _xml_root()
        p = _CastxmlParser(root, {"foo"}, set())
        assert p._visibility("", "foo") == Visibility.PUBLIC

    def test_elf_only_from_static(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), {"_Z3foov"})
        assert p._visibility("_Z3foov") == Visibility.ELF_ONLY

    def test_elf_only_from_name_static(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), {"foo"})
        assert p._visibility("", "foo") == Visibility.ELF_ONLY

    def test_hidden(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), set())
        assert p._visibility("_Z3foov") == Visibility.HIDDEN


class TestCastxmlParserFunctions:
    def test_parse_simple_function(self):
        ft = _fund_type("t1", "int")
        fn = Element("Function", id="f1", name="add", mangled="_Z3addii", returns="t1")
        SubElement(fn, "Argument", name="a", type="t1")
        SubElement(fn, "Argument", name="b", type="t1")
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, {"_Z3addii"}, set())
        funcs = p.parse_functions()
        assert len(funcs) == 1
        f = funcs[0]
        assert f.name == "add"
        assert f.mangled == "_Z3addii"
        assert f.return_type == "int"
        assert len(f.params) == 2
        assert f.params[0].name == "a"
        assert f.visibility == Visibility.PUBLIC

    def test_c_function_no_mangled(self):
        ft = _fund_type("t1", "int")
        fn = Element("Function", id="f1", name="add", returns="t1")
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, {"add"}, set())
        funcs = p.parse_functions()
        assert funcs[0].mangled == "add"
        assert funcs[0].is_extern_c is True

    def test_virtual_method(self):
        ft = _fund_type("t1", "void")
        m = Element("Method", id="m1", name="render", mangled="_ZN6Widget6renderEv",
                     returns="t1", virtual="1", vtable_index="0")
        root = _xml_root(ft, m)
        p = _CastxmlParser(root, {"_ZN6Widget6renderEv"}, set())
        funcs = p.parse_functions()
        assert funcs[0].is_virtual is True
        assert funcs[0].vtable_index == 0

    def test_constructor(self):
        fn = Element("Constructor", id="c1", name="Widget", mangled="_ZN6WidgetC1Ev")
        root = _xml_root(fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "Widget"

    def test_destructor(self):
        # castxml's real <Destructor name="..."> is the bare CLASS name (no
        # "~"), confirmed against a live castxml dump (Phase 2 parity gate,
        # PR #582) — the parser synthesizes the "~Widget" display name.
        fn = Element("Destructor", id="d1", name="Widget", mangled="_ZN6WidgetD1Ev")
        root = _xml_root(fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "~Widget"

    def test_noexcept_attribute(self):
        ft = _fund_type("t1", "void")
        fn = Element("Function", id="f1", name="safe", mangled="_Z4safev",
                      returns="t1", attributes="noexcept")
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].is_noexcept is True

    def test_static_const_volatile(self):
        ft = _fund_type("t1", "void")
        fn = Element("Method", id="m1", name="process", mangled="_Z7processv",
                      returns="t1", static="1", const="1", volatile="1")
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].is_static is True
        assert funcs[0].is_const is True
        assert funcs[0].is_volatile is True

    def test_pure_virtual(self):
        ft = _fund_type("t1", "void")
        m = Element("Method", id="m1", name="draw", mangled="_ZN5Shape4drawEv",
                     returns="t1", virtual="1", pure_virtual="1")
        root = _xml_root(ft, m)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].is_pure_virtual is True

    def test_deleted_function(self):
        ft = _fund_type("t1", "void")
        fn = Element("Function", id="f1", name="bad", mangled="_Z3badv",
                      returns="t1", deleted="1")
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].is_deleted is True

    def test_skips_unnamed_function(self):
        fn = Element("Function", id="f1", name="", mangled="")
        root = _xml_root(fn)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_functions() == []

    def test_non_function_tags_ignored(self):
        el = Element("Namespace", id="n1", name="std")
        root = _xml_root(el)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_functions() == []


class TestCastxmlParserVariables:
    def test_parse_variable(self):
        ft = _fund_type("t1", "int")
        v = Element("Variable", id="v1", name="global_var", mangled="_Z10global_var", type="t1")
        root = _xml_root(ft, v)
        p = _CastxmlParser(root, {"_Z10global_var"}, set())
        variables = p.parse_variables()
        assert len(variables) == 1
        assert variables[0].name == "global_var"
        assert variables[0].type == "int"
        assert variables[0].visibility == Visibility.PUBLIC

    def test_const_from_attribute(self):
        ft = _fund_type("t1", "int")
        v = Element("Variable", id="v1", name="cv", mangled="_Zcv", type="t1")
        v.set("const", "1")
        root = _xml_root(ft, v)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_variables()[0].is_const is True

    def test_const_from_type_name(self):
        ft = Element("CvQualifiedType", id="t1", type="t2")
        ft.set("const", "1")
        ft2 = _fund_type("t2", "int")
        v = Element("Variable", id="v1", name="cv", mangled="_Zcv", type="t1")
        root = _xml_root(ft, ft2, v)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_variables()[0].is_const is True

    def test_no_mangled_falls_back_to_name(self):
        """C-mode castxml emits Variable without mangled attr; must fall back to name.

        Previously this test asserted parse_variables() == [] (dropping the variable).
        The correct behaviour (PR #94 fix) is to use the plain name as the symbol key,
        mirroring the same fallback in parse_functions().
        """
        ft = _fund_type("t1", "int")
        v = Element("Variable", id="v1", name="local", type="t1")
        root = _xml_root(ft, v)
        p = _CastxmlParser(root, set(), set())
        variables = p.parse_variables()
        assert len(variables) == 1
        assert variables[0].name == "local"
        assert variables[0].mangled == "local"


class TestCastxmlParserTypes:
    def test_parse_struct(self):
        s = Element("Struct", id="s1", name="Point", size="64", align="32")
        SubElement(s, "Field", name="x", type="t1", offset="0")
        SubElement(s, "Field", name="y", type="t1", offset="32")
        ft = _fund_type("t1", "float")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert len(types) == 1
        assert types[0].name == "Point"
        assert types[0].kind == "struct"
        assert types[0].size_bits == 64
        assert types[0].alignment_bits == 32
        assert len(types[0].fields) == 2
        assert types[0].fields[0].name == "x"

    def test_skip_artificial(self):
        s = Element("Struct", id="s1", name="__internal", artificial="1")
        root = _xml_root(s)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_types() == []

    def test_skip_unnamed(self):
        s = Element("Struct", id="s1")
        root = _xml_root(s)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_types() == []

    def test_skip_double_underscore(self):
        s = Element("Struct", id="s1", name="__internal_type")
        root = _xml_root(s)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_types() == []

    def test_opaque_type(self):
        s = Element("Struct", id="s1", name="OpaqueHandle", incomplete="1")
        root = _xml_root(s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert len(types) == 1
        assert types[0].is_opaque is True
        assert types[0].fields == []

    def test_union_type(self):
        u = Element("Union", id="u1", name="Data")
        root = _xml_root(u)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert types[0].is_union is True
        assert types[0].kind == "union"

    def test_class_with_base(self):
        base = Element("Class", id="c1", name="Base")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        root = _xml_root(base, derived)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.bases == ["Base"]

    def test_class_with_virtual_base(self):
        base = Element("Class", id="c1", name="Base")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1", virtual="1")
        root = _xml_root(base, derived)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.virtual_bases == ["Base"]

    def test_bitfield(self):
        ft = _fund_type("t1", "unsigned int")
        s = Element("Struct", id="s1", name="Flags")
        SubElement(s, "Field", name="a", type="t1", offset="0", bits="3")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert types[0].fields[0].is_bitfield is True
        assert types[0].fields[0].bitfield_bits == 3

    def test_non_bitfield(self):
        ft = _fund_type("t1", "int")
        s = Element("Struct", id="s1", name="Plain")
        SubElement(s, "Field", name="x", type="t1", offset="0")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert types[0].fields[0].is_bitfield is False
        assert types[0].fields[0].bitfield_bits is None

    def test_invalid_bitfield_bits(self):
        ft = _fund_type("t1", "int")
        s = Element("Struct", id="s1", name="Bad")
        SubElement(s, "Field", name="x", type="t1", bits="abc")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert types[0].fields[0].is_bitfield is False


class TestCastxmlParserVtable:
    def test_vtable_from_virtual_methods(self):
        cls = Element("Class", id="c1", name="Shape")
        m1 = Element("Method", id="m1", name="draw", mangled="_ZN5Shape4drawEv",
                      virtual="1", vtable_index="0", context="c1")
        m2 = Element("Method", id="m2", name="area", mangled="_ZN5Shape4areaEv",
                      virtual="1", vtable_index="1", context="c1")
        root = _xml_root(cls, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert types[0].vtable == ["_ZN5Shape4drawEv", "_ZN5Shape4areaEv"]

    def test_vtable_inherited(self):
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", vtable_index="0", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        root = _xml_root(base, derived, m1)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert "_ZN4Base3fooEv" in derived_t.vtable

    def test_vtable_override(self):
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", vtable_index="0", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", vtable_index="0", context="c2")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_override_no_vtable_index_uses_overrides_attr(self):
        """case185: some castxml/Clang builds never emit ``vtable_index`` at
        all. Without a fallback signal, a same-signature override used to be
        appended as a spurious extra slot instead of replacing its base's
        entry (a false vtable-growth positive). castxml's ``overrides``
        attribute is that fallback: it must still collapse to one slot.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="paint", mangled="_ZN4Base5paintEi",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="paint", mangled="_ZN7Derived5paintEi",
                      virtual="1", context="c2", overrides="m1")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived5paintEi"]

    def test_vtable_no_vtable_index_different_signature_adds_slot(self):
        """The negative twin documented in case185's README: a same-named but
        different-signature virtual does NOT reuse a slot (no ``overrides``
        attribute links it to the base method), so it must still grow the
        vtable by one entry rather than being collapsed away.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="paint", mangled="_ZN4Base5paintEi",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="paint", mangled="_ZN7Derived5paintEd",
                      virtual="1", context="c2")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN4Base5paintEi", "_ZN7Derived5paintEd"]

    def test_vtable_override_chain_resolves_to_root_slot(self):
        """A 3-level override chain (Base -> Mid -> Derived), each level
        missing ``vtable_index`` and pointing ``overrides`` at its immediate
        (not root) base declaration, must still collapse to a single slot.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        mid = Element("Class", id="c2", name="Mid")
        SubElement(mid, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN3Mid3fooEv",
                      virtual="1", context="c2", overrides="m1")
        derived = Element("Class", id="c3", name="Derived")
        SubElement(derived, "Base", type="c2")
        m3 = Element("Method", id="m3", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c3", overrides="m2")
        root = _xml_root(base, mid, derived, m1, m2, m3)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_override_chain_mixed_indexed_and_unindexed(self):
        """Base and Mid carry ``vtable_index`` (slot 0), but Mid's override
        drops the index and Derived overrides Mid via ``overrides`` only.
        Derived's unindexed override must still resolve back to Base/Mid's
        int-keyed slot 0, not append a spurious extra entry -- a downstream
        override in a mixed indexed/unindexed chain has no other signal
        tying it to the base's slot once the index disappears partway
        through the hierarchy.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", vtable_index="0", context="c1")
        mid = Element("Class", id="c2", name="Mid")
        SubElement(mid, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN3Mid3fooEv",
                      virtual="1", vtable_index="0", context="c2")
        derived = Element("Class", id="c3", name="Derived")
        SubElement(derived, "Base", type="c2")
        m3 = Element("Method", id="m3", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c3", overrides="m2")
        root = _xml_root(base, mid, derived, m1, m2, m3)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_unindexed_override_of_indexed_slot_keeps_position(self):
        """Base declares two indexed virtuals (slots 0 and 1). Derived
        overrides slot 0 via ``overrides`` only, without its own
        ``vtable_index``. The reused slot must sort at position 0 (ahead of
        slot 1), not fall to the unindexed tail -- landing after slot 1 would
        read as a spurious vtable reorder even though nothing moved.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", vtable_index="0", context="c1")
        m2 = Element("Method", id="m2", name="bar", mangled="_ZN4Base3barEv",
                      virtual="1", vtable_index="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m3 = Element("Method", id="m3", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c2", overrides="m1")
        root = _xml_root(base, derived, m1, m2, m3)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv", "_ZN4Base3barEv"]

    def test_vtable_indexed_override_of_unindexed_base_collapses_to_one_slot(self):
        """The reverse mixed-index direction: Base has NO vtable_index, but
        Derived's override of it DOES carry one (plus ``overrides``). The
        override must still collapse onto Base's (string-id-keyed) slot
        rather than opening a second, int-keyed slot alongside it.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", vtable_index="0", context="c2", overrides="m1")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_indexed_override_of_unindexed_sibling_preserves_order(self):
        """Base has two UNINDEXED virtuals, foo then bar. Derived overrides
        bar only, and that override happens to carry its own vtable_index
        ("1"). That local index has no verified relationship to foo's
        (unknown) true position, so it must not be trusted to sort Derived's
        override ahead of foo -- the reconstructed vtable must preserve
        discovery order (foo, then the overridden bar), not read as
        ``[Derived::bar, Base::foo]`` (a spurious reorder).
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        m2 = Element("Method", id="m2", name="bar", mangled="_ZN4Base3barEv",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m3 = Element("Method", id="m3", name="bar", mangled="_ZN7Derived3barEv",
                      virtual="1", vtable_index="1", context="c2", overrides="m2")
        root = _xml_root(base, derived, m1, m2, m3)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN4Base3fooEv", "_ZN7Derived3barEv"]

    def test_vtable_multi_id_overrides_resolves_to_known_slot(self):
        """castxml can list more than one overridden declaration as a
        whitespace-separated ``overrides`` id list (e.g. a single override
        simultaneously covering more than one base-class branch in multiple
        inheritance). An exact-string lookup of that composite value never
        matches any registered slot, so without splitting it, the override
        would open a phantom extra slot keyed by the literal multi-id string
        instead of collapsing onto the real inherited slot any of its ids
        names.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c2", overrides="m1 nonexistent")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_multi_id_overrides_replaces_every_resolved_slot(self):
        """Non-virtual multiple inheritance: Derived : Base1, Base2, each
        declaring its own unrelated foo(). A single final overrider covers
        both (``overrides="m1 m2"``, both already-resolvable slots). Each
        resolved slot is a genuinely distinct position in the object's real
        vtable-group layout, so both must survive -- neither collapsed away
        (which would under-report the vtable's true size) nor left with
        stale pre-override content -- each showing the override's mangled
        name.
        """
        base1 = Element("Class", id="c1", name="Base1")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN5Base13fooEv",
                      virtual="1", context="c1")
        base2 = Element("Class", id="c2", name="Base2")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN5Base23fooEv",
                      virtual="1", context="c2")
        derived = Element("Class", id="c3", name="Derived")
        SubElement(derived, "Base", type="c1")
        SubElement(derived, "Base", type="c2")
        m3 = Element("Method", id="m3", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c3", overrides="m1 m2")
        root = _xml_root(base1, base2, derived, m1, m2, m3)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv", "_ZN7Derived3fooEv"]

    def test_vtable_multi_id_override_chain_propagates_to_every_slot(self):
        """Base1/Base2 -> Derived::foo (overrides both) -> MoreDerived::foo
        (overrides Derived::foo by ITS id alone). Derived's own multi-slot
        override must record both underlying slots against its own id, so
        MoreDerived's single-id ``overrides="m3"`` still resolves to (and
        updates) both positions -- not just the first one Derived happened
        to register as its primary key.
        """
        base1 = Element("Class", id="c1", name="Base1")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN5Base13fooEv",
                      virtual="1", context="c1")
        base2 = Element("Class", id="c2", name="Base2")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN5Base23fooEv",
                      virtual="1", context="c2")
        derived = Element("Class", id="c3", name="Derived")
        SubElement(derived, "Base", type="c1")
        SubElement(derived, "Base", type="c2")
        m3 = Element("Method", id="m3", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c3", overrides="m1 m2")
        more_derived = Element("Class", id="c4", name="MoreDerived")
        SubElement(more_derived, "Base", type="c3")
        m4 = Element("Method", id="m4", name="foo", mangled="_ZN11MoreDerived3fooEv",
                      virtual="1", context="c4", overrides="m3")
        root = _xml_root(base1, base2, derived, more_derived, m1, m2, m3, m4)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        more_derived_t = next(t for t in types if t.name == "MoreDerived")
        assert more_derived_t.vtable == ["_ZN11MoreDerived3fooEv", "_ZN11MoreDerived3fooEv"]

    def test_vtable_overrides_all_ids_unresolvable_falls_back_to_composite_key(self):
        """An ``overrides`` id list where none of the listed ids resolve to a
        known slot (e.g. a malformed/truncated castxml dump) must still
        register the method under *some* key rather than being dropped --
        falls back to the raw composite ``overrides`` string, same as the
        pre-multi-id behavior for a single unresolvable id.
        """
        derived = Element("Class", id="c1", name="Derived")
        m1 = Element(
            "Method",
            id="m1",
            name="foo",
            mangled="_ZN7Derived3fooEv",
            virtual="1",
            context="c1",
            overrides="nonexistent1 nonexistent2",
        )
        root = _xml_root(derived, m1)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_multi_id_overrides_deduplicates_repeated_id(self):
        """A repeated id in the whitespace-separated ``overrides`` list (or
        two ids that resolve to the same slot) must not register that slot
        twice as an "extra" -- the dedup check against already-resolved
        keys must actually skip the repeat rather than double-applying it.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        derived = Element("Class", id="c2", name="Derived")
        SubElement(derived, "Base", type="c1")
        m2 = Element("Method", id="m2", name="foo", mangled="_ZN7Derived3fooEv",
                      virtual="1", context="c2", overrides="m1 m1")
        root = _xml_root(base, derived, m1, m2)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN7Derived3fooEv"]

    def test_vtable_diamond_inheritance_does_not_infinite_loop(self):
        """Diamond inheritance (Derived : Left, Right; both : Base) revisits
        Base through two paths. The `seen` guard must return {} on the
        second visit rather than re-walking (or infinite-looping on a cycle);
        Base's own slot still comes through once, via whichever path visits
        it first.
        """
        base = Element("Class", id="c1", name="Base")
        m1 = Element("Method", id="m1", name="foo", mangled="_ZN4Base3fooEv",
                      virtual="1", context="c1")
        left = Element("Class", id="c2", name="Left")
        SubElement(left, "Base", type="c1")
        right = Element("Class", id="c3", name="Right")
        SubElement(right, "Base", type="c1")
        derived = Element("Class", id="c4", name="Derived")
        SubElement(derived, "Base", type="c2")
        SubElement(derived, "Base", type="c3")
        root = _xml_root(base, left, right, derived, m1)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == ["_ZN4Base3fooEv"]

    def test_vtable_dangling_base_type_reference_is_skipped(self):
        """A <Base type="..."> pointing at an id with no matching element
        (a malformed/truncated castxml dump) must be skipped, not crash."""
        derived = Element("Class", id="c1", name="Derived")
        SubElement(derived, "Base", type="does-not-exist")
        root = _xml_root(derived)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        derived_t = next(t for t in types if t.name == "Derived")
        assert derived_t.vtable == []

    def test_collect_virtual_methods_unresolvable_cid_returns_empty(self):
        """_collect_virtual_methods() called directly with a class id that
        isn't in the id map (defensive guard -- unreachable through the
        normal _resolve()-gated recursive call, since _resolve already
        filters out dangling Base references before recursing)."""
        root = _xml_root(Element("Class", id="c1", name="C"))
        p = _CastxmlParser(root, set(), set())
        assert p._collect_virtual_methods("does-not-exist") == {}

    def test_vtable_method_without_id_attribute_is_not_registered_as_slot_root(self):
        """A virtual method element missing its own `id` attribute (malformed
        castxml output) must still contribute its slot -- just without being
        recorded in `_vtable_slot_root`, since there's no id to key it by."""
        cls = Element("Class", id="c1", name="C")
        method = Element("Method", name="foo", mangled="_ZN1C3fooEv",
                          virtual="1", context="c1")
        root = _xml_root(cls, method)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        c_t = next(t for t in types if t.name == "C")
        assert c_t.vtable == ["_ZN1C3fooEv"]
        assert p._vtable_slot_root == {}


class TestCastxmlParserEnums:
    def test_parse_enum(self):
        e = Element("Enumeration", id="e1", name="Color")
        SubElement(e, "EnumValue", name="RED", init="0")
        SubElement(e, "EnumValue", name="GREEN", init="1")
        SubElement(e, "EnumValue", name="BLUE", init="2")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert len(enums[0].members) == 3
        assert enums[0].members[0].name == "RED"
        assert enums[0].members[0].value == 0

    def test_skip_unnamed_enum(self):
        e = Element("Enumeration", id="e1", name="")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_enums() == []

    def test_skip_internal_enum(self):
        e = Element("Enumeration", id="e1", name="__internal")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_enums() == []

    def test_invalid_init_defaults_zero(self):
        e = Element("Enumeration", id="e1", name="E")
        SubElement(e, "EnumValue", name="V", init="bad")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert enums[0].members[0].value == 0


class TestCastxmlParserTypedefs:
    def test_parse_typedef(self):
        ft = _fund_type("t1", "unsigned long")
        td = Element("Typedef", id="t2", name="size_t", type="t1")
        root = _xml_root(ft, td)
        p = _CastxmlParser(root, set(), set())
        typedefs = p.parse_typedefs()
        assert typedefs == {"size_t": "unsigned long"}

    def test_typedef_chain_flattened(self):
        ft = _fund_type("t1", "int")
        td1 = Element("Typedef", id="t2", name="int32_t", type="t1")
        td2 = Element("Typedef", id="t3", name="my_int", type="t2")
        root = _xml_root(ft, td1, td2)
        p = _CastxmlParser(root, set(), set())
        typedefs = p.parse_typedefs()
        assert typedefs["my_int"] == "int"

    def test_skip_unnamed_typedef(self):
        ft = _fund_type("t1", "int")
        td = Element("Typedef", id="t2", type="t1")
        root = _xml_root(ft, td)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_typedefs() == {}


def test_castxml_unmangled_overloaded_constructors_get_distinct_snapshot_keys():
    root = Element("GCC_XML")
    root.append(Element("Class", id="cls", name="Widget"))
    root.append(Element("FundamentalType", id="t_int", name="int"))
    root.append(Element("FundamentalType", id="t_double", name="double"))
    c1 = Element("Constructor", id="c1", name="Widget", context="cls")
    c1.append(Element("Argument", type="t_int"))
    c2 = Element("Constructor", id="c2", name="Widget", context="cls")
    c2.append(Element("Argument", type="t_double"))
    root.extend([c1, c2])

    funcs = _CastxmlParser(root, set(), set()).parse_functions()
    mangled = {f.mangled for f in funcs}

    assert "__abicheck_ctor__Widget(int)" in mangled
    assert "__abicheck_ctor__Widget(double)" in mangled
    assert len(mangled) == 2
