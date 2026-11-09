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
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ..model import AbiSnapshot, Function, Variable, Visibility
from ..model.bound_export_spelling import HEADER_SPELLING_DIAGNOSTIC_PREFIX
from ..model.export_index import ExportMatch
from ..model.fact import Fact
from ..model.mangled_name import itanium_scope_components, msvc_scope_components
from ..model.name_decoration import macho_underscore, pe_x86
from ..model.surface_facts import (
    binary_exported,
    export_match_diagnostic,
    is_confirmed_false,
    is_confirmed_true,
)
from .msvc_signature import (
    ParamSpelling,
    canonical_builtin,
    header_param_spelling,
    msvc_function_params,
    param_agrees,
)
from .surface_fact_producers import header_ast_surface_facts

__all__ = [
    "EXPORT_BINDING_PARTIAL",
    "EXPORT_BINDING_UNRESOLVED",
    "ExportBindingOutcome",
    "bind_header_declarations_to_exports",
    "binding_export_names",
    "msvc_builtin_param_types",
    "msvc_is_data_symbol",
    "unbound_export_entries",
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

#: ``AbiSnapshot.scope_fallback`` when some declarations bound but exports the
#: headers may declare stayed undecided (:attr:`ExportBindingOutcome.undecided`).
EXPORT_BINDING_PARTIAL = "export-binding-partial"

#: Diagnostic prefix naming the export a declaration was bound to.
BOUND_EXPORT_DIAGNOSTIC_PREFIX = "bound-export:"


@dataclass(frozen=True)
class ExportBindingOutcome:
    """What the binding pass decided for one snapshot.

    *direct*: declarations the header backend already matched exactly.
    *bound*: ``(header spelling, export spelling)`` pairs bound here.
    *ambiguous*: exports whose qualified name parsed declarations carry but
    that could not be paired with one of them (overloads the decoder cannot
    tell apart, a decoded signature, qualifier or function/data mismatch).
    *undetermined*: exports in the other C++ mangling scheme than the header
    parse whose qualified name could not be decoded at all (operators,
    structors, template instantiations). Neither may read as "not declared
    in the headers": the parse cannot tell (see :func:`unbound_export_entries`).
    *unbound_exports*: every export no declaration claims after the pass.
    """

    direct: int
    bound: tuple[tuple[str, str], ...]
    ambiguous: tuple[str, ...]
    unbound_exports: tuple[str, ...]
    undetermined: tuple[str, ...] = ()

    @property
    def any_declaration_bound(self) -> bool:
        """At least one declaration carries export evidence."""
        return self.direct > 0 or bool(self.bound)

    @property
    def undecided(self) -> tuple[str, ...]:
        """Exports the parsed headers may declare but no declaration binds."""
        return (*self.ambiguous, *self.undetermined)


def binding_export_names(snap: AbiSnapshot) -> tuple[list[str], bool]:
    """The export spellings a PE/Mach-O snapshot's table carries, and
    whether it is a 32-bit x86 PE (the only machine with C decoration).

    An ordinal-only PE export is spelled ``ordinal:N``, as the export-table
    path spells it: it binds to nothing, but still counts as an export.
    """
    if snap.pe is not None:
        names = [e.name or f"ordinal:{e.ordinal}" for e in snap.pe.exports]
        return names, snap.pe.machine == "IMAGE_FILE_MACHINE_I386"
    if snap.macho is not None:
        return [e.name for e in snap.macho.exports if e.name], False
    return [], False


def _itanium_components(spelling: str) -> tuple[str, ...] | None:
    comps = itanium_scope_components(macho_underscore.decode_itanium(spelling))
    return tuple(comps) if comps else None


def _export_key(sym: str, *, x86_32: bool) -> _Key | None:
    if sym.startswith("ordinal:"):
        return None
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
    # A C-linkage declaration: its spelling is its own (unqualified) name. A
    # file-scope `static` has internal linkage and is never an export.
    if isinstance(decl, Function) and decl.is_static:
        return None
    if spelling and spelling == decl.name and spelling.isidentifier():
        return ("c", (spelling,))
    return None


def _msvc_member_codes(sym: str) -> tuple[str, str | None, int] | None:
    """``(kind, cv, index after the calling convention)`` of an MSVC
    function mangling; *kind* is ``free``/``static``/``instance`` and *cv*
    the instance member's cv code (``A`` none, ``B`` const, ``C`` volatile,
    ``D`` const volatile). ``None`` when not a modelled function form."""
    idx = sym.find("@@")
    if not sym.startswith("?") or idx == -1:
        return None
    code, i = sym, idx + 2
    head = code[i : i + 1]
    cv: str | None = None
    if head == "Y":
        kind = "free"
        i += 1
    elif head and head in "CKS":
        kind = "static"
        i += 1
    elif head and head in "AEIMQU":
        kind = "instance"
        i += 1
        if code.startswith("E", i):
            i += 1
        cv = code[i : i + 1]
        if cv not in ("A", "B", "C", "D"):
            return None
        i += 1
    else:
        return None
    if code[i : i + 1] not in ("A", "G", "I", "Q"):  # cdecl/stdcall/fastcall/vectorcall
        return None
    return kind, cv, i + 1


def msvc_is_data_symbol(sym: str) -> bool | None:
    """Whether an MSVC mangling names data (``@@`` then a storage digit,
    e.g. ``?gv@ns@@3HA``) rather than a function; ``None`` if not MSVC."""
    idx = sym.find("@@")
    if not sym.startswith("?") or idx == -1:
        return None
    return sym[idx + 2 : idx + 3].isdigit()


def msvc_builtin_param_types(sym: str) -> tuple[str, ...] | None:
    """The parameter types of an MSVC function mangling when every one is a
    builtin passed by value, else ``None`` (:mod:`.msvc_signature` decodes
    the wider grammar)."""
    params = msvc_function_params(sym)
    if params is None or any(
        p.indirection or canonical_builtin(p.base) != p.base for p in params
    ):
        return None
    return tuple(p.base for p in params)


#: Itanium builtin codes for the canonical spellings, the inverse of what a
#: non-template function's parameter suffix spells.
_ITANIUM_BUILTINS = {
    "void": "v",
    "bool": "b",
    "char": "c",
    "signed char": "a",
    "unsigned char": "h",
    "short": "s",
    "unsigned short": "t",
    "int": "i",
    "unsigned int": "j",
    "long": "l",
    "unsigned long": "m",
    "long long": "x",
    "unsigned long long": "y",
    "float": "f",
    "double": "d",
    "long double": "e",
    "wchar_t": "w",
    "char8_t": "Du",
    "char16_t": "Ds",
    "char32_t": "Di",
}


def _decl_params(decl: Decl) -> tuple[ParamSpelling, ...] | None:
    if not isinstance(decl, Function):
        return None
    out: list[ParamSpelling] = []
    for p in decl.params:
        spelled = header_param_spelling(str(getattr(p, "type", "") or ""))
        if spelled is None:
            return None
        out.append(spelled)
    return tuple(out)


def _params_agree(
    declared: tuple[ParamSpelling, ...], exported: tuple[ParamSpelling, ...]
) -> bool:
    return len(declared) == len(exported) and all(
        param_agrees(d, e) for d, e in zip(declared, exported, strict=True)
    )


def _signature_agrees(decl: Decl, sym: str) -> bool | None:
    """Whether *decl* and the export *sym* can be one entity.

    ``False`` on any decoded disagreement: function vs data, a member's
    const/volatile qualification, or the parameter list (MSVC decoded by
    :mod:`.msvc_signature`; Itanium checked as the builtin parameter suffix
    a non-template function spells). ``True`` when the parameter lists
    decoded and agree, ``None`` when nothing decisive decoded.
    """
    is_data = msvc_is_data_symbol(sym)
    if is_data is not None and is_data != isinstance(decl, Variable):
        return False
    if not isinstance(decl, Function):
        return None
    declared = _decl_params(decl)
    if sym.startswith("?"):
        codes = _msvc_member_codes(sym)
        if codes is not None and codes[0] == "instance" and codes[1] is not None:
            const = codes[1] in ("B", "D")
            volatile = codes[1] in ("C", "D")
            if const != bool(decl.is_const) or volatile != bool(decl.is_volatile):
                return False
        exported = msvc_function_params(sym)
        if exported is None or declared is None:
            return None
        return _params_agree(declared, exported)
    plain = macho_underscore.decode_itanium(sym)
    if not plain.startswith("_Z"):
        return None  # a C name carries no signature
    comps = _itanium_components(plain)
    if (
        declared is None
        or comps is None
        or comps[0] == "std"  # `St` substitution: not spelled component-wise
        or any(p.indirection or p.base not in _ITANIUM_BUILTINS for p in declared)
    ):
        return None
    return plain == _itanium_spelling(comps, declared, const=bool(decl.is_const))


def _itanium_spelling(
    comps: tuple[str, ...], params: tuple[ParamSpelling, ...], *, const: bool
) -> str:
    """The Itanium mangling of a non-template function with builtin
    parameters: the oracle a decoded Itanium export is checked against."""
    names = "".join(f"{len(c)}{c}" for c in comps)
    args = "".join(_ITANIUM_BUILTINS[p.base] for p in params) or "v"
    if len(comps) == 1:
        return f"_Z{names}{args}"
    return f"_ZN{'K' if const else ''}{names}E{args}"


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
    """Bind one qualified name's candidates; return the pairs and the
    refused exports.

    A declaration binds an export only when the signatures decode and agree
    and the pairing is one-to-one -- never by uniqueness alone, which would
    bind a function with class-typed parameters in one release (one overload
    left) and not in the other (two overloads), giving the same function two
    identities across the pair. Variables and C names carry no signature:
    they bind when the name is unique on both sides.
    """
    if len(decls) == 1 and len(syms) == 1:
        verdict = _signature_agrees(decls[0], syms[0])
        if verdict is True or (
            verdict is None and not _signature_is_comparable(decls[0], syms[0])
        ):
            return [(decls[0], syms[0])], []
        return [], syms
    agree = {
        sym: [d for d in decls if _signature_agrees(d, sym) is True] for sym in syms
    }
    pairs: list[tuple[Decl, str]] = []
    refused: list[str] = []
    for sym, cands in agree.items():
        if len(cands) == 1 and sum(cands[0] in c for c in agree.values()) == 1:
            pairs.append((cands[0], sym))
        else:
            refused.append(sym)
    return pairs, refused


def _signature_is_comparable(decl: Decl, sym: str) -> bool:
    """Whether a signature *should* have decided this pairing: a C++
    function export, whose overloads a signature is what tells apart."""
    return isinstance(decl, Function) and sym.startswith(("?", "_Z", "__Z"))


def _apply(decl: Decl, sym: str) -> None:
    fact = decl.declared_in_headers_fact
    producer = fact.producer if fact is not None else None
    facts = header_ast_surface_facts(exported=ExportMatch.DEMANGLED, producer=producer)
    facts["binary_exported_fact"] = Fact.partial(
        True,
        export_match_diagnostic(ExportMatch.DEMANGLED),
        f"{BOUND_EXPORT_DIAGNOSTIC_PREFIX}{sym}",
        f"{HEADER_SPELLING_DIAGNOSTIC_PREFIX}{decl.mangled}",
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
    # Export evidence, not public-surface membership: a declaration judged
    # public without an export (a never-ODR-used CPO) binds no export.
    direct = sum(1 for d in decls if is_confirmed_true(binary_exported(d)))
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

    # The header parse's own mangling scheme(s), read before binding rewrites
    # any declaration's spelling to an export's.
    parse_schemes = {_spelling_scheme(d.mangled) for d in decls}
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
    undetermined = tuple(
        s
        for s in unbound
        if _export_key(s, x86_32=x86_32) is None
        and (scheme := _spelling_scheme(s)) in ("itanium", "msvc")
        and parse_schemes & {"itanium", "msvc"}
        and scheme not in parse_schemes
    )
    return ExportBindingOutcome(
        direct=direct,
        bound=tuple(bound),
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        unbound_exports=unbound,
        undetermined=undetermined,
    )


def _spelling_scheme(spelling: str) -> str:
    if spelling.startswith("?"):
        return "msvc"
    if spelling.startswith(("_Z", "__Z")):
        return "itanium"
    return "other"


def unbound_export_entries(
    outcome: ExportBindingOutcome, snap: AbiSnapshot, *, x86_32: bool
) -> list[Function]:
    """Export-table entries for :attr:`ExportBindingOutcome.undecided`.

    The parsed headers may declare these exports, so they must not read as
    undeclared (a private export whose removal is filtered out): each is
    recorded as an export-table entry, exported with its header
    declaration unknown, the way a headerless dump records every export.
    """
    from .export_symbol_identity import itanium_export_function, msvc_export_function

    reason = "export not bound to a parsed header declaration (binding undecided)"
    entries: list[Function] = []
    for sym in outcome.undecided:
        fn = (
            msvc_export_function(sym, is_x86_32=x86_32)
            if snap.pe is not None
            else itanium_export_function(sym)
        )
        fn.visibility = Visibility.PUBLIC
        fn.declared_in_headers_fact = Fact.not_collected(reason)
        entries.append(fn)
    return entries
