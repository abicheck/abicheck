# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Explicit template instances: their declaration kind, and their removal.

An old header's ``extern template`` / ``template`` instantiation or explicit
specialization is a declaration; a new header that drops it removes that
declaration (``func_removed``), not an export change. The oracle is the fixture
header's own text: which lines spell an explicit form.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.compare.template_instance_absence import (
    TemplateInstanceAbsence,
    _export_lost,
)
from abicheck.extract.headers.clang.explicit_instantiation import (
    explicit_instantiation_facts,
    may_hold_explicit_instantiation,
    parse_explicit_dump,
)
from abicheck.model import (
    ExplicitInstantiation,
    Fact,
    Function,
    Variable,
)

_HEADER = """\
#pragma once
namespace lib {
namespace __detail {
template <typename T> T walk(T* a, T* b);
extern template int walk<int>(int*, int*);
extern template float walk<float>(float*, float*);
template<> double walk<double>(double* a, double* b);
template <typename T> T unused(T v);
}
template <typename T> T sum(T* a, T* b) { return __detail::walk<T>(a, b); }
}
"""

_TEXT_DUMP = """\
TranslationUnitDecl 0x1 <<invalid sloc>> <invalid sloc>
|-NamespaceDecl 0x2 <v1.h:2:1, line:9:1> line:2:11 lib
| |-NamespaceDecl 0x3 <line:3:1, line:8:1> line:3:11 __detail
| | |-FunctionTemplateDecl 0x4 <line:4:1, col:30> col:3 walk
| | | |-FunctionDecl 0x5 <line:4:1, col:30> col:3 walk 'T (T *, T *)'
| | | |-FunctionDecl 0x6 <col:1, col:25> col:3 walk 'int (int *, int *)' explicit_instantiation_declaration
| | | `-FunctionDecl 0x7 <col:1, col:25> col:3 walk 'float (float *, float *)' explicit_instantiation_declaration
| | |-FunctionDecl 0x8 prev 0x9 <line:6:1, col:52> col:19 walk 'double (double *, double *)' explicit_specialization
| | `-FunctionTemplateDecl 0x10 <line:9:1> col:3 unused
| |   `-FunctionDecl 0x11 <line:9:1> col:3 unused 'T (T)'
  `-FunctionTemplateDecl 0x12 <line:10:1> col:3 sum
    `-FunctionDecl 0x13 <line:10:1> col:3 sum 'T (T *, T *)' inline
"""


def _json_for(name: str, *children: dict) -> dict:
    return {"kind": "NamespaceDecl", "name": name, "inner": list(children)}


def _fn(name: str, qual: str, *, mangled: str = "", inner: list | None = None) -> dict:
    node: dict = {"kind": "FunctionDecl", "name": name, "type": {"qualType": qual}}
    if mangled:
        node["mangledName"] = mangled
    if inner:
        node["inner"] = inner
    return node


class TestTriggerPrefilter:
    @pytest.mark.parametrize(
        "text",
        [
            "extern template float walk<float>(float*, float*);",
            "template int walk<int>(int*, int*);",
            "template<> double walk<double>(double*, double*);",
        ],
    )
    def test_an_explicit_form_triggers_the_pass(self, text: str) -> None:
        assert may_hold_explicit_instantiation([text])

    def test_an_ordinary_template_does_not_trigger(self) -> None:
        text = "template <typename T> T walk(T* a, T* b);\n"
        assert not may_hold_explicit_instantiation([text])


