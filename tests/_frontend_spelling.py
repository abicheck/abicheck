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

"""Render one logical snapshot fixture in each L2 header frontend's spelling.

Detector unit tests build snapshots by hand, and almost all of them spell every
name fully qualified (``lib::detail::Impl`` as a record name, a base, a field
type). Neither real header frontend writes that. The castxml backend writes a
type *reference* as its bare leaf (``Impl``); the clang backend writes it as the
header spelled it (``detail::Impl`` from inside ``namespace lib``). A detector
that only works on the hand-written spelling passes every unit test and still
misses a real finding: ``internal_type_leaks_via_public_api`` never fired under
clang until #1918, and no unit test could have shown it.

:func:`render` takes a *logical* snapshot, written in the usual test convention
(every record, enum, base and referenced type fully qualified; function and
variable names qualified by their scope; any builtin spelling), and returns a
copy spelled the way one frontend spells it. A test parametrized over
:data:`FRONTENDS` then checks that a detector reaches the same answer from both.

The rules below were read off real dumps of small headers (castxml 0.7.0 and
the clang ``-ast-dump=json`` backend), and ``test_frontend_spelling_
integration.py`` re-dumps those headers with both frontends and compares them to
:func:`render`'s output, so the rules cannot drift from what the backends do.

Spelling rules (``S`` is the scope a reference is written in: the record for a
field, the record's enclosing scope for a base, the function's or variable's
qualifying scope for its signature, the typedef's scope for its target):

====================  ===============================  ===============================
fact                  castxml                          clang
====================  ===============================  ===============================
record/enum ``name``  leaf, template args fully        same as castxml
                      qualified (``Box<lib::X>``)
``qualified_name``    fully qualified                  fully qualified
type reference        leaf of the name                 the name relative to ``S``
                      (``Impl``, ``Box<int>``)         (``detail::Impl``); in a
                                                       template-instance record or
                                                       through an alias template:
                                                       fully qualified
template arguments    fully qualified                  relative to ``S`` in a
                                                       reference; fully qualified in
                                                       a record name
bases/virtual bases   leaf                             relative to ``S``
builtins              ``long int``, ``short unsigned   ``long``, ``unsigned short``,
                      int``, ``long long unsigned      ``unsigned long long``
                      int``
declarators           ``const char*``, ``Impl&``       ``const char *``, ``Impl &``
type identities       ``PRESENT`` with the outermost   not collected (``None``)
                      record/enum the slot names,
                      through a typedef
function/var names    leaf (opt-in, see ``render``)    leaf (opt-in)
typedef keys          leaf (``typedefs``), qualified   same as castxml
                      (``typedefs_qualified``)
alias templates       not recorded                     recorded as typedefs
template patterns     not recorded                     one ``is_template_pattern``
                                                       record per instantiated
                                                       template
====================  ===============================  ===============================

Not modelled, because they are evidence differences rather than spelling, and a
detector is allowed to answer differently on different evidence: clang records
no size for a record without DWARF, castxml adds implicit special members, and
clang emits a record for an *explicitly* instantiated or specialized template
only (castxml for every instantiation used). :func:`render` keeps the logical
fixture's layout facts, declaration set and every non-declaration field
(``ast_producer`` and ``from_headers`` included) unchanged on both sides, so the
only thing that differs between two renderings is spelling.
"""

from __future__ import annotations

import copy
import enum
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from abicheck.model import AbiSnapshot, EnumType, Function, RecordType
from abicheck.model.fact import Fact, replace_with_fact_sync
from abicheck.model.identity import (
    Namespace,
    Record,
    entity_id_for_enum,
    entity_id_for_type,
)


class Frontend(enum.StrEnum):
    CASTXML = "castxml"
    CLANG = "clang"


FRONTENDS: tuple[Frontend, ...] = (Frontend.CASTXML, Frontend.CLANG)


# --------------------------------------------------------------------------
# Type-expression parsing
# --------------------------------------------------------------------------

