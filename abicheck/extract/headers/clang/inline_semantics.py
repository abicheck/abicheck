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

"""Whether a clang JSON AST function node is ``inline`` -- explicitly *or*
implicitly (C++ [dcl.inline]/[class.mfct]/[dcl.constexpr]).

Why this exists as its own primitive rather than ``bool(node.get("inline"))``
at the one call site: clang's ``-ast-dump=json`` emits the ``"inline"`` key
**only** for the explicit ``inline`` keyword. It emits nothing at all for the
three shapes the language makes implicitly inline, all of which are ordinary,
everyday public-header C++:

===============================  ===========================================
shape                            what clang's JSON carries instead
===============================  ===========================================
``constexpr``/``consteval`` fn   ``"constexpr": true`` (+ ``"immediate"``)
member defined in the class      nothing -- only a ``CompoundStmt`` in
body (``int get() const {…}``)   ``inner``, and a record enclosing scope
``= default`` in the class body  ``"explicitlyDefaulted": "default"``
hidden friend defined in the     nothing either, and it is not even a
class (``friend bool             member node: clang emits a plain
operator==(…) { … }``)           ``FunctionDecl`` under a ``FriendDecl``
===============================  ===========================================

Measured against clang 18.1.3 (``-std=c++20``); the castxml backend
(``extract/headers/castxml/functions.py``) folds all three into ``inline="1"``
itself, because the GCC-XML frontend resolves implicit inline before emitting,
so before this module the two backends disagreed on ``Function.is_inline`` for
every one of these shapes despite ``scripts/backend_capabilities.py`` claiming
full parity.

The disagreement is not cosmetic. ``is_inline`` is what tells four separate
consumers that a declaration legitimately emits no unique exported symbol --
``buildsource.cross_source_checks._has_export_obligation`` (a false
``public_not_exported`` at ``Confidence.HIGH`` without it: abicheck demanding
that a header-defined ``constexpr`` constructor be exported, when a consumer
links fine because the definition comes from the header),
``diff_platform``'s ``func_deleted_elf_fallback``, ``diff_cpp_patterns``'
pimpl stale-name check, and ``diff_symbols._check_inline_transitions`` (where
a castxml baseline compared against a clang candidate reported a spurious
``FUNC_LOST_INLINE`` for every such declaration).

**What this deliberately does not claim.** ``is_inline`` remains a fact about
the *declaration's linkage*, not about a definition existing -- ``inline int
f();`` is still ``True`` with no body, exactly as
``docs/contribute/known-gaps.md``'s linkage-blind-removal entry records. It
also stays a fact about the *library's own* build, never about what a consumer
emitted, which is the mistake that entry names as the recurring one.
"""

from __future__ import annotations

from typing import Any

from ....model.identity import Anonymous, Record, ScopePath

__all__ = ["encloses_class_scope", "is_effectively_inline"]

# Function-shaped clang AST node kinds that can be class members. A plain
# ``FunctionDecl`` is never one, so it can never be implicitly inline by the
# in-class rule (it can still be inline by keyword or by ``constexpr``).
_MEMBER_KINDS = frozenset(
    {
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)


#: ``Anonymous.kind`` values that name a *class* scope rather than a namespace.
#: An unnamed record is an everyday header shape (``typedef struct { … } W;``),
#: and its members are implicitly inline exactly like a named record's; an
#: anonymous *namespace* is not a class scope at all and must not match.
_ANONYMOUS_RECORD_KINDS = frozenset({"struct", "class", "union"})


def encloses_class_scope(scope_path: ScopePath) -> bool:
    """Whether *scope_path*'s innermost segment is a class/struct/union.

    This is what separates a member *defined in the class body* (implicitly
    inline) from an out-of-line definition written at namespace scope, which
    is **not** implicitly inline and must keep its export obligation. Both
    reach the parser as the same node kind with the same mangled name; only
    the enclosing scope tells them apart. ``_ClangAstParser`` records an
    out-of-line ``void W::f() {}`` at the translation-unit level with an empty
    scope path, and the in-class declaration under a ``Record`` segment.

    An **unnamed** record reaches the parser as ``Anonymous(kind="struct")``
    instead, never as ``Record`` -- so matching ``Record`` alone missed every
    member of a ``typedef struct { … } W;``, which is ordinary C-compatible
    header style. ``Anonymous`` also covers anonymous *namespaces*, which are
    not class scopes, hence the kind check rather than the bare type.
    """
    if not scope_path:
        return False
    innermost = scope_path[-1]
    if isinstance(innermost, Record):
        return True
    return (
        isinstance(innermost, Anonymous) and innermost.kind in _ANONYMOUS_RECORD_KINDS
    )


def _has_compound_body(node: dict[str, Any]) -> bool:
    """Whether *node* carries a real function body in its ``inner`` list."""
    inner = node.get("inner") or []
    return any(
        isinstance(child, dict) and child.get("kind") == "CompoundStmt"
        for child in inner
    )


def is_effectively_inline(
    node: dict[str, Any], scope_path: ScopePath, *, in_friend: bool = False
) -> bool:
    """Whether *node* has inline linkage, explicitly or implicitly.

    *scope_path* is the declaration's own enclosing scope path, which is what
    :func:`encloses_class_scope` reads to tell an in-class definition from an
    out-of-line one. *in_friend* is the parser's own ``_Decl.in_friend`` --
    True when the declaration was reached through a ``friend`` declaration.

    The implicit rules, in the order the language states them:

    * ``constexpr`` (and therefore ``consteval``) functions are implicitly
      inline -- clang spells both with ``"constexpr": true``.
    * A function *defined* inside a class body is implicitly inline. That
      covers ordinary members and, equally, a **hidden friend** defined in
      the class (``friend bool operator==(const W&, const W&) { … }``) -- the
      canonical spelling of a comparison operator, and not a member node at
      all: clang emits it as a plain ``FunctionDecl`` under a ``FriendDecl``,
      so matching on member node kinds alone misses every one of them. Both
      backends agree it is inline (checked against castxml 0.7.0).
    * A member ``= default``\\ ed inside its class body is such a definition,
      even though clang attaches no ``CompoundStmt`` to it.

    A friend merely *declared* in the class and defined out of line
    (``friend void f(const W&);``) keeps the negative answer, which is why
    *in_friend* alone is not the test -- the body is.

    ``= delete``\\ d members are deliberately **not** matched: clang spells
    them ``"explicitlyDeleted": true`` (not ``explicitlyDefaulted``), a
    deleted function has no definition to inline, and every consumer of this
    fact already handles ``is_deleted`` on its own axis.
    """
    if node.get("inline"):
        return True
    if node.get("constexpr"):
        return True
    if not encloses_class_scope(scope_path):
        return False
    if node.get("kind") not in _MEMBER_KINDS and not in_friend:
        return False
    return _has_compound_body(node) or node.get("explicitlyDefaulted") == "default"
