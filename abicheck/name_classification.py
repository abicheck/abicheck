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

"""Single source of truth for mangled-symbol name classification.

Before this module, the Itanium-ABI prefix knowledge used to answer "is this
symbol an RTTI artifact?" / "does it live in an internal namespace?" was
re-encoded as private tuples in several modules (``report_summary``,
``diff_platform``, ``diff_symbols``, …) — and the copies had begun to drift.
Concentrating the *semantically identical* tables here keeps that knowledge in
one place, so a new compiler convention is added once rather than hunted across
the tree.

Distinct concepts are kept as distinct, clearly-named constants — they are NOT
interchangeable:

* :data:`ITANIUM_RTTI_PREFIXES` — generic RTTI artifacts (vtables, VTT,
  typeinfo objects/names, virtual/covariant thunks). Used to classify a
  symbol's *origin* for reporting.
* :data:`RTTI_DATA_PREFIXES` — the vtable / typeinfo-object / typeinfo-name
  data objects (``_ZTV`` / ``_ZTI`` / ``_ZTS``) whose *size* is owned by their
  type. Thunks are excluded because they carry no size signal.
* :data:`LOCAL_RTTI_PREFIXES` — RTTI for *function-local* types (the Itanium
  ``Z <encoding> E`` local-name production). Such types can never be named in a
  public header, so their typeinfo is build-dependent churn.
* :data:`INTERNAL_NAMESPACE_COMPONENTS` — length-prefixed Itanium namespace
  components (``<len><name>``) for the conventional internal namespaces.

The stdlib-/runtime-specific RTTI skip sets (in ``elf_symbol_filter``,
``diff_elf_layout`` and ``elf_metadata``) are deliberately *not* unified here:
their memberships differ and feed ``startswith`` filters whose results would
change if merged. Unifying them safely needs per-call behaviour-equivalence
checks and is left as a follow-up.
"""

from __future__ import annotations

import re

__all__ = [
    "ITANIUM_RTTI_PREFIXES",
    "RTTI_DATA_PREFIXES",
    "LOCAL_RTTI_PREFIXES",
    "INTERNAL_NAMESPACE_COMPONENTS",
    "is_rtti_symbol",
    "is_local_rtti_symbol",
    "has_internal_namespace_component",
    "symbol_origin",
    "COMPILER_INTERNAL_TYPES",
    "is_compiler_internal_type",
    "is_non_abi_surface_type",
    "is_abi_surface_type_name",
    "is_cxx_runtime_library",
    "canonicalize_type_name",
    "cv_qualifiers_only_differ",
]

# This module has no intra-package imports on purpose: it sits at the bottom of
# the dependency graph so any module can import it without risking a cycle. Keep
# it dependency-free.

# Generic RTTI artifact prefixes (Itanium ABI): vtables, VTT, typeinfo
# objects/names, and virtual/covariant thunks. Churn in these mirrors churn in
# their owning type rather than representing independent public-API breaks.
ITANIUM_RTTI_PREFIXES: tuple[str, ...] = (
    "_ZTV",  # vtable
    "_ZTT",  # VTT (construction vtable table)
    "_ZTI",  # typeinfo object
    "_ZTS",  # typeinfo name
    "_ZTc",  # covariant-return thunk
    "_ZTh",  # virtual thunk (non-covariant, this-adjusting)
    "_ZTv",  # virtual thunk (vcall-offset)
)

# RTTI *data* objects whose size is owned by their type: vtable, typeinfo
# object, typeinfo name. Thunks carry no size signal, so they are excluded.
RTTI_DATA_PREFIXES: tuple[str, ...] = ("_ZTV", "_ZTI", "_ZTS")

# RTTI for function-local types. ``_ZT[IVST]`` is followed immediately by ``Z``
# — the Itanium "local-name" production ``Z <encoding> E``. The owning type
# (a lambda closure, or any class declared inside a function body) can never be
# named in a public header, so the presence/absence of its typeinfo is
# build-dependent churn, not a public-ABI break.
LOCAL_RTTI_PREFIXES: tuple[str, ...] = ("_ZTIZ", "_ZTSZ", "_ZTVZ", "_ZTTZ")

# Length-prefixed Itanium namespace components (``<len><name>``) for the
# conventional internal namespaces. Matching the length prefix avoids false
# hits on unrelated identifiers that merely contain the substring.
INTERNAL_NAMESPACE_COMPONENTS: tuple[str, ...] = (
    "8internal",
    "6detail",
    "4impl",
    "8__detail",
    "5_impl",
)


def is_rtti_symbol(name: str) -> bool:
    """Return True if *name* is a generic Itanium RTTI artifact.

    Used by :func:`symbol_origin`; also exposed as a building block for the
    planned report view-model (C2) and the ``model.py`` split (C10), which will
    route their RTTI checks through this module rather than re-deriving prefixes.
    """
    return name.startswith(ITANIUM_RTTI_PREFIXES)


def is_local_rtti_symbol(name: str) -> bool:
    """Return True if *name* is RTTI for a function-local (unnameable) type."""
    return name.startswith(LOCAL_RTTI_PREFIXES)


def has_internal_namespace_component(name: str) -> bool:
    """Return True if *name* contains a conventional internal-namespace component.

    Used by :func:`symbol_origin`; also exposed as a building block for the
    planned report view-model (C2) and the ``model.py`` split (C10).
    """
    return any(comp in name for comp in INTERNAL_NAMESPACE_COMPONENTS)