_BUILTIN_WORDS = frozenset(
    {
        "void",
        "bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "__int128",
    }
)
_CV = frozenset({"const", "volatile"})
_TOKEN = re.compile(r"\s*(::|&&|[A-Za-z_][A-Za-z_0-9]*|\d+|[<>,*&()\[\]])")


@dataclass
class _Name:
    """A qualified name: components of (identifier, template args or None)."""

    components: list[tuple[str, list[_Expr] | None]]


@dataclass
class _Builtin:
    words: tuple[str, ...]


@dataclass
class _Literal:
    text: str


# A type expression is a flat token list whose name/builtin atoms are parsed.
_Atom = _Name | _Builtin | _Literal | str
_Expr = list[_Atom]


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise ValueError(f"cannot tokenize type spelling {text!r} at {pos}")
        out.append(m.group(1))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> str:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def expr(self, stop: frozenset[str]) -> _Expr:
        out: _Expr = []
        depth = 0
        while (tok := self.peek()) is not None:
            if depth == 0 and tok in stop:
                break
            if tok in ("(", "["):
                depth += 1
            elif tok in (")", "]"):
                if depth == 0:
                    break
                depth -= 1
            if tok in _BUILTIN_WORDS:
                words = []
                while self.peek() in _BUILTIN_WORDS or (self.peek() in _CV and words):
                    w = self.take()
                    if w in _CV:  # `unsigned const int`: hoist the cv
                        out.append(w)
                        continue
                    words.append(w)
                out.append(_Builtin(tuple(words)))
            elif tok == "::" or (tok[0].isalpha() or tok[0] == "_") and tok not in _CV:
                out.append(self.name())
            elif tok[0].isdigit():
                out.append(_Literal(self.take()))
            else:
                out.append(self.take())
        return out

    def name(self) -> _Name:
        comps: list[tuple[str, list[_Expr] | None]] = []
        if self.peek() == "::":
            self.take()
        while True:
            ident = self.take()
            args: list[_Expr] | None = None
            if self.peek() == "<":
                self.take()
                args = []
                while self.peek() != ">":
                    args.append(self.expr(frozenset({",", ">"})))
                    if self.peek() == ",":
                        self.take()
                self.take()
            comps.append((ident, args))
            if self.peek() == "::":
                self.take()
                continue
            return _Name(comps)


def _parse(text: str) -> _Expr:
    parser = _Parser(_tokenize(text))
    expr = parser.expr(frozenset())
    if parser.peek() is not None:
        raise ValueError(f"trailing tokens in type spelling {text!r}")
    return expr


