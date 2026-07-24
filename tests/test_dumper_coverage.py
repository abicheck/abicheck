"""Coverage tests for dumper.py — target 80%+ coverage.

Covers _castxml_dump internal branches (gcc_prefix, gcc_path, sysroot,
nostdinc, gcc_options, lang, MSVC detection, castxml failure),
dump() elf_meta symbol filtering and lang parameter,
_CastxmlParser edge cases (builtin elements, anonymous fields,
members-attribute parsing, _pointer_depth, _underlying_type_name).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck import deadline
from abicheck.dumper import (
    _cache_key,
    _castxml_dump,
    _CastxmlParser,
    _resolve_force_cpp,
    dump,
)
from abicheck.elf_metadata import ElfMetadata

# ── _castxml_dump internal branches ────────────────────────────────────


def test_cheap_debug_presence_honors_forced_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "vmlinux"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: True)
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: pytest.fail("forced BTF must not probe DWARF first"),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="btf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_honors_forced_dwarf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=True),
        ),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="dwarf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_honors_forced_ctf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: True)

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="ctf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_rejects_unknown_format(tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")

    with pytest.raises(ValueError, match="Invalid debug_format"):
        cheap_debug_presence_metadata(so_path, debug_format="split-dwarf")


def test_cheap_debug_presence_auto_prefers_kernel_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "vmlinux"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._is_kernel_binary", lambda _p: True)
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: True)
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: pytest.fail("kernel BTF should be selected before DWARF"),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_prefers_dwarf_when_present(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._is_kernel_binary", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=True),
        ),
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: pytest.fail("DWARF wins")
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_falls_back_to_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._is_kernel_binary", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: True)
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: pytest.fail("BTF wins")
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_falls_back_to_ctf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._is_kernel_binary", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: False)
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: True)

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_returns_empty_when_no_debug(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr("abicheck.dwarf_presence._is_kernel_binary", lambda _p: False)
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: False)
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: False)

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is False
    assert dwarf_adv.has_dwarf is False


def test_cheap_debug_presence_helpers_treat_probe_errors_as_absent(
    monkeypatch, tmp_path
):
    import abicheck.btf_metadata as btf_metadata
    import abicheck.ctf_metadata as ctf_metadata
    from abicheck import dwarf_presence

    so_path = tmp_path / "not-elf.so"
    so_path.write_bytes(b"not an elf")

    monkeypatch.setattr(
        btf_metadata,
        "has_btf_section",
        lambda _p: (_ for _ in ()).throw(RuntimeError("btf probe failed")),
    )
    monkeypatch.setattr(
        ctf_metadata,
        "has_ctf_section",
        lambda _p: (_ for _ in ()).throw(RuntimeError("ctf probe failed")),
    )

    assert dwarf_presence._has_btf(so_path) is False
    assert dwarf_presence._has_ctf(so_path) is False
    assert dwarf_presence._is_kernel_binary(so_path) is False


class TestCastxmlDumpBranches:
    def _setup(self, monkeypatch, tmp_path):
        """Common setup: castxml available, cache miss."""
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "test_key")

        # Cache path that doesn't exist yet
        cache_file = tmp_path / "cache.xml"
        monkeypatch.setattr("abicheck.dumper._cache_path", lambda k: cache_file)

        header = tmp_path / "test.h"
        header.write_text("int foo();", encoding="utf-8")
        return header

    def _make_spy(self, monkeypatch):
        """Create a subprocess.run spy that writes valid XML and captures cmd."""
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    # Write minimal non-empty castxml XML so the empty-root guard passes.
                    Path(cmd[i + 1]).write_text(
                        '<?xml version="1.0"?>'
                        '<GCC_XML><Namespace id="_1" name="::" context="_1"/></GCC_XML>',
                        encoding="utf-8",
                    )
                    break
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deadline, "run_bounded", fake_run)
        return captured_cmd

    def test_gcc_path_used(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        result = _castxml_dump([header], [], gcc_path="/opt/cross/bin/g++")
        assert result.tag == "GCC_XML"
        assert "/opt/cross/bin/g++" in captured

    def test_gcc_prefix_cpp(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], compiler="c++", gcc_prefix="aarch64-linux-gnu-")
        assert "aarch64-linux-gnu-g++" in captured

    def test_gcc_prefix_c(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], compiler="cc", gcc_prefix="arm-none-eabi-")
        assert "arm-none-eabi-gcc" in captured

    def test_msvc_detection(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], gcc_path="cl.exe")
        assert "--castxml-cc-msvc" in captured

    @pytest.mark.parametrize("name", ["CL.EXE", "Cl.exe", "CL"])
    def test_msvc_detection_case_insensitive(self, tmp_path, monkeypatch, name):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], gcc_path=name)
        assert "--castxml-cc-msvc" in captured

    def test_sysroot_flag(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], sysroot=Path("/opt/sysroot"))
        assert "--sysroot=/opt/sysroot" in captured

    def test_nostdinc_flag(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], nostdinc=True)
        assert "-nostdinc" in captured

    def test_gcc_options_split(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], gcc_options="-march=armv8-a -mfloat-abi=hard")
        assert "-march=armv8-a" in captured
        assert "-mfloat-abi=hard" in captured

    def test_lang_c_forces_c_mode(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [], lang="C")
        assert "-x" in captured
        assert "c" in captured
        assert "-std=gnu11" in captured

    def test_castxml_failure_raises(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)

        def fake_run(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_text("", encoding="utf-8")
                    break
            return SimpleNamespace(returncode=1, stdout="", stderr="compilation error")

        monkeypatch.setattr(deadline, "run_bounded", fake_run)

        with pytest.raises(RuntimeError, match="castxml failed"):
            _castxml_dump([header], [])

    def test_extra_includes_passed(self, tmp_path, monkeypatch):
        header = self._setup(monkeypatch, tmp_path)
        inc = tmp_path / "inc"
        inc.mkdir()
        captured = self._make_spy(monkeypatch)
        _castxml_dump([header], [inc])
        assert "-I" in captured
        assert str(inc) in captured


# ── dump() elf_meta symbol filtering and lang ──────────────────────────


class TestDumpSymbolFiltering:
    def test_elf_meta_symbol_type_filtering(self, tmp_path, monkeypatch):
        """When elf_meta has symbols, they are split by type."""
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType

        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"\x7fELF")

        monkeypatch.setattr(
            "abicheck.dumper._pyelftools_exported_symbols",
            lambda _p: ({"func_sym", "obj_sym"}, {"func_sym", "obj_sym"}),
        )

        elf_meta = ElfMetadata(
            soname="libfoo.so",
            symbols=[
                ElfSymbol(name="func_sym", sym_type=SymbolType.FUNC, version=""),
                ElfSymbol(name="obj_sym", sym_type=SymbolType.OBJECT, version=""),
            ],
        )
        monkeypatch.setattr(
            "abicheck.elf_metadata.parse_elf_metadata", lambda _p: elf_meta
        )
        monkeypatch.setattr(
            "abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None
        )
        monkeypatch.setattr(
            "abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            snap = dump(so_path=so_path, headers=[], version="1.0")

        # Only FUNC symbols appear as functions in no-header mode
        func_names = {f.name for f in snap.functions}
        assert "func_sym" in func_names
        # Object symbols should NOT be in functions
        assert "obj_sym" not in func_names

    def test_lang_c_sets_profile(self, tmp_path, monkeypatch):
        """lang='C' sets language_profile to 'c'."""
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF")

        monkeypatch.setattr(
            "abicheck.dumper._pyelftools_exported_symbols",
            lambda _p: (set(), set()),
        )
        monkeypatch.setattr(
            "abicheck.elf_metadata.parse_elf_metadata", lambda _p: ElfMetadata()
        )
        monkeypatch.setattr(
            "abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None
        )
        monkeypatch.setattr(
            "abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            snap = dump(so_path=so_path, headers=[], version="1.0", lang="C")

        assert snap.language_profile == "c"

    def test_lang_cpp_sets_profile(self, tmp_path, monkeypatch):
        """lang='C++' sets language_profile to 'cpp'."""
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF")

        monkeypatch.setattr(
            "abicheck.dumper._pyelftools_exported_symbols",
            lambda _p: (set(), set()),
        )
        monkeypatch.setattr(
            "abicheck.elf_metadata.parse_elf_metadata", lambda _p: ElfMetadata()
        )
        monkeypatch.setattr(
            "abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None
        )
        monkeypatch.setattr(
            "abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            snap = dump(so_path=so_path, headers=[], version="1.0", lang="C++")

        assert snap.language_profile == "cpp"

    def test_symbols_only_skips_dwarf_expansion(self, tmp_path, monkeypatch):
        """scan --depth binary uses this path to keep native binary scans cheap."""
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF")
        elf_meta = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3foov", sym_type=SymbolType.FUNC)]
        )
        monkeypatch.setattr(
            "abicheck.dumper._pyelftools_exported_symbols",
            lambda _p: ({"_Z3foov"}, {"_Z3foov"}),
        )
        monkeypatch.setattr(
            "abicheck.elf_metadata.parse_elf_metadata", lambda _p: elf_meta
        )

        def _unexpected(*args, **kwargs):
            raise AssertionError("symbols_only must not walk DWARF DIEs")

        monkeypatch.setattr("abicheck.dumper._resolve_debug_metadata", _unexpected)
        snap = dump(so_path=so_path, headers=[], version="1.0", symbols_only=True)

        assert [f.mangled for f in snap.functions] == ["_Z3foov"]
        assert snap.elf_only_mode is True

    def test_symbols_only_skips_header_ast_even_with_headers(
        self, tmp_path, monkeypatch
    ):
        """symbols_only is an exported-symbol surface even if callers pass headers."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF")
        header = tmp_path / "api.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        elf_meta = ElfMetadata(
            symbols=[ElfSymbol(name="foo", sym_type=SymbolType.FUNC)]
        )
        monkeypatch.setattr(
            "abicheck.dumper._pyelftools_exported_symbols",
            lambda _p: ({"foo"}, {"foo"}),
        )
        monkeypatch.setattr(
            "abicheck.elf_metadata.parse_elf_metadata", lambda _p: elf_meta
        )
        monkeypatch.setattr(
            "abicheck.dwarf_presence.cheap_debug_presence_metadata",
            lambda *_a, **_kw: (DwarfMetadata(), AdvancedDwarfMetadata()),
        )

        def _unexpected(*args, **kwargs):
            raise AssertionError("symbols_only must not parse headers")

        monkeypatch.setattr("abicheck.dumper._header_ast_parser", _unexpected)
        snap = dump(
            so_path=so_path,
            headers=[header],
            version="1.0",
            symbols_only=True,
        )

        assert [f.mangled for f in snap.functions] == ["foo"]
        assert snap.elf_only_mode is True


