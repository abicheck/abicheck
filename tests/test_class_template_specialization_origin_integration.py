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

"""A class-template specialization whose *pattern* the declaring line cannot
show: a class-head a macro produces, a partial specialization, and a primary
class-head sharing its line with an explicit specialization.

``test_template_export_loss_declaration_presence.py`` pins the plain shape
(``struct Box {`` on its own line). Here the origin has to come from the
structure of clang's AST (``buildsource/template_class_origin.py``), carried
on the header graph's ``template_instantiation`` node, because castxml locates
a macro-produced record at the macro's line and an instantiation of a partial
specialization at the partial specialization's own class-head.

Each cell builds an old and a new library with g++, dumps both with the real
CLI (castxml plus the clang header graph) and compares them. Two oracles that
do not run abicheck judge it:

* the header text: the old specialization's pattern class-head (the primary's,
  the partial specialization's, or the explicit specialization's own) is
  searched for in the *preprocessed* new header (``g++ -E -P``), so a macro
  expands exactly as the compiler sees it. Still there -> the declaration
  remains, so nothing about the specialization may be a removal; gone -> the
  old declaration was deleted, so ``type_removed`` must stay;
* ``readelf --dyn-syms``: for a remaining declaration, every member export the
  old build had is reported as ``func_visibility_changed`` /
  ``var_visibility_changed`` exactly when the new build lost it.

Every new header keeps the primary template, so the negative controls differ
from the positive ones only in the pattern that instantiated the
specialization.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_template_export_loss_declaration_presence import (
    _CT_EXPORT_KIND,
    _CT_REMOVAL_KINDS,
    _TRANSITIONS,
    _build,
    _compare,
    _exported_symbols,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml"),
    pytest.mark.skipif(shutil.which("readelf") is None, reason="needs readelf"),
    pytest.mark.skipif(
        shutil.which("clang") is None, reason="the header graph needs clang"
    ),
]

_MEMBERS = "static int count; void set(T x) { v = x; }"
_PRIMARY = f"template <typename T> struct Box {{ T v; {_MEMBERS} }};\n"
_PRIMARY_STATIC = "template <typename T> int Box<T>::count = 0;\n"
_PARTIAL = (
    "template <typename T> struct Box<T*> { T* v; static int count;"
    " void set(T* x) { v = x; } };\n"
    "template <typename T> int Box<T*>::count = 0;\n"
)
_CHANGED_PARTIAL = (
    "template <typename T> struct Box<T const*> { const T* v; static int count;"
    " void set(const T* x) { v = x; } };\n"
)
_EXPLICIT = (
    "template <> struct Box<double> { double v; static int count;"
    " void set(double x) { v = x; } };\n"
)


@dataclass(frozen=True)
class _Scenario:
    old: str
    new: str
    #: The specialization, as castxml spells it, and its Itanium fragment.
    spec: str
    mangled: str
    #: The old specialization's pattern class-head (whitespace-insensitive).
    pattern_head: str
    new_instantiates: bool = False
    #: The old library defines an explicit specialization's static member.
    explicit_static: bool = False


_PRIMARY_MACRO = (
    "#define LIB_BOX_TEMPLATE(N) template <typename T> struct N "
    f"{{ T v; {_MEMBERS} }};\nLIB_BOX_TEMPLATE(Box)\n"
)
_PARTIAL_MACRO = (
    "#define LIB_BOX_POINTERS(N) template <typename T> struct N<T*> "
    "{ T* v; static int count; void set(T* x) { v = x; } };\n"
    "LIB_BOX_POINTERS(Box)\ntemplate <typename T> int Box<T*>::count = 0;\n"
)
_EXPLICIT_MACRO = (
    "#define LIB_BOX_SPEC(T) template <> struct Box<T> "
    "{ T v; static int count; void set(T x) { v = x; } };\nLIB_BOX_SPEC(double)\n"
)

_DOUBLE = {"spec": "Box<double>", "mangled": "3BoxIdE"}
_POINTER = {"spec": "Box<double *>", "mangled": "3BoxIPdE"}

_SCENARIOS: dict[str, _Scenario] = {
    # Kept: the old pattern is still declared.
    "macro_primary_stops_instantiating": _Scenario(
        old=_PRIMARY_MACRO + _PRIMARY_STATIC,
        new=_PRIMARY_MACRO + _PRIMARY_STATIC,
        pattern_head="struct Box {",
        **_DOUBLE,
    ),
    "partial_stops_instantiating": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        new=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        pattern_head="struct Box<T*> {",
        **_POINTER,
    ),
    "macro_partial_stops_instantiating": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _PARTIAL_MACRO,
        new=_PRIMARY + _PRIMARY_STATIC + _PARTIAL_MACRO,
        pattern_head="struct Box<T*> {",
        **_POINTER,
    ),
    "partial_still_instantiated": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        new=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        pattern_head="struct Box<T*> {",
        new_instantiates=True,
        **_POINTER,
    ),
    # Removed: the old pattern is gone, the primary template stays.
    "partial_deleted": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        new=_PRIMARY + _PRIMARY_STATIC,
        pattern_head="struct Box<T*> {",
        **_POINTER,
    ),
    "partial_replaced": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _PARTIAL,
        new=_PRIMARY + _PRIMARY_STATIC + _CHANGED_PARTIAL,
        pattern_head="struct Box<T*> {",
        **_POINTER,
    ),
    "macro_explicit_specialization_deleted": _Scenario(
        old=_PRIMARY + _PRIMARY_STATIC + _EXPLICIT_MACRO,
        new=_PRIMARY + _PRIMARY_STATIC,
        pattern_head="struct Box<double> {",
        explicit_static=True,
        **_DOUBLE,
    ),
    "same_line_explicit_specialization_deleted": _Scenario(
        old=_PRIMARY.rstrip("\n") + " " + _EXPLICIT + _PRIMARY_STATIC,
        new=_PRIMARY + _PRIMARY_STATIC,
        pattern_head="struct Box<double> {",
        explicit_static=True,
        **_DOUBLE,
    ),
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _pattern_declared(header: Path, head: str) -> bool:
    """Header-text oracle: the class-head is in the preprocessed header."""
    text = subprocess.run(
        ["g++", "-std=c++17", "-x", "c++", "-E", "-P", str(header)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return _squash(head) in _squash(text)


def _is_member(symbol: str, scenario: _Scenario) -> bool:
    return scenario.mangled in symbol or _squash(scenario.spec) in _squash(symbol)


def _cell(tmp_path: Path, name: str, transition: str) -> tuple[dict, dict, dict, bool]:
    scenario = _SCENARIOS[name]
    flags, new_keeps_code = _TRANSITIONS[transition]
    use_t = _squash(scenario.spec)
    arg = "nullptr" if "*" in scenario.spec else "1.0"
    body = f"{{ {use_t} b; b.set({arg}); {use_t} c(b); c = b; {use_t}::count++; }}"
    use = f"inline void use_k() {body}\n"
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    static = (
        f"int {_squash(scenario.spec)}::count = 0;\n"
        if scenario.explicit_static
        else ""
    )
    old_so = _build(
        old_dir,
        f"#pragma once\nnamespace ns {{\n{scenario.old}{use}}}\n",
        f'#include "m.h"\nnamespace ns {{\n{static}void lib_entry() {body}\n}}\n',
        [],
    )
    new_so = _build(
        new_dir,
        "#pragma once\nnamespace ns {\n"
        f"{scenario.new}{use if scenario.new_instantiates else ''}}}\n",
        '#include "m.h"\nnamespace ns {\n'
        + (f"void lib_entry() {body}\n" if new_keeps_code else "void lib_entry() {}\n")
        + "}\n",
        flags,
    )
    old_exports, new_exports = _exported_symbols(old_so), _exported_symbols(new_so)
    # Non-vacuity: the old build exports a method and the static data member.
    members = {s: t for s, t in old_exports.items() if _is_member(s, scenario)}
    assert set(members.values()) == {"FUNC", "OBJECT"}, old_exports
    assert _pattern_declared(old_dir / "m.h", scenario.pattern_head)
    declared = _pattern_declared(new_dir / "m.h", scenario.pattern_head)
    report = _compare(tmp_path, old_so, new_so, old_dir / "m.h", new_dir / "m.h")
    return report, members, new_exports, declared


def _findings(report: dict, scenario: _Scenario) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for change in report["changes"]:
        symbol = change.get("symbol") or ""
        if _is_member(symbol, scenario):
            found.setdefault(_squash(symbol), set()).add(change["kind"])
    return found


@pytest.mark.parametrize("transition", list(_TRANSITIONS))
@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_specialization_outcome_follows_its_pattern(
    tmp_path: Path, name: str, transition: str
) -> None:
    scenario = _SCENARIOS[name]
    report, old_members, new_exports, declared = _cell(tmp_path, name, transition)
    found = _findings(report, scenario)
    spec_kinds = found.get(_squash(scenario.spec), set())
    if not declared:
        assert "type_removed" in spec_kinds, (name, report["changes"])
        assert report["verdict"] == "BREAKING"
        return
    for symbol, kinds in found.items():
        assert not kinds & _CT_REMOVAL_KINDS, (name, symbol, sorted(kinds))
    for symbol, sym_type in old_members.items():
        lost = symbol not in new_exports
        kinds = found.get(_squash(symbol), set())
        assert (_CT_EXPORT_KIND[sym_type] in kinds) is lost, (symbol, sorted(kinds))
        if lost:
            assert report["verdict"] == "BREAKING", report["verdict"]