class TestParseExplicitDump:
    def test_each_form_maps_to_its_kind(self) -> None:
        dump = parse_explicit_dump(_TEXT_DUMP)
        assert dump.kinds[("lib::__detail", "walk", "int(int*,int*)")] is (
            ExplicitInstantiation.DECLARATION
        )
        assert dump.kinds[("lib::__detail", "walk", "float(float*,float*)")] is (
            ExplicitInstantiation.DECLARATION
        )
        assert dump.kinds[("lib::__detail", "walk", "double(double*,double*)")] is (
            ExplicitInstantiation.SPECIALIZATION
        )

    def test_an_unmarked_declaration_is_absent(self) -> None:
        dump = parse_explicit_dump(_TEXT_DUMP)
        assert dump.kinds[("lib::__detail", "walk", "T(T*,T*)")] is (
            ExplicitInstantiation.ABSENT
        )
        assert dump.kinds[("lib", "sum", "T(T*,T*)")] is ExplicitInstantiation.ABSENT

    def test_a_key_listed_twice_is_ambiguous_not_guessed(self) -> None:
        dup = (
            "| | |-FunctionDecl 0x20 <col:1> col:3 walk 'int (int *, int *)' "
            "explicit_instantiation_definition\n"
        )
        anchor = "| | | `-FunctionDecl 0x7"
        doubled = _TEXT_DUMP.replace(anchor, dup + anchor, 1)
        assert doubled != _TEXT_DUMP
        dump = parse_explicit_dump(doubled)
        key = ("lib::__detail", "walk", "int(int*,int*)")
        assert key in dump.ambiguous
        assert key not in dump.kinds


class TestFactsOnTheJsonTree:
    def test_an_explicit_instance_gets_its_marker_and_an_implicit_one_absent(
        self,
    ) -> None:
        float_node = _fn("walk", "float (float *, float *)", mangled="_Wf")
        int_node = _fn("walk", "int (int *, int *)", mangled="_Wi")
        pattern = {
            "kind": "FunctionTemplateDecl",
            "name": "walk",
            "inner": [_fn("walk", "T (T *, T *)"), float_node, int_node],
        }
        root = _json_for("__detail", pattern)
        root = {"kind": "TranslationUnitDecl", "inner": [_json_for("lib", root)]}
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert facts[id(float_node)].value is ExplicitInstantiation.DECLARATION
        assert facts[id(int_node)].value is ExplicitInstantiation.DECLARATION

    def test_a_template_instance_with_no_text_match_is_unsupported(self) -> None:
        node = _fn("walk", "char (char *, char *)", mangled="_Wc")
        pattern = {"kind": "FunctionTemplateDecl", "name": "walk", "inner": [node]}
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [_json_for("lib", _json_for("__detail", pattern))],
        }
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert facts[id(node)].status.value == "unsupported"
        assert facts[id(node)].value is None

    def test_an_ordinary_function_gets_no_fact(self) -> None:
        node = _fn("plain", "int (int)", mangled="_Plain")
        root = {"kind": "TranslationUnitDecl", "inner": [_json_for("lib", node)]}
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert id(node) not in facts


_HAVE_CLANG = shutil.which("clang++") is not None


@pytest.mark.skipif(not _HAVE_CLANG, reason="clang++ is required")
class TestFactsOnARealClangDump:
    """The oracle is the fixture header text, not the implementation."""

    def test_the_header_spellings_read_back_from_clang(self, tmp_path: Path) -> None:
        header = tmp_path / "h.h"
        header.write_text(_HEADER)
        base = ["clang++", "-x", "c++", "-std=gnu++17", "-fsyntax-only"]
        base += ["-ferror-limit=0", "-Xclang"]
        text = subprocess.run(
            [*base, "-ast-dump", str(header)], capture_output=True, text=True
        ).stdout
        js = subprocess.run(
            [*base, "-ast-dump=json", str(header)], capture_output=True, text=True
        ).stdout
        root = json.loads(js)
        facts = explicit_instantiation_facts(root, parse_explicit_dump(text))
        by_type = {
            n["type"]["qualType"]: facts[id(n)].value
            for n in _walk_nodes(root)
            if id(n) in facts and n.get("name") == "walk" and n.get("mangledName")
        }
        assert by_type["int (int *, int *)"] is ExplicitInstantiation.DECLARATION
        assert by_type["float (float *, float *)"] is ExplicitInstantiation.DECLARATION
        assert by_type["double (double *, double *)"] is (
            ExplicitInstantiation.SPECIALIZATION
        )


def _walk_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.get("inner") or []:
            yield from _walk_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_nodes(child)


def _fact(value: ExplicitInstantiation) -> Fact[ExplicitInstantiation]:
    return Fact.present(value, producer="clang")