# ── _CastxmlParser edge cases ─────────────────────────────────────────


def _xml_root(*children: Element) -> Element:
    root = Element("GCC_XML")
    for c in children:
        root.append(c)
    return root


def _fund_type(id_: str, name: str) -> Element:
    return Element("FundamentalType", id=id_, name=name)


class TestCastxmlParserBuiltinSkip:
    def test_builtin_function_skipped(self):
        """Functions from <builtin> file are skipped."""
        builtin_file = Element("File", id="f_builtin", name="<builtin>")
        ft = _fund_type("t1", "void")
        fn = Element(
            "Function",
            id="fn1",
            name="__builtin_trap",
            mangled="__builtin_trap",
            returns="t1",
            file="f_builtin",
        )
        root = _xml_root(builtin_file, ft, fn)
        p = _CastxmlParser(root, {"__builtin_trap"}, set())
        assert p.parse_functions() == []

    def test_builtin_variable_skipped(self):
        builtin_file = Element("File", id="f_builtin", name="<built-in>")
        ft = _fund_type("t1", "int")
        v = Element(
            "Variable",
            id="v1",
            name="__builtin_var",
            mangled="__builtin_var",
            type="t1",
            file="f_builtin",
        )
        root = _xml_root(builtin_file, ft, v)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_variables() == []

    def test_builtin_type_skipped(self):
        builtin_file = Element("File", id="f_builtin", name="<command-line>")
        s = Element("Struct", id="s1", name="CmdLineDef", file="f_builtin")
        root = _xml_root(builtin_file, s)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_types() == []

    def test_builtin_enum_skipped(self):
        builtin_file = Element("File", id="f_builtin", name="<builtin>")
        e = Element("Enumeration", id="e1", name="BuiltinEnum", file="f_builtin")
        root = _xml_root(builtin_file, e)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_enums() == []

    def test_builtin_typedef_skipped(self):
        builtin_file = Element("File", id="f_builtin", name="<builtin>")
        ft = _fund_type("t1", "int")
        td = Element(
            "Typedef", id="td1", name="__builtin_td", type="t1", file="f_builtin"
        )
        root = _xml_root(builtin_file, ft, td)
        p = _CastxmlParser(root, set(), set())
        assert p.parse_typedefs() == {}


