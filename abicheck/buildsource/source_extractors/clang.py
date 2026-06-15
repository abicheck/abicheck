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

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from ..build_evidence import CompileUnit
from ..model import LayerConfidence
from ..source_abi import SourceAbiTu, SourceEntity, SourceLocation
from ._argv import (
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
    resolve_read_files,
    split_public_roots,
    unredact_home,
)
from .base import SourceExtractionError

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8).
CLANG_EXTRACTOR_VERSION = "0.3"

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
_PUBLIC_ROOT_SAMPLE_LIMIT = 128
_PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES = 2
_PUBLIC_FILE_ROOT_SUFFIX_LIMIT = 6
_PUBLIC_HEADER_SUFFIXES = (
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".h++",
    ".inc",
    ".inl",
    ".ipp",
    ".tcc",
    ".tpp",
)


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
_LIKELY_GENERATED_RE = re.compile(r"\.(inc|def)$|config\.h$|\.pb\.h$|moc_|\.generated\.", re.I)


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
    cmd += replay_extra_flags(compile_unit, cmd, cc_id)
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
_LOCAL_DECL_KINDS = frozenset({"ParmVarDecl", "VarDecl", "BindingDecl", "DecompositionDecl"})

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


def _alpha_rename_map(node: dict[str, Any], param_ids: tuple[str, ...]) -> dict[str, str]:
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
            out[key] = placeholder if key == "name" and placeholder is not None else node[key]
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


def _mangled(node: dict[str, Any]) -> str:
    mangled = node.get("mangledName")
    name = node.get("name", "")
    if isinstance(mangled, str) and mangled and mangled != name:
        return mangled
    return ""


def _qualified(scope: list[str], name: str) -> str:
    return "::".join([*scope, name]) if scope else name


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
        origin = classify_origin(
            file, [], self.dir_segs, have_public_set=self.have_set
        )
        if origin == ScopeOrigin.PUBLIC_HEADER and is_generated_header(file):
            return "generated", "GENERATED", True
        if origin == ScopeOrigin.PUBLIC_HEADER:
            return "public_header", "PUBLIC_HEADER", True
        if origin == ScopeOrigin.GENERATED:
            return "private_header", "PRIVATE_HEADER", False
        return "unknown", "UNKNOWN", False


def _file_segments(path: str) -> tuple[str, ...]:
    posix = path.replace("\\", "/")
    return tuple(p for p in PurePosixPath(posix).parts if p not in ("/", ".", ""))


def _matches_exact_public_header(
    header: str, exact_header_segs: list[tuple[str, ...]]
) -> bool:
    header_segs = _file_segments(header)
    return any(
        len(header_segs) >= len(root) and header_segs[-len(root) :] == root
        for root in exact_header_segs
    )


def _header_samples(root: str) -> tuple[bool, list[Path]]:
    """Relative header names under an existing public root, bounded for speed.

    Package/public roots often point at an installed SDK include tree while the
    compile unit reads the corresponding build-tree include directory. A small
    deterministic sample is enough to recognize that equivalence without hashing
    or scanning the entire SDK for every TU.
    """
    p = Path(unredact_home(root)).expanduser()
    if p.is_file() and _looks_like_public_header(p):
        return True, _path_suffixes(p, _PUBLIC_FILE_ROOT_SUFFIX_LIMIT)
    if not p.is_dir():
        return False, []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames.sort()
        for filename in sorted(filenames):
            child = Path(dirpath) / filename
            if not _looks_like_public_header(child):
                continue
            out.append(child.relative_to(p))
            if len(out) >= _PUBLIC_ROOT_SAMPLE_LIMIT:
                return False, out
    return False, out


def _path_suffixes(path: Path, limit: int) -> list[Path]:
    parts = tuple(part for part in path.parts if part not in (path.anchor, "/", ""))
    return [Path(*parts[-n:]) for n in range(min(limit, len(parts)), 0, -1)]


def _looks_like_public_header(path: Path) -> bool:
    return path.suffix.lower() in _PUBLIC_HEADER_SUFFIXES or not path.suffix


def _compile_unit_include_dir(raw_inc: str, compile_unit: CompileUnit) -> Path:
    inc = Path(unredact_home(raw_inc)).expanduser()
    if inc.is_absolute():
        return inc
    directory = Path(unredact_home(compile_unit.directory or ".")).expanduser()
    return directory / inc


