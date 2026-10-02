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

"""H1's optional evidence containers: realistic values for every optional
``AbiSnapshot`` container, the corpus pairs whose break lives in one, and the
PE/Mach-O re-platforming of an ELF corpus snapshot.

Split from ``_family_f1_harness.py`` (which imports this) only to keep each
file legible. Every value here was checked against the code that reads it in
``compare()``: four containers can carry a break on their own
(``dwarf_advanced``, ``sycl``, ``kabi``, ``python_api``); the rest feed
comparability, assurance, coverage or nothing, and are ablated so that
dropping one is proven not to turn a break clean silently.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from abicheck.buildsource.model import BuildSourceRef, LayerConfidence
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.comparability import compute_extraction_contract
from abicheck.elf_metadata import SymbolType
from abicheck.extract.export_table_read import finish_binary_snapshot
from abicheck.model import AbiSnapshot, Fact
from abicheck.model.build_mode_facts import (
    BuildMode,
    CompilerFamily,
    CxxStandard,
    GlibcxxDualAbi,
    StdlibFamily,
)
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.model.extraction_contract import DependencyInfo
from abicheck.model.extraction_scope import ExtractionScope
from abicheck.model.kabi_facts import KabiEntry, KabiMetadata
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.ownership_rules import OwnershipRules
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.model.python_facts import (
    NumPyCapiSurface,
    PyFunction,
    PyParameter,
    PythonApiSurface,
    PythonExtMetadata,
)
from abicheck.model.sycl_facts import SyclMetadata, SyclPluginInfo

__all__ = [
    "BREAK_CASE_CONTAINERS",
    "PLATFORM_CASES",
    "base_containers",
    "on_platform",
]


def _advanced(convention: str = "normal") -> AdvancedDwarfMetadata:
    return AdvancedDwarfMetadata(
        has_dwarf=True,
        target_arch="x86_64",
        toolchain=ToolchainInfo(
            producer_string="GNU C++17 13.2.0 -O2", compiler="GCC", version="13.2.0"
        ),
        calling_conventions={"_Z3fooi": convention},
        all_struct_names={"Point"},
        frame_registers={"_Z3fooi": "rsp"},
    )


def _sycl(
    entry_points: tuple[str, ...] = ("piPlatformsGet", "piDevicesGet"),
) -> SyclMetadata:
    return SyclMetadata(
        implementation="dpcpp",
        runtime_version="2025.2.0",
        pi_version="14.38",
        plugins=[
            SyclPluginInfo(
                name="level_zero",
                library="libpi_level_zero.so",
                interface_type="pi",
                pi_version="14.38",
                entry_points=list(entry_points),
                backend_type="level_zero",
            )
        ],
    )


def _kabi(crc: str = "0x1") -> KabiMetadata:
    return KabiMetadata(
        entries={
            "x_entry": KabiEntry(
                crc=crc, symbol="x_entry", module="vmlinux", export_type="EXPORT_SYMBOL"
            )
        }
    )


def _python_api(params: tuple[str, ...] = ("a",)) -> PythonApiSurface:
    return PythonApiSurface(
        module_name="x",
        source_path="/x.pyi",
        functions={
            "f": PyFunction(
                name="f",
                parameters=[PyParameter(name=p) for p in params],
                return_annotation="int",
            )
        },
    )


def _entity(kind: str, name: str, value: str) -> SourceEntity:
    return SourceEntity(
        id=f"{kind}:{name}",
        kind=kind,
        qualified_name=name,
        value=value,
        visibility="public_header",
        confidence=LayerConfidence.HIGH,
    )


def base_containers() -> dict[str, Any]:
    """Every optional container a full-evidence ELF capture of the corpus
    library could carry, identical on both sides of every pair."""
    return {
        "dwarf_advanced": _advanced(),
        "sycl": _sycl(),
        "kabi": _kabi(),
        "python_api": _python_api(),
        "python_ext": PythonExtMetadata(
            module_name="x",
            init_symbol="PyInit_x",
            python_major=3,
            soabi_tag="abi3",
            limited_api=True,
            declared_abi3=(3, 9),
            cpython_imports=["PyLong_FromLong"],
        ),
        "numpy_capi": NumPyCapiSurface(
            consumes_array_api=True,
            consumes_ufunc_api=False,
            capi_target_version="1.23",
        ),
        "extraction_scope": ExtractionScope(
            ownership_rules=OwnershipRules(target_roots=("inc",))
        ),
        "dependency_info": DependencyInfo(
            nodes=[{"soname": "libc.so.6"}],
            edges=[{"from": "libx.so.1", "to": "libc.so.6"}],
        ),
        "build_mode": BuildMode(
            compiler_family=CompilerFamily.GCC,
            language_std=CxxStandard.CXX17,
            stdlib=StdlibFamily.LIBSTDCXX,
            glibcxx_dual_abi=GlibcxxDualAbi.CXX11,
        ),
        "build_source_pack": BuildSourceRef(
            content_hash="sha256:" + "0" * 64,
            path_hint="libx.evidence/",
            coverage_summary={},
        ),
        "build_source": BuildSourcePack(
            root="",
            source_abi=SourceAbiSurface(
                library="libx.so.1",
                reachable_declarations=[_entity("function", "foo", "")],
                reachable_macros=[_entity("macro", "X_VERSION", "1")],
            ),
        ),
        "contract": compute_extraction_contract(
            compiler_family="gcc",
            compiler_version="13.2.0",
            language_standard="c++17",
            target_triple="x86_64-pc-linux-gnu",
            pointer_width=64,
            endianness="little",
            declared_headers=[Path("/inc/x.h")],
            public_header_paths=[Path("/inc/x.h")],
            l2_frontend_ran=True,
            frontend_context_kind="castxml",
            compiler_identity_status="present",
        ),
    }


#: Corpus pairs whose break lives in one container: NEW's override of the
#: base value. Each produces BREAKING/API_BREAK through ``compare()`` alone.
BREAK_CASE_CONTAINERS: dict[str, dict[str, Any]] = {
    "calling_convention_changed": {"dwarf_advanced": _advanced("pass_by_reference")},
    "sycl_entry_point_removed": {"sycl": _sycl(("piPlatformsGet",))},
    "kabi_crc_changed": {"kabi": _kabi("0x2")},
    "python_api_parameter_added": {"python_api": _python_api(("a", "b"))},
}


def on_platform(snap: AbiSnapshot, platform: str, *, read: bool = True) -> AbiSnapshot:
    """*snap* (an ELF corpus snapshot) as the same library built for PE or
    Mach-O: the same exports in that format's table, with the format's own
    facts (delay imports, rpaths) captured. ``read=False`` is the block a
    failed or skipped parse leaves (no header fields, no exports)."""
    assert snap.elf is not None
    symbols = list(snap.elf.symbols)
    if platform == "pe":
        block: Any = (
            PeMetadata(
                machine="IMAGE_FILE_MACHINE_AMD64",
                exports=[
                    PeExport(name=s.name, ordinal=i + 1) for i, s in enumerate(symbols)
                ],
                delay_imports_fact=Fact.present({}),
            )
            if read
            else PeMetadata()
        )
        changes: dict[str, Any] = {"pe": block, "library": "x.dll"}
    else:
        block = (
            MachoMetadata(
                cpu_type="ARM64",
                filetype="MH_DYLIB",
                install_name="@rpath/libx.1.dylib",
                exports=[
                    MachoExport(name=s.name, is_data=s.sym_type == SymbolType.OBJECT)
                    for s in symbols
                ],
                rpaths_fact=Fact.present(["@loader_path"]),
            )
            if read
            else MachoMetadata(install_name="@rpath/libx.1.dylib")
        )
        changes = {"macho": block, "library": "libx.1.dylib"}
    return finish_binary_snapshot(
        dataclasses.replace(snap, elf=None, platform=platform, **changes)
    )


#: ELF corpus cases re-run on PE and Mach-O (a representative subset: the
#: identical pair and one break per declaration kind).
PLATFORM_CASES: tuple[str, ...] = (
    "identical",
    "func_removed",
    "var_removed",
    "return_changed",
    "struct_field_removed",
)
