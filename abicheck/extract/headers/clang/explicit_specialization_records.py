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

"""Records for explicitly instantiated or specialized class templates.

``extern template class Buffer<int>;``, ``template class Buffer<int>;`` and
``template<> struct Buffer<int> {...}`` each put a complete
``ClassTemplateSpecializationDecl`` outside its ``ClassTemplateDecl`` (an
implicit instantiation's complete node lives only inside it, so it is
``in_template`` during the walk). Such a specialization has one fixed layout,
so the clang backend records it under its spelled ``Buffer<int>`` name, the
name castxml gives it, and the layout pass can then size it (known-gaps
2026-11-04: case17/case77 had no sized record to compare).

A specialization's members are scoped by ``dumper_clang._walk``'s
``ClassTemplateSpecializationDecl`` branch, which builds *spelling* with
``templates._specialization_spelling`` under the same template parameter
context its base lookup uses, so the record name and its members' owner agree.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....model import RecordType
from ....model.identity import ScopePath
from .ast_inner import ast_inner
from .context import _Decl, is_builtin_file
from .scope import scope_segment_for

#: The last AST root indexed and its namespace index (one TU at a time).
_NAMESPACE_INDEX: list[
    tuple[dict[str, Any], dict[str, tuple[tuple[str, ...], ScopePath]]]
] = []


def _namespace_scopes(
    root: dict[str, Any],
) -> dict[str, tuple[tuple[str, ...], ScopePath]]:
    """``NamespaceDecl`` id -> the (names, typed path) of the scope it opens.

    An anonymous namespace is left out: its typed segment needs the walk's
    per-parent ordinal, so a specialization declared into one keeps its
    lexical scope instead of a guessed one.
    """
    if _NAMESPACE_INDEX and _NAMESPACE_INDEX[0][0] is root:
        return _NAMESPACE_INDEX[0][1]
    index: dict[str, tuple[tuple[str, ...], ScopePath]] = {}
    stack: list[tuple[Any, tuple[str, ...], ScopePath]] = [(root, (), ())]
    while stack:
        node, names, path = stack.pop()
        if not isinstance(node, dict):
            continue
        child_names, child_path = names, path
        if node.get("kind") == "NamespaceDecl":
            name = node.get("name") or ""
            segment = scope_segment_for(node, access="public") if name else None
            if segment is None:
                continue  # anonymous: neither it nor anything in it is indexed
            child_names, child_path = (*names, name), (*path, segment)
            if isinstance(node.get("id"), str):
                index[node["id"]] = (child_names, child_path)
        elif node.get("kind") not in ("TranslationUnitDecl", "LinkageSpecDecl"):
            continue  # a namespace is only ever nested in these
        stack.extend((c, child_names, child_path) for c in ast_inner(node))
    _NAMESPACE_INDEX[:] = [(root, index)]
    return index


def is_explicit_specialization(node: dict[str, Any], *, in_template: bool) -> bool:
    """Whether *node* is a complete specialization declared outside its template."""
    return (
        not in_template
        # clang writes the key only on a definition; absent means "not one",
        # which only withholds the record (never asserts a fact).
        and node.get("completeDefinition") is True
        and not node.get("isImplicit")
    )


def collect_explicit_specialization(
    out: list[tuple[_Decl, str]],
    node: dict[str, Any],
    spelling: str,
    in_template: bool,
    decl_args: tuple[Any, ...],
    root: dict[str, Any],
) -> None:
    """Append *node* (with its walk context *decl_args*: scope, file, access,
    extern_c, in_friend, scope_path) when it is an explicit specialization.

    ``extern template class detail::B<int>;`` written in ``namespace lib``
    sits lexically in ``lib`` but belongs to ``lib::detail``; clang names the
    semantic parent in ``parentDeclContextId``, and that scope is used.
    """
    if not is_explicit_specialization(node, in_template=in_template):
        return
    scope, file, access, extern_c, in_friend, scope_path = decl_args
    parent = node.get("parentDeclContextId")
    if isinstance(parent, str):
        semantic = _namespace_scopes(root).get(parent)
        if semantic is None:
            return  # a semantic scope that cannot be named exactly: no record
        scope, scope_path = semantic
    entry = _Decl(
        node,
        scope,
        file,
        access,
        extern_c=extern_c,
        in_friend=in_friend,
        in_template=False,
        scope_path=scope_path,
    )
    out.append((entry, spelling))


def explicit_specialization_records(
    entries: list[tuple[_Decl, str]],
    taken: set[str],
    build_record: Callable[..., RecordType],
) -> list[RecordType]:
    """One record per spelling; the first complete node wins, never shadowing
    an ordinary record already named *taken*."""
    out: list[RecordType] = []
    seen = set(taken)
    for entry, spelling in entries:
        identity = "::".join((*entry.scope, spelling)) if entry.scope else spelling
        if identity in seen or is_builtin_file(entry.file):
            continue
        seen.add(identity)
        out.append(build_record(entry, override_name=spelling))
    return out
