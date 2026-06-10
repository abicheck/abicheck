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
derives the fingerprints that final binary/debug artifacts under-represent and
that castxml (phase 2) cannot produce:

- inline function bodies (``inline_body_changed``);
- function/class **template** bodies, instantiated or not
  (``template_body_changed`` / ``uninstantiated_template_removed``);
- ``constexpr`` values (``constexpr_value_changed``);
- public default arguments (``default_argument_changed``).

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
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..build_evidence import CompileUnit
from ..model import EvidenceConfidence
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
CLANG_EXTRACTOR_VERSION = "0.1"

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


def _canonical(node: Any, id_names: dict[str, str] | None = None) -> Any:
    """Reduce a clang AST node to a build-root-stable structural form for hashing.

    Keeps only structural scalars (``kind``/``name``/``value``/``opcode``/
    ``castKind``) plus the node's ``type.qualType`` and its recursively
    canonicalized children, dropping pointer ids and source locations so a pure
    body edit changes the hash while a rebuild/relocation does not.
    """
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key in _FINGERPRINT_SCALAR_KEYS:
        if key in node:
            out[key] = node[key]
    type_obj = node.get("type")
    if isinstance(type_obj, dict) and "qualType" in type_obj:
        out["type"] = type_obj["qualType"]
    # A DeclRefExpr stores the referenced entity (e.g. another constant) in
    # ``referencedDecl``. clang's JSON exposes only its *unqualified* ``name``
    # there, so a reference change `a::k` -> `b::k` between same-named, same-typed
    # constants in different scopes would hash identically and the
    # constexpr/default-arg change would be missed (Codex review #339, P2). When
    # an ``id_names`` map is supplied we resolve the referenced decl's pointer id
    # (stable within a single dump) to its scope-qualified name — build-root
    # stable, unlike the raw id — so `a::k` and `b::k` differ. Without the map
    # (e.g. a direct unit-test call) we fall back to the unqualified name.
    ref = node.get("referencedDecl")
    if isinstance(ref, dict):
        ref_id = ref.get("id")
        resolved = id_names.get(ref_id) if id_names and isinstance(ref_id, str) else None
        ref_name = resolved or ref.get("name")
        if ref_name:
            out["ref"] = ref_name
    inner = node.get("inner")
    if isinstance(inner, list):
        out["inner"] = [_canonical(child, id_names) for child in inner]
    return out


def _subtree_hash(node: dict[str, Any], id_names: dict[str, str] | None = None) -> str:
    return _hash("clang-ast", json.dumps(_canonical(node, id_names), sort_keys=True))


def _decl_qualified_names(
    node: Any, scope: list[str] | None = None, out: dict[str, str] | None = None
) -> dict[str, str]:
    """Map every named decl's pointer ``id`` to its scope-qualified name.

    Built once per AST so a ``DeclRefExpr.referencedDecl.id`` can be resolved to a
    build-root-stable qualified name (``a::k``) instead of the raw, run-varying
    pointer or the bare unqualified ``name`` clang emits at the reference site
    (Codex review #339, P2). Pointer ids are unique and stable *within a single*
    clang dump, which is all the resolution needs.
    """
    if out is None:
        out = {}
    if scope is None:
        scope = []
    if not isinstance(node, dict):
        return out
    kind = node.get("kind")
    name = node.get("name") or ""
    node_id = node.get("id")
    if isinstance(node_id, str) and name and isinstance(kind, str) and kind.endswith("Decl"):
        out.setdefault(node_id, _qualified(scope, name))
    child_scope = [*scope, name] if (kind in _SCOPE_NODE_KINDS and name) else scope
    for child in node.get("inner", []) or []:
        _decl_qualified_names(child, child_scope, out)
    return out


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


def _expr_value(node: dict[str, Any], id_names: dict[str, str] | None = None) -> str:
    """A value string that changes iff the whole initializer expression changes.

    A lone literal (after stripping wrapper casts) keeps its human-readable value
    (``42``); any compound expression (``1 + 2``, a call, a braced-init) is
    fingerprinted as a whole, so ``1 + 2`` and ``1 + 3`` are distinguished. The
    earlier "first literal under the AST" heuristic collapsed them and missed the
    change (Codex review #339, P2). ``id_names`` resolves referenced-decl ids to
    qualified names inside the fingerprint (see :func:`_canonical`).
    """
    core = _unwrap_expr(node)
    if (
        isinstance(core, dict)
        and core.get("kind") in _LITERAL_NODE_KINDS
        and "value" in core
    ):
        return str(core["value"])
    return _subtree_hash(node, id_names)


