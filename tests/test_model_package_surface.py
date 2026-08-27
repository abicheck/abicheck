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

"""The ``abicheck.model`` package's compatibility and ownership contract.

ADR-061 Phase 5 turned the flat ``model.py`` into a package and moved the
``*_metadata.py`` fact dataclasses into it. Two things have to stay true for
that to be behaviour-preserving: every name the flat module exported still
resolves from ``abicheck.model``, and every parser still re-exports the types
it parses into. Both are pinned here rather than left to the ~700 call sites
that would notice individually.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import abicheck.model as model

MODEL_DIR = Path(model.__file__).parent

#: Exactly the public names the flat ``abicheck/model.py`` exported before the
#: package split. A removal here breaks a documented import path.
FLAT_MODEL_EXPORTS = frozenset(
    {
        "AbiSnapshot",
        "AccessLevel",
        "COMPILER_INTERNAL_TYPES",
        "DependencyInfo",
        "ElfVisibility",
        "EnumMember",
        "EnumType",
        "ExtractionContract",
        "Function",
        "Param",
        "ParamKind",
        "RecordType",
        "ScopeOrigin",
        "SymbolBinding",
        "TypeField",
        "Variable",
        "Visibility",
        "canonicalize_type_name",
        "cv_qualifiers_only_differ",
        "func_signature_cv_only_differ",
        "is_abi_surface_type_name",
        "is_compiler_internal_type",
        "is_cxx_runtime_library",
        "is_non_abi_surface_type",
        "stdlib_namespaces_excluded",
    }
)

#: Parser module → the fact types it must keep re-exporting, and the model
#: module that now owns them.
PARSER_REEXPORTS = {
    "abicheck.elf_metadata": (
        "abicheck.model.elf_facts",
        ("ElfImport", "ElfMetadata", "ElfSymbol", "SymbolBinding", "SymbolType"),
    ),
    "abicheck.pe_metadata": (
        "abicheck.model.pe_facts",
        ("PeExport", "PeMetadata", "PeSymbolType"),
    ),
    "abicheck.macho_metadata": (
        "abicheck.model.macho_facts",
        ("MachoExport", "MachoMetadata", "MachoSymbolType"),
    ),
    "abicheck.dwarf_metadata": (
        "abicheck.model.dwarf_facts",
        ("DwarfMetadata", "EnumInfo", "FieldInfo", "StructLayout"),
    ),
    "abicheck.dwarf_advanced": (
        "abicheck.model.dwarf_facts",
        ("AdvancedDwarfMetadata", "ToolchainInfo"),
    ),
    "abicheck.sycl_metadata": (
        "abicheck.model.sycl_facts",
        ("SyclMetadata", "SyclPluginInfo"),
    ),
    "abicheck.symvers_metadata": (
        "abicheck.model.kabi_facts",
        ("KabiEntry", "KabiMetadata"),
    ),
    "abicheck.python_api": (
        "abicheck.model.python_facts",
        ("PyClass", "PyFunction", "PyParameter", "PythonApiSurface"),
    ),
    "abicheck.python_ext": ("abicheck.model.python_facts", ("PythonExtMetadata",)),
    "abicheck.numpy_capi": ("abicheck.model.python_facts", ("NumPyCapiSurface",)),
    "abicheck.build_mode": (
        "abicheck.model.build_mode_facts",
        (
            "BuildMode",
            "BuildModeProvenance",
            "CompilerFamily",
            "CxxStandard",
            "GlibcxxDualAbi",
            "StdlibFamily",
        ),
    ),
}


class TestFlatModelCompatibility:
    def test_every_flat_export_still_resolves(self) -> None:
        missing = {name for name in FLAT_MODEL_EXPORTS if not hasattr(model, name)}
        assert not missing

    def test_all_still_includes_everything_the_flat_module_exported(self) -> None:
        """`__all__` may grow with genuinely new post-migration additions
        (e.g. ADR-063's Fact[T]) — it must never *shrink* below the flat
        module's own baseline, which would break a documented import path."""
        assert FLAT_MODEL_EXPORTS <= set(model.__all__)

    def test_all_is_sorted_and_free_of_duplicates(self) -> None:
        assert model.__all__ == sorted(set(model.__all__))


class TestParserReExports:
    @pytest.mark.parametrize("parser", sorted(PARSER_REEXPORTS))
    def test_parser_re_exports_the_owning_model_type(self, parser: str) -> None:
        owner_name, names = PARSER_REEXPORTS[parser]
        parser_module = importlib.import_module(parser)
        owner = importlib.import_module(owner_name)
        for name in names:
            assert getattr(parser_module, name) is getattr(owner, name), name

    @pytest.mark.parametrize("parser", sorted(PARSER_REEXPORTS))
    def test_parser_no_longer_declares_the_type_itself(self, parser: str) -> None:
        # A second definition would silently shadow the model's own, so the two
        # halves could drift back apart without any import failing.
        _, names = PARSER_REEXPORTS[parser]
        source = Path(importlib.import_module(parser).__file__).read_text()
        declared = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef)
        }
        assert declared.isdisjoint(names)


class TestModelStaysTheInnermostRing:
    """``model`` may not import a package that produces or judges a fact."""

    FORBIDDEN = ("extract", "compare", "policy", "workflows", "report", "frontends")

    @pytest.mark.parametrize(
        "path", sorted(MODEL_DIR.rglob("*.py")), ids=lambda p: p.name
    )
    def test_no_module_imports_an_outer_package(self, path: Path) -> None:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                head = (node.module or "").split(".")[0]
                assert head not in self.FORBIDDEN, f"{path.name}: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[:1] == ["abicheck"] and len(parts) > 1:
                        assert parts[1] not in self.FORBIDDEN, alias.name

    def test_every_module_stays_within_the_production_ceiling(self) -> None:
        # The package exists to keep ownership legible; a module drifting back
        # over ADR-061's 800-line ceiling is the failure mode it prevents.
        oversized = {
            path.name: len(path.read_text().splitlines())
            for path in MODEL_DIR.rglob("*.py")
            if len(path.read_text().splitlines()) > 800
        }
        assert not oversized
