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
    _parse_vtable_index,
    _pyelftools_exported_symbols,
    _resolve_debug_metadata,
    _vt_sort_key,
)
from abicheck.model import Visibility

# ── _castxml_available ──────────────────────────────────────────────────

class TestCastxmlAvailable:
    def test_returns_true_when_castxml_on_path(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/castxml")
        assert _castxml_available() is True

    def test_returns_false_when_castxml_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        assert _castxml_available() is False


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
        monkeypatch.setattr(Path, "home", lambda: Path("/proc"))
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
        monkeypatch.setattr("abicheck.dwarf_unified.parse_dwarf", lambda _path: expected)

        assert _resolve_debug_metadata(tmp_path / "lib.so", "dwarf") is expected

    def test_invalid_debug_format_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid debug_format"):
            _resolve_debug_metadata(tmp_path / "lib.so", "invalid")


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
        fn = Element("Destructor", id="d1", name="~Widget", mangled="_ZN6WidgetD1Ev")
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
