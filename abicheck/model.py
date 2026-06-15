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

"""ABI data model — shared across dumper, checker and reporter."""

from __future__ import annotations

import logging as _logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

# Re-export the name-classification predicates (moved to name_classification in
# C10) under their historical names. Redundant ``as`` aliases are the explicit
# re-export idiom mypy recognises, so ``from .model import is_non_abi_surface_type``
# keeps type-checking cleanly for the ~9 detector modules that use it.
from .name_classification import (
    COMPILER_INTERNAL_TYPES as COMPILER_INTERNAL_TYPES,
)
from .name_classification import (
    canonicalize_type_name as canonicalize_type_name,
)
from .name_classification import (
    cv_qualifiers_only_differ as cv_qualifiers_only_differ,
)
from .name_classification import (
    is_abi_surface_type_name as is_abi_surface_type_name,
)
from .name_classification import (
    is_compiler_internal_type as is_compiler_internal_type,
)
from .name_classification import (
    is_cxx_runtime_library as is_cxx_runtime_library,
)
from .name_classification import (
    is_non_abi_surface_type as is_non_abi_surface_type,
)

if TYPE_CHECKING:
    from .build_mode import BuildMode
    from .buildsource.model import BuildSourceRef
    from .buildsource.pack import BuildSourcePack
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .elf_metadata import ElfMetadata
    from .macho_metadata import MachoMetadata
    from .pe_metadata import PeMetadata
    from .sycl_metadata import SyclMetadata

_model_log = _logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Name classification (FIX-D) — single source of truth.
# The pure name → bool predicates now live in name_classification (C10) so the
# symbol-name and type-name classifiers share one home; they are re-exported
# from this module (see the imports at the top) under their historical names
# because ~9 detector modules import them ``from .model``. The snapshot-aware
# wrapper below (stdlib_namespaces_excluded) stays in model.
# ---------------------------------------------------------------------------


