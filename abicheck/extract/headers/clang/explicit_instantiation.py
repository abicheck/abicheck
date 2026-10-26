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


"""The clang header backend's explicit-instantiation evidence
(``Function.explicit_instantiation_fact``, ``Variable.explicit_instantiation_fact``).

``clang -ast-dump=json`` carries no explicit-instantiation marker, so this
module reads the declaration kind from the text dump (``clang -Xclang
-ast-dump``), which spells ``explicit_instantiation_declaration``,
``explicit_instantiation_definition`` and ``explicit_specialization`` on each
declaration. The two dumps of one translation unit are joined by
``(scope, name, whitespace-free qualified type)``:

* the scope is the names of the enclosing ``NamespaceDecl`` and
  ``ClassTemplateSpecializationDecl`` nodes, bare. The text dump does not spell
  a specialization's template arguments, so two instances of one class template
  share a scope; their declarations then share a key;
* a key the text pass lists more than once, or that more than one JSON node
  carries, is never resolved by guessing. Its nodes get ``UNSUPPORTED`` (a re-run
  of this producer cannot separate them).

The text pass is gated by :func:`may_hold_explicit_instantiation`, a source
prefilter over the headers. It decides only whether the pass runs: a spelling
it misses leaves the facts unset (``NOT_COLLECTED``), which every rule reads as
"no evidence", never as "implicit". The pass is not cached, so a warm and a
cold dump of the same headers read the same facts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ....model import Fact
from ....model.explicit_instantiation import ExplicitInstantiation
from .ast_inner import ast_inner

#: ``extern template`` and ``template <decl>;`` (no ``<`` after the keyword),
#: and ``template<>`` (an explicit specialization). A template declaration
#: always writes ``template <``, so an ordinary template does not match.
_TRIGGER_RE = re.compile(r"\btemplate\b(?!\s*<)|\btemplate\s*<\s*>")

#: One text-dump node line: its tree prefix (two columns per depth level), its
#: kind, its address, and the rest of the line.
_NODE_RE = re.compile(r"^((?:\| |  )*)(?:\|-|`-)(\w+) (0x[0-9a-f]+)(.*)$")

#: A declaration's name and quoted type (``walk 'T (T *, T *)'``).
_NAME_TYPE_RE = re.compile(r"\s([^\s']+) '([^']*)'")

_MARKERS: tuple[tuple[str, ExplicitInstantiation], ...] = (
    ("explicit_instantiation_declaration", ExplicitInstantiation.DECLARATION),
    ("explicit_instantiation_definition", ExplicitInstantiation.DEFINITION),
    ("explicit_specialization", ExplicitInstantiation.SPECIALIZATION),
)

#: Declaration kinds the text dump marks and the JSON tree carries.
DECL_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
        "VarDecl",
    }
)
_SPECIALIZATION_KIND = "ClassTemplateSpecializationDecl"
_TEMPLATE_CONTEXT_KIND = "FunctionTemplateDecl"

#: ``(scope, name, whitespace-free qualified type)`` of one declaration.
Key = tuple[str, str, str]


def may_hold_explicit_instantiation(texts: Iterable[str]) -> bool:
    """Whether any header text spells an explicit instantiation or specialization.

    A prefilter for running the text pass only: ``False`` means no pass runs,
    never that the translation unit is free of explicit instances.
    """
    return any(_TRIGGER_RE.search(text) for text in texts)


def _squash(text: str) -> str:
    return "".join(text.split())


@dataclass
class ExplicitDump:
    """The text pass's declarations: each key's marker, and the keys it cannot
    join one-to-one (listed more than once)."""

    kinds: dict[Key, ExplicitInstantiation] = field(default_factory=dict)
    ambiguous: set[Key] = field(default_factory=set)


def parse_explicit_dump(text: str) -> ExplicitDump:
    """Read a ``clang -Xclang -ast-dump`` text dump into an :class:`ExplicitDump`.

    A class-template specialization carries its own marker on its line; a
    member without a marker of its own inherits it, since the members of an
    explicit specialization or an explicit instantiation of a class are part
    of that declaration. A specialization whose key is listed twice makes its
    members ambiguous too.
    """
    counts: dict[Key, int] = {}
    own: dict[Key, ExplicitInstantiation | None] = {}
    class_markers: dict[Key, ExplicitInstantiation] = {}
    members: list[tuple[Key, Key | None]] = []  # (decl key, owning class key)
    scopes: list[tuple[int, str, Key | None]] = []  # (depth, name, class key)
    for line in text.splitlines():
        m = _NODE_RE.match(line)
        if m is None:
            continue
        depth = len(m.group(1)) // 2
        kind, rest = m.group(2), m.group(4)
        while scopes and scopes[-1][0] >= depth:
            scopes.pop()
        scope = "::".join(name for _, name, _ in scopes)
        if kind == "NamespaceDecl":
            named = re.search(r"\s([^\s<>]+)$", rest)
            scopes.append((depth, named.group(1) if named else "", None))
        elif kind == _SPECIALIZATION_KIND:
            named = re.search(r"\b(?:struct|class|union) (\w+)", rest)
            name = named.group(1) if named else ""
            class_key: Key = (scope, name, "")
            counts[class_key] = counts.get(class_key, 0) + 1
            marker = _marker_of(rest)
            if marker is not None:
                class_markers[class_key] = marker
            scopes.append((depth, name, class_key))
        elif kind in DECL_KINDS:
            named = _NAME_TYPE_RE.search(rest)
            if named is None:
                continue
            key = (scope, named.group(1), _squash(named.group(2)))
            counts[key] = counts.get(key, 0) + 1
            own[key] = _marker_of(rest)
            owner = scopes[-1][2] if scopes else None
            members.append((key, owner))
    dump = ExplicitDump()
    for key, owner in members:
        marker = own[key]
        if counts[key] > 1 or (
            marker is None and owner is not None and counts.get(owner, 0) > 1
        ):
            dump.ambiguous.add(key)
            continue
        if marker is None and owner is not None:
            marker = class_markers.get(owner)
        dump.kinds[key] = marker or ExplicitInstantiation.ABSENT
    return dump


def _marker_of(rest: str) -> ExplicitInstantiation | None:
    return next((value for word, value in _MARKERS if word in rest), None)


def explicit_instantiation_facts(
    root: dict[str, Any], dump: ExplicitDump
) -> dict[int, Fact[ExplicitInstantiation]]:
    """``id(decl node) -> Fact`` for the instances of *root* the text *dump* covers.

    A node is recorded when it is an emitted instance (it has a
    ``mangledName``) and either sits in a template context (under a
    ``FunctionTemplateDecl`` or a class-template specialization) or its key is
    explicitly marked. Keyed by object identity, as
    :func:`~abicheck.extract.headers.clang.function_template_defaults.function_template_default_index`
    does, because the parser hands the same node objects back.
    """
    found: list[tuple[dict[str, Any], bool, Key | None]] = []
    stack: list[tuple[Any, tuple[str, ...] | None, bool]] = [(root, (), False)]
    while stack:
        node, scope, in_template = stack.pop()
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        child_scope, child_template = scope, in_template
        if kind == "NamespaceDecl":
            child_scope = None if scope is None else (*scope, node.get("name", ""))
        elif kind == _SPECIALIZATION_KIND:
            child_scope = None if scope is None else (*scope, node.get("name", ""))
            child_template = True
        elif kind == _TEMPLATE_CONTEXT_KIND:
            child_template = True
        elif kind in DECL_KINDS and node.get("mangledName"):
            found.append((node, in_template, _key_of(node, scope)))
        for child in ast_inner(node):
            stack.append((child, child_scope, child_template))
    return _classify(found, dump)


def _key_of(node: dict[str, Any], scope: tuple[str, ...] | None) -> Key | None:
    qual = node.get("type")
    if scope is None or not isinstance(qual, dict) or "qualType" not in qual:
        return None
    return ("::".join(scope), node.get("name", ""), _squash(qual["qualType"]))


def _classify(
    found: list[tuple[dict[str, Any], bool, Key | None]], dump: ExplicitDump
) -> dict[int, Fact[ExplicitInstantiation]]:
    shared: dict[Key, int] = {}
    for _node, _in_template, key in found:
        if key is not None:
            shared[key] = shared.get(key, 0) + 1
    facts: dict[int, Fact[ExplicitInstantiation]] = {}
    for node, in_template, key in found:
        if key is None:
            if in_template:
                facts[id(node)] = _unsupported("its scope or type is not spelled")
            continue
        marker = dump.kinds.get(key)
        if key in dump.ambiguous or shared[key] > 1:
            if in_template or marker is not None:
                facts[id(node)] = _unsupported("the instance does not join one-to-one")
        elif marker is None:
            if in_template:
                facts[id(node)] = _unsupported("no text declaration matches it")
        elif in_template or marker is not ExplicitInstantiation.ABSENT:
            facts[id(node)] = Fact.present(marker, producer="clang")
    return facts


def _unsupported(reason: str) -> Fact[ExplicitInstantiation]:
    return Fact.unsupported(
        f"explicit-instantiation kind could not be joined: {reason}", producer="clang"
    )
