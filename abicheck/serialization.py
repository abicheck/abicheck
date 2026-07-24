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

from .errors import IncompatibleSnapshotSchemaError
from .model import (
    AbiSnapshot,
    AccessLevel,
    DependencyInfo,
    ElfVisibility,
    EnumMember,
    EnumType,
    ExtractionContract,
    Function,
    Param,
    ParamKind,
    RecordType,
    ScopeOrigin,
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
# v6: declaration provenance (source_header + origin on functions/variables/types/enums; ADR-015)
# v7: optional evidence_pack reference (ADR-028; lightweight ref to an out-of-band pack)
# v8: pack ref key renamed evidence_pack→build_source_pack + optional inline-embedded
#     build_source payload (single-artifact UX, PR #356). The bump is deliberate:
#     a v7-only reader knows only the old evidence_pack key, so without it a v8
#     snapshot's renamed provenance would be silently dropped — bumping makes such
#     readers reject the format (forward-version error) instead of misreading it.
# v9: CastXML field const/volatile/mutable facts (TypeField.is_const/
#     is_volatile/is_mutable) and full CV-qualifier type spelling became
#     reliably populated (previously silently dead — see CHANGELOG). Unlike
#     earlier bumps, a pre-v9 snapshot is not merely missing a key — it has
#     real but WRONG data (permanently False booleans, qualifier-less type
#     spelling) that reads identically to a genuine "not const"/"not
#     volatile" fact. `snapshot_from_dict` marks such a snapshot's
#     `AbiSnapshot.header_cv_facts_reliable` False so the affected detectors
#     in diff_types.py can skip it, instead of misreporting a false
#     FIELD_BECAME_CONST/VOLATILE/MUTABLE or TYPE_FIELD_TYPE_CHANGED purely
#     from a tool upgrade comparing a legacy snapshot to a fresh dump of
#     unchanged headers (Codex review, PR #582).
# v10: `--ast-frontend hybrid` (G28 Phase 3) — `AbiSnapshot.ast_producer` can
#     now be `"hybrid"`, and `AbiSnapshot.fact_provenance` records per-fact
#     producer for a snapshot that mixes castxml- and clang-backed
#     declarations. A pre-v10 reader's own detector code has no concept of
#     per-fact provenance at all (it gates purely on whole-snapshot
#     `from_headers`, which a hybrid snapshot also satisfies) — reading a v10
#     hybrid snapshot with pre-v10 code can misread a legitimate producer
#     coverage gap (e.g. a clang-only function's placeholder default value)
#     as a real removal, exactly the false positive the provenance map exists
#     to prevent. Bumping bumps the version-mismatch `UserWarning` in
#     `snapshot_from_dict` for such a reader, giving a visible "upgrade
#     abicheck" signal instead of silence (Codex review).
# v11: persist the resolved header-AST executable/compiler identity and an
#     explicit CastXML→Clang fallback reason.  This makes producer changes
#     observable in saved baselines instead of only in transient logs.
# v12: ADR-050 D1 — ``AbiSnapshot.contract`` (profile/scope fingerprints
#     proving the extraction contract two snapshots were compared under).
#     Unlike every earlier bump, this one is *verdict-blocking*: a reader
#     that doesn't recognize ``contract`` would silently compare two
#     possibly-incomparable snapshots and produce an ordinary, wrong verdict
#     — exactly the failure mode ADR-050 exists to close. See
#     ``_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` below:
#     ``snapshot_from_dict``'s hard-rejection guard protects any reader BUILT
#     FROM THIS COMMIT ONWARD whose own ``SCHEMA_VERSION`` constant is below
#     a future verdict-blocking bump's threshold — it cannot, and structurally
#     never could, retroactively protect an already-released pre-v12 install
#     (e.g. a deployed abicheck whose ``SCHEMA_VERSION`` is 11): such a reader
#     simply does not contain this guard's code at all, so it falls through
#     to the ordinary warn-and-continue path every earlier additive bump got,
#     silently drops the unrecognized ``contract`` key, and produces an
#     ordinary verdict (Codex review, PR #624) — no in-band schema-version
#     change can close that gap for code that already shipped without it.
#     `checker.compare`'s ``contract_coverage="partial"`` disclosure (ADR-050
#     D2) is the mitigation available for exactly this case -- but it comes
#     from whichever *v12-aware* `compare()` later evaluates the resulting
#     pair, never from the pre-v12 reader that did the dropping (that reader
#     predates the coverage logic too, and stays just as unaware of the drop
#     as it was of ``contract`` itself; Codex review, PR #624). A pair where
#     one side's contract is missing -- whether dropped by an old re-save or
#     never populated -- is reported as partially covered rather than
#     silently full, once a current reader does the comparing. As of this
#     PR no real producer populates ``contract`` yet (``dumper.py`` wiring
#     is separate, later work), so there is no snapshot in the wild today
#     for an old reader to mis-handle.
SCHEMA_VERSION: int = 12

# Schema version at which CastXML field CV facts became reliable (see v9 above).
_MIN_SCHEMA_VERSION_FOR_CV_FACTS = 9

# ADR-050 D1 — the schema version at which a verdict-blocking field
# (``AbiSnapshot.contract``) was first introduced. This constant only takes
# effect inside code that already contains this guard (this commit onward);
# it cannot retroactively make an already-released, pre-this-commit reader
# (whose own code simply doesn't have this check) hard-reject — that reader
# falls through to its old warn-and-continue path regardless of what this
# constant says (Codex review, PR #624; see the v12 note above for the full
# scope of what this guard can and cannot protect). Within code that DOES
# contain this guard, ``snapshot_from_dict`` raises IncompatibleSnapshotSchemaError
# whenever the snapshot's version is BOTH newer than this reader's own
# SCHEMA_VERSION AND at or above this threshold — not merely "this reader
# predates the threshold," which would stop protecting the moment a reader's
# own SCHEMA_VERSION reaches it.
_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION = 12


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
    # asdict() would recursively copy the lazy lookup caches too (wasted work,
    # and they're dropped below anyway). Clear them for the duration of the
    # call and restore afterward so this function stays pure from the
    # caller's perspective — snapshot_to_dict(snap) must not mutate `snap`,
    # or invalidate an index a caller built and is still holding a reference
    # to via the object it passed in.
    saved_caches = (snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name)
    try:
        snap._func_by_mangled = None
        snap._var_by_mangled = None
        snap._type_by_name = None
        d = asdict(snap)
    finally:
        snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name = saved_caches
    d.pop("_func_by_mangled", None)
    d.pop("_var_by_mangled", None)
    d.pop("_type_by_name", None)
    # Runtime-only provenance qualifier — never persisted.
    d.pop("from_headers_inferred", None)
    # If ``from_headers`` was only *inferred* (a legacy snapshot loaded without
    # the explicit key), do not persist it as explicit provenance: drop the key
    # so a reload re-runs the same inference and re-marks it inferred. Writing
    # ``from_headers: true`` here would promote a guess to explicit header
    # provenance on the next load, re-enabling source-level param-rename
    # detection on DWARF-only baselines this is meant to suppress.
    if snap.from_headers_inferred:
        d.pop("from_headers", None)

    # Serialize ElfMetadata enums to strings for JSON compatibility
    if d.get("elf"):
        elf = d["elf"]
        for sym in elf.get("symbols", []):
            sym["binding"] = (
                sym["binding"]
                if isinstance(sym["binding"], str)
                else sym["binding"].value
            )
            sym["sym_type"] = (
                sym["sym_type"]
                if isinstance(sym["sym_type"], str)
                else sym["sym_type"].value
            )
        for imp in elf.get("imports", []):
            imp["binding"] = (
                imp["binding"]
                if isinstance(imp["binding"], str)
                else imp["binding"].value
            )
            imp["sym_type"] = (
                imp["sym_type"]
                if isinstance(imp["sym_type"], str)
                else imp["sym_type"].value
            )

    # Serialize PeMetadata enums to strings
    if d.get("pe"):
        pe = d["pe"]
        for exp in pe.get("exports", []):
            exp["sym_type"] = (
                exp["sym_type"]
                if isinstance(exp["sym_type"], str)
                else exp["sym_type"].value
            )

    # Serialize MachoMetadata enums to strings
    if d.get("macho"):
        macho = d["macho"]
        for exp in macho.get("exports", []):
            exp["sym_type"] = (
                exp["sym_type"]
                if isinstance(exp["sym_type"], str)
                else exp["sym_type"].value
            )

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

    # The inline embedded BuildSourcePack carries Path/enum/set-bearing nested
    # models that asdict() cannot faithfully serialize; replace the raw asdict
    # output with the pack's canonical inline form (single-artifact UX), or drop
    # the key entirely when nothing was embedded.
    if snap.build_source is not None:
        converted["build_source"] = snap.build_source.to_embedded_dict()
    else:
        converted.pop("build_source", None)

    # Embed schema version for forward-compatibility.
    # Placed at top level so loaders can inspect it without parsing the full snapshot.
    converted["schema_version"] = SCHEMA_VERSION

    return converted


def _scope_origin_or_unknown(raw: Any) -> ScopeOrigin:
    """Deserialize a ScopeOrigin, defaulting unknown/invalid values to UNKNOWN.

    A hand-edited or newer-schema snapshot may carry an origin string this
    build does not recognize; that must not abort the whole load."""
    try:
        return ScopeOrigin(raw if raw is not None else "unknown")
    except ValueError:
        return ScopeOrigin.UNKNOWN


def _enum_type_from_dict(e: dict[str, Any]) -> EnumType:
    return EnumType(
        name=e["name"],
        members=[
            EnumMember(name=m["name"], value=m["value"]) for m in e.get("members", [])
        ],
        underlying_type=e.get("underlying_type", "int"),
        source_location=e.get("source_location"),
        source_header=e.get("source_header"),
        origin=_scope_origin_or_unknown(e.get("origin")),
        is_scoped=e.get("is_scoped"),
        deprecated=e.get("deprecated"),
        qualified_name=e.get("qualified_name"),
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
            value_alignment=s.get("value_alignment", 0),
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
            version_soname=i.get("version_soname", ""),
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
        relro=e.get("relro", "none"),
        bind_now=e.get("bind_now", False),
        is_pie=e.get("is_pie", False),
        has_stack_canary=e.get("has_stack_canary", False),
        has_fortify_source=e.get("has_fortify_source", False),
        has_writable_executable_segment=e.get("has_writable_executable_segment", False),
        is_symbolic=e.get("is_symbolic", False),
        has_textrel=e.get("has_textrel", False),
        pointer_size=e.get("pointer_size", 8),
        machine=e.get("machine", ""),
        # Legacy snapshots (written before elf_class existed) carry no class
        # field; derive it from pointer_size (4→32, 8→64) rather than hard-coding
        # 64, so a saved 32-bit baseline does not false-positive elf_class_changed.
        elf_class=e.get("elf_class", 32 if e.get("pointer_size", 8) == 4 else 64),
        osabi=e.get("osabi", ""),
        e_flags=e.get("e_flags", 0),
        abi_flags=frozenset(e.get("abi_flags", [])),
        has_static_tls=e.get("has_static_tls", False),
        has_tls_symbols=e.get("has_tls_symbols", False),
        gnu_properties=frozenset(e.get("gnu_properties", [])),
        has_dt_relr=e.get("has_dt_relr", False),
        hash_styles=frozenset(e.get("hash_styles", [])),
        ei_data=e.get("ei_data", ""),
        min_kernel_version=e.get("min_kernel_version", ""),
        # Tri-state loader-contract fields: absent key (legacy snapshot) must
        # stay None ("not captured"), not default to a comparable value.
        dynamic_flags=(
            frozenset(e["dynamic_flags"])
            if e.get("dynamic_flags") is not None
            else None
        ),
        has_init=e.get("has_init"),
        has_fini=e.get("has_fini"),
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
        # Tri-state: absent key (legacy snapshot) stays None ("not captured").
        delay_imports=e.get("delay_imports"),
        file_version=e.get("file_version", ""),
        product_version=e.get("product_version", ""),
        subsystem_version=e.get("subsystem_version", ""),
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
        cpu_types=e.get("cpu_types", []),
        filetype=e.get("filetype", ""),
        flags=e.get("flags", 0),
        install_name=e.get("install_name", ""),
        dependent_libs=e.get("dependent_libs", []),
        reexported_libs=e.get("reexported_libs", []),
        exports=exports,
        imported_symbols=e.get("imported_symbols", []),
        current_version=e.get("current_version", ""),
        compat_version=e.get("compat_version", ""),
        min_os_version=e.get("min_os_version", ""),
        # Tri-state: absent key (legacy snapshot) stays None ("not captured").
        rpaths=e.get("rpaths"),
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
        base_types={k: int(v) for k, v in d.get("base_types", {}).items()},
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


def _kabi_from_dict(d: dict[str, Any]) -> Any:
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


def _numpy_capi_from_dict(d: dict[str, Any]) -> Any:
    from .numpy_capi import NumPyCapiSurface

    return NumPyCapiSurface(
        consumes_array_api=d.get("consumes_array_api", False),
        consumes_ufunc_api=d.get("consumes_ufunc_api", False),
        capi_target_version=d.get("capi_target_version"),
    )


def _python_ext_from_dict(d: dict[str, Any]) -> Any:
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


def _python_api_from_dict(d: dict[str, Any]) -> Any:
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


def snapshot_from_dict(d: dict[str, Any]) -> AbiSnapshot:
    # Inspect schema version for future migration hooks.
    # Snapshots without schema_version are treated as v1 (pre-versioning format).
    # Currently only v1 and v2 exist and have the same on-disk layout, so no
    # migration is required.  This baseline lets future PRs add migration logic here.
    _schema_version: int = int(d.get("schema_version", 1))
    if (
        _schema_version > SCHEMA_VERSION
        and _schema_version >= _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION
    ):
        # ADR-050 D1 — this reader has no code path that even looks for a
        # verdict-blocking field introduced at or after this threshold
        # (starting with ``contract``). Warn-and-continue here would let this
        # reader silently compare two possibly-incomparable snapshots and
        # produce an ordinary, wrong verdict — the exact failure mode this
        # ADR exists to close. Raised as a SnapshotError subclass so existing
        # ``except SnapshotError`` handling (e.g. cli_resolve.py's clean
        # click.UsageError/ClickException translation) still catches it.
        raise IncompatibleSnapshotSchemaError(
            f"Snapshot schema_version {_schema_version} requires abicheck "
            f"supporting at least schema_version "
            f"{_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION} to read safely "
            f"(this reader supports up to schema_version {SCHEMA_VERSION}). "
            "Upgrade abicheck to read this snapshot."
        )
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
            name=f["name"],
            mangled=f["mangled"],
            return_type=f["return_type"],
            params=[
                Param(
                    name=p.get("name", ""),
                    type=p.get("type", ""),
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
            # Provenance of is_deleted: True when set via DW_AT_deleted. Must be
            # rehydrated (asdict writes it) so the public-map bypass in
            # diff_symbols keeps DWARF-deleted unexported members out of the
            # public surface after a dump-to-file → compare-files round-trip,
            # rather than re-emitting FUNC_REMOVED against a stripped build.
            deleted_from_dwarf=f.get("deleted_from_dwarf", False),
            is_inline=f.get("is_inline", False),
            is_extern_c=f.get("is_extern_c", False),
            access=AccessLevel(f.get("access", "public")),
            return_pointer_depth=f.get("return_pointer_depth", 0),
            elf_visibility=ElfVisibility(f["elf_visibility"])
            if f.get("elf_visibility")
            else None,
            ref_qualifier=f.get("ref_qualifier", ""),
            # Tri-state: a missing key (older snapshot) loads as None,
            # which suppresses CTOR_EXPLICIT_ADDED/_REMOVED in the diff
            # rather than producing spurious findings from schema evolution.
            is_explicit=f.get("is_explicit"),
            # Tri-state, same rationale as is_explicit — a missing key on
            # an older snapshot loads as None and suppresses the
            # HIDDEN_FRIEND_ADDED/_REMOVED transition detector.
            is_hidden_friend=f.get("is_hidden_friend"),
            # Provenance (v6) — missing on older snapshots → None / UNKNOWN.
            source_header=f.get("source_header"),
            origin=_scope_origin_or_unknown(f.get("origin")),
            # Tri-state language-contract fields (coverage extension) —
            # missing keys on older snapshots load as None and suppress the
            # corresponding transition detectors.
            is_variadic=f.get("is_variadic"),
            contract_attributes=f.get("contract_attributes"),
            exception_spec=f.get("exception_spec"),
            deprecated=f.get("deprecated"),
            is_override=f.get("is_override"),
        )
        for f in d.get("functions", [])
    ]
    variables = [
        Variable(
            name=v["name"],
            mangled=v["mangled"],
            type=v["type"],
            visibility=Visibility(v.get("visibility", "public")),
            source_location=v.get("source_location"),
            is_const=v.get("is_const", False),
            value=v.get("value"),
            access=AccessLevel(v.get("access", "public")),
            elf_visibility=ElfVisibility(v["elf_visibility"])
            if v.get("elf_visibility")
            else None,
            source_header=v.get("source_header"),
            origin=_scope_origin_or_unknown(v.get("origin")),
            alignment_bits=v.get("alignment_bits"),
            deprecated=v.get("deprecated"),
        )
        for v in d.get("variables", [])
    ]
    types = [
        RecordType(
            name=t["name"],
            kind=t["kind"],
            size_bits=t.get("size_bits"),
            alignment_bits=t.get("alignment_bits"),
            fields=[
                TypeField(
                    name=f["name"],
                    type=f["type"],
                    offset_bits=f.get("offset_bits"),
                    is_bitfield=f.get("is_bitfield", False),
                    bitfield_bits=f.get("bitfield_bits"),
                    is_const=f.get("is_const", False),
                    is_volatile=f.get("is_volatile", False),
                    is_mutable=f.get("is_mutable", False),
                    access=AccessLevel(f.get("access", "public")),
                    default=f.get("default"),
                    deprecated=f.get("deprecated"),
                )
                for f in t.get("fields", [])
            ],
            bases=t.get("bases", []),
            virtual_bases=t.get("virtual_bases", []),
            vtable=t.get("vtable", []),
            source_location=t.get("source_location"),
            is_union=t.get("is_union", t.get("kind") == "union"),
            is_opaque=t.get("is_opaque", False),
            is_final=t.get("is_final"),  # tri-state; absent on pre-v? snapshots → None
            is_template_pattern=t.get("is_template_pattern", False),
            has_anonymous_aggregate_fields=t.get(
                "has_anonymous_aggregate_fields", False
            ),
            source_header=t.get("source_header"),
            origin=_scope_origin_or_unknown(t.get("origin")),
            # Fine-grained layout descriptor (layout-closure work); all
            # optional/tri-state, absent on snapshots predating these fields.
            data_size_bits=t.get("data_size_bits"),
            is_standard_layout=t.get("is_standard_layout"),
            is_trivially_copyable=t.get("is_trivially_copyable"),
            vptr_offset_bits=t.get("vptr_offset_bits"),
            base_offsets=t.get("base_offsets", {}),
            qualified_name=t.get("qualified_name"),
            is_abstract=t.get("is_abstract"),
            deprecated=t.get("deprecated"),
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

    kabi_data = d.get("kabi")
    kabi = _kabi_from_dict(kabi_data) if isinstance(kabi_data, dict) else None
    numpy_capi_data = d.get("numpy_capi")
    numpy_capi = (
        _numpy_capi_from_dict(numpy_capi_data)
        if isinstance(numpy_capi_data, dict)
        else None
    )
    python_ext_data = d.get("python_ext")
    python_ext = (
        _python_ext_from_dict(python_ext_data)
        if isinstance(python_ext_data, dict)
        else None
    )
    # A snapshot dumped without the G14 key (older abicheck, or a `dump` writer
    # path that didn't attach it) has no serialized ``python_ext``. Derive it on
    # load from the already-parsed binary metadata so `dump` → `compare` never
    # silently disables the extension detector — the same recognition the dumper
    # runs, applied at read time. ``_derive_python_ext_key_absent`` records that
    # the key was missing (vs. an explicit ``null`` meaning "checked, not an
    # extension") so we only re-derive when there is no recorded answer.
    _python_ext_key_absent = "python_ext" not in d

    python_api_data = d.get("python_api")
    python_api = (
        _python_api_from_dict(python_api_data)
        if isinstance(python_api_data, dict)
        else None
    )

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

    # Build/source pack reference (schema v7, ADR-028). Optional: a missing key
    # on an older snapshot loads as None. A malformed (non-dict) value is ignored
    # rather than aborting the load, consistent with the rest of this loader.
    # Back-compat: snapshots written before the evidence→buildsource rename store
    # the ref under the legacy ``evidence_pack`` key. The ref shape is unchanged,
    # so we fall back to it to keep existing ``.abi.json`` baselines readable.
    ep_raw = d.get("build_source_pack")
    if ep_raw is None:
        ep_raw = d.get("evidence_pack")
    build_source_pack = None
    if isinstance(ep_raw, dict):
        from .buildsource.model import BuildSourceRef

        build_source_pack = BuildSourceRef.from_dict(ep_raw)

    # Inline embedded build-info/source facts (single-artifact UX). Optional and
    # additive: a missing or malformed value loads as None and the compare falls
    # back to out-of-band --old/--new flags (or skips evidence entirely).
    bs_raw = d.get("build_source")
    build_source = None
    if isinstance(bs_raw, dict):
        from .buildsource.pack import BuildSourcePack

        build_source = BuildSourcePack.from_embedded_dict(bs_raw)

    # from_headers provenance (added alongside the HEADER_AWARE tier-honesty
    # fix). An absent key means a legacy snapshot dumped before the field
    # existed: preserve the prior evidence-tier behavior by inferring header
    # provenance from a populated, non-elf-only surface, so saved baselines
    # (e.g. `abicheck compare libfoo-1.0.json libfoo-2.0.json`) do not silently
    # downgrade from HEADER_AWARE. A present key — including a legitimate False
    # for DWARF-only/symbols-only dumps — is honored verbatim.
    elf_only_mode = bool(d.get("elf_only_mode", False))
    if "from_headers" in d:
        from_headers = bool(d["from_headers"])
        from_headers_inferred = False
    else:
        from_headers = (not elf_only_mode) and bool(
            funcs or variables or types or enums or typedefs
        )
        # This provenance was guessed, not recorded. A legacy DWARF-only dump
        # populates the same surface lists, so the inference cannot tell it
        # apart from a header dump. Mark it inferred so source-level detectors
        # that demand genuine header evidence (parameter renames) stay quiet.
        from_headers_inferred = from_headers

    ast_producer_value = d.get("ast_producer")
    raw_ast_toolchain = d.get("ast_toolchain")
    ast_toolchain = (
        {
            str(key): str(value)
            for key, value in raw_ast_toolchain.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_ast_toolchain, dict)
        else {}
    )
    raw_fallback_reason = d.get("ast_fallback_reason")
    ast_fallback_reason = (
        raw_fallback_reason if isinstance(raw_fallback_reason, str) else None
    )
    if "header_cv_facts_reliable" in d:
        # Trust an explicit marker over re-deriving from schema_version: a
        # load -> snapshot_to_dict -> (save) -> load round-trip always
        # re-stamps schema_version to the CURRENT SCHEMA_VERSION (it
        # describes the writing tool's format capability, not the
        # snapshot's true field-fact origin), so re-deriving purely from
        # schema_version on a reserialized legacy snapshot would silently
        # flip an already-known-unreliable snapshot's stale, real-but-wrong
        # cv facts back to "reliable" — reintroducing the exact false
        # FIELD_BECAME_CONST/VOLATILE/TYPE_FIELD_TYPE_CHANGED positives this
        # flag exists to prevent (Codex review, PR #582).
        header_cv_facts_reliable_value = bool(d["header_cv_facts_reliable"])
    else:
        header_cv_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "clang"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CV_FACTS
        )

    # ADR-050 D1 (schema v12) — profile/scope fingerprints. Missing key (every
    # snapshot predating this field) loads as None, same as every other
    # additive optional field.
    contract = _extraction_contract_from_dict(d.get("contract"))

    snap = AbiSnapshot(
        library=d["library"],
        version=d["version"],
        source_path=d.get("source_path"),
        source_mtime=d.get("source_mtime"),
        source_mtime_epoch=d.get("source_mtime_epoch", False),
        source_size=d.get("source_size"),
        functions=funcs,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=typedefs,
        elf=elf,
        pe=pe,
        macho=macho,
        dwarf=dwarf,
        dwarf_advanced=dwarf_advanced,
        sycl=sycl,
        kabi=kabi,
        numpy_capi=numpy_capi,
        python_ext=python_ext,
        python_api=python_api,
        elf_only_mode=elf_only_mode,
        from_headers=from_headers,
        from_headers_inferred=from_headers_inferred,
        # Which L2 header-AST backend produced this snapshot ("castxml" |
        # "clang"); missing on older snapshots loads as None, which
        # correctly fails _both_castxml_backed (Codex review, PR #582 —
        # this was omitted entirely, so every persisted-then-reloaded
        # castxml snapshot silently lost the tag and permanently disabled
        # all 8 detectors gated on it).
        ast_producer=ast_producer_value,
        ast_toolchain=ast_toolchain,
        ast_fallback_reason=ast_fallback_reason,
        # See header_cv_facts_reliable_value's computation above: prefers an
        # explicit dict key (round-trip stability) and otherwise derives
        # from schema_version scoped to the CastXML header path specifically
        # (Codex review, PR #582).
        header_cv_facts_reliable=header_cv_facts_reliable_value,
        # G28 Phase 3 — per-fact provenance map for a hybrid (castxml+clang
        # merged) snapshot. Absent on every non-hybrid / pre-Phase-3 snapshot,
        # loads as the empty dict (same "unknown" default as a fresh snapshot).
        fact_provenance=dict(d.get("fact_provenance", {})),
        constants=d.get("constants", {}),
        platform=d.get("platform"),
        language_profile=d.get("language_profile"),
        scope_fallback=d.get("scope_fallback"),
        dependency_info=dep_info,
        # Provenance metadata (v4)
        git_commit=d.get("git_commit"),
        git_tag=d.get("git_tag"),
        created_at=d.get("created_at"),
        build_id=d.get("build_id"),
        # Build-mode capture (v5)
        build_mode=build_mode,
        # Evidence-pack reference (v7)
        build_source_pack=build_source_pack,
        # Inline embedded build-info/source facts (single-artifact UX)
        build_source=build_source,
        # Build-context parse provenance (v7, ADR-029) — absent on older
        # snapshots loads as False.
        parsed_with_build_context=bool(d.get("parsed_with_build_context", False)),
        # ADR-039 — active build-time define set (context-free dumps: empty).
        build_context_defines=set(d.get("build_context_defines", [])),
        # ADR-039 — {type: {field: {guard, type, is_bitfield, bitfield_bits}}}
        # registry of conditional record fields (full declaration, not just guard).
        conditional_fields={
            str(t): {str(fn): dict(decl) for fn, decl in fields.items()}
            for t, fields in dict(d.get("conditional_fields", {})).items()
        },
        # ADR-050 D1 — extraction-contract fingerprints (v12).
        contract=contract,
    )

    # G14: derive the CPython extension surface for snapshots that predate the
    # key (or a `dump` path that didn't attach it), so a saved abi3 baseline is
    # still checked at compare time. Skip when the key was present (the dumper
    # already answered, including an explicit "not an extension" null).
    #
    # Mach-O caveat: the ``imported_symbols`` table is itself new in G14. A
    # legacy Mach-O ``.abi.json`` written before it existed has no import data;
    # ``_macho_from_dict`` defaults the absent key to ``[]``. Deriving an
    # extension from that empty set would be actively misleading: `scan --abi3`
    # would audit *zero* CPython imports and certify the module clean, and
    # `compare` would treat every import re-captured from the new binary as
    # newly gained. So when a Mach-O snapshot never recorded its imports, leave
    # ``python_ext`` as ``None`` (unknown) — `--abi3` then honestly reports the
    # artifact must be re-dumped rather than silently passing.
    _macho_imports_uncaptured = (
        isinstance(macho_data, dict) and "imported_symbols" not in macho_data
    )
    if (
        snap.python_ext is None
        and _python_ext_key_absent
        and not _macho_imports_uncaptured
    ):
        if snap.elf is not None or snap.pe is not None or snap.macho is not None:
            from .python_ext import detect_python_extension

            snap.python_ext = detect_python_extension(snap)

    return snap


def _extraction_contract_from_dict(raw: Any) -> ExtractionContract | None:
    """Convert a serialized ExtractionContract dict (or None) back into the
    typed dataclass (ADR-050 D1). Returns None when the field is missing
    (every snapshot predating schema v12) or malformed."""
    if not isinstance(raw, dict):
        return None
    profile_fingerprint = raw.get("profile_fingerprint")
    scope_fingerprint = raw.get("scope_fingerprint")
    profile_fields = raw.get("profile_fields")
    scope_fields = raw.get("scope_fields")
    return ExtractionContract(
        profile_fingerprint=profile_fingerprint
        if isinstance(profile_fingerprint, str)
        else None,
        scope_fingerprint=scope_fingerprint
        if isinstance(scope_fingerprint, str)
        else None,
        profile_fields={str(k): str(v) for k, v in profile_fields.items()}
        if isinstance(profile_fields, dict)
        else {},
        scope_fields={str(k): str(v) for k, v in scope_fields.items()}
        if isinstance(scope_fields, dict)
        else {},
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
            CompilerFamily,
            raw.get("compiler_family"),
            CompilerFamily.UNKNOWN,
        ),
        language_std=_enum_or(
            CxxStandard,
            raw.get("language_std"),
            CxxStandard.UNKNOWN,
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