def stdlib_namespaces_excluded(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Return True when ``std::``/runtime namespaces should be filtered out of
    type diffing as leaked dependencies.

    False only when *either* side IS the C++ runtime (libstdc++ / libc++), where
    those types are the surface under test.  Single source of truth so every
    registered detector that consumes ``snapshot.types`` agrees on whether to
    keep std:: records (validation/REPORT.md FP-1; Codex reviews on PR #273).

    Note (cross-implementation comparisons): when two snapshots are built
    against *different* stdlib implementations (libstdc++ ↔ libc++), standalone
    ``std::`` records in debug info differ wholesale even when the public ABI
    does not embed them — so this filter stays ON to avoid flooding BREAKING
    findings for toolchain-owned internals. The cross-implementation hazard is
    surfaced instead by the build-mode diff (``diff_stdlib_impl.py``) as a RISK
    finding, and a public owner type that *does* embed a ``std::`` type by value
    is caught through its own (non-``std::``, never-filtered) layout change.
    Per-owner un-filtering of the specific embedded records is deferred to the
    layout-closure work.
    """
    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    return not (
        is_cxx_runtime_library(old.library)
        or is_cxx_runtime_library(new.library)
        or is_cxx_runtime_library(getattr(old_elf, "soname", ""))
        or is_cxx_runtime_library(getattr(new_elf, "soname", ""))
    )


# Type-name canonicalization and cv-qualifier helpers
# (``canonicalize_type_name`` / ``cv_qualifiers_only_differ``) now live in the
# dependency-free ``name_classification`` leaf (C10 stage-2). They are imported
# and re-exported above so the historical ``from .model import …`` path keeps
# working.


class Visibility(str, Enum):
    PUBLIC = "public"  # default visibility / exported
    HIDDEN = "hidden"  # __attribute__((visibility("hidden")))
    ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers


class ElfVisibility(str, Enum):
    """ELF st_other visibility from .dynsym — separate from API-level Visibility."""

    DEFAULT = "default"  # STV_DEFAULT
    PROTECTED = "protected"  # STV_PROTECTED
    HIDDEN = "hidden"  # STV_HIDDEN
    INTERNAL = "internal"  # STV_INTERNAL


class AccessLevel(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"


class ParamKind(str, Enum):
    VALUE = "value"
    POINTER = "pointer"
    REFERENCE = "reference"
    RVALUE_REF = "rvalue_ref"


class ScopeOrigin(str, Enum):
    """Where a declaration's defining header sits relative to the
    user-provided public-header set — the *Origin* axis of the two-axis
    Linkage × Origin surface model (ADR-024 D1, ADR-015 schema v6).

    Classification is opt-in: it is only meaningful when the caller
    supplies a public-header set (``--public-header`` / ``--public-header-dir``).
    Without one, every declaration is ``UNKNOWN`` and downstream behaviour
    is unchanged.
    """

    PUBLIC_HEADER = "public_header"  # defined in a provided public header
    PRIVATE_HEADER = "private_header"  # project header outside the public set
    SYSTEM_HEADER = "system_header"  # toolchain/system header (/usr/include, ...)
    GENERATED = "generated"  # machine-generated header (moc_*, *.pb.h, generated/ ...)
    EXPORT_ONLY = "export_only"  # exported by the binary but absent from any header
    UNKNOWN = "unknown"  # no public set, or no source location


@dataclass
class Param:
    name: str
    type: str
    kind: ParamKind = ParamKind.VALUE
    default: str | None = None  # has default value (value not preserved)
    pointer_depth: int = 0  # nesting: T=0, T*=1, T**=2
    is_restrict: bool = False  # restrict-qualified pointer parameter
    is_va_list: bool = False  # parameter is va_list (variadic argument list)


@dataclass
class Function:
    name: str  # demangled
    mangled: str  # mangled symbol name
    return_type: str
    params: list[Param] = field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    is_virtual: bool = False
    is_noexcept: bool = False
    is_extern_c: bool = False
    vtable_index: int | None = None
    source_location: str | None = None  # "header.h:42"
    is_static: bool = False
    is_const: bool = False  # const qualifier on this
    is_volatile: bool = False  # volatile qualifier on this
    is_pure_virtual: bool = False
    is_deleted: bool = False  # = delete; previously callable → BREAKING
    deleted_from_dwarf: bool = False  # True when is_deleted was set via DW_AT_deleted
    is_inline: bool = False  # inline keyword / attribute in header
    access: AccessLevel = AccessLevel.PUBLIC  # public/protected/private
    return_pointer_depth: int = 0  # T=0, T*=1, T**=2
    elf_visibility: ElfVisibility | None = None  # ELF st_other (populated from .dynsym)
    ref_qualifier: str = ""  # "" (none), "&" (lvalue), "&&" (rvalue)
    # explicit specifier on constructors / conversion operators (DW_AT_explicit /
    # castxml @explicit). Tri-state to keep "unknown" distinct from "implicit":
    # - True  → source has `explicit` (or `explicit(true)`)
    # - False → source does not have `explicit`
    # - None  → snapshot loader does not know (older snapshots, dumpers that
    #           don't capture this attribute). The diff must skip the
    #           detector when either side is None to avoid false API_BREAK
    #           findings from schema evolution.
    is_explicit: bool | None = None
    # Hidden-friend marker (in-class `friend` declaration, often inline).
    # Tri-state to keep "unknown" distinct from "not a friend":
    # - True  → declared as a friend inside some class body (castxml
    #           ``befriending`` attribute on the class points to this fn).
    # - False → not a friend declaration.
    # - None  → dumper/loader could not determine (older snapshots, DWARF-
    #           only path). Diff detectors skip when either side is None.
    is_hidden_friend: bool | None = None
    # Provenance (ADR-015, schema v6). source_header is the defining header
    # (source_location with the line/col stripped); origin classifies it
    # against the provided public-header set. Both are additive: missing on
    # older snapshots and default to None / UNKNOWN.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN


@dataclass
class Variable:
    name: str
    mangled: str
    type: str
    visibility: Visibility = Visibility.PUBLIC
    source_location: str | None = None
    is_const: bool = False  # const-qualified type (write → SIGSEGV)
    value: str | None = None  # initial value (compile-time constant, if known)
    access: AccessLevel = AccessLevel.PUBLIC  # public/protected/private
    elf_visibility: ElfVisibility | None = None  # ELF st_other (populated from .dynsym)
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN


@dataclass
class TypeField:
    name: str
    type: str
    offset_bits: int | None = None
    is_bitfield: bool = False
    bitfield_bits: int | None = None
    is_const: bool = False
    is_volatile: bool = False
    is_mutable: bool = False
    access: AccessLevel = AccessLevel.PUBLIC


@dataclass
class RecordType:
    """struct / class / union."""

    name: str
    kind: str  # "struct" | "class" | "union"
    size_bits: int | None = None
    alignment_bits: int | None = None
    fields: list[TypeField] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)  # base class names
    virtual_bases: list[str] = field(default_factory=list)
    vtable: list[str] = field(default_factory=list)  # ordered vtable entries (mangled)
    source_location: str | None = None
    is_union: bool = False
    is_opaque: bool = (
        False  # incomplete type (forward-decl only; was complete → BREAKING)
    )
    # `final` class-key specifier. Tri-state to keep "unknown" distinct from
    # "not final":
    # - True  → declared `class C final { ... }` (castxml `final` attribute).
    # - False → declared without `final`.
    # - None  → dumper/loader could not determine (DWARF/symbols-only mode,
    #           which carries no `final` information; older snapshots). The
    #           diff skips the finality detector when either side is None to
    #           avoid false findings from schema evolution / tier downgrade.
    is_final: bool | None = None
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN
    # ── Fine-grained layout descriptor (layout-closure work) ─────────────────
    # All tri-state / optional so "unknown" (DWARF-only or symbols-only dumps,
    # older snapshots) stays distinct from a real value; the layout detectors
    # skip a comparison whenever either side is None/empty, avoiding false
    # findings from schema evolution or an evidence-tier downgrade.
    #
    # Itanium "data size" (a.k.a. dsize/nvsize): the size occupied by the
    # object's own members *excluding* trailing tail padding. A derived class
    # may reuse a base's tail padding, so a change here can shift a derived
    # layout even when ``size_bits`` (the padded sizeof) is unchanged.
    data_size_bits: int | None = None
    # C++ type traits that govern tail-padding reuse and how the type is passed
    # by value (in registers vs. on the stack / via hidden reference).
    is_standard_layout: bool | None = None
    is_trivially_copyable: bool | None = None
    # Bit offset of the vtable pointer within the object (0 for a simple
    # polymorphic class; nonzero with virtual bases). None when the type is
    # non-polymorphic or the dumper could not determine it. Introducing the
    # first virtual function makes this go from None → 0 and shifts every field.
    vptr_offset_bits: int | None = None
    # Base-class subobject offsets: base name → bit offset within this object.
    # Distinct from ``bases`` (declaration order only): a base can *move* (e.g.
    # an empty-base-optimization is lost, or a member is inserted ahead of it)
    # without the name list reordering. Empty when unknown.
    base_offsets: dict[str, int] = field(default_factory=dict)


@dataclass
class EnumMember:
    name: str
    value: int


@dataclass
class EnumType:
    name: str
    members: list[EnumMember] = field(default_factory=list)
    underlying_type: str = "int"
    source_location: str | None = None
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN


@dataclass
class DependencyInfo:
    """Resolved transitive dependency graph and symbol bindings.

    Populated when a snapshot is created with ``--follow-deps``.
    """

    nodes: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    bindings_summary: dict[str, int] = field(default_factory=dict)
    missing_symbols: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AbiSnapshot:
    """Complete ABI snapshot of one version of a library."""

    library: str  # e.g. "libfoo.so.1"
    version: str  # e.g. "1.2.3"
    functions: list[Function] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    types: list[RecordType] = field(default_factory=list)
    elf: ElfMetadata | None = field(
        default=None
    )  # ELF dynamic/symbol metadata (Sprint 2)
    pe: PeMetadata | None = field(default=None)  # PE/COFF metadata (Windows DLL)
    macho: MachoMetadata | None = field(default=None)  # Mach-O metadata (macOS dylib)
    dwarf: DwarfMetadata | None = field(
        default=None
    )  # DWARF layout metadata (Sprint 3)
    dwarf_advanced: AdvancedDwarfMetadata | None = field(default=None)  # Sprint 4
    sycl: SyclMetadata | None = field(
        default=None
    )  # SYCL PI plugin metadata (ADR-020b)
    enums: list[EnumType] = field(default_factory=list)
    typedefs: dict[str, str] = field(
        default_factory=dict
    )  # alias -> underlying type name
    constants: dict[str, str] = field(
        default_factory=dict
    )  # #define / constexpr name -> value string
    elf_only_mode: bool = False  # True when dumped without headers (all functions are ELF_ONLY provenance)
    from_headers: bool = False  # True when the ABI surface was parsed from public headers (castxml/AST), as opposed to DWARF debug info or the symbol table. Drives the HEADER_AWARE evidence tier — DWARF-derived declarations populate the same functions/types lists but must NOT be mistaken for header-level evidence.

    # Phase 3: binary format platform — detected from ELF/PE/MachO metadata.
    # None = unknown / not yet detected.
    # Populated by detect_platform() in pipeline or by the dumper.
    platform: str | None = None  # "elf" | "pe" | "macho" | None

    # Phase 4: language profile — detected from symbol mangling / extern "C" annotations.
    # None = unknown / mixed / not yet detected.
    # Populated by detect_profile() in pipeline or by the dumper.
    language_profile: str | None = None  # "c" | "cpp" | "sycl" | None

    # ADR-024 §D5.3 — structured confidence signal for header-scope resolution.
    # Set by the dumper when public-header scoping was *requested* but could not
    # be applied as intended, so the surface had to fall back to the export
    # table. The previously bare ``UserWarning`` (PR #259) is retained for human
    # output; this field makes the same fact machine-readable so the surface
    # ledger can disclose reduced confidence. None = scoping succeeded or was
    # never requested. Recognised values:
    #   "castxml-unavailable" — castxml missing / header parse failed
    #   "mangling-fallback"   — headers parsed but no declared symbol matched the
    #                            export table (typically MSVC C++ name mangling)
    scope_fallback: str | None = None

    # Full-stack dependency info (populated by --follow-deps)
    dependency_info: DependencyInfo | None = field(default=None)

    # Provenance metadata (schema v4) — tracks where/when a snapshot was created
    git_commit: str | None = None  # SHA from git rev-parse HEAD at dump time
    git_tag: str | None = None  # e.g. "v2.0.0", set via --git-tag or auto-detected
    created_at: str | None = None  # ISO 8601 timestamp, auto-set at dump time
    build_id: str | None = None  # opaque CI identifier (run ID, build number, etc.)
    # Build-mode capture (schema v5) — normalized compiler / stdlib / std
    # mode derived from DWARF DW_AT_producer, ELF .comment, and mangled
    # symbol heuristics. Used to attribute layout/mangling differences
    # to build configuration rather than real ABI breaks. See
    # ``abicheck/build_mode.py`` for the dataclass and detector logic.
    # None when capture is unavailable or the dumper predates v5.
    build_mode: BuildMode | None = None
    # Optional on-disk artifact path that produced this snapshot.
    # Keyword-only (placed after all other fields) to prevent accidental positional binding.
    # Used by binary-only fallback detectors that need lightweight disassembly.
    source_path: str | None = field(default=None, kw_only=True)

    # ADR-028 (schema v7) — optional reference to an out-of-band BuildSourcePack
    # carrying L3/L4/L5 source/build/graph evidence. Only a lightweight
    # reference (content hash + coverage summary) lives in the snapshot; the
    # heavyweight pack is content-addressed on disk and versions independently
    # (BUILD_SOURCE_PACK_VERSION). None when no evidence was collected. Old readers
    # ignore this optional field (ADR-015 backward-compatibility).
    build_source_pack: BuildSourceRef | None = field(default=None, kw_only=True)

    # Single-artifact UX — optional *inline* BuildSourcePack carrying the
    # normalized L3 build-info + L4/L5 source facts directly inside the
    # snapshot, so `compare old.json new.json` works with no out-of-band pack
    # directories. Populated by `dump --build-info/--sources`; serialized under
    # the "build_source" key. None when nothing was embedded. Old readers ignore
    # this optional field (ADR-015). When both are present, the embedded facts
    # are authoritative for the compare and `build_source_pack` is the matching
    # provenance reference.
    build_source: BuildSourcePack | None = field(default=None, kw_only=True)

    # ADR-029 — True when this snapshot's public-header AST was parsed using the
    # real build context (a compile_commands.json supplied to `dump -p`), so the
    # declared API facts reflect the build's ABI-relevant flags. Lets the
    # build-evidence diff suppress HEADER_PARSE_CONTEXT_DRIFT when the headers
    # were in fact parsed with that context. Defaults False (older snapshots and
    # context-free dumps); ignored by old readers (additive optional field).
    parsed_with_build_context: bool = field(default=False, kw_only=True)

    # Runtime-only provenance qualifier (not serialized — popped in
    # snapshot_to_dict). True when ``from_headers`` was *inferred* for a legacy
    # snapshot that predates the explicit ``from_headers`` key, rather than set
    # explicitly by the dumper or loaded verbatim. Source-level detectors that
    # must only fire on genuine header evidence (e.g. parameter renames) require
    # ``from_headers and not from_headers_inferred`` so ambiguous legacy
    # DWARF-only baselines do not produce false API breaks.
    from_headers_inferred: bool = field(default=False, repr=False, compare=False)

    # Indexes (built lazily)
    _func_by_mangled: dict[str, Function] | None = field(
        default=None, repr=False, compare=False
    )
    _var_by_mangled: dict[str, Variable] | None = field(
        default=None, repr=False, compare=False
    )
    _type_by_name: dict[str, RecordType] | None = field(
        default=None, repr=False, compare=False
    )

    def index(self) -> None:
        """Build lookup indexes. Uses first-wins for duplicate mangled names."""
        func_map: dict[str, Function] = {}
        dup_funcs: dict[str, int] = {}
        for f in self.functions:
            if f.mangled in func_map:
                dup_funcs[f.mangled] = dup_funcs.get(f.mangled, 0) + 1
            else:
                func_map[f.mangled] = f
        if dup_funcs:
            _model_log.warning(
                "Duplicate mangled symbols skipped (first-wins) in %s@%s: %s",
                self.library,
                self.version,
                ", ".join(f"{k} (×{v + 1})" for k, v in dup_funcs.items()),
            )
        self._func_by_mangled = func_map

        var_map: dict[str, Variable] = {}
        dup_vars: dict[str, int] = {}
        for v in self.variables:
            if v.mangled in var_map:
                dup_vars[v.mangled] = dup_vars.get(v.mangled, 0) + 1
            else:
                var_map[v.mangled] = v
        if dup_vars:
            _model_log.warning(
                "Duplicate mangled variables skipped (first-wins) in %s@%s: %s",
                self.library,
                self.version,
                ", ".join(f"{k} (×{v + 1})" for k, v in dup_vars.items()),
            )
        self._var_by_mangled = var_map

        type_map: dict[str, RecordType] = {}
        dup_types: dict[str, int] = {}
        for t in self.types:
            if t.name in type_map:
                dup_types[t.name] = dup_types.get(t.name, 0) + 1
            else:
                type_map[t.name] = t
        if dup_types:
            _model_log.warning(
                "Duplicate type names skipped (first-wins) in %s@%s: %s",
                self.library,
                self.version,
                ", ".join(f"{k} (×{v + 1})" for k, v in dup_types.items()),
            )
        self._type_by_name = type_map

    @property
    def function_map(self) -> dict[str, Function]:
        if self._func_by_mangled is None:
            self.index()
        assert self._func_by_mangled is not None
        return self._func_by_mangled

    @property
    def variable_map(self) -> dict[str, Variable]:
        if self._var_by_mangled is None:
            self.index()
        assert self._var_by_mangled is not None
        return self._var_by_mangled

    def func_by_mangled(self, mangled: str) -> Function | None:
        return self.function_map.get(mangled)

    def var_by_mangled(self, mangled: str) -> Variable | None:
        return self.variable_map.get(mangled)

    def type_by_name(self, name: str) -> RecordType | None:
        if self._type_by_name is None:
            self.index()
        assert self._type_by_name is not None
        return self._type_by_name.get(name)