def symbol_origin(symbol: str) -> str:
    """Best-effort origin of a (usually mangled) symbol.

    Returns ``"rtti"``, ``"internal"`` or ``"public"``. RTTI is checked first:
    an RTTI symbol for an internal type (e.g. ``_ZTIN4daal8internal3FooE``)
    classifies as ``"rtti"``, mirroring the historical behaviour.

    Used to explain why a large C++ ``breaking`` count is dominated by churn in
    RTTI artifacts or internal-namespace symbols rather than genuine public-API
    breaks (a common pattern in libraries built without ``-fvisibility=hidden``).
    """
    if is_rtti_symbol(symbol):
        return "rtti"
    if has_internal_namespace_component(symbol):
        return "internal"
    return "public"


# ---------------------------------------------------------------------------
# Type-name classification — is a *type name* the inspected library's own ABI
# surface? Moved here from model.py (C10) so all "is this name X?" predicates,
# symbol and type alike, share one home. These are pure name → bool helpers;
# the snapshot-aware wrappers (e.g. stdlib_namespaces_excluded) stay in model.
# ---------------------------------------------------------------------------

# Compiler internal types that are never the inspected library's own surface.
COMPILER_INTERNAL_TYPES: frozenset[str] = frozenset({
    "__va_list_tag", "__builtin_va_list", "__gnuc_va_list",
    "__int128", "__int128_t", "__uint128_t",
    "__NSConstantString_tag", "__NSConstantString",
})

_TYPEDEF_ALIAS_RE = re.compile(r"^typedef\s+(.+?)\s+([A-Za-z_][\w:]*)$")

# Standard-library / runtime namespaces whose *type layout* is owned by the
# toolchain (libstdc++ / libc++ / Itanium C++ ABI), not by the library under
# inspection. These leak into DWARF when a library inlines STL usage; the layout
# the compiler emits varies by compiler/LTO, so diffing them produces
# toolchain-artifact false positives (validation/REPORT.md FP-1).
_STDLIB_TYPE_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "std::", "__gnu_cxx::", "__gnu_debug::", "__cxxabiv1::", "__cxx11::",
)

# Substrings marking an anonymous / local type with no stable cross-version ABI
# identity — lambdas and unnamed struct/union/enum (validation/REPORT.md FP-2).
_ANONYMOUS_TYPE_MARKERS: tuple[str, ...] = (
    "<lambda", "{lambda", "(anonymous", "(unnamed", "<unnamed",
)

# Core stems of the C++ runtime / standard-library DSOs (without the ``lib``
# prefix). When abicheck is pointed at one of *these* libraries, std::/
# __gnu_cxx:: types are the surface under test and must NOT be filtered out
# (Codex review on PR #273). Order matters: longer stems first so the startswith
# check is unambiguous.
_CXX_RUNTIME_CORE_STEMS: tuple[str, ...] = (
    "stdc++", "c++abi", "supc++", "c++",
)


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


def is_non_abi_surface_type(name: str, *, exclude_stdlib_namespaces: bool = True) -> bool:
    """Return True if *name* is a type that is never the inspected library's own
    ABI surface and must be excluded from type diffing.

    Superset of :func:`is_compiler_internal_type`, additionally covering
    standard-library / runtime namespaces and anonymous (lambda / unnamed)
    types. Single source of truth so the DWARF extractor and the type differ
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


def is_abi_surface_type_name(name: str, *, exclude_stdlib: bool) -> bool:
    """Return True if a type *name* belongs to the inspected library's ABI
    surface (i.e. is NOT filtered as std::/anonymous/compiler-internal).

    Convenience inverse of :func:`is_non_abi_surface_type` for use in the
    ``{t.name: t for t in snap.types if is_abi_surface_type_name(...)}`` idiom
    shared across detector modules.
    """
    return not is_non_abi_surface_type(name, exclude_stdlib_namespaces=exclude_stdlib)


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


# ---------------------------------------------------------------------------
# Type name canonicalization — normalise type names for reliable matching.
# ---------------------------------------------------------------------------


# Patterns for type-name canonicalization.
_STRUCT_PREFIX_RE = re.compile(r"^\s*(struct|class|union|enum)\s+")
# Match leading "const" followed by a base type (words, ::, spaces) and optional
# pointer/reference suffix.  The base-type group accepts scope operators (::)
# so that namespace-qualified types like "const ns::Type &" are handled.
_LEADING_CONST_RE = re.compile(r"^const\s+([\w\s:]+?)(\s*[*&].*)?$")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


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
_CV_TOKEN_RE = re.compile(r"\b(?:const|volatile)\b")


def _strip_cv_qualifiers(name: str) -> str:
    """Return *name* with all ``const`` / ``volatile`` tokens removed.

    Whitespace introduced by the removal is collapsed, and spaces adjacent to
    pointer/reference sigils are normalised so that ``const char *`` and
    ``char *`` reduce to the same string.
    """
    stripped = _CV_TOKEN_RE.sub(" ", name)
    stripped = _MULTI_SPACE_RE.sub(" ", stripped)
    # Normalise spacing around pointer/reference sigils so "char  *" == "char *".
    stripped = re.sub(r"\s*([*&])\s*", r" \1", stripped)
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
