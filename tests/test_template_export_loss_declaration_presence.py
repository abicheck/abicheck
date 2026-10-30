# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""A template instance the new header stops enumerating is not a removal.

castxml records a template instantiation only while some header translation
unit still instantiates it, or an ``extern template`` names it. When the new
header stops using a template, the new snapshot has no record for the
instantiation, and the header-depth removal path used to report it as
``func_removed`` or ``inline_function_removed`` -- although the template is still
declared, and the new binary may still export the symbol.

Each matrix cell builds an old and a new library, compares them with the real
CLI, and judges the outcome with two oracles that do not run abicheck's
detector:

* the dynamic symbol table, read with ``readelf --dyn-syms``, says whether the
  export changed (exported before, not exported after);
* the header text says the declaration still exists: every new header in the
  matrix keeps the template.

Invariants, for the mangled symbol each kind names:

1. A declaration-removal kind (``func_removed``, ``inline_function_removed``)
   never names it while its template is still declared.
2. ``func_visibility_changed`` names it exactly when readelf shows the export
   changed, and then the verdict is BREAKING: an export loss is never demoted.

The class-template section at the end extends the same invariants to the
specialization itself (no ``type_removed``) and every one of its members,
including the synthetic implicit constructors/destructor, ``operator=`` and a
static data member (``var_visibility_changed`` for an export loss).

Symbol-binding note: a template instance is WEAK in the old build, so the
``to_hidden`` transition is ``-fvisibility=hidden`` for those kinds. The GLOBAL
binding is exercised by ``plain_out_of_line_member``.

The two controls at the end pin the cases the rule must *not* change: a template
removed from the header, and a plain function dropped from the header while the
binary still exports it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml"),
    pytest.mark.skipif(shutil.which("readelf") is None, reason="needs readelf"),
]

_DECLARATION_REMOVAL_KINDS = {"func_removed", "inline_function_removed"}
_EXPORT_KINDS = {"func_visibility_changed", "func_removed_elf_only"}


@dataclass(frozen=True)
class _Kind:
    """One matrix kind: its mangled symbol and the source text it contributes."""

    symbol: str
    #: Declaration(s) in ``namespace ns``. Present in every header.
    decl: str
    #: Inline function that instantiates the template. Present only when the
    #: new header still uses it.
    use: str
    #: Body of ``lib_entry`` that instantiates the template in the library.
    entry: str | None
    #: Namespace-scope definition compiled into the library, if any.
    definition: str | None = None
    #: ``extern template`` line for the new header, if the kind can take one.
    extern: str | None = None
    #: Explicit instantiation definition compiled into the new library alongside
    #: an ``extern template`` declaration.
    explicit: str | None = None


_KINDS: dict[str, _Kind] = {
    "free_fn_template": _Kind(
        symbol="_ZN2ns5ftmplIdEEvPT_",
        decl="template <typename T> void ftmpl(T* p) { (void)p; }\n",
        use="inline void use_k() { double d = 0; ftmpl<double>(&d); }\n",
        entry="{ double d = 0; ftmpl<double>(&d); }",
        extern="extern template void ftmpl<double>(double*);\n",
        explicit="template void ftmpl<double>(double*);\n",
    ),
    "member_fn_template": _Kind(
        symbol="_ZN2ns3CSR9setArraysIdEEvPT_",
        decl=(
            "class CSR {\npublic:\n"
            "  template <typename T> void setArrays(T* p) { (void)p; }\n};\n"
        ),
        use="inline void use_k(CSR& t) { t.setArrays<double>(nullptr); }\n",
        entry="{ CSR t; t.setArrays<double>(nullptr); }",
        extern="extern template void CSR::setArrays<double>(double*);\n",
        explicit="template void CSR::setArrays<double>(double*);\n",
    ),
    "class_template_member": _Kind(
        symbol="_ZN2ns3BoxIdE3setEd",
        decl="template <typename T> struct Box {\n  T v;\n  void set(T x) { v = x; }\n};\n",
        use="inline void use_k() { Box<double> b; b.set(1.0); }\n",
        entry="{ Box<double> b; b.set(1.0); }",
        extern="extern template struct Box<double>;\n",
        explicit="template struct Box<double>;\n",
    ),
    "plain_inline_member": _Kind(
        symbol="_ZNK2ns5Plain5dummyEv",
        decl="struct Plain {\n  int dummy() const { return 1; }\n};\n",
        use="inline int use_k() { Plain p; return p.dummy(); }\n",
        entry="{ Plain p; (void)p.dummy(); }",
    ),
    "plain_out_of_line_member": _Kind(
        symbol="_ZNK2ns6Strong1gEv",
        decl="struct Strong {\n  int g() const;\n};\n",
        use="",
        entry=None,
        definition="int Strong::g() const { return 4; }\n",
    ),
}

