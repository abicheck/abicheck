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

"""``<block>_from_dict`` decoders for ``serialization.py``'s optional
platform/language sub-blocks (ELF/PE/Mach-O metadata, DWARF, SYCL, kABI,
NumPy C-API, Python extension/API surface).

Split out into a sibling leaf module once ``serialization.py`` grew past
its ``architecture/debt.yaml`` no-growth adoption baseline: each of these
functions is fully self-contained (only lazy imports of the owning
platform/language module, no dependency on ``serialization.py``'s own
module-level state) — mechanical extraction, unchanged function bodies.

Deliberately **not** placed under ``storage/`` (ADR-061): every parser
dataclass here (``ElfMetadata``, ``PeMetadata``, ``DwarfMetadata``, ...)
lives in a flat, unclassified parser module (``elf_metadata.py``,
``pe_metadata.py``, ``dwarf_metadata.py``, ...), and `storage`'s own
``may_import: [model]`` forbids a `storage -> extract` edge — the same
"genuine behavioral edge" `architecture/debt.yaml`'s own
``abicheck/serialization.py`` entry already documents for exactly this
reason. This module stays a flat root module, registered in
``architecture/modules.yaml``'s ``legacy_root_modules``, the same way
``serialization.py`` itself is exempted via ``public_root_surfaces``.
"""

from __future__ import annotations

from typing import Any


def elf_from_dict(elf: dict[str, Any], schema_version: int) -> Any:
    from dataclasses import replace

    from .elf_metadata import (
        ElfImport,
        ElfMetadata,
        ElfSymbol,
        SymbolBinding,
        SymbolType,
    )
    from .storage.fact_codec import decode_fact

    # ADR-063 Phase 5 (seventh batch): the schema_version ElfMetadata's own
    # three case-(b) *_fact siblings started being persisted at.
    _min_schema = 37
    dynamic_flags_fact = decode_fact(
        elf.get("dynamic_flags_fact"), schema_version, min_schema_version=_min_schema
    )
    if dynamic_flags_fact is not None and dynamic_flags_fact.value is not None:
        # JSON has no frozenset -- reconstruct it, matching the legacy
        # dynamic_flags field's own frozenset(...) reconstruction below,
        # since Fact[frozenset[str] | None] is the declared value type.
        dynamic_flags_fact = replace(
            dynamic_flags_fact, value=frozenset(dynamic_flags_fact.value)
        )

    syms = [
        ElfSymbol(
            name=s["name"],
            binding=SymbolBinding(s.get("binding", "global")),
            sym_type=SymbolType(s.get("sym_type", "func")),
            size=s.get("size", 0),
            version=s.get("version", ""),
            is_default=s.get("is_default", True),
            visibility=s.get("visibility", "default"),
            value_alignment=s.get("value_alignment", 0),
        )
        for s in elf.get("symbols", [])
    ]
    imports = [
        ElfImport(
            name=i["name"],
            binding=SymbolBinding(i.get("binding", "global")),
            sym_type=SymbolType(i.get("sym_type", "notype")),
            version=i.get("version", ""),
            is_default=i.get("is_default", True),
            version_soname=i.get("version_soname", ""),
        )
        for i in elf.get("imports", [])
    ]
    return ElfMetadata(
        soname=elf.get("soname", ""),
        needed=elf.get("needed", []),
        rpath=elf.get("rpath", ""),
        runpath=elf.get("runpath", ""),
        versions_defined=elf.get("versions_defined", []),
        versions_required=elf.get("versions_required", {}),
        symbols=syms,
        imports=imports,
        interpreter=elf.get("interpreter", ""),
        has_executable_stack=elf.get("has_executable_stack", False),
        relro=elf.get("relro", "none"),
        bind_now=elf.get("bind_now", False),
        is_pie=elf.get("is_pie", False),
        has_stack_canary=elf.get("has_stack_canary", False),
        has_fortify_source=elf.get("has_fortify_source", False),
        has_writable_executable_segment=elf.get(
            "has_writable_executable_segment", False
        ),
        is_symbolic=elf.get("is_symbolic", False),
        has_textrel=elf.get("has_textrel", False),
        pointer_size=elf.get("pointer_size", 8),
        machine=elf.get("machine", ""),
        # Legacy snapshots (written before elf_class existed) carry no class
        # field; derive it from pointer_size (4→32, 8→64) rather than hard-coding
        # 64, so a saved 32-bit baseline does not false-positive elf_class_changed.
        elf_class=elf.get("elf_class", 32 if elf.get("pointer_size", 8) == 4 else 64),
        osabi=elf.get("osabi", ""),
        e_flags=elf.get("e_flags", 0),
        abi_flags=frozenset(elf.get("abi_flags", [])),
        has_static_tls=elf.get("has_static_tls", False),
        has_tls_symbols=elf.get("has_tls_symbols", False),
        gnu_properties=frozenset(elf.get("gnu_properties", [])),
        has_dt_relr=elf.get("has_dt_relr", False),
        hash_styles=frozenset(elf.get("hash_styles", [])),
        ei_data=elf.get("ei_data", ""),
        min_kernel_version=elf.get("min_kernel_version", ""),
        # Tri-state loader-contract fields: absent key (legacy snapshot) must
        # stay None ("not captured"), not default to a comparable value.
        dynamic_flags=(
            frozenset(elf["dynamic_flags"])
            if elf.get("dynamic_flags") is not None
            else None
        ),
        has_init=elf.get("has_init"),
        has_fini=elf.get("has_fini"),
        dynamic_flags_fact=dynamic_flags_fact,
        has_init_fact=decode_fact(
            elf.get("has_init_fact"), schema_version, min_schema_version=_min_schema
        ),
        has_fini_fact=decode_fact(
            elf.get("has_fini_fact"), schema_version, min_schema_version=_min_schema
        ),
    )


