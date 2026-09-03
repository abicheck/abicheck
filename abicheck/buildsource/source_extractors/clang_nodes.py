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

"""Reading one node of a ``clang -ast-dump=json`` document.

Everything here answers a question about a single AST node and its own
subtree -- where it came from (``_node_file``/``_node_line``), how it is
spelled (``_signature``/``_mangled``/``_qualified``), what it names
(``_entity_names``/``_entity_ownership``), what a default argument or
initializer evaluates to (``_default_arg_repr``/``_expr_value``), and what
its body hashes to once local names are normalized away
(``_canonical``/``_alpha_rename_map``/``_subtree_hash``).

That last group is the reason this layer is worth naming: a body fingerprint
has to be stable under local-variable renaming and operand order for
commutative operators, or an ADR-030 source-ABI replay reports a body change
for a purely cosmetic edit. The rules live in the frozensets below -- which
node kinds are literals, which declarations are renameable locals, which
operators commute -- and they are used nowhere else in the package.

Split out of :mod:`abicheck.buildsource.source_extractors.clang` (which sat
exactly at the 2000-line hard cap, so no edit to it could pass the
AI-readiness gate). A leaf by construction: it imports nothing from
``abicheck``, so the extractor above it can depend on it freely.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Literal nodes whose ``value`` is a stable, human-meaningful constexpr value.
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

#: Scalar node keys that survive into the structural body fingerprint. Volatile
#: keys (``id`` pointer values, ``loc``/``range`` offsets, ``previousDecl``) are
#: dropped so the hash is stable across builds/checkouts (mirrors the build-root
#: independence of ``SourceEntity.identity()``).
_FINGERPRINT_SCALAR_KEYS = ("kind", "name", "value", "opcode", "castKind")


def _hash(*parts: str) -> str:
    blob = "\x00".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


#: AST node kinds that introduce a *local* binding — a parameter or a
#: block-scope variable. Their names are alpha-renamed to positional placeholders
#: so a pure rename of a local/parameter does not flip the body fingerprint.
_LOCAL_DECL_KINDS = frozenset(
    {"ParmVarDecl", "VarDecl", "BindingDecl", "DecompositionDecl"}
)

#: ``storageClass`` values that give a block-scope ``VarDecl`` a stable *linkage*
#: name — a function-local ``static`` emits a distinct weak symbol (``f()::x``)
#: and an ``extern`` local names a global. Such names are **not** alpha-renamed,
#: since renaming them is an observable change, not a cosmetic one.
_NON_RENAMEABLE_STORAGE = frozenset({"static", "extern"})

#: Commutative, non-short-circuiting binary operators whose two operands may be
#: sorted into a canonical order in the fingerprint (ADR-030 #6). Excludes the
#: short-circuit `&&`/`||` (reordering changes evaluation order/side effects) and
#: every non-commutative operator (`-`, `/`, `%`, `<`, `<<`, assignments, …).
_COMMUTATIVE_OPS = frozenset({"+", "*", "==", "!=", "&", "|", "^"})


def _is_renameable_local(node: dict[str, Any]) -> bool:
    """Whether a decl node is an automatic local whose name is alpha-renameable.

    Parameters and ordinary block-scope variables are renameable; a
    function-local ``static``/``extern`` ``VarDecl`` is not — its name is part of
    a linkage symbol, so a rename must change the body fingerprint (Codex review).
    """
    kind = node.get("kind")
    if kind not in _LOCAL_DECL_KINDS:
        return False
    if kind == "VarDecl" and node.get("storageClass") in _NON_RENAMEABLE_STORAGE:
        return False
    return True


def _alpha_rename_map(
    node: dict[str, Any], param_ids: tuple[str, ...]
) -> dict[str, str]:
    """Map each local-binding clang ``id`` to a positional placeholder (``$0``…).

    This is the semantic core of the fingerprint (ADR-030 follow-up #6): instead
    of hashing the raw AST — where renaming a local variable or parameter changes
    the structural shape and so the hash — we hash an **alpha-equivalence class**.
    Two bodies that differ only by the spelling of their locals/parameters map to
    the same placeholders and hash identically, so ``inline_body_changed`` /
    ``template_body_changed`` no longer fire on a cosmetic rename.

    Only ids that name a true local binding are renamed: the function's
    parameters (``param_ids``, threaded in declared order so they get the first,
    stable placeholders) plus every local ``VarDecl`` declared inside the subtree.
    A reference to anything *else* — a global, another function, a named constant
    — keeps its real name, because referencing a different entity is a real
    semantic change the fingerprint must still catch.

    Placeholders are assigned in first-occurrence (pre-order) order so the mapping
    is itself rename-invariant.
    """
    # The set of ids that denote a local binding: parameters + in-body locals.
    local_ids: set[str] = {pid for pid in param_ids if pid}

    def _collect(n: Any) -> None:
        if not isinstance(n, dict):
            return
        nid = n.get("id")
        if isinstance(nid, str) and _is_renameable_local(n):
            local_ids.add(nid)
        inner = n.get("inner")
        if isinstance(inner, list):
            for child in inner:
                _collect(child)

    _collect(node)
    if not local_ids:
        return {}

    # Assign placeholders by first occurrence (params first, then by pre-order),
    # counting both declarations and references so a use-before-decl still lands
    # on a stable slot.
    order: list[str] = [pid for pid in param_ids if pid in local_ids]
    seen: set[str] = set(order)

    def _order(n: Any) -> None:
        if not isinstance(n, dict):
            return
        nid = n.get("id")
        if isinstance(nid, str) and nid in local_ids and nid not in seen:
            seen.add(nid)
            order.append(nid)
        ref = n.get("referencedDecl")
        if isinstance(ref, dict):
            rid = ref.get("id")
            if isinstance(rid, str) and rid in local_ids and rid not in seen:
                seen.add(rid)
                order.append(rid)
        inner = n.get("inner")
        if isinstance(inner, list):
            for child in inner:
                _order(child)

    _order(node)
    return {nid: f"${i}" for i, nid in enumerate(order)}


def _canonical(node: Any, amap: dict[str, str]) -> Any:
    """Reduce a clang AST node to a build-root-stable structural form for hashing.

    Keeps only structural scalars (``kind``/``name``/``value``/``opcode``/
    ``castKind``) plus the node's ``type.qualType`` and its recursively
    canonicalized children, dropping pointer ids and source locations so a pure
    body edit changes the hash while a rebuild/relocation does not.

    ``amap`` (from :func:`_alpha_rename_map`) replaces a local binding's name —
    on both its declaration and every reference — with a positional placeholder,
    so the hash is an alpha-equivalence class invariant under local/parameter
    renaming (ADR-030 follow-up #6).
    """
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    nid = node.get("id")
    placeholder = amap.get(nid) if isinstance(nid, str) else None
    for key in _FINGERPRINT_SCALAR_KEYS:
        if key in node:
            # A local declaration's own name becomes its placeholder.
            out[key] = (
                placeholder if key == "name" and placeholder is not None else node[key]
            )
    type_obj = node.get("type")
    if isinstance(type_obj, dict) and "qualType" in type_obj:
        out["type"] = type_obj["qualType"]
    # A DeclRefExpr stores the referenced entity (e.g. another constant) in
    # ``referencedDecl``; without its name a value change `kOld` -> `kNew` of the
    # same type would hash identically and the constexpr/default-arg change would
    # be missed (Codex review #339, P2). A reference to a *local* binding uses the
    # alpha-renamed placeholder; a reference to anything else keeps its real name.
    ref = node.get("referencedDecl")
    if isinstance(ref, dict):
        rid = ref.get("id")
        ref_placeholder = amap.get(rid) if isinstance(rid, str) else None
        if ref_placeholder is not None:
            out["ref"] = ref_placeholder
        elif ref.get("name"):
            out["ref"] = ref["name"]
    inner = node.get("inner")
    if isinstance(inner, list):
        children = [_canonical(child, amap) for child in inner]
        # Commutative-operator normalization (ADR-030 #6): the operands of a
        # commutative binary operator (`a + b` vs `b + a`, `x == y` vs `y == x`)
        # are sorted into a canonical order so a pure reordering does not change
        # the fingerprint. Short-circuit `&&`/`||` are NOT commutative for the
        # fingerprint — reordering them changes evaluation order/side effects — so
        # they are excluded, as are all non-commutative operators.
        if (
            out.get("kind") == "BinaryOperator"
            and out.get("opcode") in _COMMUTATIVE_OPS
            and len(children) == 2
        ):
            children.sort(key=lambda c: json.dumps(c, sort_keys=True))
        out["inner"] = children
    return out


def _subtree_hash(node: dict[str, Any], param_ids: tuple[str, ...] = ()) -> str:
    """Alpha-equivalence-normalized structural fingerprint of a clang subtree.

    ``param_ids`` are the clang ids of the enclosing function's parameters (in
    declared order), so a body that references its parameters is normalized
    together with them even though the parameter declarations live on the
    ``FunctionDecl``, outside the hashed ``CompoundStmt`` body (ADR-030 #6).
    """
    amap = _alpha_rename_map(node, param_ids)
    return _hash("clang-ast", json.dumps(_canonical(node, amap), sort_keys=True))


def _param_ids(node: dict[str, Any]) -> tuple[str, ...]:
    """The clang ids of a function node's parameters, in declared order."""
    out: list[str] = []
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
            cid = child.get("id")
            if isinstance(cid, str):
                out.append(cid)
    return tuple(out)


def _node_file(node: dict[str, Any], current: str) -> str:
    """The declaring file for a node, honoring clang's sticky-``file`` JSON.

    clang omits a node's ``loc.file`` when it matches the previous node in source
    order, so the file must be threaded through the traversal; ``current`` is the
    last file seen.
    """
    loc = node.get("loc")
    if isinstance(loc, dict):
        f = loc.get("file")
        if isinstance(f, str) and f:
            return f
        # An expansion of a macro carries spellingLoc/expansionLoc instead.
        for sub in ("expansionLoc", "spellingLoc"):
            s = loc.get(sub)
            if isinstance(s, dict):
                sf = s.get("file")
                if isinstance(sf, str) and sf:
                    return sf
    return current


def _node_line(node: dict[str, Any]) -> int:
    loc = node.get("loc")
    if isinstance(loc, dict):
        line = loc.get("line")
        if isinstance(line, int):
            return line
        exp = loc.get("expansionLoc")
        if isinstance(exp, dict):
            exp_line = exp.get("line")
            if isinstance(exp_line, int):
                return exp_line
    return 0


#: Single-child wrapper expression nodes to descend through before deciding
#: whether an initializer is a lone literal — so `42` reads as the literal "42"
#: while a compound expression is fingerprinted whole.
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


def _has_body(node: dict[str, Any]) -> bool:
    return any(
        isinstance(c, dict) and c.get("kind") == "CompoundStmt"
        for c in node.get("inner", [])
    )


def _unwrap_expr(node: dict[str, Any]) -> dict[str, Any]:
    """Descend through single-child wrapper expressions (casts, ConstantExpr…)."""
    cur = node
    while isinstance(cur, dict) and cur.get("kind") in _WRAPPER_EXPR_KINDS:
        raw_inner = cur.get("inner")
        inner = (
            [c for c in raw_inner if isinstance(c, dict)]
            if isinstance(raw_inner, list)
            else []
        )
        if len(inner) != 1:
            break
        cur = inner[0]
    return cur


def _init_expr(node: dict[str, Any]) -> dict[str, Any] | None:
    """The initializer expression child of a Var/Parm decl, or ``None``.

    A decl's ``inner`` holds attributes/nested decls plus, last, the initializer
    expression; pick the last child that is not itself a decl/attribute/comment.
    """
    candidates = [
        c
        for c in node.get("inner", [])
        if isinstance(c, dict)
        and not str(c.get("kind", "")).endswith(("Decl", "Attr", "Comment"))
    ]
    return candidates[-1] if candidates else None


def _expr_value(node: dict[str, Any]) -> str:
    """A value string that changes iff the whole initializer expression changes.

    A lone literal (after stripping wrapper casts) keeps its human-readable value
    (``42``); any compound expression (``1 + 2``, a call, a braced-init) is
    fingerprinted as a whole, so ``1 + 2`` and ``1 + 3`` are distinguished. The
    earlier "first literal under the AST" heuristic collapsed them and missed the
    change (Codex review #339, P2).
    """
    core = _unwrap_expr(node)
    if (
        isinstance(core, dict)
        and core.get("kind") in _LITERAL_NODE_KINDS
        and "value" in core
    ):
        return str(core["value"])
    return _subtree_hash(node)


def _default_arg_repr(node: dict[str, Any]) -> str:
    """Normalized default-argument string for a function's parameters.

    Each defaulted parameter is rendered ``p<position>=<value-or-fingerprint>`` so
    both presence and value changes surface. The *position* (not the parameter
    name) keys the entry, so a pure parameter rename keeping the same default —
    ``f(int x = 1)`` → ``f(int y = 1)`` — is not a change (callers that omit the
    argument get the same value). The value covers the *whole* default expression
    (not just its first literal), so ``1 + 2`` → ``1 + 3`` is detected (Codex
    review #339, P2).
    """
    parts: list[str] = []
    position = -1
    for child in node.get("inner", []):
        if not isinstance(child, dict) or child.get("kind") != "ParmVarDecl":
            continue
        position += 1
        init = _init_expr(child)
        if not child.get("init") and init is None:
            continue
        rep = _expr_value(init) if init is not None else "default"
        parts.append(f"p{position}={rep}")
    return ",".join(parts)


def _signature(node: dict[str, Any]) -> str:
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return str(type_obj.get("qualType", ""))
    return ""


def _signature_desugared(node: dict[str, Any]) -> str:
    """Return the node's ``desugaredQualType`` (the alias-resolved spelling).

    clang carries the sugared ``qualType`` (e.g. ``CI`` for ``using CI = const
    int``) and the resolved ``desugaredQualType`` (``const int``). Top-level-const
    detection must see through the alias, so the desugared form is consulted
    alongside the sugared one. Empty when clang emitted no desugared spelling.
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return str(type_obj.get("desugaredQualType", ""))
    return ""


def _mangled(node: dict[str, Any]) -> str:
    mangled = node.get("mangledName")
    name = node.get("name", "")
    if isinstance(mangled, str) and mangled and mangled != name:
        return (
            mangled[1:] if mangled.startswith("__Z") else mangled
        )  # macOS ABI underscore (Codex review)
    return ""


def _qualified(scope: list[str], name: str) -> str:
    return "::".join([*scope, name]) if scope else name


def _entity_names(name: str, mangled: str = "") -> dict[str, str]:
    names = {"source_qualified": name}
    if mangled:
        names["mangled"] = mangled
    return names


def _entity_ownership(visibility: str, origin: str) -> dict[str, str]:
    role = {
        "public_header": "own_api_candidate",
        "generated": "generated_api_candidate",
        "system_header": "dependency_candidate",
        "private_header": "internal_candidate",
    }.get(visibility, "unknown")
    return {"visibility": visibility, "origin": origin, "role": role}


def _template_param_name(node: dict[str, Any], position: int) -> str:
    name = str(node.get("name") or "")
    if name:
        return name
    kind = str(node.get("kind") or "")
    return (
        "N" + str(position)
        if kind == "NonTypeTemplateParmDecl"
        else "T" + str(position)
    )


def _template_params(node: dict[str, Any]) -> list[str]:
    params: list[str] = []
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        if child.get("kind") in (
            "TemplateTypeParmDecl",
            "NonTypeTemplateParmDecl",
            "TemplateTemplateParmDecl",
        ):
            params.append(_template_param_name(child, len(params)))
    return params