class TestCastxmlParserAnonymousField:
    def test_anonymous_field_expanded(self):
        """Anonymous union field gets its members inlined."""
        ft = _fund_type("t1", "int")
        inner_union = Element("Union", id="u1", name="")
        SubElement(inner_union, "Field", name="i", type="t1", offset="0")
        SubElement(inner_union, "Field", name="f", type="t1", offset="0")

        s = Element("Struct", id="s1", name="Outer", size="32", align="32")
        # Anonymous field (no name) pointing to the union
        SubElement(s, "Field", name="", type="u1", offset="0")

        root = _xml_root(ft, inner_union, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert len(types) == 1
        field_names = [f.name for f in types[0].fields]
        assert "i" in field_names
        assert "f" in field_names

    def test_members_attribute_fallback(self):
        """Fields resolved via members= attribute when no inline children."""
        ft = _fund_type("t1", "int")
        f1 = Element("Field", id="_f1", name="x", type="t1", offset="0")
        f2 = Element("Field", id="_f2", name="y", type="t1", offset="32")

        s = Element("Struct", id="s1", name="Via", size="64", members="_f1 _f2")
        # No inline Field children in Struct

        root = _xml_root(ft, f1, f2, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        assert len(types) == 1
        assert len(types[0].fields) == 2
        assert types[0].fields[0].name == "x"
        assert types[0].fields[1].name == "y"


class TestCastxmlParserPointerDepth:
    def test_double_pointer(self):
        ft = _fund_type("t1", "int")
        p1 = Element("PointerType", id="t2", type="t1")
        p2 = Element("PointerType", id="t3", type="t2")
        root = _xml_root(ft, p1, p2)
        p = _CastxmlParser(root, set(), set())
        assert p._pointer_depth("t3") == 2

    def test_pointer_through_typedef(self):
        ft = _fund_type("t1", "int")
        td = Element("Typedef", id="t2", name="myint", type="t1")
        ptr = Element("PointerType", id="t3", type="t2")
        root = _xml_root(ft, td, ptr)
        p = _CastxmlParser(root, set(), set())
        assert p._pointer_depth("t3") == 1

    def test_pointer_through_cv_qualified(self):
        ft = _fund_type("t1", "int")
        cv = Element("CvQualifiedType", id="t2", type="t1", const="1")
        ptr = Element("PointerType", id="t3", type="t2")
        root = _xml_root(ft, cv, ptr)
        p = _CastxmlParser(root, set(), set())
        assert p._pointer_depth("t3") == 1

    def test_non_pointer_returns_zero(self):
        ft = _fund_type("t1", "int")
        root = _xml_root(ft)
        p = _CastxmlParser(root, set(), set())
        assert p._pointer_depth("t1") == 0

    def test_missing_returns_zero(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), set())
        assert p._pointer_depth("missing") == 0


class TestCastxmlParserUnderlyingType:
    def test_typedef_chain_resolved(self):
        ft = _fund_type("t1", "int")
        td1 = Element("Typedef", id="t2", name="int32_t", type="t1")
        td2 = Element("Typedef", id="t3", name="my_int", type="t2")
        root = _xml_root(ft, td1, td2)
        p = _CastxmlParser(root, set(), set())
        assert p._underlying_type_name("t3") == "int"

    def test_non_typedef_returns_type_name(self):
        ft = _fund_type("t1", "int")
        root = _xml_root(ft)
        p = _CastxmlParser(root, set(), set())
        assert p._underlying_type_name("t1") == "int"

    def test_missing_returns_question(self):
        root = _xml_root()
        p = _CastxmlParser(root, set(), set())
        assert p._underlying_type_name("missing") == "?"

    def test_depth_limit(self):
        """Deep typedef chain returns '?'."""
        # Create chain: t0 → t1 → t2 → ... → t25
        ft = _fund_type("t0", "int")
        elements = [ft]
        for i in range(1, 25):
            td = Element("Typedef", id=f"t{i}", name=f"td{i}", type=f"t{i - 1}")
            elements.append(td)
        root = _xml_root(*elements)
        p = _CastxmlParser(root, set(), set())
        assert p._underlying_type_name("t24") == "?"


class TestCastxmlParserFunctionSourceLoc:
    def test_function_with_source_location(self):
        """Function with location element gets source_location set."""
        file_el = Element("File", id="f1", name="test.hpp")
        loc = Element("Location", id="loc1", file="f1", line="42")
        ft = _fund_type("t1", "void")
        fn = Element(
            "Function",
            id="fn1",
            name="test_func",
            mangled="_Z9test_funcv",
            returns="t1",
            location="loc1",
        )
        root = _xml_root(file_el, loc, ft, fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].source_location == "test.hpp:42"

    def test_function_inline(self):
        ft = _fund_type("t1", "void")
        fn = Element(
            "Function",
            id="fn1",
            name="inlined",
            mangled="_Z7inlinedv",
            returns="t1",
            inline="1",
        )
        root = _xml_root(ft, fn)
        p = _CastxmlParser(root, set(), set())
        funcs = p.parse_functions()
        assert funcs[0].is_inline is True


class TestCacheKeyToolchain:
    def test_different_toolchain_params_different_keys(self, tmp_path):
        h = tmp_path / "h.h"
        h.write_text("int f();", encoding="utf-8")
        k1 = _cache_key([h], [], "c++", gcc_path="/usr/bin/g++")
        k2 = _cache_key([h], [], "c++", gcc_prefix="arm-")
        k3 = _cache_key([h], [], "c++", sysroot=Path("/opt"))
        k4 = _cache_key([h], [], "c++", nostdinc=True)
        k5 = _cache_key([h], [], "c++", lang="C")
        k6 = _cache_key([h], [], "c++", gcc_options="-march=armv8")
        # All should be different from base
        k_base = _cache_key([h], [], "c++")
        assert len({k_base, k1, k2, k3, k4, k5, k6}) == 7

    def test_resolved_force_cpp20_changes_key(self, tmp_path):
        """Regression (Codex review): the *resolved* C++20 dialect decision
        must be part of the key, not just the explicit ``lang`` the caller
        passed in. ``_detect_cpp20_headers`` is a heuristic that can itself
        change across an abicheck upgrade (a detector bug fix) with no
        header content or toolchain identity change at all — without this,
        such an upgrade would silently keep reusing a stale AST parsed
        under the old, wrong dialect decision until the on-disk cache was
        manually cleared."""
        h = tmp_path / "h.h"
        h.write_text("int f();", encoding="utf-8")
        k_false = _cache_key([h], [], "c++", lang=None, force_cpp20=False)
        k_true = _cache_key([h], [], "c++", lang=None, force_cpp20=True)
        assert k_false != k_true


class TestResolveForceCppLanguageModeDecision:
    """Regression (Codex review, twenty-first round): a C++20 construct
    confined to a ``#if __cplusplus``/``#ifdef __cplusplus``-guarded arm
    must not by itself promote an auto-detected header to C++ mode -- in C
    mode ``__cplusplus`` is undefined, so that guard's content is not
    actually reachable there. Forcing C++ (and then C++20) purely because
    it exists there turns an *active*, unguarded use of the same word as
    an ordinary C identifier elsewhere in the header into a reserved-word
    parse error once C++20 mode is wrongly forced."""

    def test_ignores_construct_behind_bare_if_cplusplus(self, tmp_path):
        h = tmp_path / "h.h"
        h.write_text(
            "#if __cplusplus\nconsteval int f();\n#endif\nint consteval;\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_ignores_construct_behind_ifdef_cplusplus(self, tmp_path):
        h = tmp_path / "h.h"
        h.write_text(
            "#ifdef __cplusplus\nconsteval int f();\n#endif\nint consteval;\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_ignores_construct_behind_defined_cplusplus(self, tmp_path):
        h = tmp_path / "h.h"
        h.write_text(
            "#if defined(__cplusplus)\nconsteval int f();\n#endif\nint consteval;\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_still_forces_cpp_for_unguarded_construct(self, tmp_path):
        """Companion: a genuine, unguarded C++20 construct must still
        force C++ mode -- only guarded content is exempted."""
        h = tmp_path / "h.h"
        h.write_text("template<class T> concept C = true;\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [h], None, ()) is True

    def test_explicit_lang_still_wins(self, tmp_path):
        """Companion: an explicit --lang always wins regardless of any
        guarded content, unchanged from before this fix."""
        h = tmp_path / "h.h"
        h.write_text("#if __cplusplus\nconsteval int f();\n#endif\n", encoding="utf-8")
        assert _resolve_force_cpp("c++", [h], None, ()) is True
        assert _resolve_force_cpp("c", [h], None, ()) is False

    def test_ignores_construct_behind_else_of_ifndef_cplusplus(self, tmp_path):
        """Regression (Codex review, twenty-third round): the opposite
        polarity case -- ``#ifndef __cplusplus`` with a genuine C
        fallback in the guarded arm and C++20-only syntax in its
        ``#else``. In C mode ``__cplusplus`` is undefined, so the guarded
        arm (the C fallback, e.g. ``int consteval;``) is exactly the
        reachable one, and the ``#else`` (C++-only) is the one that's
        circular for the language-mode decision -- the opposite of
        ``#ifdef __cplusplus``'s polarity, mirroring ``#ifndef __cpp_x``
        vs. ``#ifdef __cpp_x``. Before this fix the guarded arm was
        unconditionally masked (correct only for the dialect decision),
        leaving the ``#else`` visible and wrongly forcing C++ mode."""
        h = tmp_path / "h.h"
        h.write_text(
            "#ifndef __cplusplus\nint consteval;\n#else\nconsteval int f();\n#endif\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_ignores_construct_behind_else_of_not_defined_cplusplus(self, tmp_path):
        """Companion: the ``#if !defined(__cplusplus)`` spelling needs
        the same opposite polarity as ``#ifndef __cplusplus``."""
        h = tmp_path / "h.h"
        h.write_text(
            "#if !defined(__cplusplus)\nint consteval;\n#else\n"
            "consteval int f();\n#endif\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_settles_on_elif_not_defined_cplusplus(self, tmp_path):
        """Companion: the ``#elif !defined(__cplusplus)`` spelling,
        reached as a later arm in a chain that opens on something else,
        must settle the chain the same way."""
        h = tmp_path / "h.h"
        h.write_text(
            "#if 0\nint a;\n#elif !defined(__cplusplus)\nint consteval;\n"
            "#else\nconsteval int f();\n#endif\n",
            encoding="utf-8",
        )
        assert _resolve_force_cpp(None, [h], None, ()) is False

    def test_still_forces_cpp_for_genuine_construct_behind_ifndef_cplusplus(
        self, tmp_path
    ):
        """Companion: a genuine C++20 construct sitting in the *live*
        ``#ifndef __cplusplus`` arm itself (the C fallback) must still
        force C++ mode -- only the ``#else`` is exempted."""
        h = tmp_path / "h.h"
        h.write_text(
            "#ifndef __cplusplus\nconsteval int f();\n#endif\n", encoding="utf-8"
        )
        assert _resolve_force_cpp(None, [h], None, ()) is True


class TestCastxmlParserAccessLevel:
    def test_protected_access(self):
        ft = _fund_type("t1", "int")
        s = Element("Struct", id="s1", name="S")
        SubElement(s, "Field", name="x", type="t1", access="protected")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        from abicheck.model import AccessLevel

        assert types[0].fields[0].access == AccessLevel.PROTECTED

    def test_private_access(self):
        ft = _fund_type("t1", "int")
        s = Element("Struct", id="s1", name="S")
        SubElement(s, "Field", name="x", type="t1", access="private")
        root = _xml_root(ft, s)
        p = _CastxmlParser(root, set(), set())
        types = p.parse_types()
        from abicheck.model import AccessLevel

        assert types[0].fields[0].access == AccessLevel.PRIVATE


# ── _detect_format ──────────────────────────────────────────────────────────


class TestDetectFormat:
    """Test magic-byte format detection."""

    def test_elf(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF\x00\x00")
        assert _detect_format(f) == "elf"

    def test_macho_le64(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xcf\xfa\xed\xfe")
        assert _detect_format(f) == "macho"

    def test_macho_be32(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xce")
        assert _detect_format(f) == "macho"

    def test_macho_fat(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xca\xfe\xba\xbe")
        assert _detect_format(f) == "macho"

    def test_pe(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.dll"
        f.write_bytes(b"MZ\x90\x00")
        assert _detect_format(f) == "pe"

    def test_unknown(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        f = tmp_path / "lib.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert _detect_format(f) == "unknown"

    def test_oserror(self, tmp_path: Path) -> None:
        from abicheck.dumper import _detect_format

        assert _detect_format(tmp_path / "nonexistent.so") == "unknown"

    def test_ar_archive_is_unknown(self, tmp_path: Path) -> None:
        # ar archives are not a single linkable image; _detect_format does not
        # classify them (dump() rejects them separately with guidance — G8).
        from abicheck.dumper import _detect_format

        f = tmp_path / "libfoo.a"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        assert _detect_format(f) == "unknown"


class TestDumpRejectsArchive:
    """dump() rejects static/import library archives with actionable guidance."""

    def test_static_archive_raises(self, tmp_path: Path) -> None:
        from abicheck.dumper import dump
        from abicheck.errors import ValidationError

        f = tmp_path / "libfoo.a"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        with pytest.raises(ValidationError, match="static/import library archive"):
            dump(f, [], None, "1.0", "c++")


# ── _dump_macho / _dump_pe via dump() routing ───────────────────────────────


class TestDumpRoutingMachoPe:
    """Test that dump() routes to _dump_macho / _dump_pe correctly (mocked)."""

    def test_dump_macho_no_headers(self, tmp_path: Path, monkeypatch) -> None:
        """_dump_macho: no headers → snapshot from export table only."""
        from unittest.mock import MagicMock, patch

        from abicheck import dumper
        from abicheck.model import AbiSnapshot

        dylib = tmp_path / "libfoo.dylib"
        dylib.write_bytes(b"\xcf\xfa\xed\xfe")  # Mach-O LE64 magic

        mock_exp = MagicMock()
        mock_exp.name = "_foo_func"
        mock_meta = MagicMock()
        mock_meta.exports = [mock_exp]
        mock_meta.install_name = "libfoo.dylib"
        mock_meta.dependent_libs = []

        import abicheck.macho_metadata as _macho_mod

        with (
            patch.object(dumper, "_detect_format", return_value="macho"),
            patch.object(_macho_mod, "parse_macho_metadata", return_value=mock_meta),
            patch.object(dumper, "_castxml_dump", return_value=([], [], None, [])),
        ):
            snap = dump(dylib, headers=[], version="1.0")

        assert isinstance(snap, AbiSnapshot)
        assert snap.platform == "macho"

    def test_dump_pe_no_headers(self, tmp_path: Path, monkeypatch) -> None:
        """_dump_pe: no headers → snapshot from export table only."""
        from unittest.mock import MagicMock, patch

        from abicheck import dumper
        from abicheck.model import AbiSnapshot

        dll = tmp_path / "foo.dll"
        dll.write_bytes(b"MZ\x90\x00")  # PE magic

        mock_exp = MagicMock()
        mock_exp.name = "FooFunc"
        mock_exp.ordinal = 1
        mock_meta = MagicMock()
        mock_meta.exports = [mock_exp]
        mock_meta.machine = "x86_64"

        import abicheck.pe_metadata as _pe_mod

        with (
            patch.object(dumper, "_detect_format", return_value="pe"),
            patch.object(_pe_mod, "parse_pe_metadata", return_value=mock_meta),
            patch.object(dumper, "_castxml_dump", return_value=([], [], None, [])),
        ):
            snap = dump(dll, headers=[], version="1.0")

        assert isinstance(snap, AbiSnapshot)
        assert snap.platform == "pe"

    def test_dump_unknown_format_raises(self, tmp_path: Path) -> None:
        """dump() raises ValueError for unknown binary format."""
        from unittest.mock import patch

        from abicheck import dumper

        f = tmp_path / "weird.bin"
        f.write_bytes(b"\x00\x01\x02\x03")

        with patch.object(dumper, "_detect_format", return_value="unknown"):
            with pytest.raises(ValueError, match="(?i)unknown|unrecogni"):
                dump(f, headers=[], version="1.0")


# ── from_headers provenance flag ─────────────────────────────────────────────


class TestFromHeadersProvenance:
    """The format-specific builders (_dump_elf / _dump_pe / _dump_macho) set
    AbiSnapshot.from_headers=True only when castxml actually parses headers.

    This is set in the builders rather than dump() so that every entry point
    records it — including the CLI/service native paths that call the builders
    directly via service._try_header_scoped_dump, bypassing dump() (Codex P2).
    """

    @staticmethod
    def _mock_parser():
        from unittest.mock import MagicMock

        p = MagicMock()
        p.parse_functions.return_value = []
        p.parse_variables.return_value = []
        p.parse_types.return_value = []
        p.parse_enums.return_value = []
        p.parse_typedefs.return_value = []
        return p

    def test_pe_with_headers_is_header_parsed(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        import abicheck.pe_metadata as _pe
        from abicheck import dumper

        dll = tmp_path / "foo.dll"
        dll.write_bytes(b"MZ\x90\x00")
        meta = MagicMock(exports=[MagicMock(name="Foo", ordinal=1)])
        with (
            patch.object(_pe, "parse_pe_metadata", return_value=meta),
            patch.object(dumper, "_castxml_dump", return_value=object()),
            patch.object(dumper, "_CastxmlParser", return_value=self._mock_parser()),
        ):
            snap = dumper._dump_pe(
                dll, [tmp_path / "h.h"], [], "1.0", "c++", header_backend="castxml"
            )
        assert snap.from_headers is True

    def test_pe_without_headers_is_not_header_parsed(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        import abicheck.pe_metadata as _pe
        from abicheck import dumper

        dll = tmp_path / "foo.dll"
        dll.write_bytes(b"MZ\x90\x00")
        meta = MagicMock(exports=[MagicMock(name="Foo", ordinal=1)])
        with patch.object(_pe, "parse_pe_metadata", return_value=meta):
            snap = dumper._dump_pe(dll, [], [], "1.0", "c++")
        assert snap.from_headers is False

    def test_macho_with_headers_is_header_parsed(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        import abicheck.macho_metadata as _macho
        from abicheck import dumper

        dylib = tmp_path / "foo.dylib"
        dylib.write_bytes(b"\xcf\xfa\xed\xfe")
        meta = MagicMock(exports=[])
        with (
            patch.object(_macho, "parse_macho_metadata", return_value=meta),
            patch.object(dumper, "_castxml_dump", return_value=object()),
            patch.object(dumper, "_CastxmlParser", return_value=self._mock_parser()),
        ):
            snap = dumper._dump_macho(
                dylib, [tmp_path / "h.h"], [], "1.0", "c++", header_backend="castxml"
            )
        assert snap.from_headers is True

    def test_macho_without_headers_is_not_header_parsed(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        import abicheck.macho_metadata as _macho
        from abicheck import dumper

        dylib = tmp_path / "foo.dylib"
        dylib.write_bytes(b"\xcf\xfa\xed\xfe")
        meta = MagicMock(exports=[])
        with patch.object(_macho, "parse_macho_metadata", return_value=meta):
            snap = dumper._dump_macho(dylib, [], [], "1.0", "c++")
        assert snap.from_headers is False

    def test_elf_with_headers_is_header_parsed(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        import abicheck.elf_metadata as _elfmod
        from abicheck import dumper
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF")
        with (
            patch.object(
                dumper, "_pyelftools_exported_symbols", return_value=({"foo"}, set())
            ),
            patch.object(
                _elfmod, "parse_elf_metadata", return_value=_elfmod.ElfMetadata()
            ),
            patch.object(
                dumper,
                "_elf_classify_symbols",
                return_value=({"foo"}, {"foo"}, set(), set()),
            ),
            patch.object(
                dumper,
                "_resolve_debug_metadata",
                return_value=(DwarfMetadata(), AdvancedDwarfMetadata()),
            ),
            patch.object(dumper, "_castxml_dump", return_value=object()),
            patch.object(dumper, "_CastxmlParser", return_value=self._mock_parser()),
            patch.object(dumper, "_populate_elf_visibility", lambda snap: None),
        ):
            snap = dumper._dump_elf(
                so, [tmp_path / "h.h"], [], "1.0", "c++", header_backend="castxml"
            )
        assert snap.from_headers is True

    def test_elf_dwarf_only_is_not_header_parsed(self, tmp_path: Path) -> None:
        # --dwarf-only forces the DWARF path and never reaches castxml, even
        # when headers are supplied → from_headers must stay False.
        from unittest.mock import patch

        import abicheck.elf_metadata as _elfmod
        from abicheck import dumper
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.model import AbiSnapshot

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF")

        def _fake_resolve(
            _so_path,
            _debug_format,
            *,
            _session_out=None,
            _format_out=None,
            dwarf_source=None,
        ):
            if _format_out is not None:
                _format_out.append("dwarf")
            return DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata(has_dwarf=True)

        with (
            patch.object(
                dumper, "_pyelftools_exported_symbols", return_value=({"foo"}, set())
            ),
            patch.object(
                _elfmod, "parse_elf_metadata", return_value=_elfmod.ElfMetadata()
            ),
            patch.object(
                dumper,
                "_elf_classify_symbols",
                return_value=({"foo"}, {"foo"}, set(), set()),
            ),
            patch.object(dumper, "_resolve_debug_metadata", side_effect=_fake_resolve),
            patch.object(
                dumper,
                "_try_dwarf_snapshot",
                return_value=(
                    AbiSnapshot(library="lib", version="1.0", elf_only_mode=True),
                    [],
                ),
            ),
        ):
            snap = dumper._dump_elf(
                so, [tmp_path / "h.h"], [], "1.0", "c++", dwarf_only=True
            )
        assert snap.from_headers is False

    def test_elf_no_headers_auto_btf_does_not_trigger_dwarf_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Auto-detect resolving to BTF (debug_format stays None; has_dwarf
        mirrors BTF presence for checker compatibility) must not take the
        no-headers DWARF-primary-snapshot path: that would call
        _try_dwarf_snapshot with session=None, letting build_snapshot_from_dwarf
        open so_path directly and walk whatever real DWARF the binary also
        carries — the auto-detected BTF selection was meant to bypass exactly
        that (Codex review)."""
        from unittest.mock import patch

        import abicheck.elf_metadata as _elfmod
        from abicheck import dumper
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF")

        def _fake_resolve(
            _so_path,
            _debug_format,
            *,
            _session_out=None,
            _format_out=None,
            dwarf_source=None,
        ):
            if _format_out is not None:
                _format_out.append("btf")
            return DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata()

        with (
            patch.object(
                dumper, "_pyelftools_exported_symbols", return_value=({"foo"}, set())
            ),
            patch.object(
                _elfmod, "parse_elf_metadata", return_value=_elfmod.ElfMetadata()
            ),
            patch.object(
                dumper,
                "_elf_classify_symbols",
                return_value=({"foo"}, {"foo"}, set(), set()),
            ),
            patch.object(dumper, "_resolve_debug_metadata", side_effect=_fake_resolve),
            patch.object(dumper, "_try_dwarf_snapshot") as mock_try_dwarf,
        ):
            dumper._dump_elf(so, [], [], "1.0", "c++")
        mock_try_dwarf.assert_not_called()

    def test_dwarf_only_with_resolved_btf_does_not_trigger_dwarf_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        """Regression (Codex review, second finding): --dwarf-only combined
        with --debug-format=btf/ctf (or auto resolving to BTF on a kernel
        binary) must not call _try_dwarf_snapshot either — there is no real
        DWARF to walk, and doing so anyway (session=None) would open so_path
        directly and build the primary snapshot from whatever real,
        possibly-stale DWARF the binary also carries, contradicting the
        explicit BTF/CTF selection. A UserWarning explains --dwarf-only was
        ignored instead of silently doing something the caller didn't ask
        for."""
        from unittest.mock import patch

        import abicheck.elf_metadata as _elfmod
        from abicheck import dumper
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF")

        def _fake_resolve(
            _so_path,
            _debug_format,
            *,
            _session_out=None,
            _format_out=None,
            dwarf_source=None,
        ):
            if _format_out is not None:
                _format_out.append("btf")
            return DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata()

        with (
            patch.object(
                dumper, "_pyelftools_exported_symbols", return_value=({"foo"}, set())
            ),
            patch.object(
                _elfmod, "parse_elf_metadata", return_value=_elfmod.ElfMetadata()
            ),
            patch.object(
                dumper,
                "_elf_classify_symbols",
                return_value=({"foo"}, {"foo"}, set(), set()),
            ),
            patch.object(dumper, "_resolve_debug_metadata", side_effect=_fake_resolve),
            patch.object(dumper, "_try_dwarf_snapshot") as mock_try_dwarf,
            pytest.warns(UserWarning, match="dwarf-only"),
        ):
            dumper._dump_elf(
                so, [], [], "1.0", "c++", debug_format="btf", dwarf_only=True
            )
        mock_try_dwarf.assert_not_called()


class TestFormatHandlerRegistry:
    """C3: the binary-format handler registry is the single source of truth for
    magic recognition + dump() dispatch."""

    def test_registry_covers_the_three_formats(self):
        from abicheck import dumper

        assert set(dumper._HANDLERS_BY_NAME) == {"elf", "macho", "pe"}
        # _detect_format is driven by the registry's magics.
        elf = dumper._HANDLERS_BY_NAME["elf"]
        assert elf.matches_magic(b"\x7fELF")
        assert dumper._HANDLERS_BY_NAME["pe"].matches_magic(b"MZ\x90\x00")
        assert dumper._HANDLERS_BY_NAME["macho"].matches_magic(b"\xfe\xed\xfa\xce")
        assert not elf.matches_magic(b"MZ\x90\x00")

    def test_accepts_flags_match_builder_signatures(self):
        """Each handler declares exactly the optional kwargs its builder accepts,
        so dump() forwards the same arguments the old if/elif chain did."""
        import inspect

        from abicheck import dumper

        for handler in dumper._FORMAT_HANDLERS:
            params = set(inspect.signature(handler.builder).parameters)
            assert handler.accepts_dwarf_only == ("dwarf_only" in params), handler.name
            assert handler.accepts_debug_format == ("debug_format" in params), (
                handler.name
            )

    def test_per_format_kwarg_acceptance(self):
        from abicheck import dumper

        h = dumper._HANDLERS_BY_NAME
        assert h["elf"].accepts_dwarf_only and h["elf"].accepts_debug_format
        assert h["macho"].accepts_dwarf_only and not h["macho"].accepts_debug_format
        assert not h["pe"].accepts_dwarf_only and not h["pe"].accepts_debug_format
