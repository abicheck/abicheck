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

"""Export accounting (ADR-035 D4) — classify every exported symbol with a reason.

Pure Itanium/MSVC mangled-name classification split out of :mod:`abicheck.buildsource.cross_source_checks` (it
grew past the 2000-line file cap). Given the binary's export table and its public
declarations, ``_check_exported_not_public`` partitions every export into one of
the ``ACCOUNT_*`` buckets — documented API, a compiler artifact, an external
dependency leak (libstdc++/{fmt}/…), an internal-namespace escape, a template
instantiation, or a bare undeclared export — so a report can state "100 %
accounted". These helpers are free of any ``Change``/``ChangeKind`` concern;
:mod:`abicheck.buildsource.cross_source_checks` turns the undocumented buckets into findings.
"""

from __future__ import annotations

import re

from ..model import AbiSnapshot
from ..model.export_entity_name import (  # noqa: F401 -- re-exported for callers/tests
    _ARTIFACT_OPERAND_PREFIXES,
    _NESTED_QUALIFIERS_RE,
    _THUNK_PREFIX_RE,
    EntityName,
    _encoding_after_prefixes,
    _read_decimal_length,
    entity_name_components,
)
from .source_link import (
    _TBB_MALLOC_PROXY_C_SYMBOLS,
    _TBB_MALLOC_PROXY_CPP_SYMBOLS,
    _TBB_MALLOC_PROXY_MARKER,
)

#: The exported symbol marking a library as an allocator-interposition proxy.
#: Re-exported for :mod:`abicheck.buildsource.cross_source_checks`'s ``_check_exported_not_public`` loop.
_ALLOCATOR_INTERPOSER_MARKER = _TBB_MALLOC_PROXY_MARKER

#: Symbols an allocator-interposition library (a malloc proxy such as
#: ``libtbbmalloc_proxy``) intentionally *defines* to replace the global allocator
#: — ``malloc``/``free``/… and the global ``operator new``/``delete``. When the
#: audited DSO is such an interposer (it exports :data:`_ALLOCATOR_INTERPOSER_MARKER`)
#: these are native, not a leaked libc/libstdc++ dependency (Codex review).
#: The interposer *marker* itself is part of the intentional proxy surface (the
#: source linker treats it so), so it is native too — not a spurious undeclared
#: export (Codex review).
_ALLOCATOR_INTERPOSER_SYMBOLS = (
    _TBB_MALLOC_PROXY_C_SYMBOLS
    | _TBB_MALLOC_PROXY_CPP_SYMBOLS
    | {_TBB_MALLOC_PROXY_MARKER}
)

# --------------------------------------------------------------------------- #
# Export accounting (ADR-035 D4) — every exported symbol gets a precise reason.
#
# ``exported_not_public`` used to answer a yes/no ("is this export documented?").
# The accounting refines the *no* answers so a maintainer sees **why** each
# undocumented export exists: a leaked external-dependency symbol (libstdc++,
# {fmt}, …) is a very different problem from the library's own internal namespace
# escaping, and both differ from a genuine undeclared C entry point. Together with
# the documented + compiler-artifact buckets the categories partition the whole
# export table, so a report can state "100 % accounted".
# --------------------------------------------------------------------------- #