def _compile_unit_include_roots(
    compile_unit: CompileUnit, compiler_binary: str | None = None
) -> list[tuple[str, Path]]:
    roots = [
        (raw, _compile_unit_include_dir(raw, compile_unit))
        for raw in compile_unit.include_paths
    ]
    roots.extend(
        (raw, _compile_unit_include_dir(raw, compile_unit))
        for raw in compile_unit.system_include_paths
    )
    cc_bin = pick_compiler_binary(compile_unit, compiler_binary)
    cc_id = "msvc" if is_msvc_mode(cc_bin) else "gnu"
    replay_flags = replay_extra_flags(compile_unit, [], cc_id)
    i = 0
    while i < len(replay_flags):
        tok = replay_flags[i]
        raw: str | None = None
        if tok in {"-iquote", "-idirafter"} and i + 1 < len(replay_flags):
            raw = replay_flags[i + 1]
            i += 2
        elif tok.startswith("-iquote") and len(tok) > len("-iquote"):
            raw = tok[len("-iquote") :]
            i += 1
        elif tok.startswith("-idirafter") and len(tok) > len("-idirafter"):
            raw = tok[len("-idirafter") :]
            i += 1
        elif cc_id == "msvc" and tok in {"/I", "-I"} and i + 1 < len(replay_flags):
            raw = replay_flags[i + 1]
            i += 2
        elif (
            cc_id == "msvc"
            and len(tok) > 2
            and (tok.startswith("/I") or tok.startswith("-I"))
        ):
            raw = tok[2:]
            i += 1
        else:
            i += 1
        if raw:
            roots.append((raw, _compile_unit_include_dir(raw, compile_unit)))
    return roots


def _root_spelling(raw_inc: str, resolved_inc: Path, rel: Path | None) -> str:
    base = _include_spelling_base(raw_inc, resolved_inc)
    if rel is not None:
        return str(base / rel)
    return _dir_spelling(base)


def _include_spelling_base(raw_inc: str, resolved_inc: Path) -> Path:
    raw_path = Path(raw_inc)
    raw_unredacted = Path(unredact_home(raw_inc)).expanduser()
    return (
        resolved_inc
        if raw_path.is_absolute() or raw_unredacted.is_absolute()
        else raw_path
    )


def _dir_spelling(path: Path) -> str:
    spelling = str(path)
    return spelling if path.is_absolute() or spelling.endswith(("/", "\\")) else spelling + "/"


def _can_promote_whole_root(raw_inc: str, matched: list[Path]) -> bool:
    raw_path = Path(raw_inc)
    # A dot include root has no useful public path segments (`./` is dropped by
    # provenance); keep matched files instead of a whole-root marker.
    if str(raw_path) in {"", "."}:
        return False
    return len(matched) >= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES


def _is_dot_include_root(raw_inc: str) -> bool:
    return str(Path(raw_inc)) in {"", "."}


def _is_full_single_header_mirror(samples: list[Path], matched: list[Path]) -> bool:
    return len(samples) == 1 and len(matched) == 1


def _strip_leading_sample_dir(samples: list[Path]) -> list[Path]:
    stripped: list[Path] = []
    for rel in samples:
        parts = rel.parts
        if len(parts) <= 1:
            continue
        stripped.append(Path(*parts[1:]))
    return stripped


def _mirror_dir_candidate(
    raw_inc: str, inc: Path, prefix: Path | None, *, for_cache: bool
) -> str:
    if prefix is None:
        return str(inc) if for_cache else _root_spelling(raw_inc, inc, None)
    if for_cache:
        return str(inc / prefix)
    return _dir_spelling(_include_spelling_base(raw_inc, inc) / prefix)