def split_scope(qualified: str) -> list[str]:
    """``lib::Wrap<lib::X>::Inner`` -> ``["lib", "Wrap<lib::X>", "Inner"]``."""
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(qualified):
        ch = qualified[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if depth == 0 and qualified.startswith("::", i):
            parts.append(cur)
            cur = ""
            i += 2
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

# Canonical word multiset -> (castxml, clang). `int` is implied where the
# logical spelling omits it.
_INTEGER_SPELLINGS: dict[tuple[str, ...], tuple[str, str]] = {
    ("short",): ("short int", "short"),
    ("short", "unsigned"): ("short unsigned int", "unsigned short"),
    ("int",): ("int", "int"),
    ("unsigned",): ("unsigned int", "unsigned int"),
    ("long",): ("long int", "long"),
    ("long", "unsigned"): ("long unsigned int", "unsigned long"),
    ("long", "long"): ("long long int", "long long"),
    ("long", "long", "unsigned"): ("long long unsigned int", "unsigned long long"),
}


def _builtin(words: tuple[str, ...], fe: Frontend) -> str:
    if "char" in words or "double" in words or "__int128" in words:
        if words == ("signed", "char") or "char" not in words:
            return " ".join(words)
        return " ".join(sorted(words, key=lambda w: w != "signed" and w != "unsigned"))
    key = tuple(sorted(w for w in words if w not in ("int", "signed")))
    if not key and "signed" in words:
        key = ("int",)
    spelled = _INTEGER_SPELLINGS.get(key or ("int",))
    if spelled is None:
        return " ".join(words)
    return spelled[0] if fe is Frontend.CASTXML else spelled[1]


@dataclass(frozen=True)
class _Ctx:
    fe: Frontend
    scope: tuple[str, ...]  # components the reference is written in
    canonical: bool  # spell names fully qualified (clang substitution)


def _render_args(args: list[_Expr], ctx: _Ctx) -> str:
    return "<" + ", ".join(_render_expr(a, ctx) for a in args) + ">"


def _render_name(name: _Name, ctx: _Ctx, *, top: bool) -> str:
    # Template arguments: castxml always fully qualifies them; clang spells them
    # as written (relative) in a reference and canonically in a substitution.
    arg_ctx = ctx if ctx.fe is Frontend.CLANG else replace(ctx, canonical=True)
    rendered = [
        ident + (_render_args(args, arg_ctx) if args is not None else "")
        for ident, args in name.components
    ]
    if ctx.fe is Frontend.CASTXML:
        return rendered[-1] if top and not ctx.canonical else "::".join(rendered)
    if ctx.canonical:
        return "::".join(rendered)
    plain = [ident for ident, _ in name.components]
    k = 0
    while k < len(plain) - 1 and k < len(ctx.scope) and plain[k] == ctx.scope[k]:
        k += 1
    return "::".join(rendered[k:])


def _render_expr(expr: _Expr, ctx: _Ctx) -> str:
    out = ""
    prev = ""  # "word" | "ptr" | "open" | "close" | ""
    for atom in expr:
        if isinstance(atom, _Name):
            text, kind = _render_name(atom, ctx, top=True), "word"
        elif isinstance(atom, _Builtin):
            text, kind = _builtin(atom.words, ctx.fe), "word"
        elif isinstance(atom, _Literal):
            text, kind = atom.text, "word"
        elif atom in _CV:
            text, kind = atom, "word"
        elif atom in ("*", "&", "&&"):
            text, kind = atom, "ptr"
        elif atom in ("(", "["):
            text, kind = atom, "open"
        elif atom in (")", "]"):
            text, kind = atom, "close"
        else:
            text, kind = atom, "punct"
        if not out or prev in ("open", "punct"):
            sep = ""
        elif kind == "word":
            # castxml `char* const`; clang `char *const`.
            sep = "" if prev == "ptr" and ctx.fe is Frontend.CLANG else " "
        elif kind == "ptr":
            # castxml `Impl*`; clang `Impl *`, `int **`, `int (*)(int)`.
            sep = " " if ctx.fe is Frontend.CLANG and prev in ("word", "close") else ""
        elif kind == "open":
            sep = " " if text == "(" and prev == "word" else ""
        else:
            sep = ""
        if text == ",":
            text = ", "
        out += sep + text
        prev = kind
    return out.strip()


def spell_type(
    text: str,
    frontend: Frontend,
    *,
    scope: str = "",
    canonical: bool = False,
) -> str:
    """Spell one logical (fully qualified) type expression as *frontend* does.

    ``scope`` is the qualified scope the reference is written in (clang strips
    the components it shares); ``canonical`` forces the fully qualified spelling
    clang gives a substituted template parameter or an alias-template target.
    """
    if not text:
        return text
    ctx = _Ctx(frontend, tuple(split_scope(scope)), canonical)
    return _render_expr(_parse(text), ctx)


def spell_record_name(qualified: str, frontend: Frontend) -> tuple[str, str]:
    """``(name, qualified_name)`` of a record or enum. Both frontends agree."""
    parts = split_scope(qualified)
    ctx = _Ctx(frontend, (), True)
    leaf_expr = _parse(parts[-1])
    leaf = _render_expr(leaf_expr, replace(ctx, fe=Frontend.CASTXML))
    if len(parts) == 1:
        return leaf, _render_expr(_parse(qualified), replace(ctx, fe=Frontend.CASTXML))
    full = _render_expr(_parse(qualified), replace(ctx, fe=Frontend.CASTXML))
    return leaf, full


def _outer_names(text: str) -> list[str]:
    """Fully qualified spellings of the top-level names a type expression uses."""
    ctx = _Ctx(Frontend.CASTXML, (), True)
    return [
        _render_name(atom, ctx, top=False)
        for atom in _parse(text)
        if isinstance(atom, _Name)
    ]


# --------------------------------------------------------------------------
# Snapshot rendering
# --------------------------------------------------------------------------


def _scope_of(qualified: str) -> str:
    return "::".join(split_scope(qualified)[:-1])


def _is_instance(qualified: str) -> bool:
    return split_scope(qualified)[-1].endswith(">") if qualified else False


class _Renderer:
    def __init__(
        self,
        snap: AbiSnapshot,
        fe: Frontend,
        canonical_fields: frozenset[tuple[str, str]],
        leaf_decl_names: bool,
    ) -> None:
        self.fe = fe
        self.leaf_decl_names = leaf_decl_names
        self.canonical_fields = canonical_fields
        decls = snap.declarations
        self.records = {self._qual(r): r for r in decls.types}
        self.enums = {self._qual(e): e for e in decls.enums}
        self.typedefs = dict(decls.typedefs_qualified or {}) or dict(decls.typedefs)

    @staticmethod
    def _qual(decl: RecordType | EnumType) -> str:
        return decl.qualified_name or decl.name

    def ref(self, text: str, scope: str, *, canonical: bool = False) -> str:
        return spell_type(text, self.fe, scope=scope, canonical=canonical)

    def identities(self, text: str) -> Fact[tuple[str, ...]] | None:
        if self.fe is not Frontend.CASTXML or not text:
            return None
        found: list[str] = []
        seen: set[str] = set()

        def resolve(name: str, depth: int = 0) -> None:
            if name in self.records or name in self.enums:
                if name not in seen:
                    seen.add(name)
                    found.append(name)
            elif name in self.typedefs and depth < 8:
                for inner in _outer_names(self.typedefs[name]):
                    resolve(inner, depth + 1)

        for name in _outer_names(text):
            resolve(name)
        return Fact.present(tuple(found))

    def scope_path(self, qualified: str) -> tuple[Namespace | Record, ...]:
        parts = split_scope(qualified)[:-1]
        out: list[Namespace | Record] = []
        for i, part in enumerate(parts):
            prefix = "::".join(parts[: i + 1])
            out.append(
                Record(name=part) if prefix in self.records else Namespace(name=part)
            )
        return tuple(out)

    def record(self, rec: RecordType) -> RecordType:
        qual = self._qual(rec)
        name, qualified = spell_record_name(qual, self.fe)
        in_instance = self.fe is Frontend.CLANG and _is_instance(qual)
        fields = [
            replace_with_fact_sync(
                f,
                type=self.ref(
                    f.type,
                    qual,
                    canonical=in_instance
                    or (
                        self.fe is Frontend.CLANG
                        and (qual, f.name) in self.canonical_fields
                    ),
                ),
                type_identities_fact=self.identities(f.type),
            )
            for f in rec.fields
        ]
        outer = _scope_of(qual)

        def bases(fact: Fact[list[str]] | None) -> Fact[list[str]] | None:
            if fact is None or fact.value is None:
                return fact
            return replace(fact, value=[self.ref(b, outer) for b in fact.value])

        updates: dict[str, object] = {}
        if len(split_scope(qual)) > 1:
            updates["qualified_name"] = qualified
        return replace_with_fact_sync(
            rec,
            name=name,
            fields=fields,
            entity_id=entity_id_for_type(self.scope_path(qual), name),
            bases_fact=bases(rec.bases_fact),
            virtual_bases_fact=bases(rec.virtual_bases_fact),
            **updates,
        )

    def pattern(self, qual: str) -> RecordType | None:
        """The clang ``is_template_pattern`` record for an instantiated template."""
        parts = split_scope(qual)
        bare = parts[-1].split("<", 1)[0]
        pattern_qual = "::".join([*parts[:-1], bare])
        if pattern_qual in self.records:
            return None
        src = self.records[qual]
        return RecordType(
            name=bare,
            kind=src.kind,
            qualified_name=pattern_qual if len(parts) > 1 else None,
            is_template_pattern=True,
            entity_id=entity_id_for_type(self.scope_path(pattern_qual), bare),
        )

    def enum(self, en: EnumType) -> EnumType:
        qual = self._qual(en)
        name, qualified = spell_record_name(qual, self.fe)
        updates: dict[str, object] = {}
        if len(split_scope(qual)) > 1:
            updates["qualified_name"] = qualified
        return replace_with_fact_sync(
            en,
            **updates,
            name=name,
            underlying_type=self.ref(en.underlying_type, "")
            if en.underlying_type
            else en.underlying_type,
            entity_id=entity_id_for_enum(self.scope_path(qual), name),
        )

    def decl_name(self, name: str) -> str:
        return split_scope(name)[-1] if name and self.leaf_decl_names else name

    def function(self, fn: Function) -> Function:
        scope = _scope_of(fn.name)
        params = [
            replace_with_fact_sync(
                p,
                type=self.ref(p.type, scope),
                type_identities_fact=self.identities(p.type),
            )
            for p in fn.params
        ]
        return replace_with_fact_sync(
            fn,
            name=self.decl_name(fn.name),
            return_type=self.ref(fn.return_type, scope),
            return_type_identities_fact=self.identities(fn.return_type),
            params=params,
        )


def render(
    snapshot: AbiSnapshot,
    frontend: Frontend | None,
    *,
    canonical_fields: Iterable[tuple[str, str]] = (),
    alias_templates: Mapping[str, str] | None = None,
    leaf_decl_names: bool = False,
) -> AbiSnapshot:
    """Return *snapshot* (logical spelling) re-spelled as *frontend* writes it.

    ``frontend=None`` returns the snapshot unchanged, so a test can parametrize
    over ``(None, *FRONTENDS)`` and keep its hand-written spelling as one case.

    ``canonical_fields`` names ``(record qualified name, field name)`` pairs
    whose type the header spelled through an alias template (``pimpl<T>``):
    clang records the alias target fully qualified there. ``alias_templates``
    maps an alias template's qualified name to its logical target pattern; only
    clang records it, as a typedef.

    ``leaf_decl_names`` writes function and variable names as their leaf, as
    both frontends do. It is off by default: the frontends agree on it, so it
    cannot make them diverge, and most hand-built fixtures carry a synthetic
    ``mangled`` that cannot be demangled back to the scope a detector would
    otherwise recover. The integration check turns it on.
    """
    if frontend is None:
        return snapshot
    r = _Renderer(snapshot, frontend, frozenset(canonical_fields), leaf_decl_names)
    decls = snapshot.declarations
    types = [r.record(t) for t in decls.types]
    if frontend is Frontend.CLANG:
        for qual in list(r.records):
            if _is_instance(qual):
                pat = r.pattern(qual)
                if pat is not None and all(
                    (t.qualified_name or t.name) != (pat.qualified_name or pat.name)
                    for t in types
                ):
                    types.append(pat)
    typedefs_q: dict[str, str] = {}
    for key, target in r.typedefs.items():
        typedefs_q[key] = r.ref(target, _scope_of(key))
    if frontend is Frontend.CLANG:
        for key, target in (alias_templates or {}).items():
            typedefs_q[key] = r.ref(target, _scope_of(key))
    variables = [
        replace_with_fact_sync(
            v,
            name=r.decl_name(v.name),
            type=r.ref(v.type, _scope_of(v.name)),
            type_identities_fact=r.identities(v.type),
        )
        for v in decls.variables
    ]
    # Everything that is not a declaration spelling (ELF/DWARF metadata, the
    # call graph, producer facts, ...) is carried over untouched.
    out = copy.deepcopy(snapshot)
    store = out.declarations
    store.functions = [r.function(f) for f in decls.functions]
    store.variables = variables
    store.types = types
    store.enums = [r.enum(e) for e in decls.enums]
    store.typedefs = {split_scope(k)[-1]: v for k, v in typedefs_q.items()}
    store.typedefs_qualified = typedefs_q
    out._func_by_mangled = out._var_by_mangled = out._type_by_name = None
    return out


__all__ = [
    "FRONTENDS",
    "Frontend",
    "render",
    "spell_record_name",
    "spell_type",
    "split_scope",
]