#: A documented public-API export (a PUBLIC_HEADER decl maps to it). Not a finding.
ACCOUNT_PUBLIC = "documented_public_api"
#: A compiler-generated C++ ABI artifact owned by a class (ctor/dtor/vtable/RTTI/
#: thunk). Accounted as legitimate — its owning type is the real surface.
ACCOUNT_CXX_ARTIFACT = "cxx_abi_artifact"
#: A symbol that leaked in from an external dependency (C++ runtime or a vendored
#: third-party library statically linked and re-exported). Clearly marked.
ACCOUNT_EXTERNAL_DEP = "external_dependency"
#: The library's *own* internal namespace (``::impl``/``::internal``/``::detail``/
#: an anonymous namespace) accidentally exported — the classic visibility leak.
ACCOUNT_INTERNAL_NS = "internal_namespace"
#: An exported C++ template instantiation with no matching public declaration
#: (the header declares the template, the binary carries an instantiation).
ACCOUNT_TEMPLATE_INST = "template_instantiation"
#: An instantiation (or member of an instantiation) of a template a public
#: header declares -- ``template <attr_id A> X::set(...)``, a member of a public
#: class template. Public API a consumer links against, so documented, never a
#: "hide it" finding (hiding it breaks those consumers).
ACCOUNT_PUBLIC_TEMPLATE = "public_template_instantiation"
#: A standard/third-party template the library instantiated over its *own*
#: types (``std::_Sp_counted_deleter<dnnl::impl::stream*, ...>``): the library's
#: own vague-linkage copy, not a statically linked dependency.
ACCOUNT_OWN_TYPE_INSTANTIATION = "own_type_instantiation"
#: An undocumented export none of the finer reasons explain — a bare accidental
#: entry point (often ``extern "C"``) with no public declaration.
ACCOUNT_UNDECLARED = "undeclared_export"
#: A deliberate allocator-interposition export (``malloc``/``operator new``/… on a
#: malloc-proxy library that exports :data:`_ALLOCATOR_INTERPOSER_MARKER`). Native
#: and intentional — accounted as legitimate, never a finding (Codex review).
ACCOUNT_ALLOCATOR_INTERPOSER = "allocator_interposer"

#: The account categories that constitute *undocumented* surface (each yields an
#: ``exported_not_public`` finding), in report order.
_UNDOCUMENTED_ACCOUNTS: tuple[str, ...] = (
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_INTERNAL_NS,
    ACCOUNT_TEMPLATE_INST,
    ACCOUNT_OWN_TYPE_INSTANTIATION,
    ACCOUNT_UNDECLARED,
)

#: Itanium ``<substitution>`` abbreviations for ``std`` and its members
#: (``St`` = ``std``, ``Ss`` = ``std::string``, ``Si``/``So``/``Sd`` = the
#: iostream types, ``Sa``/``Sb`` = allocator/basic_string). A leading one marks a
#: C++-runtime owner.
_STD_SUBSTITUTIONS = ("St", "Ss", "Si", "So", "Sd", "Sa", "Sb")

#: Owner namespaces that belong to the C++ runtime (map to libstdc++). ``__cxx11``
#: /``__gnu_cxx``/``__cxxabiv1`` are libstdc++ inline/implementation namespaces.
_STD_OWNER_NAMESPACES = frozenset({"__cxx11", "__gnu_cxx", "__cxxabiv1"})

#: Vendored third-party libraries keyed by their **owner namespace** (the demangled
#: top-level namespace, e.g. ``fmt``). These commonly get statically linked and
#: re-exported while shipping no ``DT_NEEDED`` of their own, so
#: :func:`~abicheck.elf_metadata._guess_symbol_origin` cannot see them.
_VENDORED_OWNER_NAMESPACES: dict[str, str] = {
    "fmt": "{fmt} (vendored third-party)",
    "boost": "Boost (vendored third-party)",
    "absl": "Abseil (vendored third-party)",
    "re2": "RE2 (vendored third-party)",
    "spdlog": "spdlog (vendored third-party)",
    "grpc": "gRPC (vendored third-party)",
    "google": "Google/protobuf (vendored third-party)",
    "protobuf": "Protocol Buffers (vendored third-party)",
}


