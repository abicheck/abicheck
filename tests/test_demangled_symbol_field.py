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

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Variable, Visibility

_MANGLED = "_ZN3Foo3barEv"
_DEMANGLED = "Foo::bar()"
_VAR_MANGLED = "_ZN3Foo7counterE"
_VAR_DEMANGLED = "Foo::counter"
_DEMANGLE_TABLE = {_MANGLED: _DEMANGLED, _VAR_MANGLED: _VAR_DEMANGLED}


def _snapshot(functions: list[Function] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        functions=functions or [],
        elf_only_mode=True,
    )


@pytest.fixture
def deterministic_demangle(monkeypatch):
    """Stub ``compare.elf_only_demangle``'s ``demangle``/``demangle_batch``
    with a small, deterministic lookup table instead of depending on a real
    ``cxxfilt``/``c++filt`` being present on the host -- neither is
    guaranteed (Windows CI in particular ships neither by default: no
    ``cxxfilt`` package in ``[dev]``, and ``c++filt`` is a GNU binutils tool,
    not part of the MSVC toolchain), so a test asserting a *specific*
    demangled spelling must not depend on one (CodeRabbit review). Only the
    tests that assert an exact demangled string use this -- the "stays
    None"/"omitted" tests need no demangler at all (visibility short-circuits
    before ``demangle()`` is ever called), and
    ``TestDemanglePrewarmScoping`` deliberately wraps the *real*
    ``demangle_batch`` since it is testing the real batching behavior, not a
    specific demangled spelling.
    """
    import abicheck.compare.elf_only_demangle as elf_only_demangle_mod

    def fake_demangle(mangled, **kwargs):
        return _DEMANGLE_TABLE.get(mangled)

    def fake_demangle_batch(names, **kwargs):
        return {n: _DEMANGLE_TABLE[n] for n in names if n in _DEMANGLE_TABLE}

    monkeypatch.setattr(elf_only_demangle_mod, "demangle", fake_demangle)
    monkeypatch.setattr(elf_only_demangle_mod, "demangle_batch", fake_demangle_batch)


class TestFunctionRemovedElfOnly:
    def test_demangled_symbol_is_populated_for_an_elf_only_removal(
        self, deterministic_demangle
    ) -> None:
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
        self, deterministic_demangle
    ) -> None:
        mangled = _VAR_MANGLED
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
    def test_json_carries_demangled_symbol_for_an_elf_only_removal(
        self, deterministic_demangle
    ) -> None:
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

    def test_sarif_carries_demangled_symbol_for_an_elf_only_removal(
        self, deterministic_demangle
    ) -> None:
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


class TestSchema:
    def test_demangled_symbol_is_declared_in_the_report_schema(
        self, deterministic_demangle
    ) -> None:
        # Codex review, fresh evidence: the field existed in JSON output
        # before the schema declared it -- additionalProperties kept
        # validation permissive, but a schema-driven consumer couldn't
        # discover or type the field. Assert both the canonical schema and
        # a real report carrying demangled_symbol validate together.
        pytest.importorskip("jsonschema")
        import jsonschema

        from abicheck.schemas import load_compare_report_schema

        schema = load_compare_report_schema()
        change_props = schema["$defs"]["change"]["properties"]
        assert "demangled_symbol" in change_props
        assert change_props["demangled_symbol"]["type"] == "string"

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

        from abicheck.reporter import to_json

        report = json.loads(to_json(result))
        jsonschema.validate(instance=report, schema=schema)


class TestDemanglePrewarmScoping:
    """Codex review, fresh evidence: the first cut of the batch-demangle
    prewarm (``diff_symbols._prewarm_elf_only_demangling``) warmed every
    ELF_ONLY-visibility OLD-side name regardless of whether the new side
    still exported it -- so an *unchanged* large ELF-only C++ library
    demangled its entire export table on every comparison, even though
    ``demangled_symbol`` is only ever read for a removal finding. Assert the
    prewarm batch excludes a name that survives unchanged and includes one
    that is genuinely removed.
    """

    def test_function_prewarm_excludes_unchanged_symbols(self, monkeypatch) -> None:
        import abicheck.compare.elf_only_demangle as elf_only_demangle_mod

        real_demangle_batch = elf_only_demangle_mod.demangle_batch
        calls: list[list[str]] = []

        def spy(names, **kwargs):
            names = list(names)
            calls.append(names)
            return real_demangle_batch(names, **kwargs)

        monkeypatch.setattr(elf_only_demangle_mod, "demangle_batch", spy)

        stable = "_ZN3Foo6stableEv"
        removed = "_ZN3Foo7removedEv"
        old = _snapshot(
            [
                Function(
                    name=stable,
                    mangled=stable,
                    return_type="void",
                    visibility=Visibility.ELF_ONLY,
                ),
                Function(
                    name=removed,
                    mangled=removed,
                    return_type="void",
                    visibility=Visibility.ELF_ONLY,
                ),
            ]
        )
        new = _snapshot(
            [
                Function(
                    name=stable,
                    mangled=stable,
                    return_type="void",
                    visibility=Visibility.ELF_ONLY,
                ),
            ]
        )
        compare(old, new)

        assert calls, "demangle_batch was never called"
        prewarmed = calls[0]
        assert removed in prewarmed
        assert stable not in prewarmed

    def test_variable_prewarm_excludes_unchanged_symbols(self, monkeypatch) -> None:
        import abicheck.compare.elf_only_demangle as elf_only_demangle_mod

        real_demangle_batch = elf_only_demangle_mod.demangle_batch
        calls: list[list[str]] = []

        def spy(names, **kwargs):
            names = list(names)
            calls.append(names)
            return real_demangle_batch(names, **kwargs)

        monkeypatch.setattr(elf_only_demangle_mod, "demangle_batch", spy)

        stable = "_ZN3Foo6stableE"
        removed = "_ZN3Foo7removedE"
        old = AbiSnapshot(
            library="libx.so",
            version="1.0",
            variables=[
                Variable(
                    name=stable,
                    mangled=stable,
                    type="int",
                    visibility=Visibility.ELF_ONLY,
                ),
                Variable(
                    name=removed,
                    mangled=removed,
                    type="int",
                    visibility=Visibility.ELF_ONLY,
                ),
            ],
            elf_only_mode=True,
        )
        new = AbiSnapshot(
            library="libx.so",
            version="1.0",
            variables=[
                Variable(
                    name=stable,
                    mangled=stable,
                    type="int",
                    visibility=Visibility.ELF_ONLY,
                ),
            ],
        )
        compare(old, new)

        # Two calls: one from _diff_functions (empty), one from _diff_variables.
        prewarmed = [n for call in calls for n in call]
        assert removed in prewarmed
        assert stable not in prewarmed
