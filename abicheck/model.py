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
import re as _re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .build_mode import BuildMode
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .elf_metadata import ElfMetadata
    from .evidence.model import EvidencePackRef
    from .macho_metadata import MachoMetadata
    from .pe_metadata import PeMetadata
    from .sycl_metadata import SyclMetadata

_model_log = _logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiler internal type filtering (FIX-D) — single source of truth
# ---------------------------------------------------------------------------

COMPILER_INTERNAL_TYPES: frozenset[str] = frozenset({
    "__va_list_tag", "__builtin_va_list", "__gnuc_va_list",
    "__int128", "__int128_t", "__uint128_t",
    "__NSConstantString_tag", "__NSConstantString",
})

_TYPEDEF_ALIAS_RE = _re.compile(r"^typedef\s+(.+?)\s+([A-Za-z_][\w:]*)$")


def is_compiler_internal_type(name: str) -> bool:
    """Return True if *name* is a compiler internal type that should be excluded."""
    if not name:
        return False
    stripped = name.strip()
    if stripped in COMPILER_INTERNAL_TYPES:
        return True
    m = _TYPEDEF_ALIAS_RE.match(stripped)
    if not m:
        return False
    aliased, alias = m.groups()
    return aliased.strip() in COMPILER_INTERNAL_TYPES and alias in COMPILER_INTERNAL_TYPES


# Standard-library / runtime namespaces whose *type layout* is owned by the
# toolchain (libstdc++ / libc++ / Itanium C++ ABI), not by the library under
# inspection.  These types leak into DWARF when a library inlines STL usage; the
# layout the compiler happens to emit (and which static-member DIEs it keeps)
# varies by compiler/LTO, so diffing them produces toolchain-artifact false
# positives rather than real ABI changes (validation/REPORT.md FP-1).
_STDLIB_TYPE_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "std::", "__gnu_cxx::", "__gnu_debug::", "__cxxabiv1::", "__cxx11::",
)

# Substrings that mark an anonymous / local type with no stable cross-version
# ABI identity — lambdas and unnamed struct/union/enum (validation/REPORT.md
# FP-2). gcc renders these as "<lambda...>", "{lambda...}", "(anonymous ...)";
# clang/llvm uses "(unnamed ...)".
_ANONYMOUS_TYPE_MARKERS: tuple[str, ...] = (
    "<lambda", "{lambda", "(anonymous", "(unnamed", "<unnamed",
)


def is_non_abi_surface_type(name: str, *, exclude_stdlib_namespaces: bool = True) -> bool:
    """Return True if *name* is a type that is never the inspected library's own
    ABI surface and must be excluded from type diffing.

    Superset of :func:`is_compiler_internal_type`, additionally covering
    standard-library / runtime namespaces and anonymous (lambda / unnamed)
    types.  Single source of truth so the DWARF extractor and the type differ
    agree on what counts as surface.

    *exclude_stdlib_namespaces* must be set to ``False`` when the inspected DSO
    is itself the C++ runtime (libstdc++ / libc++): there ``std::`` /
    ``__gnu_cxx::`` records ARE the library's own ABI surface, so suppressing
    them would hide real breaks (see :func:`is_cxx_runtime_library`).
    """
    if not name:
        return False
    if is_compiler_internal_type(name):
        return True
    if exclude_stdlib_namespaces and name.startswith(_STDLIB_TYPE_NAMESPACE_PREFIXES):
        return True
    return any(marker in name for marker in _ANONYMOUS_TYPE_MARKERS)


# Core stems of the C++ runtime / standard-library DSOs (without the ``lib``
# prefix).  When abicheck is pointed at one of *these* libraries, std::/
# __gnu_cxx:: types are the surface under test and must NOT be filtered out
# (Codex review on PR #273).  Order matters: longer stems first so the
# startswith check is unambiguous.
_CXX_RUNTIME_CORE_STEMS: tuple[str, ...] = (
    "stdc++", "c++abi", "supc++", "c++",
)


def is_cxx_runtime_library(library: str | None) -> bool:
    """Return True if *library* names a C++ runtime / standard-library DSO that
    owns the ``std::`` namespace.

    Accepts both SONAMEs (``libstdc++.so.6``, ``/usr/lib/libc++.so.1``) and the
    short names that ``abicheck compat dump`` writes from the ABICC ``-lib``
    flag (``stdc++``, ``c++``): the optional ``lib`` prefix is stripped before
    matching the core stems.
    """
    if not library:
        return False
    base = library.rsplit("/", 1)[-1]
    if base.startswith("lib"):
        base = base[3:]
    return base.startswith(_CXX_RUNTIME_CORE_STEMS)