def _mangled_owner_namespace(symbol: str) -> str | None:
    """The owning top-level namespace of an Itanium *symbol* (best-effort).

    A namespace owner exists only for a **nested** name (a leading ``N`` after the
    prefix peel) or a ``std`` substitution (``St…``, valid even un-nested). A plain
    ``_Z<name>`` / ``_ZTV<name>`` top-level function or type has *no* namespace
    owner, so a native global named ``fmt`` (``_Z3fmtv``) is never mistaken for the
    ``{fmt}`` namespace (Codex review). The owner is the entity's *definer* — not a
    type it merely references in a parameter/template argument. ``None`` when there
    is no namespace owner or it cannot parse.
    """
    if not symbol.startswith("_Z"):
        return None
    rest = _encoding_after_prefixes(symbol)
    if rest.startswith("N"):
        # Enter the nested name and skip any leading CV-/ref-qualifiers
        # (``r``/``V``/``K`` then ``R``/``O``) so a const member export like
        # ``_ZNK3fmt…`` still reads ``fmt`` as its owner (Codex review). None of
        # those letters can begin a length-prefixed name or an ``St`` substitution.
        rest = _NESTED_QUALIFIERS_RE.sub("", rest[1:], count=1)
    elif not rest.startswith(_STD_SUBSTITUTIONS):
        return None  # un-nested top-level name — no namespace owner
    if rest.startswith(_STD_SUBSTITUTIONS):
        return "std"
    parsed = _read_decimal_length(rest, 0)
    if parsed is not None:
        length, name_start = parsed
        if length > len(rest) - name_start:
            return None
        name = rest[name_start : name_start + length]
        if name and re.match(r"[A-Za-z_]\w*", name):
            return name
    return None


_PREFIX_NO_MATCH: object = object()
"""Sentinel: the runtime-prefix table did not attribute the symbol."""


def _origin_from_prefix_table(
    symbol: str,
    needed_libs: list[str],
    self_names: tuple[str, ...],
) -> str | None | object:
    """Prefix-table origin, or :data:`_PREFIX_NO_MATCH` to fall through to owners.

    A non-sentinel return (``None`` or a lib name) is authoritative.
    """
    from ..elf_metadata import _guess_symbol_origin

    lib = _guess_symbol_origin(symbol, needed_libs)
    if lib is None:
        return _PREFIX_NO_MATCH
    # libc++abi is the ABI support library, not libc++ itself. If the prefix
    # table picked it (DT_NEEDED ordered libc++abi before libc++) for a libc++
    # ``std::__1`` symbol, re-resolve to the real libc++ runtime (Codex review).
    if lib.rsplit("/", 1)[-1].startswith("libc++abi") and "St3__1" in symbol:
        lib = _cxx_runtime_lib(symbol, needed_libs)
    # If the resolved runtime library *is* the audited library (auditing
    # libstdc++/libc++ itself), its own std/runtime symbols are native, not a
    # leak — the self gate covers the runtime path too, not only vendored
    # namespaces (Codex review).
    if _resolved_lib_is_self(lib, self_names):
        return None
    return lib


def _itanium_owner_runtime(
    owner: str,
    symbol: str,
    needed_libs: list[str],
    self_names: tuple[str, ...],
) -> str | None:
    """Resolve an Itanium owner namespace to its C++ runtime lib, else ``None``.

    The owner-fallback runtime path (a guard variable / nested-std form the
    prefix table misses); Itanium C++ runtimes only — MSVC STL attribution is
    handled by :func:`_msvc_leaked_owner`.
    """
    if owner == "__gnu_cxx":
        # libstdc++-only extension namespace — never libc++.
        return "libstdc++.so.6"
    if owner == "__cxxabiv1":
        # The Itanium C++ ABI namespace is implemented by libc++abi (or
        # libstdc++'s merged libsupc++), NOT the std runtime — so ``libc++abi``
        # is its correct owner and must not be excluded, else auditing libc++abi
        # flags its own exports (Codex review).
        return _cxx_abi_runtime_lib(needed_libs, self_names)
    if owner == "std" or owner in _STD_OWNER_NAMESPACES:
        return _cxx_runtime_lib(symbol, needed_libs)
    return None


