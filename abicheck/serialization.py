# Copyright 2026 Nikolay Petrov
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

"""Serialization helpers — AbiSnapshot ↔ JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .build_mode import BuildMode

from .model import (
    AbiSnapshot,
    AccessLevel,
    DependencyInfo,
    ElfVisibility,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

# Current schema version for snapshot serialization.
# Increment this whenever the snapshot format changes in a backward-incompatible way.
# v1: initial format (pre-schema-versioning; snapshots without schema_version are treated as v1)
# v2: schema_version field added (PR #89)
# v3: pe/macho metadata fields added (multi-format support)
# v4: provenance metadata (git_commit, git_tag, created_at, build_id)
# v5: build_mode capture (compiler/stdlib/std normalization)
SCHEMA_VERSION: int = 5


def _sets_to_lists(obj: Any) -> Any:
    """Recursively convert any set to a sorted list for JSON serialization.

    dataclasses.asdict() does NOT convert set → list, so json.dumps() would
    raise TypeError. This post-processes the entire dict tree.
    """
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _sets_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sets_to_lists(v) for v in obj]
    return obj


def snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
    # Reset cache fields to None before asdict() to prevent double-serialization.
    snap._func_by_mangled = None
    snap._var_by_mangled = None
    snap._type_by_name = None
    d = asdict(snap)
    d.pop("_func_by_mangled", None)
    d.pop("_var_by_mangled", None)
    d.pop("_type_by_name", None)

    # Serialize ElfMetadata enums to strings for JSON compatibility
    if d.get("elf"):
        elf = d["elf"]
        for sym in elf.get("symbols", []):
            sym["binding"] = sym["binding"] if isinstance(sym["binding"], str) else sym["binding"].value
            sym["sym_type"] = sym["sym_type"] if isinstance(sym["sym_type"], str) else sym["sym_type"].value
        for imp in elf.get("imports", []):
            imp["binding"] = imp["binding"] if isinstance(imp["binding"], str) else imp["binding"].value
            imp["sym_type"] = imp["sym_type"] if isinstance(imp["sym_type"], str) else imp["sym_type"].value

    # Serialize PeMetadata enums to strings
    if d.get("pe"):
        pe = d["pe"]
        for exp in pe.get("exports", []):
            exp["sym_type"] = exp["sym_type"] if isinstance(exp["sym_type"], str) else exp["sym_type"].value

    # Serialize MachoMetadata enums to strings
    if d.get("macho"):
        macho = d["macho"]
        for exp in macho.get("exports", []):
            exp["sym_type"] = exp["sym_type"] if isinstance(exp["sym_type"], str) else exp["sym_type"].value

    # Convert all sets → sorted lists (needed for AdvancedDwarfMetadata.packed_structs
    # and ToolchainInfo.abi_flags; json.dumps raises TypeError on set objects)
    converted: dict[str, Any] = _sets_to_lists(d)

    # BuildMode enums are (str, Enum), so dataclasses.asdict() carries
    # them through as Enum instances rather than plain strings; normalize
    # the build_mode subtree to bare strings for JSON serialization.
    bm = converted.get("build_mode")
    if isinstance(bm, dict):
        for k in ("compiler_family", "language_std", "stdlib", "glibcxx_dual_abi"):
            v = bm.get(k)
            if v is not None and not isinstance(v, str):
                bm[k] = v.value if hasattr(v, "value") else str(v)

    # Embed schema version for forward-compatibility.
    # Placed at top level so loaders can inspect it without parsing the full snapshot.
    converted["schema_version"] = SCHEMA_VERSION

    return converted


def _enum_type_from_dict(e: dict[str, Any]) -> EnumType:
    return EnumType(
        name=e["name"],
        members=[EnumMember(name=m["name"], value=m["value"]) for m in e.get("members", [])],
        underlying_type=e.get("underlying_type", "int"),
    )


def snapshot_to_json(snap: AbiSnapshot, indent: int = 2) -> str:
    return json.dumps(snapshot_to_dict(snap), indent=indent)


def _elf_from_dict(e: dict[str, Any]) -> Any:
    from .elf_metadata import (
        ElfImport,
        ElfMetadata,
        ElfSymbol,
        SymbolBinding,
        SymbolType,
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
        )
        for s in e.get("symbols", [])
    ]
    imports = [
        ElfImport(
            name=i["name"],
            binding=SymbolBinding(i.get("binding", "global")),
            sym_type=SymbolType(i.get("sym_type", "notype")),
            version=i.get("version", ""),
            is_default=i.get("is_default", True),
        )
        for i in e.get("imports", [])
    ]
    return ElfMetadata(
        soname=e.get("soname", ""),
        needed=e.get("needed", []),
        rpath=e.get("rpath", ""),
        runpath=e.get("runpath", ""),
        versions_defined=e.get("versions_defined", []),
        versions_required=e.get("versions_required", {}),
        symbols=syms,
        imports=imports,
        interpreter=e.get("interpreter", ""),
        has_executable_stack=e.get("has_executable_stack", False),
    )


def _pe_from_dict(e: dict[str, Any]) -> Any:
    from .pe_metadata import PeExport, PeMetadata, PeSymbolType
    exports = [
        PeExport(
            name=x["name"],
            ordinal=x.get("ordinal", 0),
            sym_type=PeSymbolType(x.get("sym_type", "exported")),
            forwarder=x.get("forwarder", ""),
        )
        for x in e.get("exports", [])
    ]
    return PeMetadata(
        machine=e.get("machine", ""),
        characteristics=e.get("characteristics", 0),
        dll_characteristics=e.get("dll_characteristics", 0),
        exports=exports,
        imports=e.get("imports", {}),
        file_version=e.get("file_version", ""),
        product_version=e.get("product_version", ""),
    )


def _macho_from_dict(e: dict[str, Any]) -> Any:
    from .macho_metadata import MachoExport, MachoMetadata, MachoSymbolType
    exports = [
        MachoExport(
            name=x["name"],
            sym_type=MachoSymbolType(x.get("sym_type", "exported")),
            is_weak=x.get("is_weak", False),
        )
        for x in e.get("exports", [])
    ]
    return MachoMetadata(
        cpu_type=e.get("cpu_type", ""),
        filetype=e.get("filetype", ""),
        flags=e.get("flags", 0),
        install_name=e.get("install_name", ""),
        dependent_libs=e.get("dependent_libs", []),
        reexported_libs=e.get("reexported_libs", []),
        exports=exports,
        current_version=e.get("current_version", ""),
        compat_version=e.get("compat_version", ""),
        min_os_version=e.get("min_os_version", ""),
    )


def _dwarf_from_dict(d: dict[str, Any]) -> Any:
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

    return DwarfMetadata(
        structs=structs,
        enums=enums,
        has_dwarf=d.get("has_dwarf", False),
    )


def _dwarf_advanced_from_dict(d: dict[str, Any]) -> Any:
    from .dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo

    tc = d.get("toolchain", {})
    toolchain = ToolchainInfo(
        producer_string=tc.get("producer_string", ""),
        compiler=tc.get("compiler", ""),
        version=tc.get("version", ""),
        abi_flags=set(tc.get("abi_flags", [])),
        vector_abi_flags=set(tc.get("vector_abi_flags", [])),
    )
    return AdvancedDwarfMetadata(
        has_dwarf=d.get("has_dwarf", False),
        toolchain=toolchain,
        calling_conventions=d.get("calling_conventions", {}),
        value_abi_traits=d.get("value_abi_traits", {}),
        packed_structs=set(d.get("packed_structs", [])),
        all_struct_names=set(d.get("all_struct_names", [])),
        frame_registers=d.get("frame_registers", {}),
        callee_saved_regs={k: frozenset(v) for k, v in d.get("callee_saved_regs", {}).items()},
    )


def _sycl_from_dict(d: dict[str, Any]) -> Any:
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


def snapshot_from_dict(d: dict[str, Any]) -> AbiSnapshot:
    # Inspect schema version for future migration hooks.
    # Snapshots without schema_version are treated as v1 (pre-versioning format).
    # Currently only v1 and v2 exist and have the same on-disk layout, so no
    # migration is required.  This baseline lets future PRs add migration logic here.
    _schema_version: int = int(d.get("schema_version", 1))
    if _schema_version > SCHEMA_VERSION:
        import warnings
        warnings.warn(
            f"Snapshot schema_version {_schema_version} is newer than this abicheck "
            f"(supports up to schema_version {SCHEMA_VERSION}). "
            "Data may be incomplete or misinterpreted. "
            "Upgrade abicheck to read this snapshot correctly.",
            UserWarning,
            stacklevel=2,
        )
    funcs = [
        Function(
            name=f["name"], mangled=f["mangled"], return_type=f["return_type"],
            params=[
                Param(
                    name=p.get("name", ""), type=p.get("type", ""),
                    kind=ParamKind(p.get("kind", "value")),
                    default=p.get("default", None),
                    pointer_depth=p.get("pointer_depth", 0),
                    is_restrict=p.get("is_restrict", False),
                    is_va_list=p.get("is_va_list", False),
                )
                for p in f.get("params", [])
            ],
            visibility=Visibility(f.get("visibility", "public")),
            is_virtual=f.get("is_virtual", False),
            is_noexcept=f.get("is_noexcept", False),
            vtable_index=f.get("vtable_index"),
            source_location=f.get("source_location"),
            is_static=f.get("is_static", False),
            is_const=f.get("is_const", False),
            is_volatile=f.get("is_volatile", False),
            is_pure_virtual=f.get("is_pure_virtual", False),
            is_deleted=f.get("is_deleted", False),
            is_inline=f.get("is_inline", False),
            is_extern_c=f.get("is_extern_c", False),
            access=AccessLevel(f.get("access", "public")),
            return_pointer_depth=f.get("return_pointer_depth", 0),
            elf_visibility=ElfVisibility(f["elf_visibility"]) if f.get("elf_visibility") else None,
            ref_qualifier=f.get("ref_qualifier", ""),
            # Tri-state: a missing key (older snapshot) loads as None,
            # which suppresses CTOR_EXPLICIT_ADDED/_REMOVED in the diff
            # rather than producing spurious findings from schema evolution.
            is_explicit=f.get("is_explicit"),
            # Tri-state, same rationale as is_explicit — a missing key on
            # an older snapshot loads as None and suppresses the
            # HIDDEN_FRIEND_ADDED/_REMOVED transition detector.
            is_hidden_friend=f.get("is_hidden_friend"),
        )
        for f in d.get("functions", [])
    ]
    variables = [
        Variable(
            name=v["name"], mangled=v["mangled"], type=v["type"],
            visibility=Visibility(v.get("visibility", "public")),
            source_location=v.get("source_location"),
            is_const=v.get("is_const", False),
            value=v.get("value"),
            access=AccessLevel(v.get("access", "public")),
            elf_visibility=ElfVisibility(v["elf_visibility"]) if v.get("elf_visibility") else None,
        )
        for v in d.get("variables", [])
    ]
    types = [
        RecordType(
            name=t["name"], kind=t["kind"],
            size_bits=t.get("size_bits"),
            alignment_bits=t.get("alignment_bits"),
            fields=[
                TypeField(
                    name=f["name"], type=f["type"],
                    offset_bits=f.get("offset_bits"),
                    is_bitfield=f.get("is_bitfield", False),
                    bitfield_bits=f.get("bitfield_bits"),
                    is_const=f.get("is_const", False),
                    is_volatile=f.get("is_volatile", False),
                    is_mutable=f.get("is_mutable", False),
                    access=AccessLevel(f.get("access", "public")),
                )
                for f in t.get("fields", [])
            ],
            bases=t.get("bases", []),
            virtual_bases=t.get("virtual_bases", []),
            vtable=t.get("vtable", []),
            source_location=t.get("source_location"),
            is_union=t.get("is_union", t.get("kind") == "union"),
            is_opaque=t.get("is_opaque", False),
        )
        for t in d.get("types", [])
    ]
    enums = [_enum_type_from_dict(e) for e in d.get("enums", [])]
    typedefs: dict[str, str] = d.get("typedefs", {})
    elf_data = d.get("elf")
    pe_data = d.get("pe")
    macho_data = d.get("macho")
    dwarf_data = d.get("dwarf")
    dwarf_adv_data = d.get("dwarf_advanced")

    elf = _elf_from_dict(elf_data) if isinstance(elf_data, dict) else None
    pe = _pe_from_dict(pe_data) if isinstance(pe_data, dict) else None
    macho = _macho_from_dict(macho_data) if isinstance(macho_data, dict) else None
    dwarf = _dwarf_from_dict(dwarf_data) if isinstance(dwarf_data, dict) else None
    dwarf_advanced = (
        _dwarf_advanced_from_dict(dwarf_adv_data)
        if isinstance(dwarf_adv_data, dict)
        else None
    )

    sycl_data = d.get("sycl")
    sycl = _sycl_from_dict(sycl_data) if isinstance(sycl_data, dict) else None

    dep_data = d.get("dependency_info")
    dep_info = (
        DependencyInfo(
            nodes=dep_data.get("nodes", []),
            edges=dep_data.get("edges", []),
            unresolved=dep_data.get("unresolved", []),
            bindings_summary=dep_data.get("bindings_summary", {}),
            missing_symbols=dep_data.get("missing_symbols", []),
        )
        if isinstance(dep_data, dict)
        else None
    )

    # Rehydrate BuildMode (schema v5). Missing key = older snapshot →
    # leave as None so build-mode-aware detectors fall back to "unknown".
    build_mode = _build_mode_from_dict(d.get("build_mode"))

    return AbiSnapshot(
        library=d["library"], version=d["version"],
        source_path=d.get("source_path"),
        functions=funcs, variables=variables, types=types,
        enums=enums, typedefs=typedefs,
        elf=elf, pe=pe, macho=macho,
        dwarf=dwarf, dwarf_advanced=dwarf_advanced, sycl=sycl,
        elf_only_mode=bool(d.get("elf_only_mode", False)),
        constants=d.get("constants", {}),
        platform=d.get("platform"),
        language_profile=d.get("language_profile"),
        dependency_info=dep_info,
        # Provenance metadata (v4)
        git_commit=d.get("git_commit"),
        git_tag=d.get("git_tag"),
        created_at=d.get("created_at"),
        build_id=d.get("build_id"),
        # Build-mode capture (v5)
        build_mode=build_mode,
    )


def _build_mode_from_dict(raw: Any) -> BuildMode | None:
    """Convert a serialized BuildMode dict (or None) back into the
    typed dataclass. Returns None when the field is missing (older
    snapshots) or malformed."""
    if not isinstance(raw, dict):
        return None
    from .build_mode import (
        BuildMode,
        BuildModeProvenance,
        CompilerFamily,
        CxxStandard,
        GlibcxxDualAbi,
        StdlibFamily,
    )

    def _enum_or(cls: type, value: Any, default: Any) -> Any:
        if value is None:
            return default
        try:
            return cls(value)
        except (ValueError, KeyError):
            return default

    # Validate provenance shape: a malformed snapshot may carry a
    # non-dict value (string/list from hand-edited JSON, or a partial
    # corruption). Per the function contract, return None for
    # malformed inputs rather than raising at .get().
    prov_raw = raw.get("provenance")
    if prov_raw is None:
        prov_raw = {}
    if not isinstance(prov_raw, dict):
        return None
    provenance = BuildModeProvenance(
        raw_producer=prov_raw.get("raw_producer"),
        raw_comment=prov_raw.get("raw_comment"),
        compiler_version=prov_raw.get("compiler_version"),
    )

    # Coerce libcpp_abi_version: int passes through; numeric string
    # (some YAML/JSON producers emit "1" instead of 1) coerces; anything
    # else (bool wraps as 0/1 which would be misleading; lists/dicts)
    # falls back to None.
    libcpp_raw = raw.get("libcpp_abi_version")
    if isinstance(libcpp_raw, bool):
        libcpp_abi_version: int | None = None
    elif isinstance(libcpp_raw, int):
        libcpp_abi_version = libcpp_raw
    elif isinstance(libcpp_raw, str) and libcpp_raw.isdigit():
        libcpp_abi_version = int(libcpp_raw)
    else:
        libcpp_abi_version = None

    return BuildMode(
        compiler_family=_enum_or(
            CompilerFamily, raw.get("compiler_family"), CompilerFamily.UNKNOWN,
        ),
        language_std=_enum_or(
            CxxStandard, raw.get("language_std"), CxxStandard.UNKNOWN,
        ),
        stdlib=_enum_or(StdlibFamily, raw.get("stdlib"), StdlibFamily.UNKNOWN),
        glibcxx_dual_abi=_enum_or(
            GlibcxxDualAbi,
            raw.get("glibcxx_dual_abi"),
            GlibcxxDualAbi.NOT_APPLICABLE,
        ),
        libcpp_abi_version=libcpp_abi_version,
        provenance=provenance,
    )


def load_snapshot(path: str | Path) -> AbiSnapshot:
    with open(path, encoding="utf-8") as f:
        return snapshot_from_dict(json.load(f))


def save_snapshot(snap: AbiSnapshot, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(snapshot_to_json(snap))