def _equivalent_public_roots_for_unit(
    public_header_roots: list[str],
    compile_unit: CompileUnit,
    *,
    for_cache: bool = False,
    compiler_binary: str | None = None,
) -> list[str]:
    """Add build include dirs that mirror an installed public-header root.

    L4 replay parses the source checkout/build tree, but release/package
    validation commonly passes `-H` roots from an extracted package. Those paths
    are different absolute trees even when they contain the same public headers,
    so pure segment matching classifies every AST declaration as non-public.
    When an include path contains the same relative public headers as an existing
    public root, treat that include path as an equivalent public root for this TU.
    """
    roots = list(public_header_roots)
    seen = {unredact_home(r) for r in roots}
    samples_by_root: dict[str, tuple[bool, list[Path], list[Path]]] = {}
    for root in public_header_roots:
        is_file_root, samples = _header_samples(root)
        if samples:
            root_path = Path(unredact_home(root)).expanduser()
            samples_by_root[root] = (
                is_file_root,
                [] if is_file_root else _path_suffixes(root_path, _PUBLIC_FILE_ROOT_SUFFIX_LIMIT),
                samples,
            )
    if not samples_by_root:
        return roots

    for raw_inc, inc in _compile_unit_include_roots(compile_unit, compiler_binary):
        whole_root = str(inc) if for_cache else _root_spelling(raw_inc, inc, None)
        if not inc.is_dir() or whole_root in seen:
            continue
        for is_file_root, root_prefixes, samples in samples_by_root.values():
            matched = [rel for rel in samples if (inc / rel).is_file()]
            if is_file_root and matched:
                matched = matched[:1]
            prefix: Path | None = None
            prefixed_match_found = False
            if not is_file_root and root_prefixes:
                for candidate_prefix in root_prefixes:
                    prefixed = [candidate_prefix / rel for rel in samples]
                    prefixed_matched = [rel for rel in prefixed if (inc / rel).is_file()]
                    if _can_promote_whole_root(
                        raw_inc, prefixed_matched
                    ) or _is_full_single_header_mirror(samples, prefixed_matched):
                        matched = prefixed_matched
                        prefix = candidate_prefix
                        prefixed_match_found = True
                        break
                if not prefixed_match_found and not _can_promote_whole_root(raw_inc, matched):
                    stripped = _strip_leading_sample_dir(samples)
                    stripped_matched = [rel for rel in stripped if (inc / rel).is_file()]
                    if _can_promote_whole_root(
                        raw_inc, stripped_matched
                    ) or _is_full_single_header_mirror(samples, stripped_matched):
                        matched = stripped_matched
                        prefix = None
            if for_cache:
                for rel in matched:
                    candidate = str(inc / rel)
                    if candidate not in seen:
                        roots.append(candidate)
                        seen.add(candidate)
                continue
            if is_file_root or (
                _is_dot_include_root(raw_inc)
                and len(matched) >= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES
            ):
                for rel in matched:
                    candidate = _root_spelling(raw_inc, inc, rel)
                    if candidate not in seen:
                        roots.append(candidate)
                        seen.add(candidate)
                continue
            if _is_full_single_header_mirror(samples, matched):
                candidate = _root_spelling(raw_inc, inc, matched[0])
                if candidate not in seen:
                    roots.append(candidate)
                    seen.add(candidate)
                continue
            if not _can_promote_whole_root(raw_inc, matched):
                continue
            if matched:
                candidate = _mirror_dir_candidate(raw_inc, inc, prefix, for_cache=for_cache)
                if candidate not in seen:
                    roots.append(candidate)
                    seen.add(candidate)
    return roots


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
    ``-E -dD`` pass as empty-valued macro entities — harmless but noisy. They are
    suppressed when **both** hold, which keeps a real empty feature flag (e.g.
    ``#define FOO_ENABLED``) from being dropped:

    - the macro has an empty replacement (a guard never expands to anything), and
    - its normalized name, with any surrounding underscores stripped, equals the
      header's filename-derived token including the extension suffix
      (``foo.h`` → ``FOO_H``; matches ``FOO_H``, ``_FOO_H``, ``FOO_H_``,
      ``__FOO_H__``).

    The match is *exact*, not a substring, so an intentional empty feature macro
    that merely starts with the stem (``FOO_H_FEATURE``, ``FOO_H_DEPRECATED``) is
    **not** dropped — only the guard spelling itself is. A guard that does not
    derive from the filename (``#ifndef GUARD_12345``) is left in place — a
    deliberate false-negative over risking a false suppression. (The parser does
    not see the matching ``#ifndef``, so the spelling is the only signal.)
    """
    if value or not file:
        return False
    base = re.split(r"[\\/]", file)[-1]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", base).upper().strip("_")  # foo.h -> FOO_H
    if not stem:
        return False
    return name.upper().strip("_") == stem


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
    tu.read_files = resolve_read_files(
        _collect_files(ast_root), compile_unit.directory
    )
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


def _is_constexpr_var_node(kind: str | None, name: str, node: dict[str, Any], accessible: bool) -> bool:
    """Return ``True`` for a named, accessible ``constexpr`` variable node."""
    return kind == "VarDecl" and bool(name) and bool(node.get("constexpr")) and accessible


def _is_type_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible record or enum declaration."""
    return kind in ("CXXRecordDecl", "EnumDecl") and bool(name) and accessible


def _is_typedef_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible typedef or type-alias declaration."""
    return kind in ("TypedefDecl", "TypeAliasDecl") and bool(name) and accessible


def _emit_node(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
    kind: str | None,
    name: str,
    accessible: bool,
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
    if _is_function_node(kind, name, accessible):
        _emit_function(node, ctx, tu, scope, file)
    elif _is_constexpr_var_node(kind, name, node, accessible):
        _emit_constexpr(node, ctx, tu, scope, file)
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


def _initial_running_access(accessible: bool, kind: str | None, node: dict[str, Any], access: str) -> str:
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
) -> str:
    """Iterate a node's ``inner`` list, threading the sticky ``file`` forward.

    Handles ``AccessSpecDecl`` sections that switch the running C++ access for
    subsequent siblings. Returns the last file seen in any child's subtree.
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

    is_template = _emit_node(node, ctx, tu, scope, file, kind, name, accessible)
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
    )


def _location(file: str, line: int, origin_label: str) -> SourceLocation:
    return SourceLocation(path=file, line=line, origin=origin_label)


def _emit_function(
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
    sig = _signature(node)
    mangled = _mangled(node)
    loc = _location(file, _node_line(node), origin)
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
    if _has_body(node):
        body = next(
            c for c in node["inner"] if isinstance(c, dict) and c.get("kind") == "CompoundStmt"
        )
        tu.inline_bodies.append(
            SourceEntity(
                id=_hash("inline", mangled or name, sig),
                kind="inline",
                qualified_name=name,
                mangled_name=mangled,
                signature_hash=_hash("sig", sig),
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
    tu.templates.append(
        SourceEntity(
            id=_hash("template", name),
            kind="template",
            qualified_name=name,
            body_hash=_subtree_hash(node),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
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
    tu.constexpr_values.append(
        SourceEntity(
            id=_hash("constexpr", name, value),
            kind="constexpr",
            qualified_name=name,
            mangled_name=_mangled(node),
            value=value,
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
    return str(
        type_obj.get("qualType")
        or type_obj.get("desugaredQualType")
        or ""
    )


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
            compile_unit, source,
            clang_bin=self.clang_bin, compiler_binary=self.compiler_binary,
        )
        result = self._run(ast_cmd, directory, compile_unit.source)
        if not result.stdout.strip():
            raise SourceExtractionError(
                f"clang produced no AST for {compile_unit.source} "
                f"(exit {result.returncode}): {result.stderr[:1000]}"
                + _missing_generated_header_hint(result.stderr)
            )
        try:
            ast_root = json.loads(result.stdout)
        except ValueError as exc:
            raise SourceExtractionError(
                f"clang AST for {compile_unit.source} was not valid JSON: {exc}"
            ) from exc
        # A non-zero exit with usable JSON means clang recovered from some errors;
        # record it as a diagnostic (partial coverage) rather than discarding the
        # dump (ADR-028 D7).
        diags: list[str] = []
        if result.returncode != 0:
            diags.append(
                f"clang exited {result.returncode} (recovered): {result.stderr[:300]}"
                + _missing_generated_header_hint(result.stderr)
            )
        effective_public_roots = _equivalent_public_roots_for_unit(
            public_header_roots, compile_unit, compiler_binary=self.compiler_binary
        )
        tu = source_abi_from_clang_ast(
            ast_root, compile_unit, effective_public_roots, target_id, diagnostics=diags,
        )
        self._attach_macros(tu, compile_unit, source, directory, effective_public_roots)
        return tu

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
                cmd, capture_output=True, text=True, timeout=self.timeout,
                check=False, cwd=directory or None,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceExtractionError(
                f"clang timed out after {self.timeout}s on {source_label}"
            ) from exc

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
            compile_unit, source,
            clang_bin=self.clang_bin, compiler_binary=self.compiler_binary,
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
