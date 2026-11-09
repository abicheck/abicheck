# SPDX-License-Identifier: Apache-2.0
"""Template-parameter defaults of the public headers, read from a clang AST.

One reading for every template family -- class, function, alias and variable
templates -- and for every parameter kind (type, non-type, template-template),
spelled by :func:`.template_default_spelling._template_param_default_spellings`
(the same spelling the clang backend records on a class-template pattern).
Both header backends use it: the clang backend over its own AST, castxml over
the AST its clang join parsed (``castxml/clang_semantics_join.py``), so the
same header gives the same table (``model/template_default_table.py``).

Only templates declared at namespace or class scope are recorded: a generic
lambda's call operator or a template inside a function body is not part of a
header's API. A member template of an explicit or partial class-template
specialization cannot be keyed stably (the specialization has no name of its
own), so one that declares a default is recorded as known-but-unspelled
(``None``): its run names the gap rather than reading clean. An explicit
instantiation (``template struct Box<int>;``) only repeats the primary
template's members and is not read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ....model import ScopeOrigin
from ....model.template_default_table import (
    TemplateDefaults,
    merge_template_defaults,
    template_default_key,
)
from ...declaration_provenance import classify_origin, header_from_location
from .ast_inner import ast_inner
from .concept_constraints import _node_file, _node_line, _without_auto_constraints
from .context import is_builtin_file
from .template_default_spelling import _template_param_default_spellings
from .template_param_indexes import _source_span_reader

__all__ = [
    "TemplateDefaultEntry",
    "collect_cxx_template_defaults",
    "public_template_defaults",
    "template_default_entries",
]

#: ``(table key, defaults, declaring header)``; JSON-serializable as a list.
TemplateDefaultEntry = tuple[str, TemplateDefaults, str]

#: Template declaration kind -> (family, the kinds of its pattern declaration).
_FAMILIES: dict[str, tuple[str, frozenset[str]]] = {
    "ClassTemplateDecl": ("class", frozenset({"CXXRecordDecl"})),
    "FunctionTemplateDecl": (
        "function",
        frozenset(
            {
                "FunctionDecl",
                "CXXMethodDecl",
                "CXXConstructorDecl",
                "CXXDestructorDecl",
                "CXXConversionDecl",
            }
        ),
    ),
    "TypeAliasTemplateDecl": ("alias", frozenset({"TypeAliasDecl"})),
    "VarTemplateDecl": ("variable", frozenset({"VarDecl"})),
}

#: Declarations whose direct members are API-level declarations.
_DECLARATION_CONTEXTS = frozenset(
    {
        "TranslationUnitDecl",
        "NamespaceDecl",
        "LinkageSpecDecl",
        "ExportDecl",
        "CXXRecordDecl",
        "ClassTemplateDecl",
    }
)
_SCOPE_KINDS = frozenset({"NamespaceDecl", "CXXRecordDecl", "ClassTemplateDecl"})
_SPECIALIZATION_KINDS = frozenset(
    {"ClassTemplateSpecializationDecl", "ClassTemplatePartialSpecializationDecl"}
)
#: The text an explicit instantiation (not a specialization) starts with.
_EXPLICIT_INSTANTIATION = re.compile(r"\s*(?:extern\s+)?template\s+(?!<)")

# Walk modes: record every template; record only that a specialization's
# member template has a default; record nothing (a body, an instantiation).
_RECORD, _SPECIALIZATION, _OFF = "record", "specialization", "off"
_TEMPLATE_PARAM_KINDS = frozenset(
    {"TemplateTypeParmDecl", "NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"}
)


def _positional(spelling: str, levels: tuple[tuple[str, ...], ...]) -> str:
    """*spelling* with every template-parameter name replaced by its position.

    ``levels`` runs from the template's own parameter list outwards through
    each enclosing class template's. A name of the template's own list becomes
    ``type-param-<i>`` (the spelling ``model.identity`` uses); a name of the
    enclosing list *d* levels out becomes ``type-param-<d>-<i>``. One combined
    pass, so a replacement is never rescanned. C++ forbids a member template
    to reuse an enclosing parameter's name, so the innermost binding is the
    only one.
    """
    tokens: dict[str, str] = {}
    for depth, names in enumerate(levels):
        for index, name in enumerate(names):
            if name and name not in tokens:
                tokens[name] = (
                    f"type-param-{index}"
                    if depth == 0
                    else f"type-param-{depth}-{index}"
                )
    if not tokens:
        return spelling
    pattern = re.compile(
        r"\b(?:"
        + "|".join(re.escape(n) for n in sorted(tokens, key=len, reverse=True))
        + r")\b"
    )
    return pattern.sub(lambda m: tokens[m.group(0)], spelling)


def _param_names(children: list[Any]) -> tuple[str, ...]:
    return tuple(
        str(c.get("name") or "")
        for c in children
        if isinstance(c, dict) and c.get("kind") in _TEMPLATE_PARAM_KINDS
    )


class _Collector:
    def __init__(self, root: dict[str, Any]) -> None:
        self._span: Callable[[str, int, int], str | None] = _source_span_reader()
        self.entries: list[TemplateDefaultEntry] = []
        self._walk(root, (), (), "", _RECORD)

    def _walk(
        self,
        node: Any,
        scope: tuple[str, ...],
        outer: tuple[tuple[str, ...], ...],
        file: str,
        mode: str,
    ) -> str:
        # Every node is visited, in order, because clang writes ``loc.file``
        # only where it changes; recording is limited to declaration contexts.
        if not isinstance(node, dict):
            return file
        file = _node_file(node, file)
        kind = node.get("kind")
        children = ast_inner(node)
        if mode != _OFF and kind in _FAMILIES and not node.get("isImplicit"):
            self._template(node, str(kind), children, scope, outer, file, mode)
        child_scope = scope
        child_outer = (
            (_param_names(children), *outer) if kind == "ClassTemplateDecl" else outer
        )
        if kind in _SCOPE_KINDS:
            name = str(node.get("name") or "")
            if not name:
                child_scope = (*scope, "(anonymous)")
            elif kind == "CXXRecordDecl" and scope and scope[-1] == name:
                child_scope = scope  # a class template's own pattern record
            else:
                child_scope = (*scope, name)
        if kind in _SPECIALIZATION_KINDS:
            child_scope = (*scope, f"{node.get('name') or ''}<specialization>")
        for child in children:
            child_mode = self._child_mode(mode, kind, child, file)
            file = self._walk(child, child_scope, child_outer, file, child_mode)
        return file

    def _child_mode(self, mode: str, kind: object, child: Any, file: str) -> str:
        if mode == _OFF or not isinstance(child, dict):
            return _OFF
        if child.get("kind") in _SPECIALIZATION_KINDS:
            # Inside a ClassTemplateDecl it is an implicit instantiation.
            if kind == "ClassTemplateDecl" or self._is_instantiation(child, file):
                return _OFF
            return _SPECIALIZATION
        if kind in _DECLARATION_CONTEXTS or kind in _SPECIALIZATION_KINDS:
            return mode
        return _OFF

    def _is_instantiation(self, node: dict[str, Any], file: str) -> bool:
        """Whether *node* is an explicit instantiation, read from its source
        text (clang's JSON spells it like an explicit specialization). An
        unreadable span counts as a specialization: its gap is named."""
        range_ = node.get("range") or {}
        begin, end = range_.get("begin") or {}, range_.get("end") or {}
        start, stop = begin.get("offset"), end.get("offset")
        path = begin.get("file") or _node_file(node, file)
        if not (
            isinstance(start, int) and isinstance(stop, int) and isinstance(path, str)
        ):
            return False
        text = self._span(path, start, min(stop, start + 64) + 1)
        return text is not None and _EXPLICIT_INSTANTIATION.match(text) is not None

    def _template(
        self,
        node: dict[str, Any],
        kind: str,
        children: list[Any],
        scope: tuple[str, ...],
        outer: tuple[tuple[str, ...], ...],
        file: str,
        mode: str,
    ) -> None:
        family, pattern_kinds = _FAMILIES[kind]
        pattern = next(
            (
                c
                for c in children
                if isinstance(c, dict) and c.get("kind") in pattern_kinds
            ),
            None,
        )
        if pattern is None:
            return
        pattern_file = _node_file(pattern, file)
        if not pattern_file or is_builtin_file(pattern_file):
            return
        line = _node_line(node)
        header = header_from_location(
            f"{pattern_file}:{line}" if line else pattern_file
        )
        if not header:
            return
        levels = (_param_names(children), *outer)
        if mode == _SPECIALIZATION:
            if not any(
                isinstance(c, dict)
                and c.get("kind") in _TEMPLATE_PARAM_KINDS
                and c.get("defaultArg") is not None
                for c in children
            ):
                return
            spelled = None  # known template, defaults not keyed: the gap
        else:
            spelled = _template_param_default_spellings(node, self._span)
        defaults: TemplateDefaults = (
            None
            if spelled is None
            else tuple(None if s is None else _positional(s, levels) for s in spelled)
        )
        name = "::".join((*scope, str(node.get("name") or "")))
        if family == "function":
            # Overloads differ in their function type; a parameter rename does
            # not change the key.
            function_type = _without_auto_constraints(
                str((pattern.get("type") or {}).get("qualType", ""))
            )
            name += f"({_positional(function_type, levels)})"
        self.entries.append((template_default_key(family, name), defaults, header))


def template_default_entries(root: dict[str, Any]) -> list[TemplateDefaultEntry]:
    """Every namespace- or class-scope template of a clang ``-ast-dump=json``
    root, as ``(key, defaults, declaring header)``, before any public-set
    filtering (a castxml run filters by its own public set)."""
    return list(_Collector(root).entries)


def public_template_defaults(
    entries: list[TemplateDefaultEntry],
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
) -> dict[str, TemplateDefaults]:
    """The table of the *entries* declared in a public header, each template's
    declarations merged (:func:`merge_template_defaults`)."""
    rows: dict[str, list[TemplateDefaults]] = {}
    for key, defaults, header in entries:
        origin = classify_origin(
            header, pub_header_segs, pub_dir_segs, have_public_set=True
        )
        if origin == ScopeOrigin.PUBLIC_HEADER:
            rows.setdefault(key, []).append(defaults)
    return {key: merge_template_defaults(rows[key]) for key in sorted(rows)}


def collect_cxx_template_defaults(
    root: dict[str, Any] | None,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, TemplateDefaults] | None:
    """The public headers' template-default table, or ``None`` when it cannot
    be modelled (no AST, or no declared public set)."""
    if not isinstance(root, dict) or not have_public_set:
        return None
    return public_template_defaults(
        template_default_entries(root), pub_header_segs, pub_dir_segs
    )
