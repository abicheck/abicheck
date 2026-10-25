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

"""What the call graph's declaration index keeps, and what it lets go.

``call_graph``'s ``member_index`` maps a clang node id to the function
declaration it names, so a later call site whose ``referencedMemberDecl`` is
a bare id string can resolve back to a real ``CXXMethodDecl`` carrying the
``virtual``/``mangledName``/``kind`` fields the classification needs.

Indexing the **node** does that, and also pins the declaration's ``inner``
-- the function's whole body -- for the lifetime of the parse. On a real AST
that is most of the tree. Whether the index holds nodes or records is
therefore not an implementation detail of the call graph; it decides whether
any streaming reader
(:mod:`abicheck.buildsource.header_graph_ast_stream`) can release a
top-level declaration after visiting it, or is pinned until the parse ends.
Measured on a 263 MiB clang AST with 14,539 indexed declarations: **+168 MiB
holding the nodes, +44 MiB holding these records.**

Stated in its own module because it is a *retention* contract, read by two
different concerns and owed an exhaustive justification either way -- the
read set below is what makes dropping the rest safe, and is checked against
real clang output rather than trusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: clang AST node kinds that mark a method as overriding/finalizing a base
#: virtual slot *without* repeating ``"virtual": true`` on the override's own
#: declaration (see ``_ref_is_virtual``'s docstring for the empirical finding).
_OVERRIDE_MARKER_KINDS = frozenset({"OverrideAttr", "FinalAttr"})

#: Every key a ``member_index`` value is ever read through. Exhaustive, and
#: the reason :func:`_compact_decl_record` is safe: the only reads of an
#: indexed node are the two ``member_index`` lookups in
#: :func:`_find_referenced_decl`, whose result reaches exactly three
#: consumers -- :func:`_resolve_ref_callee_identity` (``id``, then
#: :func:`_identity`'s ``mangledName``/``name``), :func:`_classify_call`
#: (``kind``), and :func:`_ref_is_virtual` (``virtual``, its children's
#: ``kind``, and ``previousDecl`` -- followed back from an out-of-line
#: definition to the in-class declaration that carries ``virtual``). ``type`` and ``loc``/``range`` are kept although no current
#: consumer reads them off an *indexed* node: both are small, flat values,
#: and ``_find_referenced_decl``'s own docstring names ``type.qualType`` as
#: something the full node carries for these consumers -- keeping them costs
#: a few MiB and removes the whole class of "a future reader reaches for a
#: field the index silently dropped".
_MEMBER_INDEX_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "name",
    "mangledName",
    "virtual",
    "previousDecl",
    "type",
    "loc",
    "range",
)


def _compact_decl_record(node: dict[str, Any]) -> dict[str, Any]:
    """A ``member_index`` value holding only what is ever read back off one.

    ``member_index`` keeps a *function declaration* node alive for the whole
    parse so a later call site's bare-string ``referencedMemberDecl`` id can
    resolve back to it. Storing the node itself also pins its ``inner`` --
    i.e. the function's entire **body** -- which is the bulk of a real AST:
    measured on a 263 MiB clang AST (14,539 indexed declarations), retaining
    the nodes costs **+168 MiB** of the parse's peak, against **+44 MiB** for
    these records. That is the difference between a streaming projection
    (:mod:`abicheck.buildsource.header_graph_ast_stream`) being able to
    release each top-level declaration after visiting it and it being pinned
    until the parse ends.

    ``inner`` is kept only where it is read: :func:`_ref_is_virtual` scans
    the children for an ``OverrideAttr``/``FinalAttr`` marker and looks at
    nothing else about them, so the record carries those markers' ``kind``
    and drops every other child -- including the ``CompoundStmt`` body.

    Equivalence rests on :data:`_MEMBER_INDEX_FIELDS` being the exhaustive
    read set, which is stated there and checked against the real tree by
    ``tests/test_call_graph_member_index_compaction.py`` rather than trusted.
    """
    record = {k: node[k] for k in _MEMBER_INDEX_FIELDS if k in node}
    markers = [
        {"kind": child["kind"]}
        for child in node.get("inner", []) or []
        if isinstance(child, dict) and child.get("kind") in _OVERRIDE_MARKER_KINDS
    ]
    if markers:
        record["inner"] = markers
    return record


def _ref_is_virtual(
    ref: dict[str, Any],
    member_index: Mapping[str, dict[str, Any]] | None = None,
) -> bool:
    """Whether *ref* — a resolved callee declaration node — is virtual, own or
    inherited.

    ``bool(ref.get("virtual"))`` alone under-classifies a real, common case
    (Codex review, fresh evidence, verified against real Clang 17 output for
    ``struct B { virtual void f(); }; struct D : B { void f() override; void
    h(){ f(); } };``): clang repeats ``"virtual": true`` only on the slot's
    *original* declaring ancestor (``B::f``), never on the override's own
    ``CXXMethodDecl`` (``D::f``) — so when the resolved static target of a
    member call *is itself* an override, reading only ``ref["virtual"]``
    misclassified the call as ``CALL_KIND_DIRECT``/``RESOLUTION_EXACT``,
    excluding a real further-derived-override chain from
    ``VIRTUAL_CALL_MAY_DISPATCH_TO`` entirely — the opposite of the
    over-approximation this classification exists to make honest.

    Fixed locally (no cross-module dependency on ``override_graph.py``'s own
    hierarchy-walking ``parse_clang_ast_virtual_methods`` — threading its
    output through here as a precomputed set would make ``call_graph.py``
    import from ``override_graph.py``, which already function-locally
    imports several helpers *from* this module; the reverse edge forms a
    real cycle the AI-readiness ``import-cycle-growth`` gate correctly
    rejects, confirmed empirically against this exact change): clang always
    marks a written ``override``/``final`` keyword with an ``OverrideAttr``/
    ``FinalAttr`` child on the override's own declaration (verified against
    the same real AST above — ``D::f`` carries no ``"virtual": true`` but
    does carry an ``OverrideAttr`` child), and ``ref`` here is always the
    *full* declaration node (not a compact stub) whenever this matters: the
    only caller (``_classify_call``) only consults this for a
    ``CXXMemberCallExpr``, whose target always resolves through
    ``member_index`` (see ``_find_referenced_decl``), which stores full
    nodes. A derived method that redeclares a virtual signature with
    *neither* the ``override``/``final`` keyword *nor* a repeated
    ``"virtual": true`` (legal but unusual C++ style) is a documented,
    remaining false negative — the same conservative
    false-negative-over-false-positive default this module already uses
    throughout (ADR-028 D3).

    Virtual-ness is a property of the method, not of one redeclaration:
    clang's ``referencedMemberDecl`` for a call to a method *defined out of
    line* (``double Shape::area() const { ... }``) names the definition,
    which carries neither ``"virtual": true`` nor the ``override`` marker
    (verified against real Clang 18 output). The definition's
    ``previousDecl`` points back to the in-class declaration, so the whole
    redeclaration chain is consulted through *member_index* (cycle-guarded).
    """
    seen: set[str] = set()
    cur: dict[str, Any] | None = ref
    while cur is not None:
        if bool(cur.get("virtual")) or any(
            isinstance(child, dict) and child.get("kind") in _OVERRIDE_MARKER_KINDS
            for child in cur.get("inner", []) or []
        ):
            return True
        prev = str(cur.get("previousDecl") or "")
        if not prev or prev in seen or member_index is None:
            return False
        seen.add(prev)
        cur = member_index.get(prev)
    return False
