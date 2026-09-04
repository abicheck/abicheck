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

"""Direct-clang initializer/default-argument value fingerprinting.

Split out of ``dumper_clang.py`` to stay under its line-count cap — the
fingerprint chain for ``TypeField.default``/``Param.default`` grew a lot of
carefully-verified, narrow edge-case handling across several Codex review
rounds (referenced-declaration identity/scope, template-specialization
disambiguation, ``sizeof`` operand types, anonymous-type/lambda location
normalization) and kept pushing the parent file over the hard cap. A leaf
module (must not import from ``dumper_clang`` to avoid an import cycle);
``dumper_clang.py`` imports back what it needs (``_SCOPE_NODE_KINDS`` for its
own ``_ClangAstParser._walk``, ``_unwrap_expr`` for its own
``_evaluated_int_value``, ``_field_initializer_value``/``_initializer_value``
for field/param construction).

Value representation: a lone literal (after stripping wrapper casts) keeps
its human-readable value (``"42"``); anything compound reduces to a short,
build-stable structural fingerprint (``"expr:" + sha256(...)[:16]``) so two
different compound expressions compare unequal while the same one is stable
across builds — deliberately NOT cross-comparable with castxml's verbatim
source expression (see ``diff_types``'s/``diff_symbols``'s same-producer
gates, and ``diff_default_value_reliability.py`` for the schema-version gate
this fingerprint's own algorithm churn needed).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

# Canonical definition moved to ``extract.headers.clang.templates`` (ADR-061
# Phase 5 item 1): that package's own ``build_specialization_index`` and its
# sibling `_index_template_param_*` walks need this constant, and `extract`
# may not import `diff_cxx_rules` (classified `compare`) -- so the constant
# now lives on the side that CAN be imported from unconditionally, and this
# module reads it back, keeping this as the ONE definition (never two
# independently-drifting copies) the same way ``dumper_clang.py``'s own
# ``_ClangAstParser._walk`` already did before this move. (The
# ``itanium_scope_components`` import below no longer needs this same
# workaround -- ADR-061 D1 moved it to ``model/mangled_name.py``, which any
# layer may import.)
from .extract.headers.clang.templates import _SCOPE_NODE_KINDS as _SCOPE_NODE_KINDS
from .model.mangled_name import itanium_scope_components

#: Literal node kinds whose ``value`` is a stable, human-meaningful constant.
_LITERAL_NODE_KINDS = frozenset(
    {
        "IntegerLiteral",
        "FloatingLiteral",
        "CharacterLiteral",
        "StringLiteral",
        "CXXBoolLiteralExpr",
        "FixedPointLiteral",
    }
)
#: Single-child wrapper expressions to descend through before reading a literal.
_WRAPPER_EXPR_KINDS = frozenset(
    {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "CXXStaticCastExpr",
        "ConstantExpr",
        "ExprWithCleanups",
        "ParenExpr",
        "CXXFunctionalCastExpr",
        "MaterializeTemporaryExpr",
    }
)

#: A lazy, memoized provider for :func:`_index_decl_id_qualified_names`'s
#: index -- a CALLABLE, not the dict itself, so the (potentially expensive,
#: whole-AST) index is only ever built if a fingerprint actually needs to
#: resolve a ``referencedDecl`` (Codex review, fresh evidence, second round:
#: gating the eager ``self._id_index()`` call on ``hasInClassInitializer``/
#: ``_param_has_default`` alone still built it for a plain LITERAL default,
#: e.g. `int timeout = 30;`, which never reaches the point in
#: :func:`_canonical_expr` that would actually consult it).
_IdIndexProvider = Callable[[], dict[str, str]]


def _unwrap_expr(node: dict[str, Any]) -> dict[str, Any]:
    """Descend through single-child wrapper expressions (casts, ConstantExpr…).

    A malformed/adversarial ``inner`` (present but not a list, e.g. a bare
    int or dict) degrades to no children, same as a missing key -- it used
    to raise ``TypeError`` iterating it directly (Codex review, PR #888).
    """
    cur = node
    while isinstance(cur, dict) and cur.get("kind") in _WRAPPER_EXPR_KINDS:
        raw = cur.get("inner")
        inner = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
        if len(inner) != 1:
            break
        cur = inner[0]
    return cur


def _init_expr(node: dict[str, Any]) -> dict[str, Any] | None:
    """The initializer expression child of a Var/Field decl, or ``None``."""
    candidates = [
        c
        for c in node.get("inner", []) or []
        if isinstance(c, dict)
        and not str(c.get("kind", "")).endswith(("Decl", "Attr", "Comment"))
    ]
    return candidates[-1] if candidates else None


def _initializer_value(
    node: dict[str, Any], id_index: _IdIndexProvider | None = None
) -> str | None:
    """A stable value string for a variable's initializer, or ``None`` if absent.

    A lone literal (after stripping wrapper casts) keeps its human-readable value
    (``42``); any compound initializer is reduced to a short deterministic
    fingerprint so two different compound expressions compare unequal while the
    same one is stable across builds. Mirrors the castxml ``init`` value as a
    same-backend comparison key (cross-backend constant *values* are not
    expected to match — the snapshots are still per-backend parity oracles for
    presence/scope).

    *id_index* (typically :func:`_index_decl_id_qualified_names` over the
    whole AST root) resolves a referenced declaration's scope, so ``a::VALUE``
    and ``b::VALUE`` fingerprint distinctly — see :func:`_canonical_expr`.
    """
    init = _init_expr(node)
    if init is None:
        return None
    core = _unwrap_expr(init)
    if core.get("kind") in _LITERAL_NODE_KINDS and "value" in core:
        return str(core["value"])
    return _expr_fingerprint(init, id_index)


def _field_initializer_value(
    field: dict[str, Any], id_index: _IdIndexProvider | None = None
) -> str | None:
    """A ``TypeField.default`` value for a ``FieldDecl``, or ``None``.

    G31 Phase C: the last of that phase's fact-completeness list that the
    direct-clang backend genuinely can close (vptr *placement* it still
    cannot — clang's plain ``-ast-dump=json`` carries no secondary-vtable
    offsets without the optional ``ABICHECK_CLANG_LAYOUT_TOOL`` companion).

    Presence is taken from clang's own ``hasInClassInitializer`` flag rather
    than from "does this decl have a non-attribute ``inner`` child" the way
    ``_param_has_default`` does, because a ``FieldDecl``'s ``inner`` list
    is overloaded: a **bitfield width** is nested there as a ``ConstantExpr``
    too. Verified against real Clang 18 output — ``int bf : 3;`` (no
    initializer at all) nests exactly one ``ConstantExpr`` child with
    ``value: "3"``, which the param-style "any non-attribute child" heuristic
    would read as a default member initializer of ``3``, fabricating an
    initializer for a field that has none. ``hasInClassInitializer`` is
    present-only-when-true (absent, never present-and-``false``, for both the
    plain and the bitfield case), matching the same convention
    ``scopedEnumTag`` uses.

    The width/initializer ordering is what makes reusing
    :func:`_initializer_value` safe for the combined form: ``int bfi : 3 = 2;``
    nests the width ``ConstantExpr`` *first* and the initializer second, and
    :func:`_init_expr` takes the last non-``Decl``/``Attr``/``Comment`` child
    — so the initializer wins. (A trailing ``[[deprecated]]`` is a
    ``DeprecatedAttr``, which that same filter already drops; confirmed
    against real output, where clang emits it *after* the initializer.)

    Value representation matches :func:`_initializer_value`'s contract, i.e.
    the same-backend-comparable one ``Param.default`` already uses on this
    backend: a bare literal keeps its readable value, anything compound
    reduces to a structural fingerprint. That is deliberately NOT
    cross-comparable with castxml's verbatim source expression — see
    ``diff_types._diff_field_default_initializer``'s same-producer gate.
    """
    if not field.get("hasInClassInitializer"):
        return None
    return _initializer_value(field, id_index)


def _expr_fingerprint(
    node: dict[str, Any], id_index: _IdIndexProvider | None = None
) -> str:
    """A short, build-stable structural fingerprint of an expression subtree."""
    blob = json.dumps(_canonical_expr(node, id_index), sort_keys=True).encode("utf-8")
    return "expr:" + hashlib.sha256(blob).hexdigest()[:16]


def _is_func_or_var_template_specialization(node: dict[str, Any]) -> bool:
    """Whether *node* is a function or variable TEMPLATE SPECIALIZATION —
    ``f<int>()``/``f<long>()`` (a ``FunctionDecl`` named ``"f"`` with a
    direct ``TemplateArgument`` child) or ``V<int>``/``V<long>`` (clang's
    own distinct ``VarTemplateSpecializationDecl`` kind) — as opposed to an
    ordinary, non-template ``FunctionDecl``/``VarDecl``, verified against
    real Clang 17 output (Codex review, fresh evidence). Both real shapes
    report only the bare, template-argument-free primary-template name in
    their own ``name`` field, so :func:`_index_decl_id_qualified_names`
    needs this to know when to fall back to the node's OWN ``mangledName``
    (which DOES encode the arguments) instead.
    """
    kind = node.get("kind")
    if kind == "VarTemplateSpecializationDecl":
        return True
    if kind == "FunctionDecl":
        return any(
            isinstance(c, dict) and c.get("kind") == "TemplateArgument"
            for c in node.get("inner", []) or []
        )
    return False


def _index_decl_id_qualified_names(root: dict[str, Any]) -> dict[str, str]:
    """Map every named declaration's clang ``id`` to its scope-qualified name.

    A single, dedicated pass over the WHOLE AST root (independent of
    ``_ClangAstParser``'s own categorizing walk, which only tracks the
    ABI-surface kinds it collects): a ``DeclRefExpr``'s ``referencedDecl``
    can name any declaration in the TU, so this needs to see everything.

    Feeds :func:`_canonical_expr`'s referenced-declaration fingerprinting: a
    ``referencedDecl`` stub is compact and carries only a bare, unqualified
    ``name`` (Codex review, fresh evidence: `a::VALUE`/`b::VALUE` share the
    byte-identical stub). Its own ``id`` IS unique but is a compile-time-only
    memory address, never stable across builds, so it is exchanged for this
    index's qualified-name string rather than hashed directly.

    Same namespace/class scope-tracking rule as
    ``_ClangAstParser._walk``/``_SCOPE_NODE_KINDS``, EXCEPT for
    ``ClassTemplateSpecializationDecl`` (Codex review, second round):
    distinct specializations of the same template (``A<int>`` vs.
    ``A<long>``) both expose only the bare primary-template name ``"A"``, no
    template-argument spelling at all on the node itself.
    ``_SCOPE_NODE_KINDS`` deliberately stays untouched (it also drives
    ``_ClangAstParser._walk``'s own public-surface qualified names, a much
    larger blast radius); this function special-cases the kind locally via
    :func:`_specialization_scope_key`.

    A FUNCTION or VARIABLE template specialization has the identical bare-
    name collision one level down (Codex review, fourth round, fresh
    evidence): ``f<int>()``/``f<long>()`` both report a ``FunctionDecl``
    named ``"f"``, and ``V<int>``/``V<long>`` both report a
    ``VarTemplateSpecializationDecl`` named ``"V"`` — see
    :func:`_is_func_or_var_template_specialization`. Unlike the class case,
    each specialization carries its OWN build-stable ``mangledName``
    directly, so no representative-member trick is needed — it's used as
    the index value outright.

    A redeclaration (e.g. a function declared then defined) shares its real
    entity's name, so the first sighting of a given ``id`` is kept rather
    than overwritten.
    """
    index: dict[str, str] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = node.get("name") or ""
        node_id = node.get("id")
        mangled = node.get("mangledName") or ""
        if isinstance(node_id, str) and name:
            if (
                mangled
                and mangled != name
                and _is_func_or_var_template_specialization(node)
            ):
                # A function/variable TEMPLATE SPECIALIZATION's own name is
                # just the bare primary-template name (`f<int>()`/`f<long>()`
                # both report a `FunctionDecl` named "f"; `V<int>`/`V<long>`
                # both report a `VarTemplateSpecializationDecl` named "V") --
                # verified against real Clang 17 output that both instances
                # otherwise resolve to the SAME index entry, so a field/param
                # default referencing one specialization vs. the other
                # fingerprinted identically (Codex review, fresh evidence).
                # Unlike a class specialization (a TYPE, never itself
                # mangled), a function/variable specialization carries its
                # OWN build-stable mangled name directly -- no representative-
                # member trick needed, just use it as the full index value.
                index.setdefault(node_id, mangled)
            else:
                index.setdefault(node_id, "::".join((*scope, name)) if scope else name)
        is_specialization = kind == "ClassTemplateSpecializationDecl"
        if is_specialization and name:
            # A representative member's own MANGLED name encodes the
            # template arguments (`_ZN1AIiE5VALUEE` vs `_ZN1AIlE5VALUEE` for
            # `VALUE`) and, unlike this node's own `id` (a compile-time
            # memory address), is build-stable -- so it disambiguates
            # without ever hashing an unstable value into the persisted
            # fingerprint _canonical_expr ultimately produces. Falls back to
            # the bare (collision-prone) name when no direct child carries
            # one at all -- a rarer, accepted degradation, the same
            # "conservative fallback over a wrong guess" convention this
            # whole module already follows elsewhere.
            disambiguator = _specialization_scope_key(node)
            scope_name = f"{name}#{disambiguator}" if disambiguator else name
        else:
            scope_name = name
        scope_forming = kind in _SCOPE_NODE_KINDS or is_specialization
        child_scope = (*scope, scope_name) if scope_forming and scope_name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return index


def _specialization_scope_key(node: dict[str, Any]) -> str:
    """A build-stable, MEMBER-ORDER-INDEPENDENT identity for a
    ``ClassTemplateSpecializationDecl``, or ``""`` when none can be derived.

    An earlier version used whichever direct child happened to be FIRST with
    a mangled name -- unstable to unrelated edits (Codex review: inserting
    `static constexpr int AAA` before an unchanged `VALUE` changed the
    disambiguator from `VALUE`'s own mangled name to `AAA`'s, perturbing
    every OTHER declaration referencing `VALUE` though nothing about it
    changed).

    Fixed via the SCOPE portion of a representative member's mangled name
    (:func:`model.mangled_name.itanium_scope_components`), e.g.
    ``_ZN1AIiE5VALUEE`` -> ``["AIiE", "VALUE"]``: dropping the trailing leaf
    leaves ``["AIiE"]``, identical regardless of which member contributed
    it. Tries every child until one parses -- a special member/operator's
    name isn't parseable this way, and stopping there would reopen the
    same instability.

    KNOWN GAP, Itanium-only (Codex review, fresh evidence, third round): a
    ``clang-cl``/``--target=*-windows-msvc`` snapshot mangles a
    specialization member as e.g. ``?VALUE@?$A@H@@2HB``, and
    ``itanium_scope_components`` naturally returns ``None`` for it (wrong
    scheme). Deliberately NOT falling back to
    ``diff_cxx_rules.msvc_scope_components`` here -- verified it ALSO
    returns ``None`` for this exact input, by its own documented design
    (a component starting with the template marker ``?$`` is rejected
    outright, and a specialization's class-name component always does) --
    so unlike the Mach-O/MSVC fallbacks already wired into
    ``_index_decl_id_qualified_names``'s sibling helpers, adding that call
    here would be dead code on every real input this function ever sees
    (it is only invoked for a ``ClassTemplateSpecializationDecl`` member,
    which is unconditionally template-mangled). ``A<int>``/``A<long>`` on
    an MSVC-targeted direct-clang snapshot therefore still collide to the
    bare ``"A"`` -- a real fix needs genuine MSVC template-argument
    decoding, a new capability with no existing test/caller to verify it
    against yet (same "a wrong guess here is worse than the pre-existing
    gap" reasoning AGENTS.md already records for the sibling MSVC
    toolchain-profile gap).
    """
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        mangled = child.get("mangledName")
        if not isinstance(mangled, str) or not mangled:
            continue
        comps = itanium_scope_components(mangled)
        if comps and len(comps) > 1:
            return "::".join(comps[:-1])
    return ""


#: Matches clang's location-bearing anonymous/lambda type spellings, e.g.
#: ``"(unnamed enum at t.hpp:1:1)"`` or ``"S::(lambda at t.hpp:2:38)"`` --
#: group 2 (the tag kind, absent for "lambda") is the only part kept.
_ANON_TYPE_LOCATION_RE = re.compile(r"\((unnamed (\w+)|lambda) at [^)]*\)")


def _normalize_qual_type(qual_type: str) -> str:
    """Strip the source location out of an anonymous-tag/lambda ``qualType``
    before it's folded into a build-stable fingerprint.

    clang spells an anonymous enum/struct/union/class's type as ``"(unnamed
    <kind> at <file>:<line>:<col>)"``, and a lambda's closure type as
    ``"(lambda at ...)"`` -- both embed an absolute path/line (Codex review:
    two checkout paths, or a blank line before an anonymous type/lambda
    default, produced different fingerprints for an unchanged initializer).
    A lambda's spelling recurs throughout any type built on it (e.g. a
    `std::function<...>`-wrapped lambda's instantiation chain), so every
    occurrence is substituted, not just the outermost. The "unnamed
    <kind>"/"lambda" portion is kept; only the location collapses to a
    fixed placeholder. A named (the common case) qualType passes through
    unchanged.
    """

    def _repl(m: re.Match[str]) -> str:
        kind = m.group(2)
        return f"(unnamed {kind})" if kind else "(lambda)"

    return _ANON_TYPE_LOCATION_RE.sub(_repl, qual_type)


def _canonical_expr(node: Any, id_index: _IdIndexProvider | None = None) -> Any:
    """Reduce an expression node to a structural form (drop ids/locations).

    KNOWN, deliberately-deferred gap (Codex review, fresh evidence): a
    template-DEPENDENT operand — e.g. ``T::template value<1>()`` inside an
    uninstantiated class template pattern — is a ``DependentScopeDeclRefExpr``
    that clang's ``-ast-dump=json`` prints with NO ``name``, no ``value``, no
    ``referencedDecl``, and no ``inner`` children at all (verified against
    real Clang 17 output): every key this function reads is either absent or
    the same fixed placeholder ``"<dependent type>"`` regardless of the
    actual template argument (``value<1>()`` vs. ``value<2>()`` fingerprint
    byte-identically). The dependent argument spelling genuinely isn't
    exposed anywhere in the compact JSON AST for a node in this state —
    recovering it would mean either re-invoking clang in a different dump
    mode or reading the raw source text at the node's ``range`` offsets,
    both a materially different (and unverified) architecture for this pure,
    AST-JSON-only module, not a narrow extension of it. Left as a silent
    false negative — the same conservative default this fingerprint chain
    already uses throughout (a wrong guess here is worse than the
    pre-existing gap).

    A second, independently verified gap of the same shape (Codex review,
    fresh evidence): ``offsetof(A, x)`` vs. ``offsetof(A, y)`` — clang's
    ``OffsetOfExpr`` node. Confirmed against real Clang 18 output (both
    ``-ast-dump=json`` and the plain ``-ast-dump`` text form): the node
    carries no ``inner`` children, no ``name``, and no other key naming
    which member(s) the offset walk selects — only ``kind``/``range``/
    ``type``/``valueCategory``, and ``type`` is always the fixed
    ``"unsigned long"`` regardless of the member, so two different
    ``offsetof`` calls on the same struct reduce to the byte-identical
    ``{"kind": "OffsetOfExpr", "type": "unsigned long"}``. As with the
    dependent-scope case above, the component list genuinely isn't exposed
    anywhere in the compact JSON AST — there is no key this function is
    failing to read. Left as the same kind of silent false negative.

    A third gap, found and fixed rather than deferred (Codex review, fresh
    evidence): a ``CXXNewExpr`` (``new S()`` vs. ``::new S()`` when ``S``
    declares its own ``operator new``) previously dropped ``isGlobal`` and
    ``operatorNewDecl``/``operatorDeleteDecl`` entirely, even though real
    Clang 18 output shows they're exactly what distinguishes the two calls
    (verified: the class-member form has no ``isGlobal`` key and an
    ``operatorNewDecl.kind`` of ``CXXMethodDecl``; the global form has
    ``isGlobal: true`` and ``FunctionDecl``) — both reduced to the
    byte-identical ``{"kind": "CXXNewExpr", "type": "S *"}`` before this fix.
    Fixed by keeping ``isGlobal`` verbatim and reusing the same decl-stub
    reduction ``referencedDecl`` already gets (below) for both operator
    fields.

    A fourth gap, also found and fixed (Codex review, fresh evidence): ``new
    S`` (default-initialization) vs. ``new S()`` (value-initialization, which
    zero-initializes ``S``'s scalar members first) previously fingerprinted
    identically too — confirmed against real Clang 18 output that
    ``CXXNewExpr.initStyle`` is absent for the bare form and ``"call"`` for
    the parenthesized form, with the nested ``CXXConstructExpr`` additionally
    carrying ``zeroing: true`` only for the latter; neither key was in this
    function's whitelist. Both are now kept verbatim alongside ``isGlobal``.

    A fifth gap, also found and fixed (Codex review, fresh evidence):
    ``typeid(int)`` vs. ``typeid(long)`` — a ``CXXTypeidExpr`` applied to a
    TYPE operand (as opposed to a polymorphic expression, which nests an
    ordinary expression child under ``inner`` and is already handled)
    previously fingerprinted identically too. Confirmed against real Clang
    18 output: this node is childless and its own ``type`` is always the
    fixed result type ``"const std::type_info"`` regardless of operand — the
    same "result type, not operand type" trap ``argType`` was already added
    for ``sizeof``/``alignof`` above, except clang spells this one's operand
    key differently: ``typeArg``, not ``argType``. Now read alongside it.

    A sixth gap, also found and fixed (Codex review, fresh evidence): a
    discarded increment/decrement inside a larger expression, e.g. ``(g++,
    1)`` vs. ``(++g, 1)`` (both reduce to the literal value ``1``, so only
    the SIDE EFFECT differs) — previously fingerprinted identically.
    Confirmed against real Clang 17 output: a pre/post increment/decrement
    ``UnaryOperator`` shares the same ``opcode`` (``"++"``) for both forms;
    the ONLY structural distinction is a separate ``isPostfix`` boolean key,
    which wasn't in this function's whitelist. Now kept verbatim.
    """
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key in (
        "kind",
        "value",
        "opcode",
        "name",
        "castKind",
        "isGlobal",
        "initStyle",
        "zeroing",
        "isPostfix",
    ):
        if key in node:
            out[key] = node[key]
    type_obj = node.get("type")
    if isinstance(type_obj, dict) and "qualType" in type_obj:
        out["type"] = _normalize_qual_type(type_obj["qualType"])
    # A UnaryExprOrTypeTraitExpr (sizeof/alignof/... applied to a TYPE, not
    # an expression) stores its operand EXCLUSIVELY in "argType" -- "type" is
    # just the trait's result type (always "unsigned long" for sizeof,
    # regardless of operand) (Codex review: `sizeof(int)` vs `sizeof(long
    # long)` fingerprinted identically without this). Other trait-expression
    # operand shapes are an unverified, narrower residual gap.
    arg_type_obj = node.get("argType")
    if isinstance(arg_type_obj, dict) and "qualType" in arg_type_obj:
        out["argType"] = _normalize_qual_type(arg_type_obj["qualType"])
    # A CXXTypeidExpr applied to a TYPE operand (`typeid(int)`) stores it
    # EXCLUSIVELY in "typeArg" -- a different key than sizeof/alignof's
    # "argType" above, verified against real Clang 18 output (Codex review:
    # `typeid(int)` vs. `typeid(long)` fingerprinted identically without
    # this, since the node's own "type" is always the fixed result type
    # `const std::type_info`, never the operand).
    type_arg_obj = node.get("typeArg")
    if isinstance(type_arg_obj, dict) and "qualType" in type_arg_obj:
        out["typeArg"] = _normalize_qual_type(type_arg_obj["qualType"])
    for decl_key in ("referencedDecl", "operatorNewDecl", "operatorDeleteDecl"):
        decl_out = _decl_stub(node.get(decl_key), id_index)
        if decl_out is not None:
            out[decl_key] = decl_out
    inner = node.get("inner")
    if isinstance(inner, list):
        out["inner"] = [_canonical_expr(c, id_index) for c in inner]
    return out


def _decl_stub(decl: Any, id_index: _IdIndexProvider | None) -> dict[str, Any] | None:
    """Reduce a nested declaration reference (``referencedDecl``,
    ``operatorNewDecl``, ``operatorDeleteDecl``) to a small, stable stub.

    A referenced declaration's own top-level keys never identify WHICH
    declaration it names -- that lives only in this compact sub-object,
    previously dropped entirely for ``referencedDecl`` (Codex review:
    `int x = DEFAULT_A;` vs `int x = DEFAULT_B;` fingerprinted identically
    without this -- affects both TypeField.default and the pre-existing
    Param.default, which share this helper) and, in a later round, for
    ``operatorNewDecl``/``operatorDeleteDecl`` too (`new S()` vs `::new
    S()` when `S` declares its own `operator new` -- see `_canonical_expr`'s
    own docstring). Its own "id" is a compile-time memory address, never
    stable across builds, so only "kind"/"name"/"type" are kept verbatim.
    """
    if not isinstance(decl, dict):
        return None
    stub: dict[str, Any] = {}
    for key in ("kind", "name"):
        if key in decl:
            stub[key] = decl[key]
    decl_type = decl.get("type")
    if isinstance(decl_type, dict) and "qualType" in decl_type:
        stub["type"] = _normalize_qual_type(decl_type["qualType"])
    # The bare "name" above collides across scopes (Codex review, second
    # round): `a::VALUE` vs `b::VALUE` share it. Resolve via id_index
    # when available -- falls back to bare-name-only (still better than
    # nothing) when the id isn't found, e.g. a builtin.
    decl_id = decl.get("id")
    if id_index is not None and isinstance(decl_id, str):
        # Called lazily, right here -- not by the caller -- so a literal
        # default (the common case, which never reaches this branch at
        # all) never pays for the index build; only a real referenced
        # declaration does. The provider itself is expected to be memoized
        # (_ClangAstParser._id_index()), so repeated resolution within one
        # parse is still a single AST walk.
        qualified = id_index().get(decl_id)
        if qualified is not None:
            stub["qualified_name"] = qualified
    return stub or None