#: name -> (flags for the new build, whether the new library keeps the code
#: that instantiates or defines the symbol). The old build always keeps it.
_TRANSITIONS: dict[str, tuple[list[str], bool]] = {
    "to_local": (["-fvisibility-inlines-hidden"], True),
    "to_absent": ([], False),
    "to_hidden": (["-fvisibility=hidden"], True),
}

_TEMPLATE_KINDS = ("free_fn_template", "member_fn_template", "class_template_member")

#: (kind, transition, new header instantiates, new header has extern template)
_CASES: list[tuple[str, str, bool, bool]] = [
    *(
        (kind, transition, uses, False)
        for kind in (*_TEMPLATE_KINDS, "plain_inline_member")
        for transition in _TRANSITIONS
        for uses in (True, False)
    ),
    *(
        (kind, transition, False, True)
        for kind in _TEMPLATE_KINDS
        for transition in _TRANSITIONS
    ),
    *(
        ("plain_out_of_line_member", transition, True, False)
        for transition in _TRANSITIONS
    ),
]


def _header(spec: _Kind, *, use: bool, extern: bool) -> str:
    text = "#pragma once\nnamespace ns {\n" + spec.decl
    if use:
        text += spec.use
    if extern and spec.extern:
        text += spec.extern
    return text + "}\n"


def _source(spec: _Kind, *, definition: bool, entry: bool, explicit: bool) -> str:
    text = '#include "m.h"\nnamespace ns {\n'
    if definition and spec.definition:
        text += spec.definition
    if entry and spec.entry:
        text += f"void lib_entry() {spec.entry}\n"
    if explicit and spec.explicit:
        text += spec.explicit
    return text + "}\n"


def _build(directory: Path, header: str, source: str, flags: list[str]) -> Path:
    (directory / "m.h").write_text(header, encoding="utf-8")
    (directory / "m.cpp").write_text(source, encoding="utf-8")
    so = directory / "libm.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O0",
            "-fPIC",
            "-shared",
            *flags,
            "m.cpp",
            "-o",
            str(so),
        ],
        cwd=directory,
        check=True,
        capture_output=True,
    )
    return so