def pe_from_dict(pe: dict[str, Any], schema_version: int) -> Any:
    from .pe_metadata import PeExport, PeMetadata, PeSymbolType
    from .storage.fact_codec import decode_fact

    exports = [
        PeExport(
            name=x["name"],
            ordinal=x.get("ordinal", 0),
            sym_type=PeSymbolType(x.get("sym_type", "exported")),
            forwarder=x.get("forwarder", ""),
        )
        for x in pe.get("exports", [])
    ]
    return PeMetadata(
        machine=pe.get("machine", ""),
        characteristics=pe.get("characteristics", 0),
        dll_characteristics=pe.get("dll_characteristics", 0),
        exports=exports,
        imports=pe.get("imports", {}),
        # Tri-state: absent key (legacy snapshot) stays None ("not captured").
        delay_imports=pe.get("delay_imports"),
        file_version=pe.get("file_version", ""),
        product_version=pe.get("product_version", ""),
        subsystem_version=pe.get("subsystem_version", ""),
        # ADR-063 Phase 5 (seventh batch): the schema_version PeMetadata's
        # own delay_imports_fact sibling started being persisted at.
        delay_imports_fact=decode_fact(
            pe.get("delay_imports_fact"), schema_version, min_schema_version=37
        ),
    )


def macho_from_dict(macho: dict[str, Any], schema_version: int) -> Any:
    from .macho_metadata import MachoExport, MachoMetadata, MachoSymbolType
    from .storage.fact_codec import decode_fact

    exports = [
        MachoExport(
            name=x["name"],
            sym_type=MachoSymbolType(x.get("sym_type", "exported")),
            is_weak=x.get("is_weak", False),
        )
        for x in macho.get("exports", [])
    ]
    return MachoMetadata(
        cpu_type=macho.get("cpu_type", ""),
        cpu_types=macho.get("cpu_types", []),
        filetype=macho.get("filetype", ""),
        flags=macho.get("flags", 0),
        install_name=macho.get("install_name", ""),
        dependent_libs=macho.get("dependent_libs", []),
        reexported_libs=macho.get("reexported_libs", []),
        exports=exports,
        imported_symbols=macho.get("imported_symbols", []),
        current_version=macho.get("current_version", ""),
        compat_version=macho.get("compat_version", ""),
        min_os_version=macho.get("min_os_version", ""),
        # Tri-state: absent key (legacy snapshot) stays None ("not captured").
        rpaths=macho.get("rpaths"),
        # ADR-063 Phase 5 (seventh batch): the schema_version MachoMetadata's
        # own rpaths_fact sibling started being persisted at.
        rpaths_fact=decode_fact(
            macho.get("rpaths_fact"), schema_version, min_schema_version=37
        ),
    )


