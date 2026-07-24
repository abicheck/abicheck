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
    canonicalize_type_name as canonicalize_type_name,
    cv_qualifiers_only_differ as cv_qualifiers_only_differ,
    func_signature_cv_only_differ as func_signature_cv_only_differ,
    is_abi_surface_type_name as is_abi_surface_type_name,
    is_compiler_internal_type as is_compiler_internal_type,
    is_cxx_runtime_library as is_cxx_runtime_library,
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
    from .numpy_capi import NumPyCapiSurface
    from .pe_metadata import PeMetadata
    from .python_api import PythonApiSurface
    from .python_ext import PythonExtMetadata
    from .sycl_metadata import SyclMetadata
    from .symvers_metadata import KabiMetadata

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
    # C ellipsis (...) — variadic calls use a different convention on common
    # ABIs (%al on SysV x86-64, stack args on Apple AArch64). Tri-state:
    # None = dumper/loader does not know (older snapshots); diff skips then.
    is_variadic: bool | None = None
    # Semantic contract attributes (nonnull, noreturn, format, alloc_size,
    # malloc, returns_nonnull, warn_unused_result, sentinel, ...), normalized
    # spellings. None = not captured (older snapshots / dumpers without
    # attribute support); [] = captured, none present. Diff skips on None.
    contract_attributes: list[str] | None = None
    # Dynamic exception specification spelling ("throw()", "throw(int)", ...).
    # "" = captured, no dynamic spec; None = not captured. `noexcept` is NOT
    # folded in here — it keeps its dedicated is_noexcept field and kinds.
    exception_spec: str | None = None
    # `[[deprecated]]`/`[[deprecated("msg")]]` (or castxml's `deprecation`
    # attribute, which carries the same message text): a non-empty string is
    # the message, "" is a bare `[[deprecated]]` with no message. Unlike most
    # other tri-state fields here, None does NOT unambiguously mean
    # "unsupported" — castxml also reports None for a genuinely
    # non-deprecated declaration (there is no separate "deprecated with no
    # info" state to distinguish it from). So the diff detector gates on
    # header-tier confirmation at the *snapshot* level (mirroring
    # Param.default/param_defaults's own header-tier-only gate), not by
    # skipping a None on either side of a single pair — that would silently
    # miss every real "gained/lost deprecated" transition, since one side of
    # a real transition is always None (not-deprecated) by construction.
    deprecated: str | None = None
    # Explicit C++11 `override` specifier on a virtual method declaration.
    # Tri-state like is_explicit/is_hidden_friend: True/False = captured;
    # None = dumper/loader does not know (older snapshots, non-castxml
    # producers, or a non-virtual/non-method declaration for which the
    # specifier is not applicable).
    is_override: bool | None = None


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
    # Declared alignment in bits: an explicit alignas / __attribute__((aligned))
    # override when present, else the variable's type's natural (computed)
    # alignment when a dumper can resolve it. None = not captured (older
    # snapshots / dumpers without support).
    alignment_bits: int | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None


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
    # Default member initializer expression, verbatim (value not evaluated).
    # None = no initializer, or the dumper does not capture this (older
    # snapshots / non-castxml producers). As with Function.deprecated, None
    # is not unambiguously "unsupported" here — a real "gained/lost
    # initializer" transition has one side genuinely None by construction —
    # so the detector gates on header-tier confirmation at the *snapshot*
    # level (mirroring Param.default/param_defaults) rather than skipping
    # per-pair on either side being None.
    default: str | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None


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
    # True when this RecordType is a class/struct template's own pattern body
    # (e.g. the clang header backend's CXXRecordDecl nested inside a
    # ClassTemplateDecl) rather than a concrete, instantiable type. Its field
    # *names*/*types* are still real public surface, but it has no fixed
    # layout for any one instantiation — detectors that need real
    # size/offset data (e.g. DWARF layout backfill's name-based matching)
    # must not treat it as an ordinary type. False for every non-clang
    # producer (castxml/DWARF never emit an uninstantiated pattern this way).
    is_template_pattern: bool = False
    # True when *every* entry in `fields` was flattened up from an anonymous
    # struct/union member by the clang header backend (clang emits an
    # IndirectFieldDecl for each such member; see dumper_clang.py) -- not
    # merely "at least one was" (Codex review): a mixed record like
    # `struct Foo { union { int i; }; int tag; };` has an ordinary field
    # (`tag`) with no such provenance guarantee, so the flag must be False
    # for it too. DWARF's own record builder (dwarf_snapshot.py) now flattens
    # *supported* anonymous aggregates too, but an unsupported producer/shape
    # or a cached snapshot predating that flatten still legitimately leaves
    # an all-anonymous record's DWARF view fieldless even though it carries
    # the real size_bits — a structural signal the DWARF layout backfill
    # needs to trust a
    # bare-suffix (namespaced) match for this case without also trusting an
    # ordinary record's coincidental match to an unrelated, fieldless type
    # reached the same way. False for every non-clang producer (castxml
    # computes real layout itself and is never backfilled; DWARF-only
    # snapshots have no header view to flatten).
    has_anonymous_aggregate_fields: bool = False
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
    # Namespace/enclosing-class-qualified spelling (e.g. "mylib::detail::Impl"),
    # set only when it differs from the bare ``name`` above. ``name`` itself
    # stays bare (matching the DWARF backend, which has no cheaper way to
    # qualify a struct name) so type-map lookups and DWARF/header merges keep
    # matching by the same key across both backends; this field exists solely
    # for namespace-aware checks (internal-leak detection, SYCL-queue param
    # matching) that need to see the real namespace path. None when the type
    # is at global scope or the dumper couldn't determine it (e.g. DWARF-only).
    qualified_name: str | None = None
    # Whether the class/struct declares at least one pure virtual function
    # (making it abstract — cannot be instantiated). Tri-state like
    # ``is_final``: True/False = captured (castxml's `abstract` attribute);
    # None = dumper/loader could not determine (DWARF/symbols-only mode,
    # older snapshots). The diff skips comparison when either side is None.
    is_abstract: bool | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None


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
    # `enum class` / `enum struct` (C++11 scoped enumeration) versus a plain
    # C-style enum. Tri-state like RecordType.is_final: True/False = captured
    # (castxml's `scoped` attribute); None = dumper/loader could not
    # determine (DWARF/symbols-only mode, older snapshots, non-castxml
    # header producers). The diff skips comparison when either side is None.
    is_scoped: bool | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None
    # Namespace/enclosing-class-qualified spelling, mirroring
    # ``RecordType.qualified_name`` (same bare-``name``-collision motivation:
    # PR #608 follow-up). ``name`` stays bare for the same DWARF-parity and
    # type-map-key reasons documented on ``RecordType.qualified_name``. None
    # when the enum is at global scope or the dumper couldn't determine it.
    qualified_name: str | None = None


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
class ExtractionContract:
    """ADR-050 D1 — profile/scope fingerprints proving two snapshots were
    extracted under a comparable contract, plus the resolved per-field
    inputs each fingerprint was computed from (so a mismatch report can show
    *what* differs, not just that the hashes don't match).

    Built by ``abicheck.comparability.compute_extraction_contract`` — never
    constructed by hand outside tests. Both fingerprints are independently
    optional: a symbols-only dump with no header-AST inputs but a real
    ``--public-header``/``--public-header-dir`` still attaches a
    ``scope_fingerprint`` with ``profile_fingerprint=None`` (see that
    module's docstring for the full rationale).
    """

    profile_fingerprint: str | None = None
    scope_fingerprint: str | None = None
    # Named resolved sub-inputs, one string per component, keyed the same way
    # on both sides of a compare so a mismatch can be attributed to a specific
    # field instead of an opaque hash. See ``comparability.PROFILE_FIELD_KEYS``
    # / ``comparability.SCOPE_FIELD_KEYS`` for the recognized keys.
    profile_fields: dict[str, str] = field(default_factory=dict)
    scope_fields: dict[str, str] = field(default_factory=dict)


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
    python_ext: PythonExtMetadata | None = field(
        default=None
    )  # CPython extension-module facts: init export, abi3/Limited-API status,
    # and imported CPython C-API symbols (G14). None for non-extension libraries.
    kabi: KabiMetadata | None = field(
        default=None, kw_only=True
    )  # Linux kernel Module.symvers metadata (G23-D1). Keyword-only so inserting
    # it among the optional metadata fields cannot shift any positional argument.
    python_api: PythonApiSurface | None = field(
        default=None, kw_only=True
    )  # Python-visible API surface (functions/classes/methods/signatures)
    # recovered from a `.pyi` type stub (G23). Keyword-only (like ``kabi``) so
    # inserting it among the optional metadata fields cannot shift the positional
    # slot of ``enums``/``typedefs``/… for callers that build snapshots
    # positionally. None when no stub was found — the C-ABI/export view can't see
    # this surface, so it's a separate check.
    numpy_capi: NumPyCapiSurface | None = field(
        default=None, kw_only=True
    )  # NumPy C-API consumption (_ARRAY_API/_UFUNC_API, NPY_TARGET_VERSION)
    # recovered from binary evidence (G26). Keyword-only for the same reason
    # as ``kabi``/``python_api``. None when the binary could not be scanned
    # or predates this field; an ordinary, successfully-scanned non-NumPy
    # library carries a real surface with both flags False (CodeRabbit review).
    enums: list[EnumType] = field(default_factory=list)
    typedefs: dict[str, str] = field(
        default_factory=dict
    )  # alias -> underlying type name
    constants: dict[str, str] = field(
        default_factory=dict
    )  # #define / constexpr name -> value string
    elf_only_mode: bool = False  # True when dumped without headers (all functions are ELF_ONLY provenance)
    from_headers: bool = False  # True when the ABI surface was parsed from public headers (castxml/AST), as opposed to DWARF debug info or the symbol table. Drives the HEADER_AWARE evidence tier — DWARF-derived declarations populate the same functions/types lists but must NOT be mistaken for header-level evidence.
    # Which L2 header-AST backend produced this snapshot ("castxml" | "clang" |
    # "hybrid"), set only when from_headers is True. Some facts are captured by
    # only one backend today (e.g. TypeField.default/deprecated,
    # RecordType.is_abstract, EnumType.is_scoped, Function.is_override/
    # deprecated — castxml-only as of this field's introduction); detectors for
    # those must gate on BOTH sides sharing the SAME producer, not merely on
    # from_headers, or a producer mismatch reads as every such fact being
    # silently removed (Codex review, PR #582). None for non-header snapshots
    # (DWARF/symbols-only) and for snapshots predating this field.
    #
    # "hybrid" (G28 Phase 3, ``--ast-frontend hybrid``, ``dumper_hybrid.
    # merge_snapshots()``) means this snapshot was built by running BOTH
    # castxml and clang over the same headers and merging them field-by-field
    # — see ``fact_provenance`` below for which specific facts were actually
    # castxml-sourced on this merged snapshot, since a whole-snapshot producer
    # tag alone can't tell a caller that.
    # Keyword-only (Codex review, PR #582): both this and the next field were
    # inserted ahead of several existing positional fields (platform,
    # language_profile, ...) — without kw_only, an existing positional
    # caller shifts silently, e.g. binding "elf" to ast_producer instead of
    # platform, corrupting provenance rather than failing loudly.
    ast_producer: str | None = field(default=None, kw_only=True)

    # Resolved L2 executable/compiler identity used to create the header AST.
    # Kept as string metadata so older readers can ignore it and newer tools can
    # add fields without another model migration.  Empty on snapshots predating
    # schema v11 and on binary/debug-only snapshots.
    ast_toolchain: dict[str, str] = field(default_factory=dict, kw_only=True)
    # Set only when the user explicitly opted into an auto CastXML→Clang
    # fallback.  The reason remains visible after snapshot serialization.
    ast_fallback_reason: str | None = field(default=None, kw_only=True)

    # G28 Phase 3 — per-fact producer provenance for a "hybrid" snapshot only
    # (empty for every ordinary single-backend snapshot; ``ast_producer`` alone
    # already answers the question there). Keyed by the stable strings built by
    # ``fact_provenance.func_fact_key``/``var_fact_key``/``type_fact_key``/
    # ``enum_fact_key``/``field_fact_key``, valued "castxml" or "clang" — which
    # backend's value ``dumper_hybrid.merge_snapshots()`` actually used for
    # that one fact on that one declaration. A key absent from this dict (on a
    # hybrid snapshot) means neither backend populated it — same "unknown,
    # don't manufacture a finding" convention as every other tri-state field
    # here. See ``abicheck/fact_provenance.py`` for the reader-side helpers
    # every ``_both_castxml_backed``-gated detector uses instead of trusting
    # ``ast_producer`` alone once a hybrid snapshot is in play.
    fact_provenance: dict[str, str] = field(default_factory=dict, kw_only=True)

    # True when TypeField.is_const/is_volatile/is_mutable and CV-qualifier
    # type spelling are known-reliable for this snapshot's fields. The
    # castxml parser silently left these permanently False/unqualified
    # before a fix (see CHANGELOG); a *persisted* snapshot dumped before
    # that fix has real "false" data, not merely absent data, so it cannot
    # be told apart from a genuine "not const" field by the value alone —
    # only a snapshot-level marker can. False only for a snapshot rehydrated
    # from a persisted schema_version predating the fix (see
    # serialization.SCHEMA_VERSION); a freshly-built in-memory snapshot
    # (dump(), or any snapshot never round-tripped through JSON) defaults
    # True, since it was necessarily produced by the current, fixed parser
    # (Codex review, PR #582).
    header_cv_facts_reliable: bool = field(default=True, kw_only=True)

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
    #   "header-backend-unavailable" — selected header backend missing / header
    #                                  parse failed
    #   "mangling-fallback"          — headers parsed but no declared symbol
    #                                  matched the export table (typically MSVC
    #                                  C++ name mangling)
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
    # mtime (st_mtime, seconds) of source_path at dump time. Lets a later
    # best-effort re-probe against source_path (e.g. cli_helpers_compare's
    # fold_l0_hard_removals) detect that the on-disk binary has since changed
    # — e.g. rebuilt in place after this snapshot was dumped to JSON — and
    # decline to trust it, keeping a pre-dumped-snapshot compare reproducible.
    # None for snapshots predating this field, or when source_path is None.
    # Honours SOURCE_DATE_EPOCH the same way created_at does (dumper._safe_mtime)
    # so two dumps of identical binary content stay byte-identical.
    source_mtime: float | None = field(default=None, kw_only=True)
    # True when source_mtime is a SOURCE_DATE_EPOCH substitution rather than
    # source_path's real filesystem mtime (dumper._safe_mtime). Persisted
    # because the *compare*-time environment may not have SOURCE_DATE_EPOCH
    # set even though the *dump* that produced this snapshot did (e.g. a CI
    # dump step under a pinned epoch, followed by an interactive compare
    # later with no such variable set) — fold_l0_hard_removals needs to know
    # the recorded value can never match a live re-probe's real mtime
    # regardless of what's in its own environment (Codex review: gating on
    # compare-time os.environ alone missed this combination). False (not
    # None) for snapshots predating this field, matching the pre-epoch-aware
    # default of trusting a real mtime.
    source_mtime_epoch: bool = field(default=False, kw_only=True)
    # st_size of source_path at dump time — a second, cheap identity signal
    # alongside source_mtime for the same fold_l0_hard_removals re-check.
    # mtime alone can't catch a content-preserving-timestamp rebuild (e.g.
    # `cp -p`, `touch -r`, a coarse-mtime filesystem); size doesn't need
    # SOURCE_DATE_EPOCH gating the way mtime does — two reproducible builds
    # of identical content have identical size by definition, so recording
    # the real size never threatens the byte-identical-dump guarantee.
    source_size: int | None = field(default=None, kw_only=True)

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

    # ADR-039 — the preprocessor macros the build actually defines (its active
    # ``-D`` set, harvested from the compile database). Empty means context-free
    # / unknown.
    build_context_defines: set[str] = field(default_factory=set, kw_only=True)
    # ADR-039 — registry of *conditional* record fields the header parse knows
    # about, with their full declaration: ``{type: {field: {"guard": macro,
    # "type": type_name, "is_bitfield": bool, "bitfield_bits": int|None,
    # "access": str, "is_const": bool, "is_volatile": bool, "is_mutable": bool,
    # "is_last": bool}}}`` (each field entry is a mixed-value dict, not
    # ``dict[str, str]``; a field may also carry ``"negative": True`` for an
    # ``#ifndef`` guard or ``"ambiguous": True`` when its guard macro is
    # conditionally ``#undef``/``#define``d). ``is_last`` marks a field that is
    # terminal in its record's source order — the reconciler only clears a presence
    # delta for a terminal field, so re-adding it cannot reorder a sibling. A
    # field lives here iff its presence is gated by a ``#if defined(GUARD)``
    # region, whether or not a context-free parse pruned it from the type's
    # ``fields`` list. Carrying the *declaration* (not just the guard) lets
    # ``diff_reconcile`` prove a pruned-field presence delta is a
    # context-free-parse artifact **and** that the field's declaration is
    # unchanged — so a guarded field whose type changed (a real ABI break) is
    # never cleared. Corroborating build evidence only; it never deletes a finding
    # artifact evidence proves (the authority rule, ADR-028).
    conditional_fields: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=dict, kw_only=True
    )

    # ADR-050 D1 (schema v12) — profile/scope fingerprints proving this
    # snapshot's extraction contract, checked by
    # ``comparability.check_contracts_comparable`` before a compare is
    # allowed to produce a verdict. None on every snapshot predating this
    # field and on a symbols-only dump with no header-AST/public-header
    # inputs at all (see ``ExtractionContract``'s own docstring). Keyword-only
    # for the same reason as the other optional metadata fields above.
    contract: ExtractionContract | None = field(default=None, kw_only=True)

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