class TestRemovalRule:
    def test_an_explicit_old_instance_is_left_to_the_removal_path(self) -> None:
        decl = Function(
            name="walk",
            mangled="_Wf",
            return_type="float",
            explicit_instantiation_fact=_fact(ExplicitInstantiation.DECLARATION),
        )
        absence = TemplateInstanceAbsence(new=_empty_snapshot())
        assert absence.changes_for("_Wf", decl, elf_only_mode=False) is None

    def test_an_implicit_old_instance_keeps_the_export_path(self) -> None:
        decl = Function(
            name="walk",
            mangled="_Wf",
            return_type="float",
            explicit_instantiation_fact=_fact(ExplicitInstantiation.ABSENT),
        )
        assert not _is_explicit(decl)

    def test_an_unset_fact_is_not_explicit(self) -> None:
        decl = Function(name="walk", mangled="_Wf", return_type="float")
        assert not _is_explicit(decl)

    def test_a_variable_declared_explicitly_is_left_to_the_removal_path(self) -> None:
        var = Variable(
            name="count",
            mangled="_Vc",
            type="int",
            explicit_instantiation_fact=_fact(ExplicitInstantiation.DEFINITION),
        )
        absence = TemplateInstanceAbsence(new=_empty_snapshot())
        assert absence.variable_changes_for("_Vc", var) is None

    @pytest.mark.parametrize("is_function", [True, False])
    def test_export_loss_text_never_claims_hidden(self, is_function: bool) -> None:
        old = (
            Function(name="walk", mangled="_Wf", return_type="float")
            if is_function
            else Variable(name="count", mangled="_Vc", type="int")
        )
        change = _export_lost("_Wf" if is_function else "_Vc", old)
        assert "hidden" not in change.description.lower()
        assert "no longer exported" in change.description
        assert change.new_value == "not exported"


def _is_explicit(decl: Function) -> bool:
    from abicheck.compare.template_instance_absence import _declared_explicitly

    return _declared_explicitly(decl)


def _empty_snapshot():
    from abicheck.model import AbiSnapshot

    return AbiSnapshot(library="lib.so", version="1")


_CASE = Path(__file__).resolve().parents[1] / (
    "catalog/cases/case85_internal_template_signature_changed"
)


def _compare_case85(tmp_path: Path, frontend: str) -> dict:
    for tool in ("g++", "clang++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required")
    for name in ("v1.h", "v2.h", "v1.cpp", "v2.cpp"):
        shutil.copy(_CASE / name, tmp_path / name)
    for version in ("v1", "v2"):
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-std=c++17",
                "-I.",
                f"{version}.cpp",
                "-o",
                f"libfoo_{version}.so",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
    config = tmp_path / f"cfg-{frontend}.yml"
    config.write_text(f"compile:\n  frontend: {frontend}\n  lang: c++\n")
    out = tmp_path / f"out-{frontend}.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "compare",
            str(tmp_path / "libfoo_v1.so"),
            str(tmp_path / "libfoo_v2.so"),
            "-H",
            f"old={tmp_path / 'v1.h'}",
            "-H",
            f"new={tmp_path / 'v2.h'}",
            "--config",
            str(config),
            "-o",
            f"json={out}",
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text())


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="case85 is an ELF fixture (linux and macos platforms)",
)
class TestCase85ThroughTheCli:
    def test_clang_lane_reports_the_removed_explicit_declaration(
        self, tmp_path: Path
    ) -> None:
        if shutil.which("clang++") is None:
            pytest.skip("clang++ is required for the clang header lane")
        report = _compare_case85(tmp_path, "clang")
        kinds = {c["kind"] for c in report["changes"]}
        assert "func_removed" in kinds
        assert "func_visibility_changed" not in kinds
        assert "internal_template_leaks_via_public_api" in kinds

    def test_castxml_lane_keeps_the_export_path_until_it_records_the_kind(
        self, tmp_path: Path
    ) -> None:
        if shutil.which("castxml") is None:
            pytest.skip("castxml is required for the castxml lane")
        report = _compare_case85(tmp_path, "castxml")
        kinds = {c["kind"] for c in report["changes"]}
        assert "func_visibility_changed" in kinds
        assert "func_removed" not in kinds


