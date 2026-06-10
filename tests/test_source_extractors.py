# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the ADR-030 phase-2 castxml source ABI extractor.

The context→argv builder and the model→entity mapping are pure and tested in
the default (fast) lane; the end-to-end castxml run is marked ``integration``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.evidence.build_evidence import CompileUnit
from abicheck.evidence.source_extractors import (
    CASTXML_EXTRACTOR_VERSION,
    CastxmlSourceExtractor,
    build_castxml_command,
)
from abicheck.evidence.source_extractors.base import (
    assemble_source_tu,
    entity_from_constant,
    entity_from_enum,
    entity_from_function,
    entity_from_record,
    entity_from_typedef,
    entity_from_variable,
)
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
)


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++20",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


# -- build_castxml_command (pure, D2) ----------------------------------------


def test_build_command_reflects_compile_context() -> None:
    cu = _cu(
        directory="/proj",
        defines={"FOO": "1", "BARE": ""},
        undefines=["NDEBUG"],
        include_paths=["include"],
        system_include_paths=["/opt/sdk/include"],
        sysroot="/sysroot",
        target_triple="aarch64-linux-gnu",
    )
    out = Path("build/out.xml")
    src = Path("src/foo.cpp")
    cmd = build_castxml_command(cu, src, out)
    assert cmd[:4] == ["castxml", "--castxml-output=1", "--castxml-cc-gnu", "g++"]
    assert "-std=c++20" in cmd
    assert "-DFOO=1" in cmd
    assert "-DBARE" in cmd  # valueless define carries no '='
    assert "-UNDEBUG" in cmd
    assert cmd[cmd.index("-I") + 1] == "include"
    assert cmd[cmd.index("-isystem") + 1] == "/opt/sdk/include"
    assert "--sysroot=/sysroot" in cmd
    assert "--target=aarch64-linux-gnu" in cmd
    # Compare via str(Path(...)) so the separator is OS-native (Windows uses \).
    assert cmd[-3:] == ["-o", str(out), str(src)]


def test_build_command_c_uses_gcc_and_no_target_for_msvc() -> None:
    cmd = build_castxml_command(
        _cu(language="C", standard="c11"), Path("a.c"), Path("o.xml")
    )
    assert "--castxml-cc-gnu" in cmd and "gcc" in cmd
    assert "-std=c11" in cmd
    # MSVC path: /std: form and no --target flag.
    msvc = build_castxml_command(
        _cu(standard="c++20", target_triple="x64"),
        Path("a.cpp"),
        Path("o.xml"),
        compiler_binary="cl.exe",
    )
    assert "--castxml-cc-msvc" in msvc
    assert "/std:c++20" in msvc
    assert not any(a.startswith("--target=") for a in msvc)


def test_build_command_carries_argv_only_options() -> None:
    # ABI-relevant flags and forced includes that live only in argv must be
    # carried through so castxml parses the same TU as the build (Codex review).
    cu = _cu(
        abi_relevant_flags=["-fms-extensions", "-fabi-version=11"],
        argv=["g++", "-include", "config.h", "-imacros", "m.h", "-c", "foo.cpp"],
    )
    cmd = build_castxml_command(cu, Path("foo.cpp"), Path("o.xml"))
    assert "-fms-extensions" in cmd
    assert "-fabi-version=11" in cmd
    assert cmd[cmd.index("-include") + 1] == "config.h"
    assert cmd[cmd.index("-imacros") + 1] == "m.h"
    # the build action's own -c/source/-o are NOT blindly forwarded
    assert "-c" not in cmd


def test_extract_runs_in_compile_unit_directory(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Mock castxml so we can assert the subprocess runs with cwd=directory and
    # exercise the extract() success path without the tool installed.
    from abicheck.evidence.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        captured["cwd"] = kw.get("cwd")
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.subprocess, "run", _fake_run)
    cu = _cu(source="src/foo.cpp", directory=str(tmp_path))
    tu = extractor.extract(cu, public_header_roots=["foo.h"], target_id="target://x")
    assert captured["cwd"] == str(tmp_path)
    assert tu.extractor["name"] == "castxml-source"