def _msvc_leaked_owner(symbol: str) -> tuple[str, str | None] | None:
    """``(owner, google_child)`` for an MSVC decorated name, or ``None`` to bail.

    MSVC decorated name (``?name@scope@…@@sig``): no Itanium owner, but its
    outermost ``@``-scope still names a vendored dependency (Boost/{fmt}/
    protobuf) statically linked and re-exported on Windows — otherwise those
    leaks are miscounted as ``undeclared_export`` (Codex review). MSVC C++
    runtime (STL) attribution is a separate concern, so a bare ``std`` owner
    stays native here rather than being mislabelled with an ELF soname.
    """
    comps = _msvc_scope_components(symbol)
    if len(comps) < 2:
        return None  # un-nested name — no enclosing scope, no owner
    owner = comps[-1]  # scopes are inner-to-outer; the top-level ns is last
    if owner == "std":
        return None
    return owner, (comps[-2] if len(comps) >= 3 else None)


def _vendored_owner_result(
    owner: str,
    google_child: str | None,
    self_names: tuple[str, ...],
) -> str | None:
    """Map a resolved owner namespace to its vendored dependency lib, or ``None``."""
    vendored = _VENDORED_OWNER_NAMESPACES.get(owner)
    if owner == "google" and google_child != "protobuf":
        # ``google::`` is shared by many Google libraries (glog's
        # ``google::LogMessage``, gflags, googletest, …); only ``google::protobuf``
        # is Protocol Buffers. A bare ``google::`` owner is not a protobuf leak
        # (Codex review) — leave it native (undeclared/internal).
        vendored = None
    if vendored is not None and _owner_is_self_library(owner, self_names):
        return None  # the audited library *is* this vendored library — native
    return vendored


def _external_dependency_origin(
    symbol: str,
    needed_libs: list[str],
    self_names: tuple[str, ...] = (),
) -> str | None:
    """Name the external dependency *symbol* leaked from, or ``None`` if native.

    Two signals, cheapest first: the shared
    :func:`~abicheck.elf_metadata._guess_symbol_origin` runtime-prefix table
    (libc/libgcc/libmvec/fundamental-RTTI/``operator new`` and the ``_ZNSt``/
    ``_ZTVSt`` std prefixes) via :func:`_origin_from_prefix_table`, then an
    **owner-namespace** check that also covers the leaked-definition forms the
    prefix table misses — a std/libstdc++ vtable, typeinfo, or guard variable for
    a *nested* std type (``_ZTVNSt…``, ``_ZGVZNSt…``) and a vendored third-party
    owner (``fmt``/``boost``/…). Reading the *owner* (not any referenced type)
    keeps a native symbol that merely takes a ``std`` argument from being
    mislabelled external. Conservative: only a positive match returns a name.

    ``self_names`` are the audited library's own identity tokens (soname /
    install-name / library name; see :func:`_library_self_names`): a vendored
    namespace owned by the library *being scanned* (auditing libfmt itself, whose
    ``fmt::detail`` symbols are native, not a leak) is **not** reported external so
    the finding does not tell users to unlink their own library (Codex review).
    """
    prefix = _origin_from_prefix_table(symbol, needed_libs, self_names)
    if prefix is not _PREFIX_NO_MATCH:
        return prefix  # type: ignore[return-value]
    owner = _mangled_owner_namespace(symbol)
    if owner is not None:
        runtime = _itanium_owner_runtime(owner, symbol, needed_libs, self_names)
        if runtime is not None:
            return None if _resolved_lib_is_self(runtime, self_names) else runtime
        google_child = _nested_component(symbol, 1)
    else:
        msvc = _msvc_leaked_owner(symbol)
        if msvc is None:
            return None
        owner, google_child = msvc
    return _vendored_owner_result(owner, google_child, self_names)


def _msvc_scope_components(symbol: str) -> list[str]:
    """The components of an MSVC decorated *symbol*'s name, entity-first.

    ``?foo@system@boost@@YAXXZ`` → ``["foo", "system", "boost"]`` — the entity name
    first, then each enclosing scope inner-to-outer, so the **top-level** namespace
    is the *last* element and the scope nested directly inside it is second-to-last.
    Reads only the name portion (up to the ``@@`` that introduces the type
    signature). Empty for a non-MSVC name. Best-effort: special/template forms
    (``??0…`` ctors, ``?$Name@…`` templates) still yield their outer scopes, which
    is all the vendored-owner lookup needs.
    """
    if not symbol.startswith("?"):
        return []
    return [c for c in symbol.lstrip("?").split("@@", 1)[0].split("@") if c]


