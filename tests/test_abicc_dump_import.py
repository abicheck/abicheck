from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.compat.abicc_dump_import import (
    _parse_perl_dumper_subset,
    _perl_expr_to_python_literal,
    _snapshot_from_abicc_dict,
    import_abicc_perl_dump,
    is_abicc_perl_dump_file,
    looks_like_perl_dump,
)


def test_looks_like_perl_dump_detects_var1_with_whitespace() -> None:
    assert looks_like_perl_dump("\n  \t$VAR1 = {};")
    assert not looks_like_perl_dump("<?xml version='1.0'?><ABI_dump_1.0/>")


def test_snapshot_from_abicc_dict_maps_functions_variables_and_types(
    tmp_path: Path,
) -> None:
    data = {
        "LibraryName": "libdemo",
        "LibraryVersion": "2.0",
        "TypeInfo": {
            "0": {"Name": "void", "Type": "Intrinsic"},
            "1": {"Name": "int", "Type": "Intrinsic"},
            "2": {"Name": "Demo", "Type": "Struct"},
            "3": {"Name": "DemoU", "Type": "Union"},
        },
        "SymbolInfo": {
            "10": {
                "MnglName": "foo",
                "ShortName": "foo",
                "Return": "0",
                "Param": {"1": {"type": "1", "name": "x"}},
            },
            "11": {
                "MnglName": "glob",
                "ShortName": "glob",
                "Type": "1",
            },
        },
    }

    snap = _snapshot_from_abicc_dict(data, tmp_path / "sample.dump")

    assert snap.library == "libdemo"
    assert snap.version == "2.0"
    assert len(snap.functions) == 1
    assert snap.functions[0].mangled == "foo"
    assert snap.functions[0].params[0].name == "x"
    assert snap.functions[0].params[0].type == "int"
    assert len(snap.variables) == 1
    assert snap.variables[0].mangled == "glob"
    assert snap.variables[0].type == "int"
    type_names = {t.name for t in snap.types}
    assert "Demo" in type_names
    assert "DemoU" in type_names


def test_snapshot_defaults_when_library_fields_missing(tmp_path: Path) -> None:
    snap = _snapshot_from_abicc_dict({}, tmp_path / "x.ABI.dump")
    assert snap.library == "x.ABI"
    assert snap.version == "unknown"


def test_import_abicc_perl_dump_rejects_non_dumper_content(tmp_path: Path) -> None:
    dump = tmp_path / "bad.dump"
    dump.write_text("print 'hello';", encoding="utf-8")

    with pytest.raises(ValueError, match="expected Data::Dumper content"):
        import_abicc_perl_dump(dump)


def test_import_abicc_perl_dump_rejects_malformed_content(tmp_path: Path) -> None:
    dump = tmp_path / "bad.dump"
    dump.write_text("$VAR1 = do { system('touch /tmp/pwned') };", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to parse ABICC Perl dump safely"):
        import_abicc_perl_dump(dump)


def test_import_abicc_perl_dump_safe_roundtrip_without_perl(tmp_path: Path) -> None:
    dump = tmp_path / "ok.dump"
    dump.write_text(
        """
        $VAR1 = {
          'LibraryName' => 'libok',
          'LibraryVersion' => '1',
          'TypeInfo' => {
            '0' => { 'Name' => 'void', 'Type' => 'Intrinsic' }
          },
          'SymbolInfo' => {
            '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }
          }
        };
        """,
        encoding="utf-8",
    )

    snap = import_abicc_perl_dump(dump)

    assert snap.library == "libok"
    assert snap.version == "1"
    assert any(f.mangled == "foo" for f in snap.functions)


def test_perl_expr_conversion_preserves_spaceship_inside_strings() -> None:
    expr = "{'SymbolName' => 'operator<=>', 'Kind' => 'demo'}"
    converted = _perl_expr_to_python_literal(expr)
    assert "'operator<=>'" in converted
    assert converted == "{'SymbolName' : 'operator<=>', 'Kind' : 'demo'}"


def test_parse_subset_converts_undef_only_as_bareword() -> None:
    dump = "$VAR1 = {'a' => undef, 'b' => 'undef', 'c' => ['x', undef]};"
    parsed = _parse_perl_dumper_subset(dump)
    assert parsed == {"a": None, "b": "undef", "c": ["x", None]}


def test_is_abicc_perl_dump_file_by_content(tmp_path: Path) -> None:
    p = tmp_path / "dump.txt"
    p.write_text("$VAR1 = {};", encoding="utf-8")
    assert is_abicc_perl_dump_file(p)


def test_is_abicc_perl_dump_file_false_for_regular_xml(tmp_path: Path) -> None:
    p = tmp_path / "desc.xml"
    p.write_text("<descriptor/>", encoding="utf-8")
    assert not is_abicc_perl_dump_file(p)