# -- model → SourceEntity mapping (pure, D4) ---------------------------------


def test_entity_from_function_signature_stable_under_default_change() -> None:
    common = dict(
        name="ns::f",
        mangled="_ZN2ns1fEi",
        return_type="void",
        source_header="include/f.h",
        source_location="include/f.h:10",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    no_default = entity_from_function(Function(params=[Param("x", "int")], **common))
    default_1 = entity_from_function(
        Function(params=[Param("x", "int", default="1")], **common)
    )
    default_2 = entity_from_function(
        Function(params=[Param("x", "int", default="2")], **common)
    )
    # Same type signature → signature_hash unchanged across all three.
    assert (
        no_default.signature_hash
        == default_1.signature_hash
        == default_2.signature_hash
    )
    # value carries the default-argument expression so add/remove AND value
    # changes are visible to default_argument_changed.
    assert no_default.value == ""
    assert default_1.value == "x=1"
    assert default_2.value == "x=2"
    assert default_1.value != default_2.value
    assert default_1.kind == "function"
    assert default_1.api_relevant is True
    assert default_1.source_location is not None
    assert default_1.source_location.origin == "PUBLIC_HEADER"


def test_entity_from_function_signature_changes_with_param_type() -> None:
    a = entity_from_function(
        Function(name="f", mangled="m", return_type="int", params=[Param("x", "int")])
    )
    b = entity_from_function(
        Function(name="f", mangled="m", return_type="int", params=[Param("x", "long")])
    )
    assert a.signature_hash != b.signature_hash


def test_entity_from_record_type_hash_tracks_layout() -> None:
    r1 = entity_from_record(
        RecordType(
            name="S", kind="struct", size_bits=64, fields=[TypeField("a", "int")]
        )
    )
    r2 = entity_from_record(
        RecordType(
            name="S", kind="struct", size_bits=128, fields=[TypeField("a", "int")]
        )
    )
    assert r1.kind == "record"
    assert r1.type_hash != r2.type_hash


def test_entity_from_enum_and_variable_and_constant_and_typedef() -> None:
    en = entity_from_enum(
        EnumType(name="E", members=[EnumMember("A", 0), EnumMember("B", 1)])
    )
    assert en.kind == "enum" and en.type_hash

    var = entity_from_variable(
        Variable(
            name="g",
            mangled="g",
            type="int",
            value="7",
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
    )
    assert var.kind == "variable" and var.value == "7" and var.api_relevant is True

    const = entity_from_constant("kMax", "100")
    assert (
        const.kind == "constexpr"
        and const.value == "100"
        and const.api_relevant is True
    )

    td = entity_from_typedef("Handle", "void*")
    assert td.kind == "typedef" and td.value == "void*"


def test_non_public_origin_is_not_api_relevant() -> None:
    fn = entity_from_function(
        Function(
            name="impl",
            mangled="i",
            return_type="void",
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
    )
    assert fn.api_relevant is False
    assert fn.visibility == "private_header"


# -- assemble_source_tu (pure, D4) -------------------------------------------


def test_assemble_source_tu_routes_entities_to_buckets() -> None:
    cu = _cu(target_id="target://libfoo")
    tu = assemble_source_tu(
        cu,
        public_header_roots=["include/foo.h"],
        target_id="",
        extractor_name="castxml-source",
        extractor_version=CASTXML_EXTRACTOR_VERSION,
        functions=[Function(name="f", mangled="mf", return_type="void")],
        records=[RecordType(name="S", kind="struct")],
        enums=[EnumType(name="E")],
        variables=[Variable(name="g", mangled="mg", type="int")],
        constants={"kMax": "10"},
        typedefs={"Alias": "int"},
    )
    assert tu.tu_id == "cu://src/foo.cpp#cfg"
    assert tu.target_id == "target://libfoo"
    assert tu.extractor == {
        "name": "castxml-source",
        "version": CASTXML_EXTRACTOR_VERSION,
    }
    assert tu.compile_context_hash.startswith("sha256:")
    assert [e.qualified_name for e in tu.functions] == ["f"]
    # records + enums + typedefs all land in the types bucket
    assert {e.qualified_name for e in tu.types} == {"S", "E", "Alias"}
    assert [e.qualified_name for e in tu.variables] == ["g"]
    assert [e.qualified_name for e in tu.constexpr_values] == ["kMax"]
    # round-trips through the normalized schema
    from abicheck.evidence.source_abi import SourceAbiTu

    assert SourceAbiTu.from_dict(tu.to_dict()).tu_id == tu.tu_id


# -- extractor orchestration (no real castxml) -------------------------------


def test_extract_raises_when_castxml_unavailable() -> None:
    from abicheck.evidence.source_extractors import SourceExtractionError

    extractor = CastxmlSourceExtractor(castxml_bin="castxml-does-not-exist-xyz")
    assert extractor.available() is False
    with pytest.raises(SourceExtractionError):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


def test_parse_root_maps_castxml_xml_without_running_castxml() -> None:
    # Drive the XML→SourceAbiTu path on a hand-built GCC_XML document, so the
    # parser/assembly is covered without castxml installed.
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    cls = SubElement(
        root, "Class", id="c1", name="Widget", size="64", align="64", location="loc1"
    )
    SubElement(cls, "Field", name="a", type="t_int", offset="0")
    fn = SubElement(
        root, "Function", id="fn1", name="add", returns="t_int", location="loc1"
    )
    SubElement(fn, "Argument", name="x", type="t_int")

    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["foo.h"], target_id="target://libfoo"
    )
    names = {e.qualified_name for e in tu.all_entities()}
    assert any("add" in n for n in names)
    assert any("Widget" in n for n in names)
    assert tu.extractor["name"] == "castxml-source"
    # Provenance is applied (P1 fix): public-header decls are api_relevant, not
    # left UNKNOWN — otherwise the linker would filter every declaration out.
    assert any(e.api_relevant for e in tu.functions)
    assert any(e.api_relevant for e in tu.types)
    # And they survive linking onto the public source surface.
    from abicheck.evidence import link_source_abi

    surface = link_source_abi([tu], target_id="target://libfoo")
    assert any("add" in e.qualified_name for e in surface.reachable_declarations)
    assert any("Widget" in e.qualified_name for e in surface.reachable_types)


# -- end-to-end via real castxml (integration) -------------------------------


@pytest.mark.integration
def test_castxml_extractor_end_to_end(tmp_path: Path) -> None:
    extractor = CastxmlSourceExtractor()
    if not extractor.available():
        pytest.skip("castxml not installed")
    header = tmp_path / "foo.h"
    header.write_text(
        textwrap.dedent(
            """
            #ifndef FOO_H
            #define FOO_H
            struct Widget { int a; int b; };
            int add(int x, int y);
            const int kAnswer = 42;
            #endif
            """
        )
    )
    src = tmp_path / "foo.cpp"
    src.write_text('#include "foo.h"\n')
    cu = CompileUnit(
        id="cu://foo.cpp",
        source=str(src),
        language="CXX",
        standard="c++17",
    )
    tu = extractor.extract(
        cu, public_header_roots=[str(header)], target_id="target://libfoo"
    )
    names = {e.qualified_name for e in tu.all_entities()}
    assert any("add" in n for n in names)
    assert any("Widget" in n for n in names)
    # The public const is captured with its value (enables constexpr_value_changed).
    consts = {e.qualified_name: e.value for e in tu.constexpr_values}
    assert any("kAnswer" in k for k in consts)
