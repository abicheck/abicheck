from __future__ import annotations

import warnings
from xml.etree.ElementTree import Element

import pytest

from abicheck.dumper import dump
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import Function, Visibility


def test_dump_without_headers_warns_and_returns_exported_symbols(tmp_path, monkeypatch):
    so_path = tmp_path / "libfoo.so"
    so_path.write_bytes(b"\x7fELF")

    monkeypatch.setattr("abicheck.dumper._pyelftools_exported_symbols", lambda _p: ({"z_sym", "a_sym"}, {"z_sym", "a_sym"}))
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda _p: ElfMetadata())
    monkeypatch.setattr("abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None)
    monkeypatch.setattr("abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap = dump(so_path=so_path, headers=[], version="1.0")

    assert any("No headers provided" in str(w.message) for w in caught)
    assert [f.name for f in snap.functions] == ["a_sym", "z_sym"]
    assert all(f.visibility == Visibility.ELF_ONLY for f in snap.functions)


class _FakeParser:
    def __init__(self, root, exported_dynamic, exported_static):
        assert root.tag == "GCC_XML"
        assert exported_dynamic == {"pub"}
        assert exported_static == {"pub", "local"}

    def parse_functions(self):
        return [Function(name="foo", mangled="_Z3foov", return_type="void")]

    def parse_variables(self):
        return []

    def parse_types(self):
        return []

    def parse_enums(self):
        return []

    def parse_typedefs(self):
        return {"SizeT": "unsigned long"}


def test_dump_with_headers_uses_castxml_parser_results(tmp_path, monkeypatch):
    so_path = tmp_path / "libfoo.so"
    so_path.write_bytes(b"\x7fELF")
    header = tmp_path / "foo.h"
    header.write_text("void foo();\n", encoding="utf-8")

    monkeypatch.setattr("abicheck.dumper._pyelftools_exported_symbols", lambda _p: ({"pub"}, {"pub", "local"}))
    monkeypatch.setattr("abicheck.dumper._castxml_dump", lambda *_args, **_kwargs: Element("GCC_XML"))
    monkeypatch.setattr("abicheck.dumper._CastxmlParser", _FakeParser)
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda _p: ElfMetadata())
    monkeypatch.setattr("abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None)
    monkeypatch.setattr("abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None)

    snap = dump(so_path=so_path, headers=[header], extra_includes=[tmp_path], version="2.0")

    assert snap.version == "2.0"
    assert len(snap.functions) == 1
    assert snap.functions[0].mangled == "_Z3foov"
    assert snap.typedefs == {"SizeT": "unsigned long"}


def test_dump_with_headers_propagates_castxml_error(tmp_path, monkeypatch):
    so_path = tmp_path / "libfoo.so"
    so_path.write_bytes(b"\x7fELF")
    header = tmp_path / "foo.h"
    header.write_text("void foo();\n", encoding="utf-8")

    monkeypatch.setattr("abicheck.dumper._pyelftools_exported_symbols", lambda _p: (set(), set()))
    monkeypatch.setattr("abicheck.dumper._castxml_dump", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("castxml failed")))
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda _p: ElfMetadata())
    monkeypatch.setattr("abicheck.dwarf_metadata.parse_dwarf_metadata", lambda _p: None)
    monkeypatch.setattr("abicheck.dwarf_advanced.parse_advanced_dwarf", lambda _p: None)

    with pytest.raises(RuntimeError, match="castxml failed"):
        dump(so_path=so_path, headers=[header], version="1.0")