def dwarf_from_dict(d: dict[str, Any]) -> Any:
    from .dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout

    structs = {
        name: StructLayout(
            name=s.get("name", name),
            byte_size=s.get("byte_size", 0),
            alignment=s.get("alignment", 0),
            fields=[
                FieldInfo(
                    name=f.get("name", ""),
                    type_name=f.get("type_name", "unknown"),
                    byte_offset=f.get("byte_offset", 0),
                    byte_size=f.get("byte_size", 0),
                    bit_offset=f.get("bit_offset", 0),
                    bit_size=f.get("bit_size", 0),
                )
                for f in s.get("fields", [])
            ],
            is_union=s.get("is_union", False),
        )
        for name, s in d.get("structs", {}).items()
    }

    enums = {
        name: EnumInfo(
            name=e.get("name", name),
            underlying_byte_size=e.get("underlying_byte_size", 0),
            members=e.get("members", {}),
        )
        for name, e in d.get("enums", {}).items()
    }

    has_dwarf = d.get("has_dwarf", False)
    return DwarfMetadata(
        structs=structs,
        enums=enums,
        base_types={k: int(v) for k, v in d.get("base_types", {}).items()},
        has_dwarf=has_dwarf,
        # A legacy (pre-v44) block carries no debug-evidence provenance at
        # all, so this fails closed rather than claiming a real parse: a
        # has_dwarf block degrades to "presence_only" (the cheapest tier,
        # not "parsed" -- has_dwarf alone never proved a completed parse),
        # and no block at all degrades to "not_available". Mirrors the
        # pre-merge dwarf_metadata_codec.legacy_dwarf_evidence_kwargs().
        evidence_source=d.get("evidence_source", "unknown"),
        evidence_state=d.get(
            "evidence_state", "presence_only" if has_dwarf else "not_available"
        ),
        cu_total=d.get("cu_total", 0),
        cu_failed=d.get("cu_failed", 0),
    )


def dwarf_advanced_from_dict(d: dict[str, Any]) -> Any:
    from .dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo

    tc = d.get("toolchain", {})
    toolchain = ToolchainInfo(
        producer_string=tc.get("producer_string", ""),
        compiler=tc.get("compiler", ""),
        version=tc.get("version", ""),
        abi_flags=set(tc.get("abi_flags", [])),
        vector_abi_flags=set(tc.get("vector_abi_flags", [])),
    )
    adv_has_dwarf = d.get("has_dwarf", False)
    return AdvancedDwarfMetadata(
        has_dwarf=adv_has_dwarf,
        target_arch=d.get("target_arch", ""),
        toolchain=toolchain,
        calling_conventions=d.get("calling_conventions", {}),
        value_abi_traits=d.get("value_abi_traits", {}),
        return_value_sizes=d.get("return_value_sizes", {}),
        return_memory_classified=set(d.get("return_memory_classified", [])),
        packed_structs=set(d.get("packed_structs", [])),
        all_struct_names=set(d.get("all_struct_names", [])),
        frame_registers=d.get("frame_registers", {}),
        callee_saved_regs={
            k: frozenset(v) for k, v in d.get("callee_saved_regs", {}).items()
        },
        # See the matching comment in dwarf_from_dict.
        evidence_state=d.get(
            "evidence_state", "presence_only" if adv_has_dwarf else "not_available"
        ),
        cu_total=d.get("cu_total", 0),
        cu_failed=d.get("cu_failed", 0),
    )