def _nested_component(symbol: str, index: int) -> str | None:
    """The *index*-th (0-based) component of an Itanium entity name.

    Reads only the entity-name qualifiers (template arguments are skipped as
    whole productions and the nested-name close ends the walk), so
    ``_ZN6google8protobuf7MessageEv`` yields ``google`` at 0 and ``protobuf``
    at 1. ``None`` for an unparseable name or when the component does not
    exist. See :func:`entity_name_components`.
    """
    parsed = entity_name_components(symbol)
    if parsed is None or not parsed.nested:
        return None
    comps = parsed.components
    return comps[index] if 0 <= index < len(comps) else None


#: Library-name stems a vendored owner ships under, when they differ from the owner
#: token. protobuf's ``google::protobuf`` namespace lives in ``libprotobuf`` — so
#: the self-match for a ``google``/``protobuf`` owner is the **protobuf** library,
#: NOT any ``libgoogle_*`` (a ``libgoogle_cloud_cpp`` wrapper that re-exports
#: protobuf must still flag; Codex review). Owners absent here self-match on the
#: owner token itself.
_VENDORED_SELF_ALIASES: dict[str, tuple[str, ...]] = {
    "google": ("protobuf",),
    "protobuf": ("protobuf",),
}


def _library_stem(name: str) -> str:
    """A library basename minus its ``.so``/``.dylib`` extension and version.

    ``libstdc++.so.6`` → ``libstdc++``; ``libc++.1.dylib`` → ``libc++``;
    ``libfmt.so.9`` → ``libfmt``. Used to compare a resolved runtime name against
    the audited library's own identity.
    """
    base = name.rsplit("/", 1)[-1]
    return re.sub(r"(?:\.\d+)*\.(?:so|dylib)(?:\.\d+)*$", "", base).lower()


def _resolved_lib_is_self(lib: str, self_names: tuple[str, ...]) -> bool:
    """Whether a resolved dependency *lib* is the audited library itself."""
    stem = _library_stem(lib)
    return any(_library_stem(n) == stem for n in self_names)


def _owner_is_self_library(owner: str, self_names: tuple[str, ...]) -> bool:
    """Whether a vendored *owner* names the audited library itself (not a wrapper).

    Boundary-aware, not a bare substring: ``libfmt.so.9``/``libfmt`` is fmt, but a
    wrapper/plugin like ``libfmtshim.so`` that statically re-exports ``fmt::`` is
    NOT — its leaked fmt surface must still flag (Codex review). Matches when a
    self-name, minus any ``lib`` prefix, is exactly one of the owner's library-name
    stems (:data:`_VENDORED_SELF_ALIASES`, e.g. ``google`` → ``libprotobuf``) or that
    stem followed by a ``.``/``_``/``-``/``+`` separator. The ``+`` boundary matters
    for C++ libraries that carry it in their soname (``libgrpc++.so`` → ``grpc``,
    ``libc++`` family), so a gRPC self-scan does not report its own ``grpc::`` surface
    as a vendored-dependency leak (``libboost_system`` → ``boost``; Codex review).
    """
    stems = _VENDORED_SELF_ALIASES.get(owner, (owner,))
    boundary = re.compile(
        "(?:" + "|".join(re.escape(s) for s in stems) + r")([._+-]|$)"
    )
    for name in self_names:
        stem = name[3:] if name.startswith("lib") else name
        if boundary.match(stem):
            return True
    return False


