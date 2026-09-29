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

"""What the public headers say about an export *no parsed declaration maps to*.

``exported_not_public`` and ``*_added_elf_only`` start from the export table
and ask which exports no public declaration maps to. A header AST records
concrete declarations only, so three shapes reached those detectors as
"declared in no public header" when the headers did in fact speak for them
(validation against oneCCL and oneDNN):

* **An instantiation of a publicly declared template.** A header declares
  ``template <attr_id A> ... allgatherv_attr::set(V)`` or a class template
  ``Box<T>``; the library instantiates it and exports the instantiation. No
  concrete declaration carries that mangled name, but the template it
  instantiates is public API -- and hiding the export ("hide it") breaks every
  consumer that links against it. :func:`public_template_for_export` answers
  this from the snapshot's own public declarations (qualified template names,
  and public classes whose member templates are instantiated) plus, when the
  header files are readable, the ``template <...>`` declarations in them.
* **A standard/third-party template instantiated over the library's own
  types** (``std::_Sp_counted_deleter<dnnl::impl::stream*, ...>``). Its owner
  namespace is ``std``, but the copy is the library's own vague-linkage
  instantiation, not a statically linked libstdc++.
  :func:`instantiated_over_owned_types` reads the template arguments for a
  namespace the library's declarations own.
* **A declaration the run could not see** -- inside a conditionally compiled
  region (``#ifdef DNNL_EXPERIMENTAL_PROFILING``) or in a header excluded with
  ``--exclude-header``. Absence of a parsed declaration is then not evidence
  of absence. :func:`textual_declaration_hint` finds the name in the header
  text so the finding can say so instead of asserting "declared in no public
  header".

Every answer is a narrowing of a claim, never a new finding: a miss leaves the
caller's existing behavior unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..extract.header_exclusions import header_matches_exclusion
from ..header_conditionals import _include_guard_macro, _strip_comments
from ..model.export_entity_name import (
    PublicTemplateScopes,
    entity_name_components,
    public_template_for_symbol,
    public_template_scopes,
    split_qualified,
    strip_template_args,
)
from ..model.vocabulary import ScopeOrigin

if TYPE_CHECKING:
    from ..model import AbiSnapshot

#: Namespaces that belong to the C++ runtime, never to the audited library.
_RUNTIME_NAMESPACES = frozenset(
    {"std", "__gnu_cxx", "__cxx11", "__cxxabiv1", "__1", "__detail", "__gnu_debug"}
)
#: Header suffixes read when looking for textual evidence.
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inc", ".ipp", ".tpp")
#: Bounds on how much header text one run reads (untrusted, possibly huge trees).
_MAX_HEADER_FILES = 4000
_MAX_HEADER_BYTES = 4 * 1024 * 1024

_TEMPLATE_INTRO = re.compile(r"\btemplate\s*<")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DIRECTIVE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")


@dataclass(frozen=True)
class DeclarationHint:
    """Where the header *text* mentions an export no parsed declaration covers."""

    reason: str  # "excluded_header" | "conditional"
    header: str
    guard: str | None = None


@dataclass
class _HeaderText:
    path: str
    excluded: bool
    #: Source lines paired with the innermost non-include-guard condition.
    lines: list[tuple[str, str | None]] = field(default_factory=list)
    #: Every identifier the file spells, so a lookup skips files quickly.
    identifiers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ExportDeclarationEvidence:
    """Everything the public headers say, computed once per snapshot."""

    owned_namespaces: frozenset[str] = frozenset()
    #: Public templates/classes from the snapshot's own public declarations.
    public: PublicTemplateScopes = field(default_factory=PublicTemplateScopes)
    #: Unqualified names a readable public header declares as a template.
    textual_templates: frozenset[str] = frozenset()
    headers: tuple[_HeaderText, ...] = ()


def _public_header_files(snapshot: AbiSnapshot) -> set[str]:
    """Defining-header paths of every public-header declaration."""
    files: set[str] = set()
    for decls in (snapshot.functions, snapshot.variables, snapshot.types):
        for d in decls:
            if d.origin == ScopeOrigin.PUBLIC_HEADER and d.source_header:
                files.add(d.source_header)
    return files


def _owned_namespaces(snapshot: AbiSnapshot) -> frozenset[str]:
    owned: set[str] = set()
    for d in (
        *snapshot.functions,
        *snapshot.variables,
        *snapshot.types,
        *snapshot.enums,
    ):
        if getattr(d, "origin", None) in (
            ScopeOrigin.SYSTEM_HEADER,
            ScopeOrigin.GENERATED,
        ):
            continue
        parts = split_qualified(str(getattr(d, "name", "") or ""))
        if len(parts) >= 2:
            owned.add(parts[0])
        mangled = getattr(d, "mangled", None)
        if mangled:
            parsed = entity_name_components(mangled)
            if parsed is not None and parsed.nested and len(parsed.components) >= 2:
                owned.add(parsed.components[0])
    return frozenset(
        n for n in owned if n not in _RUNTIME_NAMESPACES and not n.startswith("{")
    )


def _candidate_header_files(snapshot: AbiSnapshot) -> list[Path]:
    """Public header files plus their sibling headers (where excluded ones live)."""
    roots: set[Path] = set()
    for src in _public_header_files(snapshot):
        p = Path(src)
        if p.is_file():
            roots.add(p.parent)
    files: list[Path] = []
    seen: set[Path] = set()
    for root in sorted(roots):
        try:
            children = sorted(root.rglob("*"))
        except OSError:
            continue
        for f in children:
            if len(files) >= _MAX_HEADER_FILES:
                return files
            if f in seen or f.suffix.lower() not in _HEADER_SUFFIXES:
                continue
            seen.add(f)
            try:
                if f.is_file() and f.stat().st_size <= _MAX_HEADER_BYTES:
                    files.append(f)
            except OSError:
                continue
    return files


def _conditioned_lines(source: str) -> list[tuple[str, str | None]]:
    """Each line with the innermost active condition (include guard ignored)."""
    lines = _strip_comments(source).splitlines()
    guard_macro = _include_guard_macro(lines)
    stack: list[str | None] = []
    out: list[tuple[str, str | None]] = []
    for line in lines:
        m = _DIRECTIVE.match(line)
        if m:
            word, rest = m.group(1), m.group(2).strip()
            if word in ("if", "ifdef", "ifndef"):
                is_guard = word == "ifndef" and rest == guard_macro and not stack
                stack.append(None if is_guard else f"#{word} {rest}")
            elif word in ("elif", "else") and stack and stack[-1] is not None:
                stack[-1] = f"#{word} {rest}".strip() + f" (after {stack[-1]})"
            elif word == "endif" and stack:
                stack.pop()
            continue
        active = next((c for c in reversed(stack) if c is not None), None)
        out.append((line, active))
    return out


def _textual_templates(headers: list[_HeaderText]) -> frozenset[str]:
    names: set[str] = set()
    for h in headers:
        if h.excluded:
            continue
        text = "\n".join(line for line, _ in h.lines)
        for m in _TEMPLATE_INTRO.finditer(text):
            i, depth = m.end(), 1
            while i < len(text) and depth:
                depth += {"<": 1, ">": -1}.get(text[i], 0)
                i += 1
            end = len(text)
            for stop in (";", "{"):
                j = text.find(stop, i)
                if j != -1:
                    end = min(end, j)
            head = strip_template_args(text[i:end])
            called = re.findall(r"([A-Za-z_]\w*)\s*\(", head)
            if called:
                names.add(called[0])
            keyed = re.search(
                r"\b(?:class|struct|union)\s+(?:\[\[[^\]]*\]\]\s*)?([A-Za-z_]\w*)", head
            )
            if keyed:
                names.add(keyed.group(1))
    return frozenset(names)


def build_export_declaration_evidence(
    snapshot: AbiSnapshot,
) -> ExportDeclarationEvidence:
    """Collect the public-template, owned-namespace and header-text evidence."""
    patterns = tuple(getattr(snapshot, "excluded_header_patterns", ()) or ())
    exact = getattr(snapshot, "excluded_header_matching", "glob") == "exact"
    headers: list[_HeaderText] = []
    for f in _candidate_header_files(snapshot):
        path = str(f)
        if exact:
            excluded = any(path == p or path.endswith("/" + p) for p in patterns)
        else:
            excluded = header_matches_exclusion(path, patterns)
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = _conditioned_lines(source)
        idents = frozenset(_IDENT.findall("\n".join(line for line, _ in lines)))
        headers.append(_HeaderText(path, excluded, lines, idents))
    return ExportDeclarationEvidence(
        owned_namespaces=_owned_namespaces(snapshot),
        public=public_template_scopes(snapshot),
        textual_templates=_textual_templates(headers),
        headers=tuple(headers),
    )


def public_template_for_export(
    symbol: str, evidence: ExportDeclarationEvidence
) -> str | None:
    """The public template *symbol* instantiates (or is a member of), or ``None``.

    Only an export whose entity name carries template arguments is considered.
    It matches when a template-bearing prefix of its bare scope is a public
    template or public class (a member of a public class template
    specialization), when the entity is a member template of a public class,
    or -- from header text -- when the templated component's name is declared
    as a template in a readable public header and the export lives in a
    namespace the library owns.
    """
    matched = public_template_for_symbol(symbol, evidence.public)
    if matched is not None:
        return matched
    parsed = entity_name_components(symbol)
    if parsed is None or not parsed.template_positions:
        return None
    comps = parsed.components
    top = comps[0]
    if top in evidence.owned_namespaces:
        for p in sorted(parsed.template_positions):
            name = comps[p]
            if name.startswith("{") and p > 0:
                name = comps[p - 1]  # a templated ctor names its class
            if name in evidence.textual_templates:
                return "::".join(comps[: p + 1])
    return None


def _source_names(encoding: str) -> set[str]:
    """Every length-prefixed source name inside an Itanium encoding fragment."""
    from ..model.mangled_name_template_args import (
        read_length_prefixed_name,
        skip_substitution,
    )

    names: set[str] = set()
    i, n = 0, len(encoding)
    while i < n:
        c = encoding[i]
        if "0" <= c <= "9":
            name, j = read_length_prefixed_name(encoding, i)
            if name is None:
                return names
            names.add(name)
            i = j
        elif c in ("S", "T"):
            i = skip_substitution(encoding, i)
        elif c == "L" and i + 1 < n and "a" <= encoding[i + 1] <= "z":
            close = encoding.find("E", i)
            i = n if close == -1 else close + 1  # builtin literal value
        else:
            i += 1
    return names


def instantiated_over_owned_types(symbol: str, owned: frozenset[str]) -> bool:
    """Whether *symbol*'s template arguments name a namespace the library owns.

    ``std::_Sp_counted_deleter<dnnl::impl::stream*, ...>::_M_dispose`` is the
    library's own vague-linkage instantiation of a libstdc++ template, emitted
    because the library used it with its own type: attributing it to a
    statically linked libstdc++ is wrong, and so is "link the dependency
    dynamically".
    """
    if not owned:
        return False
    parsed = entity_name_components(symbol)
    if parsed is None:
        return False
    return any(_source_names(span) & owned for span in parsed.template_arg_spans)


def _leaf_and_scope(symbol: str) -> tuple[str, str | None] | None:
    """The entity's own name and its immediately enclosing scope, if any."""
    parsed = entity_name_components(symbol)
    if parsed is not None:
        comps = [c for c in parsed.components if not c.startswith("{")]
        if not comps:
            return None
        return comps[-1], comps[-2] if len(comps) >= 2 else None
    if _IDENT.fullmatch(symbol):
        return symbol, None  # extern "C" / C symbol
    return None