def sycl_from_dict(d: dict[str, Any]) -> Any:
    from .sycl_metadata import SyclMetadata, SyclPluginInfo

    plugins = [
        SyclPluginInfo(
            name=p.get("name", ""),
            library=p.get("library", ""),
            interface_type=p.get("interface_type", "pi"),
            pi_version=p.get("pi_version", ""),
            entry_points=p.get("entry_points", []),
            backend_type=p.get("backend_type", ""),
            min_driver_version=p.get("min_driver_version"),
        )
        for p in d.get("plugins", [])
    ]
    return SyclMetadata(
        implementation=d.get("implementation", ""),
        runtime_version=d.get("runtime_version", ""),
        pi_version=d.get("pi_version", ""),
        plugins=plugins,
        plugin_search_paths=d.get("plugin_search_paths", []),
    )


def kabi_from_dict(d: dict[str, Any]) -> Any:
    from .symvers_metadata import KabiEntry, KabiMetadata

    entries = {
        sym: KabiEntry(
            crc=e.get("crc", ""),
            symbol=e.get("symbol", sym),
            module=e.get("module", ""),
            export_type=e.get("export_type", ""),
            namespace=e.get("namespace", ""),
        )
        for sym, e in (d.get("entries", {}) or {}).items()
    }
    return KabiMetadata(entries=entries)


def numpy_capi_from_dict(d: dict[str, Any]) -> Any:
    from .numpy_capi import NumPyCapiSurface

    return NumPyCapiSurface(
        consumes_array_api=d.get("consumes_array_api", False),
        consumes_ufunc_api=d.get("consumes_ufunc_api", False),
        capi_target_version=d.get("capi_target_version"),
    )


def python_ext_from_dict(d: dict[str, Any]) -> Any:
    from .python_ext import PythonExtMetadata

    declared = d.get("declared_abi3")
    # JSON has no tuples: a persisted (major, minor) floor round-trips as a list.
    declared_abi3 = (
        (int(declared[0]), int(declared[1]))
        if isinstance(declared, (list, tuple)) and len(declared) == 2
        else None
    )
    return PythonExtMetadata(
        module_name=d.get("module_name"),
        init_symbol=d.get("init_symbol"),
        python_major=d.get("python_major"),
        soabi_tag=d.get("soabi_tag"),
        limited_api=bool(d.get("limited_api", False)),
        declared_abi3=declared_abi3,
        free_threaded=bool(d.get("free_threaded", False)),
        cpython_imports=list(d.get("cpython_imports", [])),
        cpython_dlls=list(d.get("cpython_dlls", [])),
    )


def python_api_from_dict(d: dict[str, Any]) -> Any:
    from .python_api import PyClass, PyFunction, PyParameter, PythonApiSurface

    def _param(p: dict[str, Any]) -> PyParameter:
        return PyParameter(
            name=p.get("name", ""),
            kind=p.get("kind", "positional_or_keyword"),
            has_default=bool(p.get("has_default", False)),
            annotation=p.get("annotation"),
        )

    def _func(fn: dict[str, Any]) -> PyFunction:
        return PyFunction(
            name=fn.get("name", ""),
            parameters=[_param(p) for p in fn.get("parameters", [])],
            return_annotation=fn.get("return_annotation"),
            is_async=bool(fn.get("is_async", False)),
            descriptor=fn.get("descriptor", "function"),
            overloads=[_func(v) for v in fn.get("overloads", [])],
        )

    functions = {name: _func(fn) for name, fn in (d.get("functions") or {}).items()}
    classes = {
        name: PyClass(
            name=c.get("name", name),
            methods={m: _func(fn) for m, fn in (c.get("methods") or {}).items()},
        )
        for name, c in (d.get("classes") or {}).items()
    }
    return PythonApiSurface(
        module_name=d.get("module_name"),
        source=d.get("source", "stub"),
        source_path=d.get("source_path"),
        functions=functions,
        classes=classes,
        parse_ok=bool(d.get("parse_ok", True)),
    )
