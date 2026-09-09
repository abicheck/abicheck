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

"""`Change.demangled_symbol` (Codex review, item 8): an export-table-only
(``Visibility.ELF_ONLY``) removal's ``symbol``/``old_value`` is the raw
mangled name -- unlike a header-backed removal, whose ``Function.name`` is
already demangled text from header-AST parsing (see
``extract.export_symbol_identity.itanium_export_function``'s own
docstring). A machine format (JSON/SARIF) never demangles ``symbol``/
``old_value`` themselves by design, so ``demangled_symbol`` is the one
deliberate, opt-out-free exception: a reader gets a readable name without
losing the raw mangled spelling machine tooling needs to match on.
"""

from __future__ import annotations

import json

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Variable, Visibility

_MANGLED = "_ZN3Foo3barEv"
_DEMANGLED = "Foo::bar()"


def _snapshot(functions: list[Function] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        functions=functions or [],
        elf_only_mode=True,
    )


class TestFunctionRemovedElfOnly:
    def test_demangled_symbol_is_populated_for_an_elf_only_removal(self) -> None:
        old = _snapshot(
            [
                Function(
                    name=_MANGLED,  # raw mangled -- no header confirmation
                    mangled=_MANGLED,
                    return_type="?",
                    visibility=Visibility.ELF_ONLY,
                )
            ]
        )
        new = _snapshot([])
        result = compare(old, new)
        removed = [
            c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED_ELF_ONLY
        ]
        assert len(removed) == 1
        assert removed[0].demangled_symbol == _DEMANGLED
        # The raw fields stay raw -- machine-format matching is unaffected.
        assert removed[0].symbol == _MANGLED
        assert removed[0].old_value == _MANGLED

    def test_demangled_symbol_is_none_for_a_header_backed_removal(self) -> None:
        old = _snapshot(
            [
                Function(
                    name="Foo::bar()",  # already demangled by header parsing
                    mangled=_MANGLED,
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                )
            ]
        )
        new = _snapshot([])
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 1
        assert removed[0].demangled_symbol is None


class TestVarRemovedElfOnly:
    def test_demangled_symbol_is_populated_for_an_elf_only_variable_removal(
        self,
    ) -> None:
        mangled = "_ZN3Foo7counterE"
        old = AbiSnapshot(
            library="libx.so",
            version="1.0",
            variables=[
                Variable(
                    name=mangled,
                    mangled=mangled,
                    type="int",
                    visibility=Visibility.ELF_ONLY,
                )
            ],
            elf_only_mode=True,
        )
        new = AbiSnapshot(library="libx.so", version="1.0", variables=[])
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.VAR_REMOVED]
        assert len(removed) == 1
        assert removed[0].demangled_symbol == "Foo::counter"


class TestReportSurfacing:
    def test_json_carries_demangled_symbol_for_an_elf_only_removal(self) -> None:
        from abicheck.reporter import to_json

        old = _snapshot(
            [
                Function(
                    name=_MANGLED,
                    mangled=_MANGLED,
                    return_type="?",
                    visibility=Visibility.ELF_ONLY,
                )
            ]
        )
        new = _snapshot([])
        result = compare(old, new)
        report = json.loads(to_json(result))
        removed = [
            c
            for c in report["changes"]
            if c["kind"] == ChangeKind.FUNC_REMOVED_ELF_ONLY.value
        ]
        assert len(removed) == 1
        assert removed[0]["demangled_symbol"] == _DEMANGLED

    def test_json_omits_demangled_symbol_for_a_header_backed_removal(self) -> None:
        from abicheck.reporter import to_json

        old = _snapshot(
            [
                Function(
                    name="Foo::bar()",
                    mangled=_MANGLED,
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                )
            ]
        )
        new = _snapshot([])
        result = compare(old, new)
        report = json.loads(to_json(result))
        removed = [
            c for c in report["changes"] if c["kind"] == ChangeKind.FUNC_REMOVED.value
        ]
        assert len(removed) == 1
        assert "demangled_symbol" not in removed[0]

    def test_sarif_carries_demangled_symbol_for_an_elf_only_removal(self) -> None:
        from abicheck.sarif import to_sarif

        old = _snapshot(
            [
                Function(
                    name=_MANGLED,
                    mangled=_MANGLED,
                    return_type="?",
                    visibility=Visibility.ELF_ONLY,
                )
            ]
        )
        new = _snapshot([])
        result = compare(old, new)
        doc = to_sarif(result)
        results = [
            r
            for r in doc["runs"][0]["results"]
            if r["ruleId"] == ChangeKind.FUNC_REMOVED_ELF_ONLY.value
        ]
        assert len(results) == 1
        assert results[0]["properties"]["demangledSymbol"] == _DEMANGLED
