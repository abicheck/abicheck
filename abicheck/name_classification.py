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
from collections.abc import Callable
from typing import ClassVar

__all__ = [
    "ITANIUM_RTTI_PREFIXES",
    "RTTI_DATA_PREFIXES",
    "LOCAL_RTTI_PREFIXES",
    "LOCAL_NAME_PREFIX",
    "STDLIB_RTTI_PREFIXES",
    "INTERNAL_NAMESPACE_COMPONENTS",
    "is_rtti_symbol",
    "is_local_rtti_symbol",
    "is_local_name_symbol",
    "is_stdlib_local_name_symbol",
    "has_internal_namespace_component",
    "symbol_origin",
    "COMPILER_INTERNAL_TYPES",
    "is_compiler_internal_type",
    "is_non_abi_surface_type",
    "is_abi_surface_type_name",
    "is_cxx_runtime_library",
    "canonicalize_type_name",
    "cv_qualifiers_only_differ",
    "STDLIB_TYPE_NAMESPACE_PREFIXES",
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

# The generic Itanium ``<local-name>`` production for a *variable* (as
# opposed to LOCAL_RTTI_PREFIXES, which is the same production applied to a
# type's RTTI): ``Z <function encoding> E <entity name>``, e.g. a function-
# local ``static`` object mangles as ``_ZZ4mainE1x``. Unlike LOCAL_RTTI_PREFIXES
# (four specific ``_ZT[IVST]Z`` combinations), a bare local variable/entity has
# no preceding special-name marker, so the production appears immediately
# after the leading ``_Z``.
#
# NOT, by itself, a "never ABI-relevant" marker (Codex review, PR #641): a
# PUBLIC inline/template function's function-local ``static`` is exactly what
# the Itanium ``STB_GNU_UNIQUE``/weak-symbol mechanism exists to
# cross-TU-deduplicate, so consumers genuinely can bind against it and rely on
# its declared alignment -- a real regression there is a real hazard, not
# noise. Only the runtime/standard-library-OWNED subset (see
# :func:`is_stdlib_local_name_symbol`) is safe to treat as address-placement
# noise; a local static declared in the library-under-test's own public
# header is not covered by this constant alone.
LOCAL_NAME_PREFIX = "_ZZ"

# A LOCAL_NAME_PREFIX symbol whose *enclosing function* belongs to the C++
# runtime/standard library (std::, __gnu_cxx::, __cxxabiv1::) rather than the
# library under test -- the <CV-qualifiers> a const/volatile/restrict-
# qualified member function's <encoding> may carry (``r``/``V``/``K``, in
# that grammar order) plus an optional trailing ref-qualifier (``R``/``O``
# for &/&&) appear between the ``_ZZN`` nested-name opener and the namespace
# component itself (e.g. ``_ZZNKSt7__cxx11...`` for a const std::__cxx11::
# member function), hence the ``[rVK]{0,3}[RO]?`` gap rather than a plain
# fixed-prefix match -- ``[rVK]{0,3}`` for the 0-3 CV-qualifier letters
# (loosely: real manglings never repeat one, but a whitelist doesn't need to
# enforce that), then an independent, at-most-one ``[RO]?`` for the separate
# ref-qualifier production, not folded into the same repeated class (Codex
# review, PR #641: the qualifier class must include ``r`` -- omitted in an
# earlier version, matching only ``K``/``V``/``R``/``O`` and silently
# failing to recognize a restrict-qualified stdlib member function's local
# name as stdlib-owned). ``__cxxabiv1`` is matched as the complete, exact
# length-prefixed name (``10__cxxabiv1``, never with an optional trailing
# digit -- an earlier version's ``10__cxxabiv1?`` could match a truncated,
# never-actually-emitted ``10__cxxabiv`` + arbitrary next character, which
# is not what any real compiler emits and is strictly more permissive than
# the grammar warrants).
#
# Deliberately narrower than :func:`is_local_name_symbol`: a library's OWN
# public inline function's local static (e.g. ``_ZZN4somelib...``) must NOT
# match here, since its alignment genuinely matters to consumers (see
# LOCAL_NAME_PREFIX's docstring above).
#
# The alternation also includes the Itanium ABI's *standard substitution*
# codes for extremely common std:: templates -- ``Sa`` (std::allocator),
# ``Sb`` (std::basic_string), ``Ss`` (std::string), ``Si``/``So``/``Sd``
# (std::istream/ostream/iostream) -- not just the bare ``St`` (::std::)
# substitution (CodeRabbit review, PR #641): a local static inside e.g.
# ``std::allocator<int>::f() const`` mangles as ``_ZZNKSaIiE1fEvE1x``, where
# ``Sa`` occupies the exact grammar position ``St`` would, so it was missed
# entirely by the earlier version and left the alignment false positive live
# for this common class of stdlib type. ``11__gnu_debug`` is included too
# (Codex review, PR #641): libstdc++ debug mode (``_GLIBCXX_DEBUG``) wraps
# containers in the ``__gnu_debug::`` namespace -- already recognized as a
# stdlib/runtime implementation namespace elsewhere in this module (see
# ``_STDLIB_TYPE_NAMESPACE_PREFIXES`` below) -- so a local static in one of
# its functions must be recognized here too, the same way ``9__gnu_cxx`` is.
#
# ``Z*`` after the mandatory ``_ZZ`` handles *recursively nested*
# <local-name> productions (Codex review, PR #641): a lambda or local class
# defined inside a stdlib function is itself "local" to that function, so a
# static local to the LAMBDA's own call operator mangles with one additional
# leading ``Z`` per nesting level before the qualifiers/namespace -- e.g. GCC
# emits ``std::outer()::{lambda()#1}::operator()() const::x`` as
# ``_ZZZSt5outervENKUlvE_clEvE1x`` (three ``Z``s: the mandatory one plus one
# for the lambda's own nested local-name). Stripping any number of extra
# ``Z``s before checking for the namespace marker is safe: none of the
# markers below start with ``Z``, so this can't spuriously swallow real
# content, and a chain that bottoms out at a NON-stdlib function (e.g. a
# lambda inside the library-under-test's own code) still correctly fails to
# match afterward.
#
# The optional ``(?:GV)?`` right after the leading ``_Z`` recognizes the
# Itanium *guard variable* wrapper (Codex review, PR #641 follow-up, sixth
# P2): a dynamically-initialized function-local static also gets a
# companion one-time-init guard object, mangled as ``GV`` + the same local-
# name object name -- i.e. the local static's own ``_ZZ<encoding>E<name>``
# becomes ``_ZGVZ<encoding>E<name>`` for its guard variable, not a fresh
# production of its own. That companion is exported as an ordinary data
# object (``STT_OBJECT``) with the same address-placement-only alignment
# signal as the local static it guards -- no header ever declares it either
# -- so without this, an alignment shift on e.g. the guard for a libstdc++
# ``<regex>`` local static's own guard variable still produced the same
# false positive this exemption exists to suppress. See
# ``export_accounting.py``'s ``_ZGVZ``/``_ZGV`` handling for the same
# mangled-shape fact used elsewhere in this codebase.
_STDLIB_LOCAL_NAME_RE = re.compile(
    r"^_Z(?:GV)?ZZ*N?[rVK]{0,3}[RO]?"
    r"(?:St|Sa|Sb|Ss|Si|So|Sd|3std|9__gnu_cxx|11__gnu_debug|10__cxxabiv1|7__cxx11)"
)

# A small, deliberately non-exhaustive set of standard-library templates the
# C++ standard explicitly permits *user code* to specialize for a
# program-defined type (customization points) -- unlike an ordinary
# instantiation such as std::vector<MyType> (100% stdlib-authored code, just
# instantiated for MyType), a specialization of one of these contains
# USER-AUTHORED code, so its ABI is the library-under-test's concern even
# though the mangled name nominally lives in namespace std (Codex review,
# PR #641: real GCC output for an inline `std::hash<MyType>::operator()`'s
# local static is `_ZZNKSt4hashI6MyTypeEclERKS0_E4salt` -- the plain
# namespace check above would otherwise wrongly treat this as toolchain
# noise). Matching one of these ALWAYS excludes the symbol from the
# stdlib-owned classification below, even for a stdlib-provided
# specialization over a builtin type (e.g. std::hash<int>) -- deliberately
# erring toward reporting a possibly-noisy finding rather than risking
# hiding a real one, the same guiding principle as the rest of this
# exemption. This list can never be complete (the standard permits
# specializing effectively any library template this way), so it only
# covers the templates most commonly specialized in practice. Includes
# `4swap` alongside the class-template customization points (Codex review,
# PR #641 follow-up): `std::swap` is a *function* template the standard
# explicitly permits overloading/specializing for a program-defined type
# (e.g. `template<> inline void std::swap<MyType>(...)`, mangling as
# `_ZZSt4swapI6MyTypeE...`) -- the same "user-authored code nominally in
# namespace std" shape as the class-template entries, just a function
# rather than a class member. Also includes `10tuple_size` (Codex review,
# PR #641 follow-up): `std::tuple_size<MyType>` is another standard
# customization-point template class-templates a program can legally
# specialize for its own type (e.g. to support structured bindings), the
# same "user-authored code nominally in namespace std" shape as `std::hash`
# -- real GCC output for such a specialization's local static mangles as
# `_ZZNSt10tuple_sizeI6MyTypeE1fEvE1x`. Also includes `11common_type`
# (Codex review, PR #641 follow-up): `std::common_type<A, A>` is yet
# another standard customization-point class template a program can
# legally specialize for its own type -- real GCC output for such a
# specialization's local static mangles as
# `_ZZNSt11common_typeIJ1AS0_EE1fEvE1x`. This is the fourth instance of the
# exact same gap class (`hash`/`swap`/`tuple_size`/`common_type`) found
# across successive review rounds -- an intrinsic limitation of
# mangled-name string matching, not a bug being progressively fixed: the
# C++ standard permits specializing dozens of library templates this way
# (`tuple_element`, `pointer_traits`, `allocator_traits`,
# `is_error_code_enum`, ...), so this allowlist can never be exhaustive by
# construction, no matter how many more entries are added. Treated here as
# an accepted, permanent limitation of this exemption (not a to-do list to
# keep clearing) -- closing it fully needs a real demangler or type-graph
# analysis, per this comment block's own opening paragraph.
#
# `(?:\d+__[A-Za-z0-9_]+?)?` between the `St`/`3std` substitution and the
# customization-point name accepts libc++'s versioned inline ABI namespace
# (Codex review, PR #641 follow-up, fifth P2): libc++ wraps its entire
# standard-library implementation in an inline namespace -- `__1` in
# mainline libc++, `__ndk1` on Android NDK, etc. -- so a user specialization
# of e.g. `std::hash<X>` mangles as `_ZZNKSt3__14hashI1XEclERKS1_E4salt`
# (`3__1` = the length-prefixed `__1` namespace component) rather than the
# bare `_ZZNKSt4hashI...` this alternation originally expected. Without
# this, `_STDLIB_LOCAL_NAME_RE` still matches the `St` prefix (classifying
# the symbol as stdlib-owned) while this exclusion regex misses it entirely
# (nothing after `St3__1` matched a listed customization point), so a real
# user-owned regression under libc++ was wrongly suppressed. The inner
# `+?` is non-greedy, not `+`: greedily consuming word characters would eat
# into the customization-point name itself (e.g. `3__1` followed directly
# by `4hash` reads as one contiguous word-character run), so the engine
# must backtrack to the shortest inline-namespace match that still lets one
# of the listed alternatives match immediately after.
# The optional ``(?:GV)?`` right after the leading ``_Z`` mirrors
# `_STDLIB_LOCAL_NAME_RE`'s own guard-variable handling above (Codex review,
# PR #641 follow-up, sixth P2): a user specialization's local static can
# just as well be dynamically-initialized, e.g. `_ZGVZNKSt4hashI6MyTypeEclE...`
# for `std::hash<MyType>::operator()`'s guard, so this exclusion must
# recognize the guard-wrapped form too -- otherwise `_STDLIB_LOCAL_NAME_RE`
# would match the guard variable's `St4hash...` prefix and misclassify a
# user-owned specialization's guard as stdlib-owned, the same failure mode
# this regex exists to prevent for the plain local-static form.
_USER_SPECIALIZABLE_STD_TEMPLATE_RE = re.compile(
    r"^_Z(?:GV)?ZZ*N?[rVK]{0,3}[RO]?(?:St|3std)"
    r"(?:\d+__[A-Za-z0-9_]+?)?"
    r"(?:4hash|4less|7greater|8equal_to|12not_equal_to|10less_equal|"
    r"13greater_equal|11char_traits|14numeric_limits|15iterator_traits|"
    r"14default_delete|9formatter|4swap|10tuple_size|11common_type)I"
)

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


# RTTI mangling prefixes owned by the C++ standard library / Itanium runtime
# (libstdc++ / libc++ / libcxxabi), NOT by the library under test. These encode
# the runtime namespaces ``std::`` (``St`` / ``NSt``), ``__gnu_cxx`` (``N9__gnu_cxx``),
# ``__cxxabiv1`` (``N10__cxxabiv``) and ``std::__cxx11`` (``N7__cxx11``) in the
# typeinfo (``_ZTI``), typeinfo-name (``_ZTS``), vtable (``_ZTV``) and VTT
# (``_ZTT``) forms. A library's own type RTTI never starts with one of these
# fixed runtime-namespace prefixes, so a symbol matching here is always
# toolchain-owned.
#
# This is the single source of truth merged from two historically-separate
# copies that had drifted (C1 follow-up / C10 sub-task): the surface-filter set
# in ``elf_symbol_filter`` and the L0-layout exclusion set in ``diff_elf_layout``.
# It is the *union* of both — a superset — and both call sites now share it.
STDLIB_RTTI_PREFIXES: tuple[str, ...] = (
    # std:: (libstdc++ / libc++)
    "_ZTISt",
    "_ZTSSt",
    "_ZTVSt",
    "_ZTTSt",
    # nested std:: names
    "_ZTINSt",
    "_ZTSNSt",
    "_ZTVNSt",
    "_ZTTNSt",
    # __gnu_cxx::
    "_ZTIN9__gnu_cxx",
    "_ZTSN9__gnu_cxx",
    "_ZTVN9__gnu_cxx",
    "_ZTTN9__gnu_cxx",
    # __cxxabiv1:: (Itanium runtime)
    "_ZTIN10__cxxabiv",
    "_ZTSN10__cxxabiv",
    "_ZTVN10__cxxabiv",
    "_ZTTN10__cxxabiv",
    # std::__cxx11::
    "_ZTIN7__cxx11",
    "_ZTSN7__cxx11",
    "_ZTVN7__cxx11",
    "_ZTTN7__cxx11",
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


def is_local_name_symbol(name: str) -> bool:
    """Return True if *name* is the Itanium ``<local-name>`` production — an
    entity (typically a variable) declared inside a function body, never
    nameable by any header declaration. See :data:`LOCAL_NAME_PREFIX`."""
    return name.startswith(LOCAL_NAME_PREFIX)


def is_stdlib_local_name_symbol(name: str) -> bool:
    """Return True if *name* is a local-name-production symbol (see
    :func:`is_local_name_symbol`) whose enclosing function is owned by the
    C++ runtime/standard library, not the library under test. See
    :data:`_STDLIB_LOCAL_NAME_RE`.

    Returns False for a specialization of a user-specializable customization
    point (e.g. ``std::hash<MyType>``) even though it nominally lives in
    namespace std -- see :data:`_USER_SPECIALIZABLE_STD_TEMPLATE_RE`."""
    if _USER_SPECIALIZABLE_STD_TEMPLATE_RE.match(name):
        return False
    return bool(_STDLIB_LOCAL_NAME_RE.match(name))


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
COMPILER_INTERNAL_TYPES: frozenset[str] = frozenset(
    {
        "__va_list_tag",
        "__builtin_va_list",
        "__gnuc_va_list",
        "__int128",
        "__int128_t",
        "__uint128_t",
        "__NSConstantString_tag",
        "__NSConstantString",
    }
)

_TYPEDEF_ALIAS_RE = re.compile(r"^typedef\s+(.+?)\s+([A-Za-z_][\w:]*)$")

# Standard-library / runtime namespaces whose *type layout* is owned by the
# toolchain (libstdc++ / libc++ / Itanium C++ ABI), not by the library under
# inspection. These leak into DWARF when a library inlines STL usage; the layout
# the compiler emits varies by compiler/LTO, so diffing them produces
# toolchain-artifact false positives (validation/REPORT.md FP-1).
STDLIB_TYPE_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "std::",
    "__gnu_cxx::",
    "__gnu_debug::",
    "__cxxabiv1::",
    "__cxx11::",
)

# Substrings marking an anonymous / local type with no stable cross-version ABI
# identity — lambdas and unnamed struct/union/enum (validation/REPORT.md FP-2).
_ANONYMOUS_TYPE_MARKERS: tuple[str, ...] = (
    "<lambda",
    "{lambda",
    # Clang's own closure spelling is ``(lambda at <path>:<line>:<col>)`` --
    # and after :func:`strip_anonymous_type_location` normalizes it, simply
    # ``(lambda:<file>:<line>:<col>)``. Neither form starts the marker with
    # ``<`` or ``{``, so a template instantiation over a closure type
    # (``raii_guard<(lambda:task_group.h:522:26)>``) matched none of the
    # markers above and was treated as ordinary ABI surface -- even though
    # the *same* module already parses this exact spelling one screen down
    # (``_ANON_LOCATION_RE``), i.e. the omission was in this list, not in
    # what the codebase knows about clang's spelling. The consequence is a
    # type whose identity carries a source *line number*: an unrelated edit
    # earlier in the header shifts it, and the shifted spelling reads as a
    # whole type removed and a whole type added, at BREAKING severity, for a
    # closure class that has no user-nameable identity to break in the first
    # place. GCC/DWARF's ``{lambda(...)#1}`` spelling matched all along,
    # which is why this only ever showed up on clang-derived spellings.
    #
    # Like its two siblings this is a substring test, so it also matches a
    # type whose name merely *contains* the text (the documented
    # ``Tag<"(lambda at a.hpp:1:2)">`` string-NTTP shape). That exposure is
    # identical in kind to what ``<lambda``/``{lambda`` already carry, and
    # errs toward excluding a synthetic-looking type rather than admitting a
    # line-number-keyed one.
    "(lambda",
    "(anonymous",
    "(unnamed",
    "<unnamed",
)

# Core stems of the C++ runtime / standard-library DSOs (without the ``lib``
# prefix). When abicheck is pointed at one of *these* libraries, std::/
# __gnu_cxx:: types are the surface under test and must NOT be filtered out
# (Codex review on PR #273). Order matters: longer stems first so the startswith
# check is unambiguous.
_CXX_RUNTIME_CORE_STEMS: tuple[str, ...] = (
    "stdc++",
    "c++abi",
    "supc++",
    "c++",
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
    return (
        aliased.strip() in COMPILER_INTERNAL_TYPES and alias in COMPILER_INTERNAL_TYPES
    )


def contains_anonymous_type_marker(text: str | None) -> bool:
    """Return True if *text* embeds an anonymous/lambda-closure type marker.

    A narrower, standalone sibling of :func:`is_non_abi_surface_type`'s
    anonymous-type check (the marker test alone, with none of that
    function's compiler-internal/stdlib-namespace exclusions) for a caller
    that isn't testing a *type identity* string but some other piece of
    already-recorded text a type's own spelling can flow into — e.g. a
    ``Change.symbol``/``old_value``/``new_value`` for a function-level
    finding whose parameter or owner type is closure-parameterized. Safe on
    ``None`` (returns ``False``) so a caller doesn't need to guard every
    optional ``Change`` field before checking it.
    """
    if not text:
        return False
    return any(marker in text for marker in _ANONYMOUS_TYPE_MARKERS)


def is_non_abi_surface_type(
    name: str, *, exclude_stdlib_namespaces: bool = True
) -> bool:
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
    if exclude_stdlib_namespaces and name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
        return True
    return contains_anonymous_type_marker(name)


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


_PTR_REF_SIGIL_RE = re.compile(r"\s*([*&])\s*")

# clang's ``-ast-dump=json`` spells an anonymous struct/union/enum field's type
# as e.g. "enum (unnamed enum at /abs/path/to/header.h:56:5)" — the absolute
# source path is an artifact of *where the tool ran*, not the type's ABI
# identity. Comparing an old-tree checkout against a new-tree checkout of the
# identical declaration (e.g. ".../old/include/foo.h" vs ".../new/include/foo.h")
# then falsely reports a type change purely from the differing root, even
# though both spellings denote the same anonymous type at the same line/column
# within the (unchanged) header. Stripping the location leaves just the
# "this is anonymous" marker, which is what should actually be compared.
_ANON_TYPE_LOCATION_RE = re.compile(r"\bat\s+\S+:\d+:\d+(?=\s*\))")

#: Like _ANON_TYPE_LOCATION_RE, but keeps the trailing ``:<line>:<col>`` as a
#: captured discriminator instead of discarding it outright — see
#: strip_anonymous_type_location's docstring for why identity extraction
#: needs the discriminator kept while a downstream *comparison*
#: (canonicalize_type_name) does not. The path itself is matched with
#: ``.*?`` (not ``\S+?``, unlike _ANON_TYPE_LOCATION_RE above) because a
#: real checkout or Windows path can contain spaces (Codex review: a
#: checkout directory literally named "release build", or a bare "Program
#: Files" component) -- \S+? cannot reach the trailing coordinates in that
#: case, so the substitution silently does nothing and the checkout-
#: dependent path survives into the type's identity. Not ``[^)]*?`` either
#: (CodeRabbit review, round 3): a real path can itself contain a literal
#: ``)`` (e.g. ``C:\release (old)\foo.hpp``), which that class excludes by
#: construction, so it could never reach the trailing coordinates for such
#: a path either -- the identical failure mode, just from a different
#: character. ``.*?`` is non-greedy, so it still stops at the *first*
#: ``:<line>:<col>)`` it finds, same as before.
#:
#: Anchored on an actual ``(lambda`` / ``(unnamed <kind>`` marker (round-4
#: review, Codex, fresh evidence): the previous version matched a bare
#: ``\bat\s+...`` anywhere in the name, so a C++20 fixed-string NTTP
#: argument that merely *contains* location-shaped text (e.g.
#: ``Tag<"at /checkout:1:2)">``) was rewritten too, risking a collision
#: with a genuinely distinct specialization. Group 1 captures the marker
#: itself so the substitution can reconstruct it without reproducing the
#: literal ``at``/path text -- this also means the match never introduces
#: extra whitespace to clean up afterward (the previous unconditional
#: multi-space collapse this function used to apply is gone; see its own
#: past instance of exactly this over-broad-collision failure mode, fixed
#: two rounds ago, since fixed generically here at the regex level instead). ``anonymous\s+\w+`` mirrors the identical, real-corpus-driven addition to ``model.graph_identity._BARE_ANON_TYPE_LOCATION_RE``.
_ANON_TYPE_LOCATION_PATH_ONLY_RE = re.compile(
    r"(\((?:lambda|unnamed\s+\w+|anonymous\s+\w+))\s+at\s+(.*?)(:\d+:\d+)(?=\s*\))"
)


def _declaring_header_discriminator(path: str) -> str:
    """Checkout-independent discriminator derived from *path*'s own
    basename (the declaring header's filename), used alongside
    ``:<line>:<col>`` to distinguish two anonymous/lambda declarations that
    share the same coordinates but live in DIFFERENT headers (Codex
    review, round 8, fresh evidence): keeping only ``:<line>:<col>``
    collapsed ``guard<(lambda at /src/one.hpp:4:3)>`` and ``guard<(lambda
    at /src/two.hpp:4:3)>`` onto the identical identity
    ``guard<(lambda:4:3)>``, since both files can legitimately declare
    their own lambda at line 4, column 3. The basename alone (not the full
    path, which embeds the checkout root) is stable across checkouts of
    the same source tree while still separating two distinctly-named
    headers -- the only residual collision is two DIFFERENT headers
    sharing both a basename AND the same line:col, an accepted, narrower
    limitation than the pre-fix "any two headers" collision.
    """
    posix = path.replace("\\", "/")
    return posix.rsplit("/", 1)[-1]


def _quoted_spans(name: str) -> list[tuple[int, int]]:
    """Return ``[start, end)`` character ranges of every ``"..."`` quoted
    literal in *name*, respecting backslash-escaped quotes (``\\"``).

    Used by :func:`strip_anonymous_type_location` to avoid rewriting
    location-shaped text that only *looks* like a real CastXML anonymous/
    lambda marker because it happens to sit inside a C++20 fixed-string NTTP
    argument's own quoted value (CodeRabbit review, fresh evidence):
    ``Tag<"(lambda at /a/foo.hpp:1:2)">`` is a string *literal* naming that
    exact marker text, not a real anonymous-type location CastXML emitted —
    rewriting it collapses two otherwise-distinct specializations (one
    genuinely quoting ``.../a/foo.hpp:1:2)"``, another quoting a different
    path that happens to share a basename) onto the same identity, exactly
    the kind of spurious same-identity collision this whole module exists to
    avoid introducing.
    """
    spans: list[tuple[int, int]] = []
    start: int | None = None
    i = 0
    length = len(name)
    while i < length:
        ch = name[i]
        if ch == "\\" and start is not None:
            # An escaped character inside a quote never ends it -- skip
            # both the backslash and the escaped character together so an
            # escaped quote (\") is never mistaken for the closing quote.
            i += 2
            continue
        if ch == '"':
            if start is None:
                start = i
            else:
                spans.append((start, i + 1))
                start = None
        i += 1
    # An unterminated trailing quote (malformed/truncated input) is not
    # treated as an open span -- nothing after it is "inside quotes" for
    # our purposes, so it contributes nothing further to skip.
    return spans


def strip_anonymous_type_location(name: str) -> str:
    """Strip the checkout-dependent *directory* out of an embedded ``at
    <path>:<line>:<col>`` in an anonymous-tag or lambda-closure type
    spelling (``"(unnamed struct at /a/foo.h:56:5)"``, ``"raii_guard<(lambda
    at /a/foo.h:4:37)>"``), while keeping the declaring header's own
    basename plus its ``:<line>:<col>`` as a discriminator
    (``"(unnamed struct:foo.h:56:5)"``, ``"raii_guard<(lambda:foo.h:4:37)>"``).

    A leaf of :func:`canonicalize_type_name` (which additionally normalizes
    whitespace, elaborated-type-specifier prefixes, and const/pointer
    spelling — none of which apply to a raw declaration name) so a producer
    can strip *just* the checkout-dependent path at the point a type's own
    identity (``RecordType.name``/``.qualified_name``, ``EnumType.name``) is
    extracted, rather than leaving the raw, location-bearing spelling to
    flow into old/new type matching (``diff_helpers.type_map_key``) and
    manufacture a spurious ``type_removed``/``type_added`` pair for two
    build trees of the identical declaration under different checkout
    paths.

    The ``:<line>:<col>`` is kept — not dropped the way
    :func:`canonicalize_type_name`'s own (comparison-only, string-equality)
    stripping does — because it is the only discriminator two distinct
    anonymous/lambda declarations in the *same* header have: dropping it
    entirely would collapse ``guard<(lambda at a.hpp:4:3)>`` and
    ``guard<(lambda at a.hpp:40:3)>`` (two unrelated lambdas) to the
    identical key ``guard<(lambda)>``, silently overwriting one entry in
    ``diff_helpers.TypeMap`` (Codex review). Line/column depends only on the
    header's own content, not where it's checked out, so it is stable across
    a checkout-root change for the *same*, *unedited* declaration — the case
    this function exists to fix.

    Known, accepted limitation (Codex review, second round): this is a
    genuine tradeoff, not a fully general fix. If an *unrelated* edit
    earlier in the same header shifts an unchanged anonymous/lambda
    declaration to a different line, its ``:line:col`` changes too, and
    old/new matching sees a different identity for a declaration whose own
    ABI is unchanged — a spurious ``type_removed``/``type_added`` pair in
    the *other* direction from the collision case above. Dropping the
    discriminator entirely (what the pre-existing clang-frontend
    normalizer, ``dumper_clang_expr._normalize_qual_type``, already does)
    would trade this failure mode for the collision one instead — there is
    no location-based discriminator that is simultaneously stable under
    unrelated line movement AND distinguishing between two declarations in
    one header; a genuinely robust identity would need a structural
    fingerprint of the declaration itself, not a location string. Accepted
    as a documented limitation (checkout-root stability was this fix's own
    motivating bug) rather than a third, unproven heuristic.

    Both header-mode dumpers should apply this at extraction time;
    :func:`canonicalize_type_name` remains the right tool for a downstream
    *comparison* that only has the raw spelling to work with (and where a
    same-snapshot collision risk does not apply).

    No whitespace collapse/strip runs here at all (round-4 review, Codex,
    fresh evidence, second finding on the same over-broad-rewrite theme):
    this function is now applied to every castxml record/enum name at
    extraction time, not just anonymous ones, so even a collapse gated on
    "the substitution fired somewhere" still touched unrelated whitespace
    elsewhere in a *composite* name (e.g. a C++20 fixed-string NTTP
    argument alongside a real lambda marker in the same template argument
    list, ``Tag<"a  b", (lambda at a.hpp:4:3)>``). The regex above is now
    anchored and captures its own marker, so the substitution itself never
    introduces stray whitespace to clean up -- nothing here needs
    collapsing, so nothing should be attempted.

    The declaring header's own basename is also kept as a discriminator
    (round 8, Codex review, fresh evidence), alongside ``:line:col``:
    ``"raii_guard<(lambda at /a/foo.h:4:37)>"`` becomes
    ``"raii_guard<(lambda:foo.h:4:37)>"``. Line/column alone cannot tell
    apart two DIFFERENT headers that each declare their own anonymous/
    lambda type at the identical coordinates -- both a real occurrence in
    practice and something no fixed test corpus can rule out in general.
    See :func:`_declaring_header_discriminator`'s own docstring for what
    this still doesn't cover (two same-named headers, at the same
    coordinates, in different directories).

    A match that falls inside a ``"..."`` quoted literal is left completely
    untouched (CodeRabbit review, fresh evidence): a real CastXML anonymous/
    lambda marker is never itself quoted, so a match starting inside quotes
    can only be ordinary string-literal *content* that happens to spell
    location-shaped text -- e.g. a C++20 fixed-string NTTP argument like
    ``Tag<"(lambda at /a/foo.hpp:1:2)">``. Rewriting that would fabricate a
    same-identity collision between two distinct literal values. See
    :func:`_quoted_spans` for the quote-tracking this relies on.
    """
    quoted_spans = _quoted_spans(name)

    def _inside_quotes(pos: int) -> bool:
        return any(start <= pos < end for start, end in quoted_spans)

    def _replace(match: re.Match[str]) -> str:
        if _inside_quotes(match.start()):
            return match.group(0)
        marker, path, coords = match.group(1), match.group(2), match.group(3)
        return f"{marker}:{_declaring_header_discriminator(path)}{coords}"

    return _ANON_TYPE_LOCATION_PATH_ONLY_RE.sub(_replace, name)


def canonicalize_type_name(name: str) -> str:
    """Normalise a C/C++ type name for comparison.

    Transformations (in order):
    0. Strip leading/trailing whitespace and collapse internal whitespace.
    0b. Strip an anonymous struct/union/enum's embedded ``at <path>:<line>:<col>``
        location (clang's ``-ast-dump=json`` spelling), which otherwise makes
        two build trees of the identical declaration compare as different.
    1. Strip leading ``struct ``/``class ``/``union ``/``enum `` elaborated-type-specifier.
    2. Normalise leading ``const T`` → ``T const`` (east-const canonical form),
       but only when the base type contains no angle brackets (templates).
    3. Normalise pointer/reference sigil spacing to a single leading space
       (``int*`` and ``int *`` both become ``int *``).
    4. Final whitespace cleanup.

    This prevents false positives from dumpers that emit different
    elaborated-type-specifier forms, or different pointer/reference sigil
    spacing, for the same type — confirmed as a REAL, live discrepancy
    between castxml (``"char const*"``, no space) and clang's
    ``-ast-dump=json`` (``"char const *"``, with space) via the Phase 2
    castxml↔clang parity gate (PR #582): without this step,
    ``_params_differ``'s own ``canonicalize_type_name(...) ==
    canonicalize_type_name(...)`` equality check — the very first thing it
    tries — would treat a cross-producer (or cross-castxml-version)
    comparison of an otherwise-unchanged pointer parameter as a real,
    breaking type change purely from this spelling convention.

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
    >>> canonicalize_type_name("char const*")
    'char const *'
    >>> canonicalize_type_name("int*")
    'int *'
    >>> canonicalize_type_name("enum (unnamed enum at /a/old/foo.h:56:5)")
    '(unnamed enum)'
    >>> canonicalize_type_name("enum (unnamed enum at /b/new/foo.h:56:5)")
    '(unnamed enum)'
    """
    # 0. Normalise whitespace early so anchored regexes work consistently.
    result = _MULTI_SPACE_RE.sub(" ", name.strip())
    # 0b. Strip the absolute-path/line/col clang embeds in an anonymous
    #     struct/union/enum spelling — it identifies "where the tool ran", not
    #     the type. See _ANON_TYPE_LOCATION_RE above.
    result = _ANON_TYPE_LOCATION_RE.sub("", result)
    result = _MULTI_SPACE_RE.sub(" ", result).replace(" )", ")").strip()
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
    # 3. Normalise pointer/reference sigil spacing (same technique already
    #    used by _strip_cv_qualifiers below).
    result = _PTR_REF_SIGIL_RE.sub(r" \1", result)
    # 4. Final cleanup.
    result = _MULTI_SPACE_RE.sub(" ", result)
    return result.strip()


# Matches whole-word ``const`` / ``volatile`` qualifier tokens. Word boundaries
# keep identifiers such as ``std::integral_constant`` or ``ConstIterator``
# untouched — only the standalone cv keywords are stripped.
_CV_TOKEN_RE = re.compile(r"\b(?:const|volatile)\b")


def _find_matching_close(name: str, open_idx: int) -> int:
    """Index of the bracket matching the opener at *open_idx*.

    Tracks combined ``<([``/``>)]`` depth rather than per-family matching —
    real compiler-produced type spellings are always well-nested, so this is
    sufficient and mirrors the depth-counting already used elsewhere in this
    module (e.g. ``_has_top_level_ptr_or_ref``).
    """
    depth = 1
    i = open_idx + 1
    while i < len(name):
        if name[i] in "<([":
            depth += 1
        elif name[i] in ">)]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(name) - 1


def _skip_spaces(name: str, i: int, end: int) -> int:
    """First non-space index at or after *i*, bounded by *end*."""
    while i < end and name[i].isspace():
        i += 1
    return i


def _skip_trailing_cv_tokens(name: str, i: int, end: int) -> int:
    """Skip past every cv token (and surrounding space) starting at *i*.

    A cv qualifier immediately following a REAL parameter list's closing paren
    is a member-function-POINTER's own cv-qualification (e.g.
    ``"void (C::*)(int) const"`` -- the pointer points to a const member
    function) -- a genuinely different, non-interchangeable type (confirmed
    against real g++ mangling: two same-named overloads differing only in this
    trailing const compile as distinct symbols, matching the existing
    FUNC_CV_CHANGED precedent for a member function's own const/volatile).
    Never neutral, regardless of strict/non-strict context or position relative
    to any pointer sigil -- skipping past it leaves it completely untouched
    rather than stripping it (permissive/non-strict mode) or treating it as a
    stripping candidate (strict mode) (CodeRabbit review, PR #589).
    """
    i = _skip_spaces(name, i, end)
    m = _CV_TOKEN_RE.match(name, i)
    while m and m.end() <= end:
        i = _skip_spaces(name, m.end(), end)
        m = _CV_TOKEN_RE.match(name, i)
    return i


def _is_declarator_grouping_paren(name: str, close_idx: int, end: int) -> bool:
    """Whether the paren closing at *close_idx* groups a declarator rather than
    opening a parameter list.

    A pointer/pointer-to-member DECLARATOR-GROUPING paren -- e.g. the
    ``"(*)"``/``"(*const)"``/``"(C::*)"`` in ``"RetType (*)(Params)"``/
    ``"RetType (C::*)(Params)"`` -- is recognized structurally by being
    immediately followed by ANOTHER top-level paren/bracket (the real parameter
    list or array dimensions), regardless of what's inside it (a bare sigil, or
    a qualified pointer-to-member ``"Class::*"`` -- deliberately NOT restricted
    to "only sigils/cv tokens inside", since that missed the class-qualified
    case: Codex review, PR #589, round 2).
    """
    k = _skip_spaces(name, close_idx + 1, end)
    return k < end and name[k] in "(["


class _CvSegmentScan:
    """One scan of ``name[start:end]``, blanking strippable cv tokens in
    *chars*.

    Split out of :func:`_strip_cv_in_segment` so each syntactic case is its own
    named method rather than a branch in one long loop; the per-case rules
    below are exactly the ones that function's docstring states, and each
    method carries the evidence for its own rule.
    """

    #: Which method handles a given leading character (bound below, once the
    #: methods exist). Every other character falls through to
    #: :meth:`_at_other`, which is where a cv token is actually recognized.
    _HANDLERS: ClassVar[dict[str, Callable[[_CvSegmentScan, int], int]]]

    def __init__(self, name: str, chars: list[str], end: int, *, strict: bool):
        self.name = name
        self.chars = chars
        self.end = end
        self.strict = strict
        self.last_ptr_pos: int | None = None
        self.candidates: list[tuple[int, int]] = []

    def run(self, start: int) -> None:
        i = start
        while i < self.end:
            handler = self._HANDLERS.get(self.name[i], _CvSegmentScan._at_other)
            i = handler(self, i)
        self._flush()

    def _flush(self) -> None:
        """Blank every candidate not shadowed by a pointer sigil to its left.

        In strict mode the pointee-vs-pointer-own position is resolved
        independently PER comma-separated parameter within this paren (a
        callback can itself take multiple parameters, e.g.
        ``void (*)(int*, const int)`` -- the second parameter's own by-value cv
        must not be judged against the FIRST parameter's unrelated pointer
        sigil), which is why this runs at each comma as well as at the end.
        """
        for tok_start, tok_end in self.candidates:
            if self.last_ptr_pos is None or tok_start > self.last_ptr_pos:
                for k in range(tok_start, tok_end):
                    self.chars[k] = " "
        self.candidates.clear()

    def _at_angle(self, i: int) -> int:
        """``<...>`` is skipped whole: a cv qualifier inside a
        template-argument list names a genuinely different type
        (``Box<const int>`` vs. ``Box<int>``; Codex/CodeRabbit review, PR
        #582)."""
        return min(_find_matching_close(self.name, i), self.end - 1) + 1

    def _at_bracket(self, i: int) -> int:
        """``[...]`` is skipped whole, and may move the pointer boundary."""
        j = min(_find_matching_close(self.name, i), self.end - 1)
        if self.last_ptr_pos is None:
            # An array-typed function PARAMETER (no preceding pointer sigil in
            # this segment yet) decays to a pointer, so a cv qualifier before
            # it is pointee-position, not by-value -- same non-strippable
            # treatment as a real pointer sigil (confirmed against real
            # clang/gcc mangling: void(*)(int[3]) and void(*)(const int[3])
            # are different, non-interchangeable function pointer types, same
            # as the int*/const int* case). Only matters in strict mode;
            # harmless otherwise since non-strict stripping ignores
            # last_ptr_pos entirely (Codex review, PR #589).
            self.last_ptr_pos = i
        # else: a "[...]" AFTER an already-seen pointer sigil is the POINTEE's
        # array bound (e.g. "int (*)[3]" -- a pointer to an array, not a
        # decaying array parameter), analogous to a callback's own parameter
        # list: it doesn't move last_ptr_pos, so the declarator's own preceding
        # qualifier (e.g. "int (* const)[3]") stays recognized as its own
        # trailing cv, dropped for mangling (confirmed identical types by real
        # g++: two same-named overloads differing only in that const are a hard
        # redefinition error, not distinct overloads) -- treating this "[" the
        # same as the decay case above wrongly moved the boundary past the
        # declarator's own const, leaving it unstrippable (Codex review, PR
        # #589, round 3).
        return j + 1

    def _at_paren(self, i: int) -> int:
        """``(...)`` is either a declarator grouping (stepped into in place) or
        a real parameter list (recursed into with ``strict=True``)."""
        j = min(_find_matching_close(self.name, i), self.end - 1)
        if _is_declarator_grouping_paren(self.name, j, self.end):
            # NOT itself a nested parameter list, so don't open a fresh strict
            # scope for it: its "*" is the CURRENT (possibly
            # nested-callback-parameter's own) declarator's top-level sigil and
            # must stay visible to it. Recursing here would hide that "*"
            # inside an isolated scan whose own last_ptr_pos never reaches this
            # scope's tracking, so a callback parameter that is itself a
            # function pointer with a cv-qualified return type
            # (``void(*)(int (*)())`` vs. ``void(*)(const int (*)())`` --
            # confirmed distinct, non-interchangeable types by real g++
            # mangling) had its return-type cv wrongly treated as the callback
            # parameter's own neutral by-value qualifier instead. Just step
            # past the "(" and let this same scan process the interior in
            # place.
            return i + 1
        _strip_cv_in_segment(self.name, self.chars, i + 1, j, strict=True)
        return _skip_trailing_cv_tokens(self.name, j + 1, self.end)

    def _at_comma(self, i: int) -> int:
        """A strict-mode comma closes one callback parameter's own scope."""
        if not self.strict:
            return self._at_other(i)
        self._flush()
        self.last_ptr_pos = None
        return i + 1

    def _at_sigil(self, i: int) -> int:
        """``*``/``&`` moves the pointee/pointer-own boundary."""
        self.last_ptr_pos = i
        return i + 1

    def _at_other(self, i: int) -> int:
        """Anything else: a cv token here is recorded (strict) or blanked
        outright (non-strict); any other character is just stepped over."""
        m = _CV_TOKEN_RE.match(self.name, i)
        if not (m and m.end() <= self.end):
            return i + 1
        if self.strict:
            self.candidates.append((m.start(), m.end()))
        else:
            for k in range(m.start(), m.end()):
                self.chars[k] = " "
        return m.end()


_CvSegmentScan._HANDLERS = {
    "<": _CvSegmentScan._at_angle,
    "[": _CvSegmentScan._at_bracket,
    "(": _CvSegmentScan._at_paren,
    ",": _CvSegmentScan._at_comma,
    "*": _CvSegmentScan._at_sigil,
    "&": _CvSegmentScan._at_sigil,
}


def _strip_cv_in_segment(
    name: str, chars: list[str], start: int, end: int, *, strict: bool
) -> None:
    """Blank out strippable ``const``/``volatile`` tokens in ``name[start:end]``.

    A ``<...>``/``[...]`` sub-range is always fully skipped (a cv qualifier
    inside a template-argument list or array subscript names a genuinely
    different type — ``Box<const int>`` vs. ``Box<int>``, Codex/CodeRabbit
    review, PR #582). A ``(...)`` sub-range recurses with ``strict=True``.

    When *strict* (i.e. we're inside a function-parameter list, spelling a
    callback/function-pointer type's own parameters), only a token that is
    NOT in pointee position is strippable: dropped from a function type for
    mangling purposes is the parameter's own by-value or pointer-own
    (trailing, ``"int * const"``) cv, at every nesting level of a function
    type — confirmed against real clang/gcc output. But a callback
    parameter's POINTEE cv (``"const int *"`` — leading, before a top-level
    ``*``/``&``) is NOT mangling-equivalent (``void(*)(int*)`` and
    ``void(*)(const int*)`` are different, non-interchangeable function
    pointer types, unlike an ordinary top-level parameter where ``T*``
    implicitly converts to ``const T*``) — an earlier version of this
    function stripped indiscriminately inside a paren, wrongly neutralizing
    that case too (Codex review, PR #589).

    When not strict (the true top level, i.e. not nested in anything), a cv
    token is strippable regardless of pointee/pointer-own position — this
    intentionally more permissive rule for a plain top-level parameter/
    return/field-adjacent type spelling predates this function and is left
    unchanged (``cv_qualifiers_only_differ``'s own docstring).

    In strict mode, the pointee-vs-pointer-own position is resolved
    independently PER comma-separated parameter within this paren (a
    callback can itself take multiple parameters, e.g. ``void
    (*)(int*, const int)`` — the second parameter's own by-value cv must
    not be judged against the FIRST parameter's unrelated pointer sigil).
    """
    _CvSegmentScan(name, chars, end, strict=strict).run(start)


def _strip_cv_qualifiers(name: str) -> str:
    """Return *name* with ``const`` / ``volatile`` tokens removed — but only
    where doing so can't hide a genuinely different type (see
    ``_strip_cv_in_segment`` for the exact top-level vs. nested-callback-
    parameter rules).

    Whitespace introduced by the removal is collapsed; spaces adjacent to
    pointer/reference sigils and parentheses are normalised so that
    ``const char *`` / ``char *`` and ``void (*)(int)`` / ``void (*)( int
    )`` each reduce to the same string.
    """
    chars = list(name)
    _strip_cv_in_segment(name, chars, 0, len(name), strict=False)
    stripped = "".join(chars)
    stripped = _MULTI_SPACE_RE.sub(" ", stripped)
    # Normalise spacing around pointer/reference sigils so "char  *" == "char *".
    stripped = re.sub(r"\s*([*&])\s*", r" \1", stripped)
    # Normalise spacing around parens so removing a token flush against "("
    # or ")" doesn't leave a stray space a non-stripped spelling never had.
    stripped = re.sub(r"\(\s+", "(", stripped)
    stripped = re.sub(r"\s+\)", ")", stripped)
    stripped = re.sub(r"\s+,", ",", stripped)
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


def func_signature_cv_only_differ(old_type: str, new_type: str) -> bool:
    """Return True when two *function-parameter or return-type* spellings
        differ only by ``const``/``volatile`` tokens, including a top-level
        BY-VALUE difference (``int`` -> ``volatile int``) that
        :func:`cv_qualifiers_only_differ` deliberately excludes.

    DO NOT use this for an ordinary field/variable comparison — a top-level
        by-value cv change on THOSE is intentionally treated as a source-level
        contract change (see :func:`cv_qualifiers_only_differ`'s own docstring
        and the ``case30_field_qualifiers`` example; confirmed by
        ``test_top_level_field_const_is_not_neutralised``). The one deliberate
        exception is a *legacy-snapshot* fallback (``header_cv_facts_reliable``
        is False): both ``diff_types._field_type_genuinely_changed`` and
        ``diff_symbols._check_variable`` reuse this function there specifically
        because a pre-fix CastXML snapshot's spelling may have silently dropped
        the real qualifier, making a genuine field/variable cv change
        indistinguishable from tool-upgrade noise — see those callers' own
        docstrings. This function exists in the first place because the field/
        variable reasoning does NOT extend to a function's own parameter or
        return type: per the C++ standard, a top-level cv-qualifier on a
        by-value parameter or return type is dropped from the function's type
        for linkage/mangling purposes — ``void f(int)`` and ``void f(const
        int)`` name the very same function — so unlike a field, there is no
        corresponding dedicated detector and no ABI-relevant meaning to escalate
        (Codex review, PR #582: castxml's parser began spelling a by-value
        ``volatile`` parameter as ``"volatile int"``, which without this check
        misfired the generic, breaking ``FUNC_PARAMS_CHANGED``/return-type-
        changed path for a change with zero ABI/mangling effect).

        Returns False when the canonical forms are already identical (the
        caller's own equality check already handles that), or when a genuine
        non-cv difference remains after stripping.

        >>> func_signature_cv_only_differ("int", "volatile int")
        True
        >>> func_signature_cv_only_differ("int", "const int")
        True
        >>> func_signature_cv_only_differ("int", "long")
        False
        >>> func_signature_cv_only_differ("char *", "const char *")
        True
    """
    co = canonicalize_type_name(old_type)
    cn = canonicalize_type_name(new_type)
    if co == cn:
        return False
    return _strip_cv_qualifiers(co) == _strip_cv_qualifiers(cn)
