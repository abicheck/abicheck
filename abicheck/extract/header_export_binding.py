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

"""Bind parsed header declarations to PE/Mach-O exports across manglings.

A header backend computes each declaration's linker spelling for *its own*
target. When the binary was built for another C++ ABI, the spellings never
meet: castxml or Linux clang writes Itanium ``_Z15library_versionv``, and an
MSVC DLL exports ``?library_version@@YAHXZ``. A 32-bit x86 DLL decorates C
names as ``_f``, ``_f@8`` or ``@f@8``. A MinGW DLL exports Itanium names that
an MSVC-targeted parse spells ``?...``. The exact lookup in the header
backends then marks every such declaration ``HIDDEN`` although the binary
exports it.

:func:`bind_header_declarations_to_exports` closes that gap after the parse.
It keys each still-unexported declaration and each export not claimed by any
declaration on one normalized identity, the qualified name parsed
structurally from the mangling (``model.mangled_name``), or the undecorated C
name (``model.name_decoration.pe_x86``). It binds a declaration to an export
only when:

* the two spellings are one entity in two schemes: Itanium and MSVC, or an
  undecorated C name and its x86 PE decoration. Two spellings in one scheme
  are different symbols (a changed signature), and so are a C name and a C++
  mangling of the same identifier (different linkage);
* the key is unique on both sides. Overloads are separated by their decoded
  parameter list when every parameter is a builtin type, and left unbound
  otherwise;
* the decoded MSVC parameter list, when decodable, agrees with the
  declaration's own parameters.

A bound declaration takes the export's spelling as its ``mangled`` linker
name, and :attr:`ExportMatch.DEMANGLED` as its export fact. Its header
identity (``entity_id``) is untouched. Everything this cannot decide is
returned in :class:`ExportBindingOutcome`, never dropped.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ..model import AbiSnapshot, Function, Variable, Visibility
from ..model.export_index import ExportMatch
from ..model.fact import Fact
from ..model.mangled_name import itanium_scope_components, msvc_scope_components
from ..model.name_decoration import macho_underscore, pe_x86
from ..model.surface_facts import (
    binary_exported,
    export_match_diagnostic,
    in_public_surface,
    is_confirmed_false,
    is_confirmed_true,
)
from .surface_fact_producers import header_ast_surface_facts

__all__ = [
    "EXPORT_BINDING_UNRESOLVED",
    "ExportBindingOutcome",
    "bind_header_declarations_to_exports",
    "binding_export_names",
    "msvc_builtin_param_types",
]

_log = logging.getLogger(__name__)

Decl = Function | Variable
#: ``(scheme, qualified-name components)``; schemes are ``itanium``,
#: ``msvc``, ``c`` (undecorated) and ``c-decorated`` (x86 PE decoration).
_Key = tuple[str, tuple[str, ...]]

#: ``AbiSnapshot.scope_fallback`` when the headers parsed and were kept but no
#: declaration could be bound to any export (ADR-024 §D5.3 note code).
EXPORT_BINDING_UNRESOLVED = "export-binding-unresolved"

#: ``(declaration scheme, export scheme)`` pairs that are one entity spelled
#: two ways. A C declaration and an Itanium/MSVC export of the same name are
#: two different symbols (C and C++ linkage), and so are two spellings in
#: one scheme.
_SPELLING_GAPS = frozenset(
    {
        ("itanium", "msvc"),
        ("msvc", "itanium"),
        ("c", "c-decorated"),
    }
)

#: Diagnostic prefix naming the export a declaration was bound to.
BOUND_EXPORT_DIAGNOSTIC_PREFIX = "bound-export:"


@dataclass(frozen=True)
class ExportBindingOutcome:
    """What the binding pass decided for one snapshot.

    *direct*: declarations the header backend already matched exactly.
    *bound*: ``(header spelling, export spelling)`` pairs bound here.
    *ambiguous*: exports whose key several declarations (or several exports)
    share, or whose decoded signature disagrees with the only candidate.
    *unbound_exports*: exports no declaration claims after the pass,
    *ambiguous* included.
    """

    direct: int
    bound: tuple[tuple[str, str], ...]
    ambiguous: tuple[str, ...]
    unbound_exports: tuple[str, ...]

    @property
    def any_declaration_bound(self) -> bool:
        """At least one declaration is in the public surface by export."""
        return self.direct > 0 or bool(self.bound)


def binding_export_names(snap: AbiSnapshot) -> tuple[list[str], bool]:
    """The export spellings a PE/Mach-O snapshot's table carries, and
    whether it is a 32-bit x86 PE (the only machine with C decoration).

    Ordinal-only PE exports have no name to bind and are left out.
    """
    if snap.pe is not None:
        names = [e.name for e in snap.pe.exports if e.name]
        return names, snap.pe.machine == "IMAGE_FILE_MACHINE_I386"
    if snap.macho is not None:
        return [e.name for e in snap.macho.exports if e.name], False
    return [], False


def _itanium_components(spelling: str) -> tuple[str, ...] | None:
    comps = itanium_scope_components(macho_underscore.decode_itanium(spelling))
    return tuple(comps) if comps else None


def _export_key(sym: str, *, x86_32: bool) -> _Key | None:
    if sym.startswith("?"):
        comps = msvc_scope_components(sym)
        return ("msvc", tuple(comps)) if comps else None
    if sym.startswith(("_Z", "__Z")):
        it = _itanium_components(sym)
        return ("itanium", it) if it else None
    decoded = pe_x86.decode_c_name(sym, x86_32=x86_32)
    if decoded:
        return ("c-decorated", (decoded,))
    return ("c", (sym,))


def _decl_key(decl: Decl) -> _Key | None:
    spelling = decl.mangled or ""
    if spelling.startswith("?"):
        comps = msvc_scope_components(spelling)
        return ("msvc", tuple(comps)) if comps else None
    if spelling.startswith(("_Z", "__Z")):
        it = _itanium_components(spelling)
        return ("itanium", it) if it else None
    # A C-linkage declaration: its spelling is its own (unqualified) name.
    if spelling and spelling == decl.name and spelling.isidentifier():
        return ("c", (spelling,))
    return None


# MSVC builtin type codes -> the canonical spellings _canonical_builtin
# produces for a header parameter. Anything else is undecodable here.
_MSVC_BUILTINS = {
    "X": "void",
    "D": "char",
    "C": "signed char",
    "E": "unsigned char",
    "F": "short",
    "G": "unsigned short",
    "H": "int",
    "I": "unsigned int",
    "J": "long",
    "K": "unsigned long",
    "M": "float",
    "N": "double",
    "O": "long double",
    "_N": "bool",
    "_J": "long long",
    "_K": "unsigned long long",
    "_W": "wchar_t",
    "_S": "char16_t",
    "_U": "char32_t",
    "_Q": "char8_t",
}
_INT_WORDS = {"short", "int", "long", "signed", "unsigned"}
_OTHER_BUILTINS = {
    "void",
    "bool",
    "_Bool",
    "wchar_t",
    "char8_t",
    "char16_t",
    "char32_t",
    "float",
}


def _canonical_builtin(spelling: str) -> str | None:
    """One spelling for a builtin parameter type, or ``None``."""
    words = spelling.replace("const ", " ").replace("volatile ", " ").split()
    if not words:
        return None
    if len(words) == 1 and words[0] in _OTHER_BUILTINS:
        return "bool" if words[0] == "_Bool" else words[0]
    allowed = _INT_WORDS | {"char", "double"}
    if any(w not in allowed for w in words):
        return None
    longs = words.count("long")
    if "double" in words:
        return "long double" if longs else "double"
    if "char" in words:
        if "unsigned" in words:
            return "unsigned char"
        return "signed char" if "signed" in words else "char"
    size = (
        "short"
        if "short" in words
        else ("long long" if longs >= 2 else ("long" if longs else "int"))
    )
    return f"unsigned {size}" if "unsigned" in words else size


def _msvc_type(code: str, i: int) -> tuple[str | None, int]:
    if code.startswith("_", i):
        return _MSVC_BUILTINS.get(code[i : i + 2]), i + 2
    return _MSVC_BUILTINS.get(code[i : i + 1]), i + 1


def msvc_builtin_param_types(sym: str) -> tuple[str, ...] | None:
    """The parameter types of an MSVC function mangling, when every one is
    a builtin type; ``None`` for anything this does not decode.

    Handles free functions (``Y`` + calling convention) and member
    functions (an access code, then for a non-static member an optional
    ``E`` pointer-size marker and a cv code, then the calling convention).
    """
    idx = sym.find("@@")
    if not sym.startswith("?") or idx == -1:
        return None
    code, i = sym, idx + 2
    head = code[i : i + 1]
    if head == "Y":
        i += 1
    elif head in "CKS" and head:  # static members
        i += 1
    elif head in "AEIMQU" and head:  # instance members
        i += 1
        if code.startswith("E", i):
            i += 1
        if code[i : i + 1] not in ("A", "B", "C", "D"):
            return None
        i += 1
    else:
        return None
    if code[i : i + 1] not in ("A", "G", "I", "Q"):  # cdecl/stdcall/fastcall/vectorcall
        return None
    i += 1
    ret, i = _msvc_type(code, i)
    if ret is None:
        return None
    if code[i:] == "XZ":
        return ()
    params: list[str] = []
    while i < len(code):
        if code[i:] in ("@Z", "Z"):
            return tuple(params)
        t, i = _msvc_type(code, i)
        if t is None or t == "void":
            return None
        params.append(t)
    return None


def _decl_param_types(decl: Decl) -> tuple[str, ...] | None:
    if not isinstance(decl, Function):
        return None
    out: list[str] = []
    for p in decl.params:
        canon = _canonical_builtin(str(getattr(p, "type", "") or ""))
        if canon is None:
            return None
        out.append(canon)
    return tuple(out)


def _signature_agrees(decl: Decl, sym: str) -> bool | None:
    """``True``/``False`` when both parameter lists decode, else ``None``."""
    if not isinstance(decl, Function) or not sym.startswith("?"):
        return None
    exported = msvc_builtin_param_types(sym)
    declared = _decl_param_types(decl)
    if exported is None or declared is None:
        return None
    return exported == declared


def _claimed_spellings(decls: Iterable[Decl]) -> set[str]:
    claimed: set[str] = set()
    for d in decls:
        if not is_confirmed_true(binary_exported(d)):
            continue
        for spelling in (d.mangled, d.name):
            if spelling:
                claimed.add(spelling)
                claimed.add(macho_underscore.decode_itanium(spelling))
                if spelling.startswith("_"):
                    claimed.add(spelling[1:])
    return claimed


def _pair(
    decls: list[Decl], syms: list[str]
) -> tuple[list[tuple[Decl, str]], list[str]]:
    """Bind one key's candidates; return the pairs and the refused exports."""
    if len(decls) == 1 and len(syms) == 1:
        if _signature_agrees(decls[0], syms[0]) is False:
            return [], syms
        return [(decls[0], syms[0])], []
    # Overloads: pair by a fully decoded builtin parameter list, uniquely.
    by_sig_decl: dict[tuple[str, ...], list[Decl]] = defaultdict(list)
    for d in decls:
        sig = _decl_param_types(d)
        if sig is not None:
            by_sig_decl[sig].append(d)
    sigs = {s: msvc_builtin_param_types(s) if s.startswith("?") else None for s in syms}
    sig_counts = Counter(sig for sig in sigs.values() if sig is not None)
    pairs: list[tuple[Decl, str]] = []
    refused: list[str] = []
    for sym, sig in sigs.items():
        if (
            sig is not None
            and len(by_sig_decl.get(sig, [])) == 1
            and sig_counts[sig] == 1
        ):
            pairs.append((by_sig_decl[sig][0], sym))
        else:
            refused.append(sym)
    return pairs, refused


