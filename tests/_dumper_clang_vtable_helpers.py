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

"""Shared hand-built ``-ast-dump=json``-shaped node builders for
``test_dumper_clang_vtable.py`` and ``test_dumper_clang_vtable_redecl.py``
(split out once the combined file crossed the AI-readiness 2000-line hard
cap -- see ``tests/CLAUDE.md``'s file-size convention). A leaf, non-``test_``
module so pytest never collects it directly, mirroring the existing
``tests/_libabigail.py``/``tests/_comparability_gate_helpers.py`` pattern
for a helper module shared across sibling test files.
"""

from __future__ import annotations

from abicheck.dumper_clang import _ClangAstParser


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _record(name: str, *inner: dict, bases: list[dict] | None = None) -> dict:
    node = {
        "kind": "CXXRecordDecl",
        "name": name,
        "tagUsed": "struct",
        "loc": {"file": "include/foo.h", "line": 1},
        "completeDefinition": True,
        "inner": list(inner),
    }
    if bases:
        node["bases"] = bases
    return node


def _base(qualtype: str, *, is_virtual: bool = False) -> dict:
    return {"type": {"qualType": qualtype}, "access": "public", "isVirtual": is_virtual}


def _method(
    name: str,
    mangled: str,
    *,
    virtual: bool = False,
    override_attr: bool = False,
    params: list[str] | None = None,
    is_const: bool = False,
) -> dict:
    qual = f"void ({', '.join(params or [])})" + (" const" if is_const else "")
    inner = [{"kind": "ParmVarDecl", "type": {"qualType": p}} for p in (params or [])]
    if override_attr:
        inner.append({"kind": "OverrideAttr"})
    node: dict = {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": mangled,
        "type": {"qualType": qual},
        "inner": inner,
    }
    if virtual:
        node["virtual"] = True
    return node


def _dtor(mangled: str, *, virtual: bool = False, implicit: bool = False) -> dict:
    node: dict = {"kind": "CXXDestructorDecl", "mangledName": mangled}
    if virtual:
        node["virtual"] = True
    if implicit:
        node["isImplicit"] = True
    return node


def _types(root: dict) -> dict[str, object]:
    return {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}


def _specialization(name: str, *inner: dict, type_args: list[str]) -> dict:
    """A ``ClassTemplateSpecializationDecl`` node, mirroring real clang
    output: one ``TemplateArgument`` child per type argument (each carrying
    only the ``type.qualType`` this module's spelling-reconstruction
    reads), followed by *inner*'s own children.
    """
    args = [{"kind": "TemplateArgument", "type": {"qualType": t}} for t in type_args]
    return {
        "kind": "ClassTemplateSpecializationDecl",
        "name": name,
        "completeDefinition": True,
        "inner": [*args, *inner],
    }


def _forward_specialization(name: str, *, type_args: list[str]) -> dict:
    """A forward-declared explicit specialization node -- no
    ``completeDefinition``, no member children -- sharing the same spelling
    a later complete definition would."""
    args = [{"kind": "TemplateArgument", "type": {"qualType": t}} for t in type_args]
    return {
        "kind": "ClassTemplateSpecializationDecl",
        "name": name,
        "inner": args,
    }