def textual_declaration_hint(
    symbol: str, evidence: ExportDeclarationEvidence
) -> DeclarationHint | None:
    """Where the header text declares *symbol* although no parsed decl covers it.

    Looks for the entity's own name (followed by ``(`` for a function) in a
    header that also names its enclosing scope. A hit in an excluded header
    reads ``excluded_header``; a hit in a public header inside a
    ``#if``/``#ifdef`` region (other than the include guard) reads
    ``conditional``. An unconditional hit in an included header is not a hint:
    the parse saw that text, so its absence from the parsed declarations is
    real evidence, not missing evidence.
    """
    if not evidence.headers:
        return None
    names = _leaf_and_scope(symbol)
    if names is None:
        return None
    leaf, scope = names
    leaf_re = re.compile(rf"\b{re.escape(leaf)}\s*(\(|\[|;|=|,)")
    conditional: DeclarationHint | None = None
    for h in evidence.headers:
        if leaf not in h.identifiers or (scope and scope not in h.identifiers):
            continue
        for line, cond in h.lines:
            if not leaf_re.search(line):
                continue
            if h.excluded:
                return DeclarationHint("excluded_header", h.path, cond)
            if cond is not None and conditional is None:
                conditional = DeclarationHint("conditional", h.path, cond)
    return conditional
