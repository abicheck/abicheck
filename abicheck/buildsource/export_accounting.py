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

Pure Itanium/MSVC mangled-name classification split out of :mod:`crosscheck` (it
grew past the 2000-line file cap). Given the binary's export table and its public
declarations, ``_check_exported_not_public`` partitions every export into one of
the ``ACCOUNT_*`` buckets — documented API, a compiler artifact, an external
dependency leak (libstdc++/{fmt}/…), an internal-namespace escape, a template
instantiation, or a bare undeclared export — so a report can state "100 %
accounted". These helpers are free of any ``Change``/``ChangeKind`` concern;
:mod:`crosscheck` turns the undocumented buckets into findings.
"""

from __future__ import annotations

import re

from ..model import AbiSnapshot

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
#: An undocumented export none of the finer reasons explain — a bare accidental
#: entry point (often ``extern "C"``) with no public declaration.
ACCOUNT_UNDECLARED = "undeclared_export"

#: The account categories that constitute *undocumented* surface (each yields an
#: ``exported_not_public`` finding), in report order.
_UNDOCUMENTED_ACCOUNTS: tuple[str, ...] = (
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_INTERNAL_NS,
    ACCOUNT_TEMPLATE_INST,
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

#: Itanium prefixes whose *operand* is a type whose owning namespace decides
#: external-vs-native: vtable/typeinfo/typeinfo-name/VTT (``_ZTV``/``_ZTI``/
#: ``_ZTS``/``_ZTT``) and a guard variable (``_ZGV``, ``_ZGVZ`` for a local static).
#: Peeling them lets a leaked *dependency* vtable/typeinfo (``_ZTVNSt…``,
#: ``_ZTIN3fmt…``) be attributed to the dependency instead of exempted as this
#: library's own class artifact (Codex review). Thunks carry a numeric call-offset
#: before the operand and are peeled by :data:`_THUNK_PREFIX_RE` instead.
_ARTIFACT_OPERAND_PREFIXES = (
    "_ZTV",
    "_ZTI",
    "_ZTS",
    "_ZTT",
    "_ZGVZ",
    "_ZGV",
)

#: A non-virtual/virtual/covariant thunk prefix (``_ZTh``/``_ZTv``/``_ZTc``)
#: followed by its call-offset run. Each offset chunk is ``n?<num>_`` and, for a
#: covariant (``_ZTc``) thunk, may carry an ``h``/``v`` tag (``h<n>_`` /
#: ``v<n>_<n>_``), so the run token is ``[hv]?n?\d+_``. Peeling the whole run —
#: not just the ``_ZTh`` letters — leaves the nested-name operand, so a leaked
#: dependency thunk (``_ZThn16_N3fmt…``, ``_ZTv0_n24_N5boost…``, covariant
#: ``_ZTchn16_h16_N3fmt…``) attributes to its dependency instead of falling through
#: to the artifact exemption (Codex review).
_THUNK_PREFIX_RE = re.compile(r"^_ZT[hvc](?:[hv]?n?\d+_)+")

#: Itanium nested-name CV-qualifiers (``r`` restrict / ``V`` volatile / ``K`` const)
#: and ref-qualifiers (``R`` ``&`` / ``O`` ``&&``) that sit between the ``N`` intro
#: and the first name component. Skipped before reading the owner so a const/ref
#: member export (``_ZNK3fmt…``) still resolves its namespace (Codex review).
_NESTED_QUALIFIERS_RE = re.compile(r"^[rVK]*[RO]?")


def _encoding_after_prefixes(symbol: str) -> str:
    """Strip ``_Z`` and any vtable/typeinfo/guard-variable/thunk prefix.

    Returns the Itanium *encoding* that follows — the operand type for an artifact,
    or the name+signature for a plain function/data — with the nested-name ``N``
    intro left intact so callers can tell a nested name (``N3fmt…E``) from an
    un-nested top-level name (``3fmt`` = a global ``fmt``, no namespace).
    """
    thunk = _THUNK_PREFIX_RE.match(symbol)
    if thunk:
        return symbol[thunk.end() :]
    for pfx in _ARTIFACT_OPERAND_PREFIXES:
        if symbol.startswith(pfx):
            return symbol[len(pfx) :]
    return symbol[2:] if symbol.startswith("_Z") else symbol


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
    m = re.match(r"(\d+)([A-Za-z_]\w*)", rest)
    if m:
        return m.group(2)[: int(m.group(1))]
    return None


def _external_dependency_origin(
    symbol: str, needed_libs: list[str], self_names: tuple[str, ...] = ()
) -> str | None:
    """Name the external dependency *symbol* leaked from, or ``None`` if native.

    Two signals, cheapest first: the shared
    :func:`~abicheck.elf_metadata._guess_symbol_origin` runtime-prefix table
    (libc/libgcc/libmvec/fundamental-RTTI/``operator new`` and the ``_ZNSt``/
    ``_ZTVSt`` std prefixes), then an **owner-namespace** check that also covers the
    leaked-definition forms the prefix table misses — a std/libstdc++ vtable,
    typeinfo, or guard variable for a *nested* std type (``_ZTVNSt…``,
    ``_ZGVZNSt…``) and a vendored third-party owner (``fmt``/``boost``/…). Reading
    the *owner* (not any referenced type) keeps a native symbol that merely takes a
    ``std`` argument from being mislabelled external. Conservative: only a positive
    match returns a name.

    ``self_names`` are the audited library's own identity tokens (soname /
    install-name / library name; see :func:`_library_self_names`): a vendored
    namespace owned by the library *being scanned* (auditing libfmt itself, whose
    ``fmt::detail`` symbols are native, not a leak) is **not** reported external so
    the finding does not tell users to unlink their own library (Codex review).
    """
    from ..elf_metadata import _guess_symbol_origin

    lib = _guess_symbol_origin(symbol, needed_libs)
    if lib is not None:
        return lib
    owner = _mangled_owner_namespace(symbol)
    if owner is None:
        return None
    if owner == "__gnu_cxx":
        # libstdc++-only extension namespace — never libc++.
        return "libstdc++.so.6"
    if owner == "std" or owner in _STD_OWNER_NAMESPACES:
        return _cxx_runtime_lib(symbol, needed_libs)
    vendored = _VENDORED_OWNER_NAMESPACES.get(owner)
    if vendored is not None and any(owner in name for name in self_names):
        return None  # the audited library *is* this vendored library — native
    return vendored


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


#: Namespace/class name fragments that mark an internal-implementation surface.
_INTERNAL_NS_TOKENS = ("impl", "internal", "detail")


def _entity_owner_is_internal(symbol: str) -> bool:
    """Whether the entity's own name sits in an internal namespace.

    Scans only the **entity name** components (an ``impl``/``internal``/``detail``
    namespace or an anonymous ``_GLOBAL__N_`` namespace), never a parameter type
    that merely *references* such a namespace — so ``foo(lib::detail::Type*)``
    (``_ZN3lib3fooEPN3lib6detail4TypeE``) is not mis-bucketed as internal (Codex
    review). Mirrors :func:`_has_template_args`'s entity-name scoping.
    """
    if not symbol.startswith("_Z"):
        return False
    rest = _encoding_after_prefixes(symbol)
    if not rest.startswith("N"):
        m = re.match(r"(\d+)", rest)
        if not m:
            return False
        name = rest[m.end() : m.end() + int(m.group(1))]
        return "_GLOBAL__N_" in name or any(t in name for t in _INTERNAL_NS_TOKENS)
    # Nested name: check each depth-0 component up to the closing ``E`` (parameters
    # follow it). Template arguments (depth > 0) are skipped, not treated as owners.
    rest = _NESTED_QUALIFIERS_RE.sub("", rest[1:], count=1)
    i, depth = 0, 0
    while i < len(rest):
        c = rest[i]
        if c == "I":
            depth += 1
            i += 1
        elif c == "E":
            if depth == 0:
                break
            depth -= 1
            i += 1
        elif c.isdigit():
            j = i
            while j < len(rest) and rest[j].isdigit():
                j += 1
            length = int(rest[i:j])
            name = rest[j : j + length]
            i = j + length
            if depth == 0 and (
                "_GLOBAL__N_" in name or any(t in name for t in _INTERNAL_NS_TOKENS)
            ):
                return True
        else:
            i += 1
    return False


def _has_template_args(symbol: str) -> bool:
    """Whether the *entity* an Itanium *symbol* names is a template instantiation.

    Scans only the encoded entity **name**, never the function signature's parameter
    encodings: a plain ``foo(std::vector<int>)`` (``_ZN3lib3fooESt6vectorIiSaIiEE``)
    is *not* a template instantiation even though a parameter type carries ``I…E``
    (Codex review). For a nested name the entity is a template iff **any** of its
    components carries template arguments — including an enclosing class-template
    specialization whose member is exported (``lib::Box<int>::bar()`` =
    ``_ZN3lib3BoxIiE3barEv``), not only the final component (Codex review). An ``I``
    inside an ordinary identifier (``InitEngine``) is never a template opener.
    """
    if not symbol.startswith("_Z"):
        return False
    rest = _encoding_after_prefixes(symbol)
    if rest.startswith("N"):
        # Walk the nested name tracking template-arg depth; the entity name ends at
        # the first depth-0 ``E`` (parameters follow it and must not be scanned). A
        # template-args opener (``I`` at depth 0) on *any* component makes the entity
        # a template instantiation.
        i, depth, saw_template = 1, 0, False
        while i < len(rest):
            c = rest[i]
            if c == "I":
                if depth == 0:
                    saw_template = True
                depth += 1
                i += 1
            elif c == "E":
                if depth == 0:
                    return saw_template  # depth-0 ``E`` closes the nested name
                depth -= 1
                i += 1
            elif c.isdigit():
                j = i
                while j < len(rest) and rest[j].isdigit():
                    j += 1
                i = j + int(rest[i:j])  # skip the source-name characters
            else:
                i += 1
        return saw_template
    # Un-nested ``_Z<len><name>…``: a template iff the single name is followed by an
    # ``I`` template-args opener (``_Z9transformIdEv``); the tail is the signature.
    m = re.match(r"(\d+)", rest)
    if not m:
        return False
    end = m.end() + int(m.group(1))
    return end < len(rest) and rest[end] == "I"
