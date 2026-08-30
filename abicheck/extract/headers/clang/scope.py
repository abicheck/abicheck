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

"""Typed scope segments from a clang JSON AST node (ADR-063 Phase 2).

``dumper_clang._ClangAstParser._walk`` already decides, per node, whether a
scope is entered and under which bare name. This module answers the strictly
richer question the flat ``tuple[str, ...]`` throws away: *which kind* of
scope it is, and with what payload. Both are answered from the node clang
already handed the walker — nothing here re-derives a fact from a flattened
string.

Every claim below is confirmed against real ``clang -Xclang
-ast-dump=json`` output (clang 20, C++17), not inferred:

* an inline namespace is an ordinary ``NamespaceDecl`` carrying
  ``"isInline": true`` — the one signal distinguishing it;
* an anonymous namespace is a ``NamespaceDecl`` with **no** ``name`` key at
  all (not an empty-but-present one, and not a synthesized spelling);
* an anonymous struct/union member is a ``CXXRecordDecl`` with no ``name``
  and a ``tagUsed`` of ``"struct"``/``"class"``/``"union"``;
* an ``extern "C"`` block is a ``LinkageSpecDecl`` with a ``language``
  attribute and **never** a ``name`` — see :func:`scope_segment_for` for why
  that matters.
"""

from __future__ import annotations

from typing import Any

from ....model.identity import ScopeSegment
from ..scope_segments import (
    ANONYMOUS_NAMESPACE,
    RECORD_TAG_KINDS,
    anonymous_segment,
    namespace_segment,
    record_segment,
)

__all__ = ["anonymous_scope_key", "anonymous_scope_kind", "scope_segment_for"]

#: Node kinds that introduce a *record* nesting scope.
_RECORD_NODE_KINDS = frozenset({"CXXRecordDecl", "RecordDecl"})


def anonymous_scope_kind(node: dict[str, Any]) -> str | None:
    """The :class:`~abicheck.model.identity.Anonymous` kind for *node*, or ``None``.

    ``None`` means "not an anonymous scope" — a named node, a non-scope
    node, or an implicit one. Implicit nodes are excluded deliberately: the
    walker descends into clang's implicit injected-class-name records, and
    counting one as an anonymous sibling would consume an ordinal that no
    real declaration owns, making every later sibling's ordinal depend on a
    detail of clang's own synthesis rather than on the source.

    An unnamed ``LinkageSpecDecl`` (``extern "C" { ... }``) is **not** an
    anonymous scope: it introduces no scope in C++ at all, and the existing
    flat spelling never recorded one either.

    The caller owns the ordinal, because the ordinal is a property of the
    *parent* (a per-parent sequence), not of the node itself.
    """
    if node.get("isImplicit"):
        return None
    if node.get("name"):
        return None
    kind = node.get("kind")
    if kind == "NamespaceDecl":
        return ANONYMOUS_NAMESPACE
    if kind in _RECORD_NODE_KINDS:
        tag = node.get("tagUsed") or ""
        return tag if tag in RECORD_TAG_KINDS else None
    return None


def anonymous_scope_key(node: dict[str, Any]) -> str | None:
    """The id identifying the *entity* *node* declares, or ``None`` if unknown.

    Two ``namespace { ... }`` blocks in one translation unit **reopen the
    same** unnamed namespace — C++ merges them, so a declaration in the first
    and a declaration in the second share one containing scope. clang's JSON
    AST nevertheless emits one ``NamespaceDecl`` node per *block*, so
    counting blocks positionally would hand those two declarations different
    ``Anonymous.ordinal``s and split one real scope into two identities —
    an over-split, the mirror image of the sibling collision ``ordinal``
    exists to prevent, and a cross-backend divergence too: castxml emits a
    single merged ``<Namespace>`` element for both blocks (both facts
    confirmed by running the two producers on the same two-block header, not
    inferred).

    clang records the merge explicitly: a reopening block carries
    ``originalNamespace`` (and ``previousDecl``) pointing at the first
    block's node id. Returning that id lets the caller assign one ordinal per
    *namespace*, not per block.

    ``None`` when the node carries no id at all (a hand-built AST in a test),
    which the caller must treat as "cannot be merged with anything" — never
    as "same entity as the last one that also had no id".
    """
    original = node.get("originalNamespace")
    if isinstance(original, dict):
        original_id = original.get("id")
        if isinstance(original_id, str) and original_id:
            return original_id
    previous = node.get("previousDecl")
    if isinstance(previous, str) and previous:
        return previous
    own_id = node.get("id")
    return own_id if isinstance(own_id, str) and own_id else None


def scope_segment_for(
    node: dict[str, Any],
    *,
    access: str,
    anonymous_ordinal: int | None = None,
) -> ScopeSegment | None:
    """The typed scope segment *node* contributes, or ``None`` for no segment.

    *access* is *node*'s own access specifier within its parent (what
    ``_walk`` already threads for the node it is visiting), used only for a
    record segment's non-identity payload. *anonymous_ordinal* is the
    per-parent sequence number the caller assigned; ``None`` suppresses the
    anonymous segment entirely rather than inventing an ordinal, since an
    invented one would be neither deterministic nor per-parent.

    A **named** ``LinkageSpecDecl`` returns ``None``. C++ has no such thing
    (a linkage specification is spelled with a string literal, never an
    identifier, and real clang output carries ``language`` with no ``name``
    — confirmed directly), so the case is unreachable; mapping it onto a
    ``Namespace`` "just in case" would make two genuinely different node
    kinds produce structurally identical segments, which is exactly the
    ambiguity ``ScopePath`` exists to prevent. A ``ClassTemplateSpecial
    izationDecl`` likewise returns ``None`` here: its scope spelling is
    reconstructed by the caller (``_walk``'s own specialization branch,
    which owns the trimmed ``A<double>``-style spelling and shares it with
    the base-lookup index), so this function must not form a second opinion
    about it.
    """
    name = node.get("name") or ""
    kind = node.get("kind")
    if name:
        if kind == "NamespaceDecl":
            return namespace_segment(name, is_inline=bool(node.get("isInline")))
        if kind in _RECORD_NODE_KINDS:
            return record_segment(name, access=access)
        return None
    anon_kind = anonymous_scope_kind(node)
    if anon_kind is None or anonymous_ordinal is None:
        return None
    return anonymous_segment(anon_kind, anonymous_ordinal)
