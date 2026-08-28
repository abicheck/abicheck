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

"""ADR-063 Phase 0: the direct-clang backend now constructs ``Fact[...]``
siblings explicitly at parse time, mirroring ``dumper_castxml.py``.

Unlike CastXML, the direct-clang backend genuinely evaluates
``is_va_list`` per parameter (``_clang_param_is_va_list``), so its
``is_va_list_fact`` states ``Fact.present(...)`` with whatever the check
returned — never ``Fact.unsupported()``, which stays CastXML-specific.
"""
from __future__ import annotations

from abicheck.dumper_clang import _ClangAstParser
from abicheck.model.fact import FactStatus


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def test_polymorphic_record_facts_present_and_match_legacy_fields() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "bases": [
                {"type": {"qualType": "Base"}, "access": "public", "isVirtual": False},
            ],
            "inner": [
                {
                    "kind": "CXXMethodDecl",
                    "name": "run",
                    "type": {"qualType": "void ()"},
                    "mangledName": "_ZN6Widget3runEv",
                    "virtual": True,
                }
            ],
        }
    )
    (rec,) = [t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "Widget"]
    assert rec.bases == ["Base"]
    assert rec.bases_fact.status is FactStatus.PRESENT
    assert rec.bases_fact.value == rec.bases
    assert rec.virtual_bases_fact.status is FactStatus.PRESENT
    assert rec.virtual_bases_fact.value == rec.virtual_bases == []
    assert rec.vtable_fact.status is FactStatus.PRESENT
    assert rec.vtable_fact.value == rec.vtable
    assert rec.vptr_offset_bits_fact.status is FactStatus.PRESENT
    assert rec.vptr_offset_bits_fact.value == rec.vptr_offset_bits


def test_opaque_record_facts_present_and_match_legacy_empty_values() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Opaque",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 10},
        }
    )
    (rec,) = [t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "Opaque"]
    assert rec.is_opaque is True
    assert rec.bases == rec.virtual_bases == rec.vtable == []
    assert rec.vptr_offset_bits is None
    assert rec.bases_fact.status is FactStatus.PRESENT
    assert rec.bases_fact.value == []
    assert rec.vtable_fact.status is FactStatus.PRESENT
    assert rec.vptr_offset_bits_fact.status is FactStatus.PRESENT
    assert rec.vptr_offset_bits_fact.value is None


def test_param_is_va_list_fact_is_present_not_unsupported() -> None:
    # Direct-clang genuinely evaluates the va_list check per parameter --
    # UNSUPPORTED is reserved for a producer (CastXML) that structurally
    # never can, so this must stay Fact.present(...), matching whatever
    # the check returned.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 3},
            "mangledName": "_Z1fi",
            "type": {"qualType": "void (int)"},
            "inner": [
                {"kind": "ParmVarDecl", "name": "a", "type": {"qualType": "int"}},
            ],
        }
    )
    (fn,) = _ClangAstParser(root, {"_Z1fi"}, set()).parse_functions()
    param = fn.params[0]
    assert param.is_va_list is False
    assert param.is_va_list_fact.status is FactStatus.PRESENT
    assert param.is_va_list_fact.value is False