def _default_arg_repr(node: dict[str, Any], id_names: dict[str, str] | None = None) -> str:
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
        rep = _expr_value(init, id_names) if init is not None else "default"
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

    def __init__(
        self,
        public_header_roots: list[str],
        id_names: dict[str, str] | None = None,
    ) -> None:
        from ...provenance import build_public_set

        # A public root may be a *directory* (`--headers include/`). Feeding it to
        # build_public_set as a header file would never match a decl under it
        # (`include` vs `include/api.h`), dropping the whole public include tree;
        # split file roots from directory roots first (Codex review #339, P2).
        file_roots, dir_roots = split_public_roots(public_header_roots)
        self.header_segs, self.dir_segs, self.have_set = build_public_set(
            file_roots, dir_roots
        )
        # decl pointer-id -> scope-qualified name, used to resolve referenced
        # decls in default-arg/constexpr fingerprints (Codex review #339, P2).
        self.id_names: dict[str, str] = id_names or {}

    def classify(self, file: str) -> tuple[str, str, bool]:
        """Return ``(visibility, origin_label, api_relevant)`` for a file.

        Mirrors the castxml extractor: a header that is both public and generated
        stays public but is marked ``GENERATED`` (so ``generated_header_changed``
        owns it); a generated *private* header (not in the public set) is demoted
        off the public surface.
        """
        from ...model import ScopeOrigin
        from ...provenance import classify_origin, is_generated_header

        origin = classify_origin(
            file, self.header_segs, self.dir_segs, have_public_set=self.have_set
        )
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
        entities.append(
            SourceEntity(
                id=_hash("macro", name, value),
                kind="macro",
                qualified_name=name,
                value=value,
                source_location=_location(file, 0, origin),
                visibility=visibility,
                api_relevant=True,
                confidence=EvidenceConfidence.HIGH,
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
    ctx = _ClassifyContext(public_header_roots, _decl_qualified_names(ast_root))
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

    if kind in _FUNCTION_NODE_KINDS and name and accessible:
        _emit_function(node, ctx, tu, scope, file)
    elif kind in _TEMPLATE_NODE_KINDS and name:
        # The template's body is captured whole in its fingerprint; do not
        # descend into the templated pattern, or its inner FunctionDecl/Record
        # would be re-emitted as a duplicate non-template entity.
        if accessible:
            _emit_template(node, ctx, tu, scope, file)
        return file
    elif kind == "VarDecl" and name and node.get("constexpr") and accessible:
        _emit_constexpr(node, ctx, tu, scope, file)
    elif kind in ("CXXRecordDecl", "EnumDecl") and name and accessible:
        _emit_type(node, ctx, tu, scope, file)

    # Descend, extending the scope name stack for namespaces/records so members
    # get a fully-qualified name.
    child_scope = scope
    if kind in _SCOPE_NODE_KINDS and name:
        child_scope = [*scope, name]
    # Access applying to this node's children: a private/protected subtree stays
    # hidden wholesale; a record opens with its tag default; any other context
    # (namespace, linkage spec, TU) imposes no restriction.
    if not accessible:
        running_access = access
    elif kind == "CXXRecordDecl":
        running_access = _default_member_access(node)
    else:
        running_access = "public"
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
            value=_default_arg_repr(node, ctx.id_names),
            source_location=loc,
            visibility=visibility,
            api_relevant=True,
            confidence=EvidenceConfidence.HIGH,
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
                body_hash=_subtree_hash(body, ctx.id_names),
                source_location=loc,
                visibility=visibility,
                api_relevant=True,
                confidence=EvidenceConfidence.HIGH,
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
            body_hash=_subtree_hash(node, ctx.id_names),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=EvidenceConfidence.HIGH,
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
    value = (
        _expr_value(init, ctx.id_names)
        if init is not None
        else _subtree_hash(node, ctx.id_names)
    )
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
            confidence=EvidenceConfidence.HIGH,
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
            type_hash=_subtree_hash(node, ctx.id_names),
            source_location=_location(file, _node_line(node), origin),
            visibility=visibility,
            api_relevant=True,
            confidence=EvidenceConfidence.HIGH,
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
            )
        tu = source_abi_from_clang_ast(
            ast_root, compile_unit, public_header_roots, target_id, diagnostics=diags,
        )
        self._attach_macros(tu, compile_unit, source, directory, public_header_roots)
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