def stdlib_namespaces_excluded(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Return True when ``std::``/runtime namespaces should be filtered out of
    type diffing as leaked dependencies.

    False only when *either* side IS the C++ runtime (libstdc++ / libc++), where
    those types are the surface under test.  Single source of truth so every
    registered detector that consumes ``snapshot.types`` agrees on whether to
    keep std:: records (validation/REPORT.md FP-1; Codex reviews on PR #273).
    """
    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    return not (
        is_cxx_runtime_library(old.library)
        or is_cxx_runtime_library(new.library)
        or is_cxx_runtime_library(getattr(old_elf, "soname", ""))
        or is_cxx_runtime_library(getattr(new_elf, "soname", ""))
    )


def is_abi_surface_type_name(name: str, *, exclude_stdlib: bool) -> bool:
    """Return True if a type *name* belongs to the inspected library's ABI
    surface (i.e. is NOT filtered as std::/anonymous/compiler-internal).

    Convenience inverse of :func:`is_non_abi_surface_type` for use in the
    ``{t.name: t for t in snap.types if is_abi_surface_type_name(...)}`` idiom
    shared across detector modules."""
    return not is_non_abi_surface_type(name, exclude_stdlib_namespaces=exclude_stdlib)

# ---------------------------------------------------------------------------
# Type name canonicalization — normalise type names for reliable matching.
# ---------------------------------------------------------------------------


# Patterns for type-name canonicalization.
_STRUCT_PREFIX_RE = _re.compile(r"^\s*(struct|class|union|enum)\s+")
# Match leading "const" followed by a base type (words, ::, spaces) and optional
# pointer/reference suffix.  The base-type group accepts scope operators (::)
# so that namespace-qualified types like "const ns::Type &" are handled.
_LEADING_CONST_RE = _re.compile(r"^const\s+([\w\s:]+?)(\s*[*&].*)?$")
_MULTI_SPACE_RE = _re.compile(r"\s{2,}")


def canonicalize_type_name(name: str) -> str:
    """Normalise a C/C++ type name for comparison.

    Transformations (in order):
    0. Strip leading/trailing whitespace and collapse internal whitespace.
    1. Strip leading ``struct ``/``class ``/``union ``/``enum `` elaborated-type-specifier.
    2. Normalise leading ``const T`` → ``T const`` (east-const canonical form),
       but only when the base type contains no angle brackets (templates).
    3. Final whitespace cleanup.

    This prevents false positives from dumpers that emit different
    elaborated-type-specifier forms for the same type.

    >>> canonicalize_type_name("struct Foo")
    'Foo'
    >>> canonicalize_type_name("const int *")
    'int const *'
    >>> canonicalize_type_name("  class   Bar  ")
    'Bar'
    >>> canonicalize_type_name("const unsigned long long")
    'unsigned long long const'
    >>> canonicalize_type_name("const ns::Type &")
    'ns::Type const &'
    """
    # 0. Normalise whitespace early so anchored regexes work consistently.
    result = _MULTI_SPACE_RE.sub(" ", name.strip())
    # 1. Strip elaborated type specifier prefix (handles leading whitespace).
    result = _STRUCT_PREFIX_RE.sub("", result)
    # 2. East-const normalisation: move leading "const" after the full base
    #    type (all words/:: before any pointer/reference sigil).  Only applies
    #    when the base portion contains no angle brackets (templates).
    m = _LEADING_CONST_RE.match(result)
    if m:
        base = m.group(1).strip()
        suffix = m.group(2) or ""
        if "<" not in base:
            # Strip elaborated prefix from the base too, handling
            # "const struct Foo" → base="struct Foo" → "Foo"
            base = _STRUCT_PREFIX_RE.sub("", base)
            result = base + " const" + suffix
    # 3. Final cleanup.
    result = _MULTI_SPACE_RE.sub(" ", result)
    return result.strip()


# Matches whole-word ``const`` / ``volatile`` qualifier tokens. Word boundaries
# keep identifiers such as ``std::integral_constant`` or ``ConstIterator``
# untouched — only the standalone cv keywords are stripped.
_CV_TOKEN_RE = _re.compile(r"\b(?:const|volatile)\b")


def _strip_cv_qualifiers(name: str) -> str:
    """Return *name* with all ``const`` / ``volatile`` tokens removed.

    Whitespace introduced by the removal is collapsed, and spaces adjacent to
    pointer/reference sigils are normalised so that ``const char *`` and
    ``char *`` reduce to the same string.
    """
    stripped = _CV_TOKEN_RE.sub(" ", name)
    stripped = _MULTI_SPACE_RE.sub(" ", stripped)
    # Normalise spacing around pointer/reference sigils so "char  *" == "char *".
    stripped = _re.sub(r"\s*([*&])\s*", r" \1", stripped)
    return _MULTI_SPACE_RE.sub(" ", stripped).strip()


def _has_top_level_ptr_or_ref(type_name: str) -> bool:
    """Return True if *type_name* has a ``*`` or ``&`` at top level (depth 0).

    Sigils nested inside template arguments, function-parameter lists, or array
    subscripts (e.g. ``Box<int *>``, ``std::function<void(int&)>``) are NOT
    top-level declarators — the type itself is passed/stored by value. Only a
    depth-0 ``*``/``&`` means the value is a pointer/reference.
    """
    angle = paren = bracket = 0
    for ch in type_name:
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(0, angle - 1)
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket = max(0, bracket - 1)
        elif ch in "*&" and angle == 0 and paren == 0 and bracket == 0:
            return True
    return False


def cv_qualifiers_only_differ(old_type: str, new_type: str) -> bool:
    """Return True when two *pointer/reference* spellings differ only by ``const`` / ``volatile``.

    ``const`` / ``volatile`` qualifiers on (or behind) a pointer or reference
    never change the parameter's calling convention, the pointer's width, or a
    struct field's size/offset. Adding ``const`` to a pointed-to type
    (``char *`` → ``const char *``), or to the pointer value itself
    (``int *`` → ``int * const``), leaves the binary ABI identical — it is at
    most a source/API-signature difference, not a binary break (ISSUE-29/52,
    ISSUE-30/35/65).

    The check is deliberately restricted to types whose *top-level* declarator
    is a pointer (``*``) or reference (``&``). A *by-value* cv change such as
    ``int`` → ``const int`` — or one on a template type like
    ``Box<int *>`` → ``const Box<int *>``, where the only sigil is nested inside
    a template argument — is intentionally **not** neutralised here: although it
    too is binary-layout-neutral, abicheck treats top-level field/variable
    const/volatile as a source-level contract change (see the ``field_qualifiers``
    detector and the ``case30_field_qualifiers`` example), reported through its
    own dedicated change kinds.

    Returns ``False`` when the canonical forms are already identical (no
    difference), when stripping cv-qualifiers still leaves a genuine type
    difference (a real ABI-relevant change), or when either spelling is not a
    top-level pointer/reference type.

    >>> cv_qualifiers_only_differ("char *", "const char *")
    True
    >>> cv_qualifiers_only_differ("int", "const int")
    False
    >>> cv_qualifiers_only_differ("Box<int *>", "const Box<int *>")
    False
    >>> cv_qualifiers_only_differ("int *", "long *")
    False
    >>> cv_qualifiers_only_differ("Foo *", "Foo *")
    False
    """
    co = canonicalize_type_name(old_type)
    cn = canonicalize_type_name(new_type)
    if not (_has_top_level_ptr_or_ref(co) and _has_top_level_ptr_or_ref(cn)):
        return False
    if co == cn:
        return False
    return _strip_cv_qualifiers(co) == _strip_cv_qualifiers(cn)


class Visibility(str, Enum):
    PUBLIC = "public"       # default visibility / exported
    HIDDEN = "hidden"       # __attribute__((visibility("hidden")))
    ELF_ONLY = "elf_only"   # present in ELF symbol table, not in headers


class ElfVisibility(str, Enum):
    """ELF st_other visibility from .dynsym — separate from API-level Visibility."""
    DEFAULT = "default"       # STV_DEFAULT
    PROTECTED = "protected"   # STV_PROTECTED
    HIDDEN = "hidden"         # STV_HIDDEN
    INTERNAL = "internal"     # STV_INTERNAL


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

    PUBLIC_HEADER = "public_header"    # defined in a provided public header
    PRIVATE_HEADER = "private_header"  # project header outside the public set
    SYSTEM_HEADER = "system_header"    # toolchain/system header (/usr/include, ...)
    GENERATED = "generated"            # machine-generated header (moc_*, *.pb.h, generated/ ...)
    EXPORT_ONLY = "export_only"        # exported by the binary but absent from any header
    UNKNOWN = "unknown"                # no public set, or no source location


@dataclass
class Param:
    name: str
    type: str
    kind: ParamKind = ParamKind.VALUE
    default: str | None = None  # has default value (value not preserved)
    pointer_depth: int = 0      # nesting: T=0, T*=1, T**=2
    is_restrict: bool = False   # restrict-qualified pointer parameter
    is_va_list: bool = False    # parameter is va_list (variadic argument list)


@dataclass
class Function:
    name: str                        # demangled
    mangled: str                     # mangled symbol name
    return_type: str
    params: list[Param] = field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    is_virtual: bool = False
    is_noexcept: bool = False
    is_extern_c: bool = False
    vtable_index: int | None = None
    source_location: str | None = None  # "header.h:42"
    is_static: bool = False
    is_const: bool = False        # const qualifier on this
    is_volatile: bool = False     # volatile qualifier on this
    is_pure_virtual: bool = False
    is_deleted: bool = False      # = delete; previously callable → BREAKING
    deleted_from_dwarf: bool = False  # True when is_deleted was set via DW_AT_deleted
    is_inline: bool = False       # inline keyword / attribute in header
    access: AccessLevel = AccessLevel.PUBLIC  # public/protected/private
    return_pointer_depth: int = 0  # T=0, T*=1, T**=2
    elf_visibility: ElfVisibility | None = None  # ELF st_other (populated from .dynsym)
    ref_qualifier: str = ""       # "" (none), "&" (lvalue), "&&" (rvalue)
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
    is_const: bool = False         # const-qualified type (write → SIGSEGV)
    value: str | None = None       # initial value (compile-time constant, if known)
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
    bases: list[str] = field(default_factory=list)       # base class names
    virtual_bases: list[str] = field(default_factory=list)
    vtable: list[str] = field(default_factory=list)      # ordered vtable entries (mangled)
    source_location: str | None = None
    is_union: bool = False
    is_opaque: bool = False       # incomplete type (forward-decl only; was complete → BREAKING)
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
    library: str                   # e.g. "libfoo.so.1"
    version: str                   # e.g. "1.2.3"
    functions: list[Function] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    types: list[RecordType] = field(default_factory=list)
    elf: ElfMetadata | None = field(default=None)    # ELF dynamic/symbol metadata (Sprint 2)
    pe: PeMetadata | None = field(default=None)      # PE/COFF metadata (Windows DLL)
    macho: MachoMetadata | None = field(default=None)  # Mach-O metadata (macOS dylib)
    dwarf: DwarfMetadata | None = field(default=None)           # DWARF layout metadata (Sprint 3)
    dwarf_advanced: AdvancedDwarfMetadata | None = field(default=None)  # Sprint 4
    sycl: SyclMetadata | None = field(default=None)  # SYCL PI plugin metadata (ADR-020b)
    enums: list[EnumType] = field(default_factory=list)
    typedefs: dict[str, str] = field(default_factory=dict)  # alias -> underlying type name
    constants: dict[str, str] = field(default_factory=dict)  # #define / constexpr name -> value string
    elf_only_mode: bool = False  # True when dumped without headers (all functions are ELF_ONLY provenance)
    from_headers: bool = False  # True when the ABI surface was parsed from public headers (castxml/AST), as opposed to DWARF debug info or the symbol table. Drives the HEADER_AWARE evidence tier — DWARF-derived declarations populate the same functions/types lists but must NOT be mistaken for header-level evidence.

    # Phase 3: binary format platform — detected from ELF/PE/MachO metadata.
    # None = unknown / not yet detected.
    # Populated by detect_platform() in pipeline or by the dumper.
    platform: str | None = None   # "elf" | "pe" | "macho" | None

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
    git_commit: str | None = None   # SHA from git rev-parse HEAD at dump time
    git_tag: str | None = None      # e.g. "v2.0.0", set via --git-tag or auto-detected
    created_at: str | None = None   # ISO 8601 timestamp, auto-set at dump time
    build_id: str | None = None     # opaque CI identifier (run ID, build number, etc.)
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

    # ADR-028 (schema v7) — optional reference to an out-of-band EvidencePack
    # carrying L3/L4/L5 source/build/graph evidence. Only a lightweight
    # reference (content hash + coverage summary) lives in the snapshot; the
    # heavyweight pack is content-addressed on disk and versions independently
    # (EVIDENCE_PACK_VERSION). None when no evidence was collected. Old readers
    # ignore this optional field (ADR-015 backward-compatibility).
    evidence_pack: EvidencePackRef | None = field(default=None, kw_only=True)

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
    _func_by_mangled: dict[str, Function] | None = field(default=None, repr=False, compare=False)
    _var_by_mangled: dict[str, Variable] | None = field(default=None, repr=False, compare=False)
    _type_by_name: dict[str, RecordType] | None = field(default=None, repr=False, compare=False)

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
                self.library, self.version,
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
                self.library, self.version,
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
                self.library, self.version,
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