def _library_self_names(snapshot: AbiSnapshot) -> tuple[str, ...]:
    """The audited library's own identity tokens (lower-cased), for self-detection.

    The ELF soname, the Mach-O install-name basename, and the snapshot's library
    name — so a vendored-namespace owner that is actually the *scanned* library
    (auditing libfmt/libboost themselves) can be recognised as native rather than a
    leaked dependency (Codex review).
    """
    names: list[str] = []
    if snapshot.library:
        names.append(snapshot.library.rsplit("/", 1)[-1])
    if snapshot.elf is not None and snapshot.elf.soname:
        names.append(snapshot.elf.soname.rsplit("/", 1)[-1])
    if snapshot.macho is not None and snapshot.macho.install_name:
        names.append(snapshot.macho.install_name.rsplit("/", 1)[-1])
    return tuple(n.lower() for n in names if n)


def _linked_library_names(snapshot: AbiSnapshot) -> list[str]:
    """The binary's linked-library names across ELF / Mach-O / PE.

    ELF ``DT_NEEDED``, Mach-O ``LC_LOAD_DYLIB`` (``dependent_libs``), and PE import
    DLL names — so the C++-runtime origin picker can name the dependency the binary
    actually links (a ``libc++.1.dylib`` dylib on macOS, not a hard-coded ELF
    soname; Codex review). Best-effort: an absent table contributes nothing.
    """
    names: list[str] = []
    if snapshot.elf is not None:
        names.extend(getattr(snapshot.elf, "needed", []) or [])
    if snapshot.macho is not None:
        names.extend(getattr(snapshot.macho, "dependent_libs", []) or [])
        names.extend(getattr(snapshot.macho, "reexported_libs", []) or [])
    if snapshot.pe is not None:
        names.extend((getattr(snapshot.pe, "imports", {}) or {}).keys())
    return names


def _cxx_runtime_lib(symbol: str, needed_libs: list[str]) -> str:
    """Which C++ runtime a leaked ``std`` symbol belongs to (libc++ vs libstdc++).

    libc++ mangles ``std`` through its ``std::__1`` inline namespace (``…St3__1…``);
    libstdc++ does not. Prefer whichever runtime the binary actually links (from the
    platform's linked-library list) — so a macOS build names its real
    ``libc++.1.dylib`` dylib and a libc++ ELF build is not mislabelled libstdc++
    (Codex review) — falling back to a canonical soname only when the dependency
    list does not carry a match.
    """
    libcxx_marker = "St3__1" in symbol
    needed_bases = [lib.rsplit("/", 1)[-1] for lib in needed_libs]

    def _is_libcxx(base: str) -> bool:
        # ``libc++abi`` is the separate ABI runtime, not libc++ itself — a
        # ``std::__1`` symbol comes from libc++, so it must not be attributed to a
        # ``libc++abi.so.1`` that happens to precede libc++ in the list (Codex).
        return base.startswith("libc++") and not base.startswith("libc++abi")

    if libcxx_marker:
        for base in needed_bases:
            if _is_libcxx(base):
                return base
        return "libc++.so.1"
    for base in needed_bases:
        if _is_libcxx(base):
            return base
    for base in needed_bases:
        if base.startswith("libstdc++"):
            return base
    return "libstdc++.so.6"


def _cxx_abi_runtime_lib(
    needed_libs: list[str], self_names: tuple[str, ...] = ()
) -> str:
    """Which library owns a ``__cxxabiv1`` (Itanium C++ ABI) symbol.

    The ABI runtime lives in ``libc++abi`` (libc++ toolchains) or, merged as
    libsupc++, in ``libstdc++`` (GCC). Prefer whichever the binary actually links —
    libc++abi is a valid owner here (unlike the ``std`` runtime picker, which
    excludes it). When the audited library *is* libc++abi but has no self-dependency
    in ``DT_NEEDED``, ``self_names`` lets it still be recognised so the self gate can
    mark its own ABI exports native. Defaults to libstdc++ when neither is present.
    """
    needed_bases = [lib.rsplit("/", 1)[-1] for lib in needed_libs]
    for base in needed_bases:
        if base.startswith("libc++abi"):
            return base
    for name in self_names:
        if name.startswith("libc++abi"):
            return name
    # A libc++ toolchain without a separate libc++abi in DT_NEEDED still owns its
    # ABI symbols through the libc++ family, not libstdc++.
    for base in needed_bases:
        if base.startswith("libc++"):
            return base
    for base in needed_bases:
        if base.startswith("libstdc++"):
            return base
    return "libstdc++.so.6"


