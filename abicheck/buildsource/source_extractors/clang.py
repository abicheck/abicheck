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

"""Clang source ABI extractor (ADR-030 D3, phase 5).

This is the *source-based* L4 backend. It parses a translation unit under its
real per-TU build context (ADR-030 D2) with ``clang -Xclang -ast-dump=json`` and
emits the full public source surface — the JSON AST already carries declarations
and types, so a separate libclang/cindex backend is not needed (gap G4):

- public **declarations**: free functions/methods with their type-level
  signature and mangled name (→ ``reachable_declarations``);
- public **types**: records/enums/typedefs with a build-root-stable type hash
  (→ ``reachable_types``);
- inline function bodies (``inline_body_changed``);
- function/class **template** bodies, instantiated or not
  (``template_body_changed`` / ``uninstantiated_template_removed``);
- ``constexpr`` values (``constexpr_value_changed``);
- public default arguments (``default_argument_changed``);
- public macros (via a second ``-E -dD`` preprocess pass; ``public_macro_value_changed``).

**Requires clang.** Source ABI replay is the one tier that depends on a C++
front-end being present. When ``clang`` is not on ``PATH`` the extractor raises
:class:`SourceExtractionError`; callers record that as *partial L4 coverage*
(ADR-028 D7) and the artifact tiers (L0–L2) stay authoritative — abicheck never
aborts a comparison because the source tier is unavailable.

No new Python dependency is added (ADR-001): clang is an optional external tool,
discovered at runtime exactly like castxml. For a GCC-built project clang
replays the **GCC build's flags** (standard, defines, include paths, target,
sysroot) so it parses the same headers under the same macros; a TU using a
GCC-only extension clang rejects degrades to partial coverage rather than a hard
failure (ADR-030 Consequences).

The argv builder and the JSON-AST → :class:`SourceAbiTu` mapping are pure and
unit-tested without clang installed; only :meth:`ClangSourceExtractor.extract`
shells out (integration-marked).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...header_conditionals import _include_guard_macro, _strip_comments
from ..build_evidence import CompileUnit
from ..model import LayerConfidence
from ..source_abi import (
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    coverage_state_for_family,
    default_fact_set,
)
from ._argv import (
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
    resolve_read_files,
    split_public_roots,
    unredact_home,
)
from .base import SourceExtractionError

# Public-header-root equivalence + path classification was split into a leaf
# sibling module (``clang_public_roots``) to keep this file under the size cap.
# Re-exported here so ``from ...clang import NAME`` keeps working unchanged.
from .clang_public_roots import (  # noqa: F401
    _can_promote_whole_root,
    _compile_unit_include_dir,
    _compile_unit_include_roots,
    _dir_spelling,
    _equivalent_public_roots_for_unit,
    _file_segments,
    _header_samples,
    _include_spelling_base,
    _is_dot_include_root,
    _is_full_single_header_mirror,
    _looks_like_public_header,
    _matches_exact_public_header,
    _mirror_dir_candidate,
    _path_suffixes,
    _root_spelling,
    _strip_leading_sample_dir,
)

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8). Bump on ANY change to the
#: emitted-record recipe so a stale ``--cache-dir`` never silently reuses a dump
#: from an older recipe. 0.6: emit external-linkage ``variables``.
CLANG_EXTRACTOR_VERSION = "0.6"


@functools.lru_cache(maxsize=8)
def _clang_compiler_version(clang_bin: str) -> str:
    """``clang -dumpversion`` for *clang_bin*, cached (ADR-038 C.8 fact_set).

    Cached per binary path so a many-TU build pays this subprocess once, not
    once per compile unit. Best-effort: any failure yields ``""`` rather than
    aborting extraction (compiler_version is provenance, not required input).
    """
    try:
        r = subprocess.run(
            [clang_bin, "-dumpversion"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""

#: AST node kinds clang emits for the entities we fingerprint. Includes the C++
#: special members (constructor/destructor/conversion) so a change to a public
#: ``Widget(int n = 1)`` default, or an inline constructor body edit, is detected
#: — not just ordinary functions/methods (Codex review #339, P2).
_FUNCTION_NODE_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
_TEMPLATE_NODE_KINDS = frozenset({"FunctionTemplateDecl", "ClassTemplateDecl"})
#: Decl contexts we descend into to reach members/nested decls, tracking the
#: enclosing scope name so a member's qualified name is built (``ns::Cls::f``).
_SCOPE_NODE_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "ClassTemplateDecl", "LinkageSpecDecl"}
)
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


#: Header extensions that are typically *generated* by the build (TableGen `.inc`,
#: autotools/CMake `*.h.in` → `config.h`, protobuf/flatbuffers/moc outputs). When a
#: "file not found" names one of these, the real cause is "the target wasn't built".
#: Matches both clang ("'X' file not found") and gcc ("X: No such file or
#: directory") missing-include wording for a header-looking path.
_GENERATED_HEADER_RE = re.compile(
    r"fatal error:\s*['\"]?([^'\":\n]+\.(?:inc|def|h|hpp|hxx))['\"]?"
    r"\s*(?:file not found|: No such file or directory)",
    re.IGNORECASE,
)
_LIKELY_GENERATED_RE = re.compile(
    r"\.(inc|def)$|config\.h$|\.pb\.h$|moc_|\.generated\.", re.I
)


def _missing_generated_header_hint(stderr: str) -> str:
    """P19: turn a bare clang 'file not found' into an actionable build hint.

    L4 replay parses each TU under its real flags, but a *configure-only* tree has
    not produced its generated headers (TableGen ``*.inc``, ``config.h``, protobuf
    ``*.pb.h``…), so clang fails with a generic include error. Detect that shape and
    point the user at building the target first, rather than reporting an opaque
    parse failure. Returns ``""`` when the stderr is not a missing-header failure.
    """
    m = _GENERATED_HEADER_RE.search(stderr or "")
    if not m:
        return ""
    header = m.group(1)
    generated = bool(_LIKELY_GENERATED_RE.search(header))
    what = "generated header" if generated else "header"
    return (
        f"\n  hint: missing {what} '{header}'. L4 source replay needs the target's "
        "generated headers to exist — build the target (or its codegen step) first, "
        "then re-run; configure-only trees do not produce them."
    )


def _std_flag(standard: str, msvc: bool) -> list[str]:
    if not standard:
        return []
    return [f"/std:{standard}"] if msvc else [f"-std={standard}"]


def _clang_context_args(
    compile_unit: CompileUnit, compiler_binary: str | None
) -> tuple[list[str], bool]:
    """The shared compile-context argv prefix (no mode tail / source) and msvc flag.

    Mirrors the compile unit's language standard, defines/undefines, include and
    system-include paths, sysroot, target triple, and ABI-relevant flags, so both
    the AST pass and the macro pass parse the same TU the real build compiled.
    """
    cc_bin = pick_compiler_binary(compile_unit, compiler_binary)
    msvc = is_msvc_mode(cc_bin)
    cc_id = "msvc" if msvc else "gnu"

    cmd: list[str] = []
    if msvc:
        cmd.append("--driver-mode=cl")
    # Force the language so a header replayed directly still parses as C/C++.
    lang = "c++" if compile_unit.language.lower() in ("cxx", "c++", "cpp") else "c"
    if not msvc:
        cmd += ["-x", lang]
    cmd += _std_flag(compile_unit.standard, msvc)
    define_opt = "/D" if msvc else "-D"
    undef_opt = "/U" if msvc else "-U"
    for key, value in compile_unit.defines.items():
        cmd.append(f"{define_opt}{key}={value}" if value else f"{define_opt}{key}")
    for undef in compile_unit.undefines:
        cmd.append(f"{undef_opt}{undef}")
    inc_opt = "/I" if msvc else "-I"
    for inc in compile_unit.include_paths:
        cmd += [inc_opt, inc]
    for inc in compile_unit.system_include_paths:
        cmd += ["/I", inc] if msvc else ["-isystem", inc]
    if compile_unit.sysroot and not msvc:
        cmd.append(f"--sysroot={compile_unit.sysroot}")
    if compile_unit.target_triple and not msvc:
        cmd.append(f"--target={compile_unit.target_triple}")
    extra = replay_extra_flags(compile_unit, cmd, cc_id)
    if compile_unit.standard:
        # ``standard`` is normalized from the *effective* (last) dialect flag.
        # Do not replay an earlier conflicting -std=/std: token from
        # abi_relevant_flags after it, or clang would silently parse the TU at
        # the wrong language level (e.g. CMake's gnu++17 then c++20 pair).
        extra = [
            flag for flag in extra
            if not flag.startswith("-std=")
            and not flag.lower().startswith(("/std:", "-std:"))
        ]
    cmd += extra
    return cmd, msvc


def build_clang_command(
    compile_unit: CompileUnit,
    source: Path,
    *,
    clang_bin: str = "clang",
    compiler_binary: str | None = None,
) -> list[str]:
    """Build the ``clang -ast-dump=json`` argv for a compile unit's context (D2).

    A clang-cl/MSVC compile unit is driven through clang's ``cl`` driver mode.
    """
    cmd, _msvc = _clang_context_args(compile_unit, compiler_binary)
    # Syntax-only AST dump to stdout as JSON. -ferror-limit=0 keeps parsing past
    # recoverable errors so a single bad decl does not blank the whole dump.
    return [
        clang_bin,
        *cmd,
        "-fsyntax-only",
        "-ferror-limit=0",
        "-Xclang",
        "-ast-dump=json",
        str(source),
    ]


def build_clang_macro_command(
    compile_unit: CompileUnit,
    source: Path,
    *,
    clang_bin: str = "clang",
    compiler_binary: str | None = None,
) -> list[str]:
    """Build the ``clang -E -dD`` argv that dumps macro definitions (ADR-030 D6).

    The JSON AST carries no preprocessor macros, so a separate preprocess pass
    (``-E -dD``: emit ``#define`` directives with line markers) is needed for
    ``public_macro_value_changed`` to ever fire (Codex review #339, P2). Same
    compile context as the AST pass so the macro set matches the real build.
    """
    cmd, msvc = _clang_context_args(compile_unit, compiler_binary)
    # cl-driver mode ignores -dD; clang-cl's `/d1PP` is the documented "retain
    # macro definitions in /E mode" flag, so a Windows/clang-cl build still emits
    # #define directives for macros_from_preprocessor (Codex review #339, P2). We
    # keep the line markers (no -P / no /EP) to attribute each macro to its file.
    if msvc:
        preprocess = ["/E", "/d1PP"]
    else:
        preprocess = ["-E", "-dD"]
    return [clang_bin, *cmd, *preprocess, "-ferror-limit=0", str(source)]


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
        inner = [c for c in cur.get("inner", []) if isinstance(c, dict)]
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
        return mangled
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


class _ClassifyContext:
    """Public-surface classification for clang file paths (ADR-024 / ADR-030)."""

    def __init__(self, public_header_roots: list[str]) -> None:
        from ...provenance import build_public_set

        # A public root may be a *directory* (`--headers include/`). Feeding it to
        # build_public_set as a header file would never match a decl under it
        # (`include` vs `include/api.h`), dropping the whole public include tree;
        # split file roots from directory roots first (Codex review #339, P2).
        file_roots, dir_roots = split_public_roots(public_header_roots)
        self.exact_header_segs = [_file_segments(root) for root in file_roots]
        self.exact_header_segs = [seg for seg in self.exact_header_segs if seg]
        _, self.dir_segs, self.have_set = build_public_set([], dir_roots)
        self.have_set = self.have_set or bool(self.exact_header_segs)

    def classify(self, file: str) -> tuple[str, str, bool]:
        """Return ``(visibility, origin_label, api_relevant)`` for a file.

        Mirrors the castxml extractor: a header that is both public and generated
        stays public but is marked ``GENERATED`` (so ``generated_header_changed``
        owns it); a generated *private* header (not in the public set) is demoted
        off the public surface.
        """
        from ...model import ScopeOrigin
        from ...provenance import classify_origin, is_generated_header

        if _matches_exact_public_header(file, self.exact_header_segs):
            if is_generated_header(file):
                return "generated", "GENERATED", True
            return "public_header", "PUBLIC_HEADER", True
        origin = classify_origin(file, [], self.dir_segs, have_public_set=self.have_set)
        if origin == ScopeOrigin.PUBLIC_HEADER and is_generated_header(file):
            return "generated", "GENERATED", True
        if origin == ScopeOrigin.PUBLIC_HEADER:
            return "public_header", "PUBLIC_HEADER", True
        if origin == ScopeOrigin.GENERATED:
            return "private_header", "PRIVATE_HEADER", False
        return "unknown", "UNKNOWN", False


#: A ``-E`` line marker: ``# <line> "<file>" [flags]`` — sets the current file.
_LINE_MARKER_RE = re.compile(r'^#\s+\d+\s+"([^"]*)"')
#: A C identifier (a macro name).
_MACRO_NAME_RE = re.compile(r"[A-Za-z_]\w*")


def _parse_define(rest: str) -> tuple[str, str] | None:
    """Parse the text after ``#define `` into ``(name, normalized-value)``.

    Keeps the function-like parameter list as part of the value (``(a,b) body``),
    so a change to either the parameters or the body reads as a value change.
    """
    m = _MACRO_NAME_RE.match(rest)
    if not m:
        return None
    name = m.group(0)
    i = m.end()
    params = ""
    if i < len(rest) and rest[i] == "(":  # function-like macro
        depth = 0
        j = i
        while j < len(rest):
            if rest[j] == "(":
                depth += 1
            elif rest[j] == ")":
                depth -= 1
            j += 1
            if depth == 0:
                break
        params = rest[i:j]
        i = j
    body = rest[i:].strip()
    value = re.sub(r"\s+", " ", f"{params} {body}".strip())
    return name, value


def _is_include_guard(name: str, value: str, file: str) -> bool:
    """Whether ``name`` is the include guard of ``file`` (ADR-030 follow-up #2).

    Include guards (``#ifndef FOO_H`` / ``#define FOO_H``) surface from the
    ``-E -dD`` pass as empty-valued macro entities — harmless but noisy. Both
    checks below require an empty replacement (a guard never expands to
    anything), which keeps a real empty feature flag (e.g.
    ``#define FOO_ENABLED``) from being dropped:

    - **Filename-derived** (cheap, no I/O): the name, with surrounding
      underscores stripped, equals the header's filename-derived token
      including the extension suffix (``foo.h`` → ``FOO_H``; matches
      ``FOO_H``, ``_FOO_H``, ``FOO_H_``, ``__FOO_H__``). Exact match, not a
      substring, so an intentional empty feature macro that merely starts
      with the stem (``FOO_H_FEATURE``) is not dropped.
    - **Structural** (fallback): a project-prefixed guard
      (``MYLIB_FOO_H``, ``CASE47_V1_HPP``) doesn't derive from the filename
      at all, so the preprocessed ``-E -dD`` stream's spelling-only signal
      misses it. Reading the file's own source and checking whether ``name``
      is genuinely its leading ``#ifndef``/``#define`` pair (the standard
      whole-file guard idiom, see :func:`_include_guard_macro`) catches this
      regardless of naming convention. Best-effort: a read failure just skips
      to the false-negative default below.
    """
    if value or not file:
        return False
    base = re.split(r"[\\/]", file)[-1]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", base).upper().strip("_")  # foo.h -> FOO_H
    if stem and name.upper().strip("_") == stem:
        return True
    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = _strip_comments(text).splitlines()
    return _include_guard_macro(lines) == name


def _unfold_continuations(lines: list[str]) -> list[str]:
    """Join backslash-continued physical lines into single logical lines.

    A multi-line macro (``#define FOO(x) \\`` then its body) is split by
    ``splitlines()``; without unfolding, only the first physical line — usually
    ending in ``\\`` — is captured and the rest of the body is dropped, hiding
    any edit below the first line from ``public_macro_value_changed`` (CodeRabbit
    review). ``#`` line markers never carry a trailing backslash, so they are
    unaffected.
    """
    out: list[str] = []
    pending: str | None = None
    for line in lines:
        chunk = line[:-1] if line.endswith("\\") else line
        pending = chunk if pending is None else pending + " " + chunk.lstrip()
        if not line.endswith("\\"):
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


def macros_from_preprocessor(
    text: str, public_header_roots: list[str]
) -> tuple[list[SourceEntity], list[str]]:
    """Parse ``clang -E -dD`` output into public-header macro entities (ADR-030 D6).

    Pure: tracks the current file from ``#`` line markers, records the final
    definition of each macro (honoring later ``#undef``), and keeps only macros
    whose declaring file is on the public source surface — builtin/command-line
    and system macros carry ``<built-in>``/system files and are filtered out.

    Returns ``(macro entities, every real file the preprocessor read)``. The file
    list feeds the per-TU cache dependency set, so it must contain *all* files
    the preprocessor touched — not just the public macro-declaring ones — or a
    macro-only *private* header (e.g. ``detail/config.h`` whose ``#define`` gates
    an ``#if`` in a public header) would never invalidate the dump: it
    contributes no public macro entity and no clang AST node, so an edit to it
    would otherwise pass cache validation and reuse stale facts (Codex review
    #339, P2; P1 covered only the public ones).
    """
    ctx = _ClassifyContext(public_header_roots)
    current = ""
    defs: dict[str, tuple[str, str]] = {}  # name -> (value, file)
    # Every real file named by a `#` line marker — the complete set the
    # preprocessor read. `<built-in>`/`<command line>`/`<scratch space>`
    # pseudo-files are not real dependencies and are skipped.
    touched: set[str] = set()
    for line in _unfold_continuations(text.splitlines()):
        marker = _LINE_MARKER_RE.match(line)
        if marker:
            current = marker.group(1)
            if current and not current.startswith("<"):
                touched.add(current)
            continue
        if line.startswith("#define "):
            parsed = _parse_define(line[len("#define ") :])
            if parsed:
                defs[parsed[0]] = (parsed[1], current)
        elif line.startswith("#undef "):
            defs.pop(line[len("#undef ") :].strip(), None)

    entities: list[SourceEntity] = []
    for name, (value, file) in sorted(defs.items()):
        visibility, origin, public = ctx.classify(file)
        if not public:
            continue
        if _is_include_guard(name, value, file):
            continue
        entities.append(
            SourceEntity(
                id=_hash("macro", name, value),
                kind="macro",
                qualified_name=name,
                value=value,
                names=_entity_names(name),
                ownership=_entity_ownership(visibility, origin),
                source_location=_location(file, 0, origin),
                visibility=visibility,
                api_relevant=True,
                confidence=LayerConfidence.HIGH,
            )
        )
    return entities, sorted(touched)


def source_abi_from_clang_ast(
    ast_root: dict[str, Any],
    compile_unit: CompileUnit,
    public_header_roots: list[str],
    target_id: str,
    *,
    diagnostics: list[str] | None = None,
) -> SourceAbiTu:
    """Map a clang JSON AST root to a normalized :class:`SourceAbiTu` (D4).

    Pure: any producer of the clang AST JSON (the extractor below, or a fixture
    in a test) reuses this. Emits only public-surface entities so the linker does
    not have to filter private/system decls.
    """
    ctx = _ClassifyContext(public_header_roots)
    tu = SourceAbiTu(
        tu_id=compile_unit.id,
        target_id=target_id or compile_unit.target_id,
        extractor={"name": "clang-source", "version": CLANG_EXTRACTOR_VERSION},
        compile_context_hash=_hash(
            "ctx",
            compile_unit.standard,
            compile_unit.target_triple,
            compile_unit.sysroot or "",
            ",".join(f"{k}={v}" for k, v in sorted(compile_unit.defines.items())),
            ",".join(compile_unit.include_paths),
        ),
        source=compile_unit.source,
        public_header_roots=list(public_header_roots),
        diagnostics=list(diagnostics or []),
    )
    _walk(ast_root, ctx, tu, scope=[], current_file="")
    # Record every file that contributed a node, so the per-TU cache (D8)
    # invalidates on an edit to any transitively included header — not just the
    # configured public roots (Codex review #339, P1). Resolve to absolute paths
    # against the TU's build directory: clang emits *relative* paths for headers
    # found via relative -I, which the cache (running in a different CWD) could
    # not otherwise read, silently dropping the dependency (Codex review, P2).
    tu.read_files = resolve_read_files(_collect_files(ast_root), compile_unit.directory)
    return tu


def _collect_files(node: Any, files: set[str] | None = None) -> set[str]:
    """Every distinct file path referenced anywhere in the clang AST.

    clang's ``file`` field is sticky (omitted when unchanged from the prior node
    in source order), so each file it parsed is named at least once at its first
    contributing node; the set of explicit mentions is the read-file set.
    """
    if files is None:
        files = set()
    if isinstance(node, dict):
        loc = node.get("loc")
        if isinstance(loc, dict):
            for key in ("file", "expansionLoc", "spellingLoc", "includedFrom"):
                val = loc.get(key)
                if isinstance(val, str) and val:
                    files.add(val)
                elif isinstance(val, dict) and isinstance(val.get("file"), str):
                    files.add(val["file"])
        for child in node.get("inner", []):
            _collect_files(child, files)
    return files


#: C++ access specifiers that hide a member from consumers. A private/protected
#: member cannot be called or its inline body relied on, so it must stay off the
#: L4 public surface even when declared in a public header (Codex review #339,
#: P2). ``""``/``"none"``/``"public"`` mean "no restriction" (free functions,
#: namespace-scope decls, public members).
_NON_PUBLIC_ACCESS = frozenset({"private", "protected"})


def _is_accessible(access: str) -> bool:
    """Whether a decl with this C++ member-access is reachable by consumers."""
    return access not in _NON_PUBLIC_ACCESS


def _default_member_access(record: dict[str, Any]) -> str:
    """Default member access for a record's body before any ``AccessSpecDecl``.

    ``class`` defaults to private; ``struct``/``union`` default to public
    (clang records this as ``tagUsed``). Determines the access of members that
    appear before the first explicit ``public:``/``private:`` section.
    """
    return "private" if record.get("tagUsed") == "class" else "public"


def _is_template_node(kind: str | None, name: str) -> bool:
    """Return ``True`` when this AST node is a named template declaration.

    Template bodies are fingerprinted whole; callers must skip descent into
    the templated pattern to avoid re-emitting the inner FunctionDecl/Record.
    """
    return kind in _TEMPLATE_NODE_KINDS and bool(name)


def _is_function_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible function/method node."""
    return kind in _FUNCTION_NODE_KINDS and bool(name) and accessible


def _is_constexpr_var_node(
    kind: str | None, name: str, node: dict[str, Any], accessible: bool
) -> bool:
    """Return ``True`` for a named, accessible ``constexpr`` variable node."""
    return (
        kind == "VarDecl" and bool(name) and bool(node.get("constexpr")) and accessible
    )


#: Decl containers whose direct ``VarDecl`` children have linkage (become real
#: symbols). A ``VarDecl`` reached anywhere else in the walk — inside a function
#: body's ``CompoundStmt``/``DeclStmt`` — is a stack local with no symbol and is
#: never an ABI variable (``TranslationUnitDecl`` is the walk root's own kind).
_VARIABLE_SCOPE_KINDS = frozenset(
    {"TranslationUnitDecl", "NamespaceDecl", "CXXRecordDecl", "LinkageSpecDecl"}
)


def _mangled_has_internal_linkage(mangled: str) -> bool:
    """Return ``True`` when an Itanium *mangled* name marks internal linkage.

    clang has already done the linkage analysis and encoded it two ways, both
    handled here:

    * the GCC/clang seniority marker ``L`` prefixing the entity's own
      ``<unqualified-name>`` — a namespace/file-scope ``static`` *or* a
      namespace-scope ``const`` without ``extern`` (``_ZN2nsL7g_constE``,
      ``_ZL1xE``);
    * an **anonymous-namespace** component ``_GLOBAL__N_`` (``namespace { int x; }``
      mangles as ``_ZN12_GLOBAL__N_11xE``) — internal linkage with *no* ``L``
      marker (Codex review).

    Such an entity never appears in the dynamic symbol table, so emitting it as a
    ``variable`` would populate ``decls_without_symbol`` and risk a spurious
    ``source_binary_provenance_mismatch`` against the correct binary. This parses
    by Itanium length prefixes — so an ``L`` *inside* a source name (a namespace
    literally ending in ``L``) is never miscounted — and bails to ``False``
    (external, keep) on any exotic production, so a real export is never dropped.
    """
    m = mangled
    if not m.startswith("_Z"):
        return False
    # Anonymous namespace: a reserved compiler component, so a plain substring
    # test is unambiguous (a user cannot name an entity `_GLOBAL__N_`).
    if "_GLOBAL__N_" in m:
        return True
    i = 2
    n = len(m)
    if i < n and m[i] == "N":  # nested-name: N [CV/ref] <prefix> <name> E
        i += 1
        while i < n and m[i] in "rVKO":  # cv-qualifiers / ref-qualifiers
            i += 1
    while i < n:
        c = m[i]
        if c == "E":
            break
        if c == "L":  # seniority marker before a <source-name> ⇒ internal linkage
            return i + 1 < n and m[i + 1].isdigit()
        if c.isdigit():  # <source-name> ::= <length> <identifier> — skip wholesale
            j = i
            length = 0
            while j < n and m[j].isdigit():
                length = length * 10 + (ord(m[j]) - 48)
                j += 1
                if length > n - j:  # malformed length ⇒ bail safe
                    return False
            i = j + length
            continue
        return False  # template/substitution/other production ⇒ treat as external
    return False


def _is_top_level_const(qual: str) -> bool:
    """Return ``True`` when a type spelling is const-qualified at the top level.

    Top-level const (``const int``, ``ns::Foo const``, ``int *const``) marks the
    *object* itself const — which at namespace scope without ``extern`` gives
    internal linkage in C++. An **array** is const iff its element type is const,
    so it is reduced to that element type (``const int[2]`` / ``const char[8]``
    are internal; an array of mutable pointers ``const char *[2]`` is not).
    Pointer/reference-to-const (``const char *``) is NOT top-level const (the
    pointer object is mutable, external), so a leading ``const`` only counts when
    the reduced element type is not a pointer.
    """
    q = qual.strip().rstrip("&").strip()
    # Array: reduce to the element type (spelled before the first ``[``).
    bracket = q.find("[")
    if bracket != -1:
        q = q[:bracket].strip()
    # Trailing ``const`` must be a standalone token, not the tail of an identifier
    # (a type named ``almost_const`` is not const-qualified). ``int *const`` /
    # ``ns::Foo const`` qualify; the char before ``const`` is then a non-identifier
    # character (space / ``*``) or the string is exactly ``const``.
    if q.endswith("const") and (len(q) == 5 or not (q[-6].isalnum() or q[-6] == "_")):
        return True
    return q.startswith("const ") and "*" not in q


def _is_variable_node(
    kind: str | None,
    name: str,
    node: dict[str, Any],
    accessible: bool,
    enclosing_kind: str | None,
) -> bool:
    """Return ``True`` for a named, accessible **externally-linked** data variable.

    These are the globals and static data members that become exported ``OBJECT``
    symbols (e.g. ``llvm::raw_ostream::RED``), so capturing them lets a binary
    data export map back to a source declaration (ADR-030 D4). ``constexpr``
    variables are handled by ``_emit_constexpr`` and excluded here.

    A block-scope local (enclosing kind is a statement, not a decl container) has
    no linkage and is dropped. Internal-linkage variables never appear in the
    dynamic symbol table, so they are dropped two ways: (1) the mangled-name
    marker — a C++ *namespace/file-scope* ``static``, a namespace-scope ``const``
    without ``extern``, or an anonymous-namespace variable all carry it; and
    (2) an explicit ``storageClass == "static"`` filter for the case clang gives
    **no** mangled name (a C / ``extern "C"`` file-scope ``static``, whose
    ``mangledName`` is absent or equals ``name``), which (1) cannot see. A
    ``static`` **data member** (enclosing ``CXXRecordDecl``) has external linkage
    and is kept by both. The mangled name is clang's own linkage verdict, so this
    stays authoritative without re-deriving C++ linkage rules.
    """
    if kind != "VarDecl" or not name or not accessible:
        return False
    if node.get("constexpr"):
        return False
    if enclosing_kind not in _VARIABLE_SCOPE_KINDS:
        return False
    mangled = _mangled(node)
    if _mangled_has_internal_linkage(mangled):
        return False
    # C / extern "C" file-scope static: no mangled name carries the marker, so
    # filter on storageClass directly. A static data member is external, so keep
    # it (its enclosing kind is a record).
    if node.get("storageClass") == "static" and enclosing_kind != "CXXRecordDecl":
        return False
    # MSVC / clang-cl mangling (`?name@...`) has no Itanium internal-linkage
    # marker, so a namespace-scope top-level `const` without `extern` — internal
    # linkage in C++ — would slip through the marker check. Fall back to the
    # language rule from the type (Codex review). A static data member is
    # external (enclosing record), and `extern` overrides, so both are excluded.
    if (
        mangled.startswith("?")
        and enclosing_kind != "CXXRecordDecl"
        and node.get("storageClass") != "extern"
        and not node.get("inline")
        and (
            _is_top_level_const(_signature(node))
            or _is_top_level_const(_signature_desugared(node))
        )
    ):
        return False
    return True


def _is_type_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible record or enum declaration."""
    return kind in ("CXXRecordDecl", "EnumDecl") and bool(name) and accessible


def _is_typedef_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible typedef or type-alias declaration."""
    return kind in ("TypedefDecl", "TypeAliasDecl") and bool(name) and accessible


def _is_concept_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible C++20 concept declaration."""
    return kind == "ConceptDecl" and bool(name) and accessible


def _emit_node(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
    kind: str | None,
    name: str,
    accessible: bool,
    enclosing_kind: str | None = None,
) -> bool:
    """Dispatch a single AST node to the appropriate emit helper.

    Returns ``True`` when the node is a template kind (caller must skip
    descent into the templated pattern to avoid duplicate emissions).
    """
    if _is_template_node(kind, name):
        # The template's body is captured whole in its fingerprint; do not
        # descend into the templated pattern, or its inner FunctionDecl/Record
        # would be re-emitted as a duplicate non-template entity.
        if accessible:
            _emit_template(node, ctx, tu, scope, file)
        return True
    if _is_concept_node(kind, name, accessible):
        _emit_concept(node, ctx, tu, scope, file)
    elif _is_function_node(kind, name, accessible):
        _emit_function(node, ctx, tu, scope, file)
    elif _is_constexpr_var_node(kind, name, node, accessible):
        _emit_constexpr(node, ctx, tu, scope, file)
    elif _is_variable_node(kind, name, node, accessible, enclosing_kind):
        _emit_variable(node, ctx, tu, scope, file)
    elif _is_type_node(kind, name, accessible):
        _emit_type(node, ctx, tu, scope, file)
    elif _is_typedef_node(kind, name, accessible):
        _emit_typedef(node, ctx, tu, scope, file)
    return False


def _child_scope(scope: list[str], kind: str | None, name: str) -> list[str]:
    """Extend the scope stack when descending into a namespace or record."""
    if kind in _SCOPE_NODE_KINDS and name:
        return [*scope, name]
    return scope


def _initial_running_access(
    accessible: bool, kind: str | None, node: dict[str, Any], access: str
) -> str:
    """Compute the initial ``running_access`` for iterating a node's children.

    - Non-accessible subtree: preserve the inherited access so the whole subtree
      stays hidden wholesale.
    - ``CXXRecordDecl``: open with the tag's default (``class`` → private,
      ``struct``/``union`` → public).
    - Everything else (namespace, linkage spec, TU): no restriction → ``"public"``.
    """
    if not accessible:
        return access
    if kind == "CXXRecordDecl":
        return _default_member_access(node)
    return "public"


def _walk_children(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    *,
    child_scope: list[str],
    file: str,
    accessible: bool,
    running_access: str,
    enclosing_kind: str | None = None,
) -> str:
    """Iterate a node's ``inner`` list, threading the sticky ``file`` forward.

    Handles ``AccessSpecDecl`` sections that switch the running C++ access for
    subsequent siblings. Returns the last file seen in any child's subtree.
    ``enclosing_kind`` is this node's kind, forwarded so a child ``VarDecl`` knows
    whether it is a static data member (``CXXRecordDecl`` parent → external
    linkage) vs a namespace-scope ``static`` (internal linkage).
    """
    for child in node.get("inner", []):
        if not isinstance(child, dict):
            continue
        if accessible and child.get("kind") == "AccessSpecDecl":
            # `public:` / `private:` / `protected:` switches the running access
            # for subsequent siblings in this record body.
            running_access = child.get("access", running_access)
            continue
        # Thread the last file seen in each child's subtree forward so the next
        # sibling inherits it (clang's sticky loc.file). Honor an explicit
        # per-decl `access` when clang emits one, else the running section access.
        file = _walk(
            child,
            ctx,
            tu,
            scope=child_scope,
            current_file=file,
            access=child.get("access", running_access),
            enclosing_kind=enclosing_kind,
        )
    return file


def _walk(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    *,
    scope: list[str],
    current_file: str,
    access: str = "public",
    enclosing_kind: str | None = None,
) -> str:
    """Pre-order traversal that emits public entities, tracking file + scope.

    Returns the last file seen anywhere in this node's subtree. clang's
    ``loc.file`` is sticky (omitted when unchanged from the previous node in
    source order), so the last file a child's *subtree* saw must flow to the next
    sibling — otherwise a sibling that omits ``loc.file`` after a nested file
    switch is attributed to the wrong header, flipping public/private
    classification (CodeRabbit review).

    ``access`` is the C++ member access that applies to ``node`` (established by
    the enclosing record's default + ``AccessSpecDecl`` sections, or carried on
    the node itself in newer clang). A private/protected member is never emitted
    and its whole subtree stays non-public, matching the castxml path (Codex
    review #339, P2).
    """
    if not isinstance(node, dict):
        return current_file
    file = _node_file(node, current_file)
    kind = node.get("kind")
    name = node.get("name", "") or ""
    accessible = _is_accessible(access)

    is_template = _emit_node(
        node, ctx, tu, scope, file, kind, name, accessible, enclosing_kind
    )
    if is_template:
        return file

    return _walk_children(
        node,
        ctx,
        tu,
        child_scope=_child_scope(scope, kind, name),
        file=file,
        accessible=accessible,
        running_access=_initial_running_access(accessible, kind, node, access),
        enclosing_kind=kind,
    )


def _location(file: str, line: int, origin_label: str) -> SourceLocation:
    return SourceLocation(path=file, line=line, origin=origin_label)


def _emit_concept(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    constraint_hash = _subtree_hash(node)
    loc = _location(file, _node_line(node), origin)
    tu.functions.append(
        SourceEntity(
            id=_hash("concept", name, constraint_hash),
            kind="concept",
            qualified_name=name,
            signature_hash=_hash("concept-signature", name),
            body_hash=constraint_hash,
            value=constraint_hash,
            names=_entity_names(name),
            ownership=_entity_ownership(visibility, origin),
            source_location=loc,
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )


def _emit_function(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
    *,
    emit_inline_body: bool = True,
) -> None:
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    sig = _signature(node)
    mangled = _mangled(node)
    loc = _location(file, _node_line(node), origin)
    relations = {
        str(k): str(v) for k, v in dict(node.get("_abicheck_relations", {})).items()
    }
    # A function entity always carries the signature + default-argument value so
    # default_argument_changed fires; a body present in a public header
    # additionally yields an inline-body fingerprint for inline_body_changed.
    tu.functions.append(
        SourceEntity(
            id=_hash("function", mangled or name, sig),
            kind="function",
            qualified_name=name,
            mangled_name=mangled,
            signature_hash=_hash("sig", sig),
            value=_default_arg_repr(node),
            names=_entity_names(name, mangled),
            relations=relations,
            ownership=_entity_ownership(visibility, origin),
            source_location=loc,
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )
    # Any function/method *defined* in a public header (it has a CompoundStmt
    # body) ships that body to consumers — whether explicitly inline/constexpr,
    # an in-class member (implicitly inline, no `inline` key in clang's JSON), or
    # a header out-of-line definition. Fingerprint the body whenever one is
    # present, so an implicitly-inline method body change fires inline_body_changed
    # (Codex review #339, P2).
    if emit_inline_body and _has_body(node):
        body = next(
            c
            for c in node["inner"]
            if isinstance(c, dict) and c.get("kind") == "CompoundStmt"
        )
        tu.inline_bodies.append(
            SourceEntity(
                id=_hash("inline", mangled or name, sig),
                kind="inline",
                qualified_name=name,
                mangled_name=mangled,
                signature_hash=_hash("sig", sig),
                names=_entity_names(name, mangled),
                relations=relations,
                ownership=_entity_ownership(visibility, origin),
                # Alpha-rename the function's parameters together with the body so
                # a parameter rename does not flip the fingerprint (ADR-030 #6).
                body_hash=_subtree_hash(body, _param_ids(node)),
                source_location=loc,
                visibility=visibility,
                api_relevant=True,
                confidence=LayerConfidence.HIGH,
            )
        )


def _emit_template(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    params = _template_params(node)
    tu.templates.append(
        SourceEntity(
            id=_hash("template", name),
            kind="template",
            qualified_name=name,
            body_hash=_subtree_hash(node),
            names=_entity_names(name),
            relations={
                "template_kind": str(node.get("kind", "")),
                "template_parameters": ",".join(params),
            },
            ownership=_entity_ownership(visibility, origin),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )
    _emit_class_template_member_patterns(node, ctx, tu, scope, file)


def _emit_class_template_member_patterns(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    """Emit public member-function patterns from a class template declaration.

    The template entity records the whole template body, but the source↔binary
    linker also needs method-shaped declarations such as ``Box<T>::get`` to
    attribute concrete instantiations like ``Box<int>::get()``. Without this
    evidence the matcher must leave ordinary template methods unmatched.
    """
    if node.get("kind") != "ClassTemplateDecl":
        return
    name = str(node.get("name", "") or "")
    params = _template_params(node)
    owner = f"{name}<{','.join(params)}>" if params else name
    record = next(
        (
            child
            for child in node.get("inner", []) or []
            if isinstance(child, dict) and child.get("kind") == "CXXRecordDecl"
        ),
        None,
    )
    if record is None:
        return
    owner_scope = [*scope, owner]
    running_access = _default_member_access(record)
    for child in record.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        if child.get("kind") == "AccessSpecDecl":
            running_access = child.get("access", running_access)
            continue
        child_access = child.get("access", running_access)
        kind = child.get("kind")
        name = str(child.get("name", "") or "")
        if _is_function_node(kind, name, _is_accessible(child_access)):
            child_file = _node_file(child, file)
            child["_abicheck_relations"] = {
                "template_owner": _qualified(scope, owner),
                "template_parameters": ",".join(params),
                "declaration_role": "class_template_member_pattern",
            }
            _emit_function(
                child,
                ctx,
                tu,
                owner_scope,
                child_file,
                emit_inline_body=False,
            )


def _emit_constexpr(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    init = _init_expr(node)
    value = _expr_value(init) if init is not None else _subtree_hash(node)
    mangled = _mangled(node)
    tu.constexpr_values.append(
        SourceEntity(
            id=_hash("constexpr", name, value),
            kind="constexpr",
            qualified_name=name,
            mangled_name=mangled,
            value=value,
            names=_entity_names(name, mangled),
            ownership=_entity_ownership(visibility, origin),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )


def _emit_variable(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    """Emit an external-linkage data variable / static data member entity.

    Mirrors the castxml ``entity_from_variable`` recipe (``base.py``): identity is
    keyed on the mangled name (or qualified name) and the variable's type, so a
    binary ``OBJECT`` export links to it by mangled name and ``variable_type_changed``
    can fire on a type change.
    """
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    type_repr = _signature(node)
    mangled = _mangled(node)
    tu.variables.append(
        SourceEntity(
            id=_hash("variable", mangled or name, type_repr),
            kind="variable",
            qualified_name=name,
            mangled_name=mangled,
            type_hash=_hash("type", type_repr),
            names=_entity_names(name, mangled),
            ownership=_entity_ownership(visibility, origin),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )


def _emit_type(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    # Only definitions (a record with members / an enum with constants) carry a
    # meaningful type hash; a forward declaration has no `inner`, so skip it to
    # avoid a false same-name/empty-hash ODR signal.
    if not node.get("inner"):
        return
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    kind = "record" if node.get("kind") == "CXXRecordDecl" else "enum"
    tu.types.append(
        SourceEntity(
            id=_hash("type", name),
            kind=kind,
            qualified_name=name,
            type_hash=_subtree_hash(node),
            names=_entity_names(name),
            ownership=_entity_ownership(visibility, origin),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )


def _typedef_underlying(node: dict[str, Any]) -> str:
    """The underlying type a typedef/alias resolves to, build-root-stable.

    clang records the aliased spelling in ``type.qualType`` — the same key the
    rest of this extractor reads for signatures (``typedef int32_t handle_t;`` →
    ``"int32_t"`` as written). The written spelling is what matters for a
    source/API change, so use it verbatim; fall back to ``desugaredQualType``
    only when the spelling is absent.
    """
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    return str(type_obj.get("qualType") or type_obj.get("desugaredQualType") or "")


def _emit_typedef(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    """Emit a public typedef/alias entity so a target change is detectable (D6).

    A bare typedef leaves no exported symbol of its own, so an underlying-type
    change is invisible to L0/L1 unless some other declaration's signature
    happens to spell it. Recording the alias and its underlying type lets the
    source diff flag ``public_typedef_target_changed`` (ADR-030 follow-up #3).
    """
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    underlying = _typedef_underlying(node)
    if not underlying:
        return
    name = _qualified(scope, str(node.get("name", "")))
    tu.types.append(
        SourceEntity(
            id=_hash("typedef", name, underlying),
            kind="typedef",
            qualified_name=name,
            type_hash=_hash("typedef-target", underlying),
            value=underlying,
            names=_entity_names(name),
            ownership=_entity_ownership(visibility, origin),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )


class ClangSourceExtractor:
    """Produce a :class:`SourceAbiTu` from one compile unit via clang (D3, phase 5).

    Requires ``clang`` on ``PATH``; :meth:`extract` raises
    :class:`SourceExtractionError` otherwise, which callers record as partial L4
    coverage (ADR-028 D7) without aborting the artifact comparison.
    """

    name = "clang-source"
    version = CLANG_EXTRACTOR_VERSION

    def __init__(
        self,
        *,
        clang_bin: str = "clang",
        compiler_binary: str | None = None,
        timeout: int = 180,
    ) -> None:
        self.clang_bin = clang_bin
        self.compiler_binary = compiler_binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def effective_public_header_roots_for_cache(
        self, compile_unit: CompileUnit, public_header_roots: list[str]
    ) -> list[str]:
        return _equivalent_public_roots_for_unit(
            public_header_roots,
            compile_unit,
            for_cache=True,
            compiler_binary=self.compiler_binary,
        )

    def extract(
        self,
        compile_unit: CompileUnit,
        *,
        public_header_roots: list[str],
        target_id: str = "",
    ) -> SourceAbiTu:
        if not self.available():
            raise SourceExtractionError(
                f"{self.clang_bin} not found in PATH; source ABI replay (L4) requires "
                "clang. Install clang to enable source-only checks (macros, default "
                "arguments, inline/template/constexpr bodies), or omit --source-abi."
            )
        directory = unredact_home(compile_unit.directory)
        source = Path(unredact_home(compile_unit.source))
        if not source.is_absolute() and directory:
            source = Path(directory) / source

        ast_cmd = build_clang_command(
            compile_unit,
            source,
            clang_bin=self.clang_bin,
            compiler_binary=self.compiler_binary,
        )
        # Spill the (potentially multi-GiB) JSON AST to a temp file rather than
        # capturing it into a Python str. json.load still reads it back to parse, so
        # one TU's parse peak is unchanged — the win is that N concurrent workers
        # keep their AST payloads on disk (not heap) until each parses, so the
        # GIL-serialized thread pool stops stacking N giant strings (the UXL OOM).
        ast_path, ast_stderr, ast_rc = self._run_ast_to_file(
            ast_cmd, directory, compile_unit.source
        )
        try:
            if ast_path.stat().st_size == 0:
                raise SourceExtractionError(
                    f"clang produced no AST for {compile_unit.source} "
                    f"(exit {ast_rc}): {ast_stderr[:1000]}"
                    + _missing_generated_header_hint(ast_stderr)
                )
            try:
                with open(ast_path, "rb") as fh:  # bytes: json detects encoding
                    ast_root = json.load(fh)
            except ValueError as exc:
                raise SourceExtractionError(
                    f"clang AST for {compile_unit.source} was not valid JSON: {exc}"
                ) from exc
        finally:
            ast_path.unlink(missing_ok=True)
        # A non-zero exit with usable JSON means clang recovered from some errors;
        # record it as a diagnostic (partial coverage) rather than discarding the
        # dump (ADR-028 D7).
        diags: list[str] = []
        if ast_rc != 0:
            diags.append(
                f"clang exited {ast_rc} (recovered): {ast_stderr[:300]}"
                + _missing_generated_header_hint(ast_stderr)
            )
        effective_public_roots = _equivalent_public_roots_for_unit(
            public_header_roots, compile_unit, compiler_binary=self.compiler_binary
        )
        tu = source_abi_from_clang_ast(
            ast_root,
            compile_unit,
            effective_public_roots,
            target_id,
            diagnostics=diags,
        )
        # Drop the large AST tree before the macro pass spawns another subprocess,
        # so its memory is reclaimed and doesn't stack with the macro pass.
        del ast_root
        self._attach_macros(tu, compile_unit, source, directory, effective_public_roots)
        self._stamp_fact_set_and_coverage(tu)
        return tu

    def _stamp_fact_set_and_coverage(self, tu: SourceAbiTu) -> None:
        """ADR-038 C.8: record this TU's canonical fact-set identity + per-family
        coverage. Always attempts every family (no user-selectable mode) — a
        family is ``unsupported`` only when this extractor structurally never
        collects it (``source_edges``), never because of a flag.
        """
        # A non-zero *recovered* AST exit is TU-wide (clang.py has no per-family
        # granularity, unlike the plugin's per-decl JSON-dump diagnostics), so it
        # marks every AST-derived family as partial/failed rather than only the
        # families that happen to be empty.
        ast_recovered = any(d.startswith("clang exited") for d in tu.diagnostics)
        coverage: dict[str, str] = {
            family: coverage_state_for_family(
                entities_present=bool(entities), family_diagnostics_seen=ast_recovered
            )
            for family, entities in (
                ("functions", tu.functions),
                ("variables", tu.variables),
                ("types", tu.types),
                ("templates", tu.templates),
                ("inline_bodies", tu.inline_bodies),
                ("constexpr_values", tu.constexpr_values),
            )
        }
        macro_diag = any(d.startswith("macro pass") for d in tu.diagnostics)
        coverage["macros"] = coverage_state_for_family(
            entities_present=bool(tu.macros), family_diagnostics_seen=macro_diag
        )
        # source_edges: this extractor never populates them (no second AST-walk
        # for call/type edges yet) — a structural producer limitation, not a mode.
        coverage["source_edges"] = coverage_state_for_family(
            entities_present=False, family_diagnostics_seen=False, unsupported=True
        )
        coverage["read_files"] = coverage_state_for_family(
            entities_present=bool(tu.read_files), family_diagnostics_seen=False
        )
        tu.coverage = coverage
        tu.fact_set = default_fact_set(
            producer="abicheck-cc-clang-extractor",
            producer_version=CLANG_EXTRACTOR_VERSION,
            compiler_version=_clang_compiler_version(self.clang_bin),
        )

    def _run(
        self, cmd: list[str], directory: str, source_label: str
    ) -> subprocess.CompletedProcess[str]:
        """Run a clang command in the TU directory, un-redacting redacted paths.

        Every token is un-redacted, including macro values: a home path used
        inside a macro (e.g. ``-DCFG=~/build/cfg.h`` consumed by ``#include CFG``)
        must be expanded or clang parses a different TU / cannot find the header.
        ``unredact_home`` only rewrites a ``~`` standing in for a home directory,
        so a literal ``~`` mid-token is left intact (mirrors castxml, PR #336).
        """
        cmd = [unredact_home(tok) for tok in cmd]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                cwd=directory or None,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceExtractionError(
                f"clang timed out after {self.timeout}s on {source_label}"
            ) from exc

    def _run_ast_to_file(
        self, cmd: list[str], directory: str, source_label: str
    ) -> tuple[Path, str, int]:
        """Run clang with its JSON AST streamed to a temp file; return its path.

        Returns ``(ast_file_path, stderr_text, returncode)``. The caller owns the
        file and must delete it. Unlike :meth:`_run` (``capture_output``), clang's
        large stdout is written **to disk**, not captured into a Python ``str``.
        ``json.load`` still reads the file back to parse it, so a *single* TU's
        parse peak is unchanged (~serialized bytes + tree); the wins are across
        **concurrent** workers: payloads sit on disk — not the heap — until each
        worker's turn, so the default thread pool (whose C ``json`` parse serializes
        on the GIL) no longer stacks N giant AST strings in one address space the way
        captured stdout did. It also drops the ``text=True`` decode copy (bytes
        parse). ``stderr`` stays buffered (it is small). The temp file is removed on
        timeout/failure here; on success the caller's ``finally`` removes it.
        """
        cmd = [unredact_home(tok) for tok in cmd]
        fd, name = tempfile.mkstemp(prefix="abicheck-l4-ast-", suffix=".json")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                proc = subprocess.run(
                    cmd,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    check=False,
                    cwd=directory or None,
                )
            stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
            return path, stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            path.unlink(missing_ok=True)
            raise SourceExtractionError(
                f"clang timed out after {self.timeout}s on {source_label}"
            ) from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _attach_macros(
        self,
        tu: SourceAbiTu,
        compile_unit: CompileUnit,
        source: Path,
        directory: str,
        public_header_roots: list[str],
    ) -> None:
        """Run the ``-E -dD`` preprocessor pass and fold public macros into the TU.

        Best-effort: the JSON AST has no macros, so this second pass is what makes
        ``public_macro_value_changed`` possible (Codex review #339, P2). A failure
        here only records a diagnostic (partial macro coverage) — it never discards
        the AST-derived dump or aborts the comparison (ADR-028 D7).
        """
        macro_cmd = build_clang_macro_command(
            compile_unit,
            source,
            clang_bin=self.clang_bin,
            compiler_binary=self.compiler_binary,
        )
        try:
            result = self._run(macro_cmd, directory, compile_unit.source)
        except SourceExtractionError as exc:
            tu.diagnostics.append(f"macro pass skipped: {exc}")
            return
        # A non-zero exit means clang stopped on a preprocessing error; it may
        # still have emitted some markers/defines. Record the partial coverage so
        # the capability report does not overstate L4 macro coverage, mirroring
        # the AST pass (CodeRabbit review).
        if result.returncode != 0:
            tu.diagnostics.append(
                f"macro pass exited {result.returncode} (partial): "
                f"{result.stderr[:300]}"
            )
        if not result.stdout.strip():
            return
        macros, macro_files = macros_from_preprocessor(
            result.stdout, public_header_roots
        )
        tu.macros = macros
        # A header that only defines macros contributes no AST node, so add its
        # path (resolved against the build directory) to the cache dependency set
        # or a macro-only edit would be a stale hit (Codex review #339, P1/P2).
        resolved = resolve_read_files(set(macro_files), compile_unit.directory)
        tu.read_files = sorted(set(tu.read_files) | set(resolved))