def _exported_functions(so: Path) -> dict[str, str]:
    """Defined dynamic function symbols, name -> binding (readelf is the oracle)."""
    out = subprocess.run(
        ["readelf", "--dyn-syms", "-W", str(so)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    table: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        # Num: Value Size Type Bind Vis Ndx Name
        if len(parts) >= 8 and parts[0].endswith(":") and parts[3] == "FUNC":
            if parts[6] != "UND":
                table[parts[7]] = parts[4]
    return table


def _compare(
    tmp_path: Path, old_so: Path, new_so: Path, old_h: Path, new_h: Path
) -> dict:
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    out_json = tmp_path / "out.json"
    for so, header, out, version in (
        (old_so, old_h, old_json, "1"),
        (new_so, new_h, new_json, "2"),
    ):
        dumped = CliRunner().invoke(
            main,
            ["dump", str(so), "-H", str(header), "--label", version, "-o", str(out)],
        )
        assert dumped.exit_code == 0, dumped.output
    result = CliRunner().invoke(
        main, ["compare", str(old_json), str(new_json), "-o", f"json={out_json}"]
    )
    assert result.exit_code in (0, 2, 4), result.output
    return json.loads(out_json.read_text(encoding="utf-8"))


def _assert_cell(
    report: dict, symbol: str, old_exports: dict[str, str], new_exports: dict[str, str]
) -> None:
    kinds = {c["kind"] for c in report["changes"] if c.get("symbol") == symbol}
    export_changed = symbol in old_exports and symbol not in new_exports
    assert not kinds & _DECLARATION_REMOVAL_KINDS, (symbol, sorted(kinds))
    if export_changed:
        assert "func_visibility_changed" in kinds, (symbol, sorted(kinds))
        assert report["verdict"] == "BREAKING", report["verdict"]
    else:
        assert not kinds & _EXPORT_KINDS, (symbol, sorted(kinds))


@pytest.mark.parametrize(
    ("kind", "transition", "new_instantiates", "extern"),
    _CASES,
    ids=[
        f"{kind}-{transition}-{'newinst' if uses else 'noinst'}"
        f"{'-extern' if ext else ''}"
        for kind, transition, uses, ext in _CASES
    ],
)
def test_declared_template_instance_outcome_follows_the_export_table(
    tmp_path: Path,
    kind: str,
    transition: str,
    new_instantiates: bool,
    extern: bool,
) -> None:
    spec = _KINDS[kind]
    flags, new_keeps_code = _TRANSITIONS[transition]
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_h = old_dir / "m.h"
    new_h = new_dir / "m.h"
    old_so = _build(
        old_dir,
        _header(spec, use=True, extern=False),
        _source(spec, definition=True, entry=True, explicit=False),
        [],
    )
    new_so = _build(
        new_dir,
        _header(spec, use=new_instantiates, extern=extern),
        _source(
            spec,
            definition=new_keeps_code,
            entry=new_keeps_code,
            explicit=extern,
        ),
        flags,
    )
    old_exports = _exported_functions(old_so)
    new_exports = _exported_functions(new_so)
    # Non-vacuity: the fixture exports the symbol before the change.
    assert spec.symbol in old_exports, (spec.symbol, sorted(old_exports))
    report = _compare(tmp_path, old_so, new_so, old_h, new_h)
    _assert_cell(report, spec.symbol, old_exports, new_exports)


def test_template_removed_from_header_is_still_a_removal(tmp_path: Path) -> None:
    """Control: the declaration itself is gone, so the removal is real."""
    spec = _KINDS["free_fn_template"]
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_so = _build(
        old_dir,
        _header(spec, use=True, extern=False),
        _source(spec, definition=True, entry=True, explicit=False),
        [],
    )
    new_so = _build(
        new_dir,
        "#pragma once\nnamespace ns {\n}\n",
        '#include "m.h"\nnamespace ns {\n}\n',
        [],
    )
    report = _compare(tmp_path, old_so, new_so, old_dir / "m.h", new_dir / "m.h")
    kinds = {c["kind"] for c in report["changes"] if c.get("symbol") == spec.symbol}
    assert "func_removed" in kinds, sorted(kinds)
    assert report["verdict"] == "BREAKING"


def test_plain_function_dropped_from_header_stays_a_removal(tmp_path: Path) -> None:
    """Control: a plain function the new header drops is a removal even though
    the new binary still exports the symbol. The declaration is the evidence."""
    symbol = "_ZN2ns3barEi"
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_so = _build(
        old_dir,
        "#pragma once\nnamespace ns { int bar(int x); int keep(int x); }\n",
        '#include "m.h"\nnamespace ns { int bar(int x) { return x + 1; }'
        " int keep(int x) { return x; } }\n",
        [],
    )
    new_so = _build(
        new_dir,
        "#pragma once\nnamespace ns { int keep(int x); }\n",
        '#include "m.h"\nnamespace ns { int bar(int x) { return x + 1; }'
        " int keep(int x) { return x; } }\n",
        [],
    )
    assert symbol in _exported_functions(new_so)
    report = _compare(tmp_path, old_so, new_so, old_dir / "m.h", new_dir / "m.h")
    kinds = {c["kind"] for c in report["changes"] if c.get("symbol") == symbol}
    assert "func_removed" in kinds, sorted(kinds)
    assert report["verdict"] == "BREAKING"


# ── Class-template specializations ─────────────────────────────────────────
#
# The same question one level up: a class-template specialization the new
# header stops instantiating. Its record, its implicit constructors/destructor/
# operator=, its inline methods and its static data member all drop out of the
# new castxml enumeration while the primary template stays declared. Oracles:
# readelf (FUNC and OBJECT exports, so the static data member is judged too)
# and the header text each scenario writes. Every member of ``Box<double>`` is
# found by its spelling, mangled (``3BoxIdE``) or synthetic (``Box<double>``).

_CT_PRIMARY = (
    "template <typename T> struct Box {\n"
    "  T v;\n"
    "  static int count;\n"
    "  void set(T x) { v = x; }\n"
    "};\n"
    "template <typename T> int Box<T>::count = 0;\n"
)
_CT_EXPLICIT = (
    "template <> struct Box<double> {\n"
    "  double v;\n"
    "  static int count;\n"
    "  void set(double x) { v = x; }\n"
    "};\n"
)
_CT_BODY = (
    "{ Box<double> b; b.set(1.0); Box<double> c(b); c = b; Box<double>::count++; }"
)
_CT_SPEC = "Box<double>"

#: scenario -> (old header decls, new header decls, new header instantiates,
#: the old library defines the explicit specialization's static member)
_CT_SCENARIOS: dict[str, tuple[str, str, bool, bool]] = {
    "newinst": (_CT_PRIMARY, _CT_PRIMARY, True, False),
    "noinst": (_CT_PRIMARY, _CT_PRIMARY, False, False),
    "explicit_specialization_removed": (
        _CT_PRIMARY + _CT_EXPLICIT,
        _CT_PRIMARY,
        False,
        True,
    ),
    "template_removed": (_CT_PRIMARY, "", False, False),
}
_CT_REMOVAL_KINDS = {
    "type_removed",
    "func_removed",
    "inline_function_removed",
    "var_removed",
}
_CT_EXPORT_KIND = {
    "FUNC": "func_visibility_changed",
    "OBJECT": "var_visibility_changed",
}


def _exported_symbols(so: Path) -> dict[str, str]:
    """Defined dynamic FUNC/OBJECT symbols, name -> type (readelf oracle)."""
    out = subprocess.run(
        ["readelf", "--dyn-syms", "-W", str(so)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    table: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0].endswith(":") and parts[3] in _CT_EXPORT_KIND:
            if parts[6] != "UND":
                table[parts[7]] = parts[3]
    return table


def _is_box_double_member(symbol: str) -> bool:
    return "3BoxIdE" in symbol or _CT_SPEC in symbol


def _class_template_cell(
    tmp_path: Path, scenario: str, transition: str
) -> tuple[dict, dict[str, str], dict[str, str]]:
    old_decl, new_decl, new_uses, explicit_static = _CT_SCENARIOS[scenario]
    flags, new_keeps_code = _TRANSITIONS[transition]
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    use = f"inline void use_k() {_CT_BODY}\n"
    old_static = "int Box<double>::count = 0;\n" if explicit_static else ""
    old_so = _build(
        old_dir,
        f"#pragma once\nnamespace ns {{\n{old_decl}{use}}}\n",
        f'#include "m.h"\nnamespace ns {{\n{old_static}'
        f"void lib_entry() {_CT_BODY}\n}}\n",
        [],
    )
    keeps = new_keeps_code and bool(new_decl)
    new_so = _build(
        new_dir,
        f"#pragma once\nnamespace ns {{\n{new_decl}{use if new_uses else ''}}}\n",
        '#include "m.h"\nnamespace ns {\n'
        + (f"void lib_entry() {_CT_BODY}\n" if keeps else "void lib_entry() {}\n")
        + "}\n",
        flags,
    )
    old_exports, new_exports = _exported_symbols(old_so), _exported_symbols(new_so)
    # Non-vacuity: the old build exports the specialization's method and its
    # static data member, so the export oracle has something to judge.
    assert {"_ZN2ns3BoxIdE3setEd", "_ZN2ns3BoxIdE5countE"} <= set(old_exports)
    report = _compare(tmp_path, old_so, new_so, old_dir / "m.h", new_dir / "m.h")
    return report, old_exports, new_exports


def _box_findings(report: dict) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for change in report["changes"]:
        symbol = change.get("symbol") or ""
        if _is_box_double_member(symbol):
            found.setdefault(symbol, set()).add(change["kind"])
    return found


@pytest.mark.parametrize("transition", list(_TRANSITIONS))
@pytest.mark.parametrize("scenario", ["newinst", "noinst"])
def test_class_template_specialization_the_header_stops_instantiating(
    tmp_path: Path, scenario: str, transition: str
) -> None:
    """The primary template is still declared: neither the specialization nor
    any of its members (method, static data member, implicit special members)
    is a removal, and each export is judged by readelf."""
    report, old_exports, new_exports = _class_template_cell(
        tmp_path, scenario, transition
    )
    found = _box_findings(report)
    for symbol, kinds in found.items():
        assert not kinds & _CT_REMOVAL_KINDS, (symbol, sorted(kinds))
    for symbol, sym_type in old_exports.items():
        if not _is_box_double_member(symbol):
            continue
        lost = symbol not in new_exports
        kinds = found.get(symbol, set())
        assert (_CT_EXPORT_KIND[sym_type] in kinds) is lost, (symbol, sorted(kinds))
        if lost:
            assert report["verdict"] == "BREAKING", report["verdict"]


@pytest.mark.parametrize("transition", list(_TRANSITIONS))
@pytest.mark.parametrize(
    "scenario", ["explicit_specialization_removed", "template_removed"]
)
def test_class_template_specialization_real_removal_stays(
    tmp_path: Path, scenario: str, transition: str
) -> None:
    """Controls: a deleted explicit specialization and a removed class template
    are declarations the new header no longer has, so ``type_removed`` stays."""
    report, _, _ = _class_template_cell(tmp_path, scenario, transition)
    found = _box_findings(report)
    assert "type_removed" in found.get(_CT_SPEC, set()), report["changes"]
    assert report["verdict"] == "BREAKING"


def test_non_template_class_removed_from_header_stays_a_removal(
    tmp_path: Path,
) -> None:
    """Control: a plain class has no primary template that keeps it declared."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_so = _build(
        old_dir,
        "#pragma once\nnamespace ns {\nstruct Plain { int v; };\n"
        "inline int use_k() { Plain p{1}; return p.v; }\n}\n",
        '#include "m.h"\nnamespace ns { int lib_entry() { return use_k(); } }\n',
        [],
    )
    new_so = _build(
        new_dir,
        "#pragma once\nnamespace ns {\n}\n",
        '#include "m.h"\nnamespace ns { int lib_entry() { return 1; } }\n',
        [],
    )
    report = _compare(tmp_path, old_so, new_so, old_dir / "m.h", new_dir / "m.h")
    removed = {c["symbol"] for c in report["changes"] if c["kind"] == "type_removed"}
    assert "Plain" in removed, report["changes"]
