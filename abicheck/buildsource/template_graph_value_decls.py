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

"""``index_value_decls``/``arg_label_spelling`` — split out of
``template_graph.py`` (at its own 2000-line hard cap) to keep the
TEMPLATE_USES_DECL follow-up (G29 Phase 5 item 1) from pushing the parent
module over. Deliberately a **leaf** module with no import of
``template_graph`` at all, not even under ``if TYPE_CHECKING:`` — the
AI-readiness ``import-cycle-growth`` gate's own static AST walk counts
*any* ``from .X import Y`` (module-level, function-local, or
``TYPE_CHECKING``-guarded alike) as a real edge, so even a type-only
back-reference would register as a
``template_graph -> template_graph_value_decls -> template_graph`` cycle.
The caller passes its own ``_RECORD_DECL_KINDS``/``_VALUE_DECL_KINDS``/
``_normalize_mangled`` in rather than this module importing them back, and
:func:`arg_label_spelling` takes its argument untyped (``Any``) for the
same reason -- ``TemplateArgUse`` is structurally simple enough (``.
target_decl_kind``/``.target_qname``/``.spelling``) that this loses little.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from ..model.source_graph import function_decl_identity


def index_value_decls(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_decl_kind: dict[str, str],
    record_decl_kinds: Collection[str],
    value_decl_kinds: Collection[str],
    normalize_mangled: Callable[[str], str],
    *,
    enclosing_func: bool = False,
) -> None:
    """Populate ``id_to_qname``/``id_to_decl_kind`` (the same dicts
    ``template_graph._index_type_decls`` builds) for TEMPLATE_USES_DECL
    resolution: every namespace/global-scope ``FunctionDecl``/``VarDecl``'s
    own clang node id -> its ``SourceEntity``-compatible identity string (a
    mangled name, or the ``qualified_name#sha256:<type-hash>`` fallback),
    computed by
    :func:`~abicheck.model.source_graph.function_decl_identity` -- the
    exact same computation ``call_graph._function_identity``/
    ``type_graph._var_decl_ident`` already use for their own ``decl://``
    node identity, so a resolved TEMPLATE_USES_DECL target lands on the
    *same* node those modules' own edges do
    (``template_graph._resolve_specialization_qname``'s existing "not a
    known specialization -- return the bare ``id_to_qname`` entry" fallback
    already does the rest; this function only needs to make that entry the
    right string).

    Deliberately scoped to free functions and namespace-scope variables only
    this slice (see ``template_graph.py``'s own module docstring, TEMPLATE_
    USES_DECL section) -- a ``CXXRecordDecl``/``RecordDecl``/specialization
    subtree (*record_decl_kinds*) is skipped entirely rather than descended
    into without pushing a class scope (which would otherwise misattribute a
    class member's identity to its enclosing namespace instead of silently
    leaving it unresolved, the "degrade, never fabricate" discipline this
    whole family of modules already follows) -- a member-function/static-
    data-member NTTP target is a known, undocumented-until-now gap, left for
    its own follow-up. A local *variable* inside a function body is skipped
    the same way ``type_graph.py``'s own ``enclosing_func`` tracking does --
    an NTTP target must have external linkage, so a local one (``static`` or
    not) can never legitimately be one. A block-scope *function*
    declaration is indexed regardless of ``enclosing_func`` (Codex review,
    verified against real clang output: ``void outer() { extern void
    target(); ... }`` gives ``target`` a real top-level ``FunctionDecl``
    node with a genuine ``mangledName``, nested under ``outer``'s own body
    purely as a matter of C++ scoping syntax) -- unlike a local variable, a
    block-scope function declaration always has external linkage (barring
    an explicit ``static``, a distinction this module doesn't special-case
    any more than it does for a namespace-scope one), so skipping it would
    only manufacture a false "unresolved" for a genuinely resolvable
    target.

    ``normalize_mangled`` is applied unconditionally to every indexed
    identity here, matching ``call_graph._function_identity``'s own
    unconditional call (not this module's own guarded, exact-first-then-
    strip join in ``augment_graph_with_templates``) -- **intentionally**,
    since diverging from it would break the "lands on the same node"
    promise above. This carries the identical, already-documented "known
    gap, not fixed here" ``call_graph._normalize_mangled`` accepts for the
    same reason (Codex review): a literal GNU ``asm("__Zfake")`` label on
    ELF/Windows collides with a genuine ``_Zfake`` after stripping. Left as
    a known, cross-module gap rather than special-cased here alone.
    """
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    if kind in record_decl_kinds:
        return
    name = str(node.get("name") or "")
    if kind == "NamespaceDecl" and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            index_value_decls(
                child,
                child_scope,
                id_to_qname,
                id_to_decl_kind,
                record_decl_kinds,
                value_decl_kinds,
                normalize_mangled,
                enclosing_func=enclosing_func,
            )
        return
    if (
        kind in value_decl_kinds
        and name
        and (kind == "FunctionDecl" or not enclosing_func)
    ):
        node_id = str(node.get("id") or "")
        if node_id:
            type_obj = node.get("type")
            type_qual = (
                str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
            )
            identity = function_decl_identity(
                normalize_mangled(str(node.get("mangledName") or "")),
                name,
                "::".join([*scope, name]),
                type_qual if kind == "FunctionDecl" else "",
            )
            id_to_qname.setdefault(node_id, identity)
            id_to_decl_kind.setdefault(node_id, kind)
        if kind == "FunctionDecl":
            for child in node.get("inner", []) or []:
                index_value_decls(
                    child,
                    scope,
                    id_to_qname,
                    id_to_decl_kind,
                    record_decl_kinds,
                    value_decl_kinds,
                    normalize_mangled,
                    enclosing_func=True,
                )
        return
    for child in node.get("inner", []) or []:
        index_value_decls(
            child,
            scope,
            id_to_qname,
            id_to_decl_kind,
            record_decl_kinds,
            value_decl_kinds,
            normalize_mangled,
            enclosing_func=enclosing_func,
        )


def arg_label_spelling(a: Any, value_decl_kinds: Collection[str]) -> str:
    """The text one argument contributes to an instantiation's own label.

    A resolved decl-referencing (TEMPLATE_USES_DECL) argument's ``spelling``
    is the target's bare, unqualified name — not enough to disambiguate
    ``Holder<&ns1::f>`` from ``Holder<&ns2::f>`` (the exact collision risk
    the G29 plan doc's TEMPLATE_USES_DECL entry documents as the reason
    this stayed unimplemented for a prior investigation round).
    ``target_qname`` is the target's own unique identity string once
    resolved, so it's used instead; an unresolved decl argument falls back
    to the bare spelling, same as an unresolved type argument already does.
    """
    if a.target_decl_kind in value_decl_kinds and a.target_qname:
        return str(a.target_qname)
    return str(a.spelling)


def disambiguated_specialization_qname(
    spec_id: str,
    fallback: str | None,
    is_specialization: bool,
    resolve_specialization_qname: Callable[[str], str | None],
) -> str | None:
    """*resolve_specialization_qname(spec_id)* (``template_graph.
    _resolve_specialization_qname``, passed in rather than imported --
    this module stays a leaf) when *is_specialization*, else *fallback*
    unchanged. Shared by ``template_graph._walk_function_templates``'s two
    member-scope-naming call sites (Codex review): two specializations
    differing only in an unresolvable decl-kind NTTP argument (a known
    gap, e.g. a class-member target) previously both fell back to one
    bare/raw-spelling name, merging their distinct member template
    instantiations onto one graph node.

    Returns ``None`` (never a fabricated fallback) when *is_specialization*
    and resolution genuinely fails -- an earlier revision suffixed
    *fallback* with *spec_id* itself as a "unique by construction"
    disambiguator, but clang's own node ``id`` is a process-local address,
    not stable across separate compiler invocations of the identical TU
    (Codex review, fresh evidence, verified empirically: two ``clang++``
    runs over the same source report different ids for the same
    specialization) -- callers must skip rather than use a ``None`` here,
    the same "degrade, never guess" discipline this whole module follows.
    """
    return resolve_specialization_qname(spec_id) if is_specialization else fallback
