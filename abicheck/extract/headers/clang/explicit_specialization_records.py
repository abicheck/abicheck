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
from .context import _Decl, is_builtin_file


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
) -> None:
    """Append *node* (with its walk context *decl_args*: scope, file, access,
    extern_c, in_friend, scope_path) when it is an explicit specialization."""
    if not is_explicit_specialization(node, in_template=in_template):
        return
    scope, file, access, extern_c, in_friend, scope_path = decl_args
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