def _account_undocumented_export(symbol: str) -> str:
    """Classify a *non-external*, undocumented export into one account category.

    The caller has already excluded documented (:data:`ACCOUNT_PUBLIC`), compiler-
    artifact (:data:`ACCOUNT_CXX_ARTIFACT`), and external-dependency
    (:data:`ACCOUNT_EXTERNAL_DEP`) symbols; this decides between an internal-
    namespace escape, a template instantiation, and a bare undeclared export using
    only the mangled name, so it needs no demangler and stays deterministic. Both
    signals read the **entity name** only, never the signature's parameter types.
    """
    if _entity_owner_is_internal(symbol):
        return ACCOUNT_INTERNAL_NS
    if _has_template_args(symbol):
        return ACCOUNT_TEMPLATE_INST
    return ACCOUNT_UNDECLARED


#: Namespace/class component names that mark an internal-implementation surface.
#: Matched **exactly** against a whole name component (not a substring): an
#: ordinary name like ``Simple`` merely *containing* ``impl`` is not internal
#: (Codex review). The anonymous namespace (``_GLOBAL__N_…``) is handled separately.
_INTERNAL_NS_NAMES = frozenset({"impl", "internal", "detail"})


def _is_internal_ns_component(name: str) -> bool:
    """Whether a mangled name component is an internal namespace/anonymous namespace."""
    return name in _INTERNAL_NS_NAMES or name.startswith("_GLOBAL__N_")


def _entity_owner_is_internal(symbol: str) -> bool:
    """Whether the entity's *enclosing* namespace/class is an internal one.

    Only the enclosing namespace/class components decide — never the entity's own
    final name (so an ordinary ``lib::detail()`` function is not internal; only
    ``lib::detail::foo`` is; Codex review) and never a parameter type that merely
    *references* an internal namespace (``foo(lib::detail::Type*)``; Codex review).
    Handles Itanium and MSVC decorated names; an un-nested name has no enclosing
    scope and is therefore never internal.
    """
    if symbol.startswith("?"):
        # MSVC ``?<name>@<scope>@…@@<type>``: the FIRST ``@``-component is the entity
        # name; only the enclosing scopes that follow decide internal-ness.
        comps = symbol.lstrip("?").split("@@", 1)[0].split("@")
        return any(_is_internal_ns_component(c) for c in comps[1:] if c)
    parsed = entity_name_components(symbol)
    if parsed is None or not parsed.nested:
        return False  # unparseable, or un-nested: no enclosing namespace
    # The last component is the entity's own name; only its enclosing
    # namespace/class components decide internal-ness (Codex review). Names
    # that occur *inside* a template-argument list or the signature -- a
    # ``detail::traits<...>::return_type`` return type, a ``dnnl::impl::T``
    # template argument -- are never components, because
    # :func:`entity_name_components` skips each template-argument list as one
    # balanced production.
    return any(_is_internal_ns_component(c) for c in parsed.components[:-1])


def _has_template_args(symbol: str) -> bool:
    """Whether the *entity* an Itanium *symbol* names is a template instantiation.

    Reads only the encoded entity **name**, never the function signature's
    parameter encodings: a plain ``foo(std::vector<int>)``
    (``_ZN3lib3fooESt6vectorIiSaIiEE``) is *not* a template instantiation even
    though a parameter type carries ``I…E`` (Codex review). The entity is a
    template iff **any** of its components carries template arguments --
    including an enclosing class-template specialization whose member is
    exported (``lib::Box<int>::bar()`` = ``_ZN3lib3BoxIiE3barEv``). An ``I``
    inside an ordinary identifier (``InitEngine``) is never a template opener.
    """
    parsed = entity_name_components(symbol)
    return parsed is not None and bool(parsed.template_positions)