def _apply(decl: Decl, sym: str) -> None:
    fact = decl.declared_in_headers_fact
    producer = fact.producer if fact is not None else None
    facts = header_ast_surface_facts(exported=ExportMatch.DEMANGLED, producer=producer)
    facts["binary_exported_fact"] = Fact.partial(
        True,
        export_match_diagnostic(ExportMatch.DEMANGLED),
        f"{BOUND_EXPORT_DIAGNOSTIC_PREFIX}{sym}",
        producer=producer,
    )
    decl.visibility = Visibility.PUBLIC
    decl.mangled = sym
    decl.declared_in_headers_fact = facts["declared_in_headers_fact"]
    decl.in_public_contract_fact = facts["in_public_contract_fact"]
    decl.binary_exported_fact = facts["binary_exported_fact"]


def bind_header_declarations_to_exports(
    snap: AbiSnapshot, exports: Iterable[str], *, x86_32: bool = False
) -> ExportBindingOutcome:
    """Bind *snap*'s unexported header declarations to *exports* by a
    normalized identity, in place (see the module docstring)."""
    decls: list[Decl] = [*snap.declarations.functions, *snap.declarations.variables]
    direct = sum(1 for d in decls if in_public_surface(d))
    claimed = _claimed_spellings(decls)
    open_exports = [s for s in dict.fromkeys(exports) if s not in claimed]

    decls_by_key: dict[_Key, list[Decl]] = defaultdict(list)
    for d in decls:
        # Only a declaration the export lookup answered "absent" for: one
        # judged public on other evidence (a never-ODR-used CPO, a
        # constructor) may still name an export under another scheme.
        if is_confirmed_false(binary_exported(d)):
            key = _decl_key(d)
            if key is not None:
                decls_by_key[key].append(d)
    exports_by_name: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for sym in open_exports:
        ekey = _export_key(sym, x86_32=x86_32)
        if ekey is not None:
            exports_by_name[ekey[1]].append((ekey[0], sym))

    bound: list[tuple[str, str]] = []
    ambiguous: list[str] = []
    bound_syms: set[str] = set()
    for (scheme, comps), cands in decls_by_key.items():
        syms = [
            s
            for sch, s in exports_by_name.get(comps, ())
            if (scheme, sch) in _SPELLING_GAPS and s not in bound_syms
        ]
        if not syms:
            continue
        pairs, refused = _pair(cands, syms)
        for decl, sym in pairs:
            bound.append((decl.mangled, sym))
            _apply(decl, sym)
            bound_syms.add(sym)
        ambiguous.extend(refused)
    if bound:
        # The mangled-name indexes were built from the pre-binding spellings.
        snap._func_by_mangled = None
        snap._var_by_mangled = None
        snap._type_by_name = None
        _log.info(
            "bound %d header declaration(s) to exports by normalized identity",
            len(bound),
        )
    unbound = tuple(s for s in open_exports if s not in bound_syms)
    return ExportBindingOutcome(
        direct=direct,
        bound=tuple(bound),
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        unbound_exports=unbound,
    )