class TestSnapshotRoundTrip:
    def test_the_kind_survives_a_save_and_load_as_the_enum(self) -> None:
        from abicheck.serialization import snapshot_from_dict
        from abicheck.storage.snapshot_encode import snapshot_to_dict

        snap = _empty_snapshot()
        snap.declarations.functions.append(
            Function(
                name="walk",
                mangled="_Wf",
                return_type="float",
                explicit_instantiation_fact=_fact(ExplicitInstantiation.DECLARATION),
            )
        )
        document = json.loads(json.dumps(snapshot_to_dict(snap)))
        loaded = snapshot_from_dict(document).declarations.functions[0]
        fact = loaded.explicit_instantiation_fact
        assert fact is not None
        assert fact.value is ExplicitInstantiation.DECLARATION

    def test_a_pre_v65_function_reads_the_fact_as_unset(self) -> None:
        from abicheck.serialization import snapshot_from_dict
        from abicheck.storage.snapshot_encode import snapshot_to_dict

        snap = _empty_snapshot()
        snap.declarations.functions.append(
            Function(name="walk", mangled="_Wf", return_type="float")
        )
        document = json.loads(json.dumps(snapshot_to_dict(snap)))
        document["schema_version"] = 64
        for fn in document.get("functions", []):
            fn.pop("explicit_instantiation_fact", None)
        loaded = snapshot_from_dict(document).declarations.functions[0]
        assert loaded.explicit_instantiation_fact is None


class TestEdgeShapes:
    """Shapes the real dumps rarely hit: unnamed scopes, odd nodes, and joins
    that must not be guessed. Each oracle is the dump or tree written here."""

    def test_unnamed_scopes_and_nameless_declarations_do_not_crash(self) -> None:
        text = (
            "TranslationUnitDecl 0x1 <<invalid sloc>> <invalid sloc>\n"
            "|-NamespaceDecl 0x2 <line:1:1, line:3:1>\n"
            "| `-FunctionDecl 0x3 <line:2:1, col:9> col:3 f 'void (void)' "
            "explicit_instantiation_definition\n"
            "`-ClassTemplateSpecializationDecl 0x4 <line:4:1> col:1 "
            "explicit_instantiation_declaration\n"
            "  `-VarDecl 0x5 <col:2> col:3 v explicit_instantiation_declaration\n"
            "`-FunctionDecl 0x6 <col:1> col:2 no_quoted_type\n"
        )
        dump = parse_explicit_dump(text)
        assert dump.kinds[("", "f", "void(void)")] is (ExplicitInstantiation.DEFINITION)
        assert ("", "no_quoted_type", "") not in dump.kinds

    def test_a_template_instance_without_a_type_is_unsupported(self) -> None:
        node = {"kind": "FunctionDecl", "name": "walk", "mangledName": "_Wx"}
        pattern = {"kind": "FunctionTemplateDecl", "name": "walk", "inner": [node]}
        root = {"kind": "TranslationUnitDecl", "inner": [_json_for("lib", pattern)]}
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert facts[id(node)].status.value == "unsupported"

    def test_a_non_dict_child_is_skipped(self) -> None:
        root = {"kind": "TranslationUnitDecl", "inner": ["not a node", None]}
        assert explicit_instantiation_facts(root, parse_explicit_dump("")) == {}

    def test_two_json_nodes_sharing_a_key_are_both_unsupported(self) -> None:
        first = _fn("walk", "float (float *, float *)", mangled="_W1")
        second = _fn("walk", "float (float *, float *)", mangled="_W2")
        pattern = {
            "kind": "FunctionTemplateDecl",
            "name": "walk",
            "inner": [first, second],
        }
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [_json_for("lib", _json_for("__detail", pattern))],
        }
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert facts[id(first)].status.value == "unsupported"
        assert facts[id(second)].status.value == "unsupported"

    def test_an_absent_instance_with_no_template_context_gets_no_fact(self) -> None:
        plain = _fn("walk", "T (T *, T *)", mangled="_Wp")
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [_json_for("lib", _json_for("__detail", plain))],
        }
        facts = explicit_instantiation_facts(root, parse_explicit_dump(_TEXT_DUMP))
        assert id(plain) not in facts
