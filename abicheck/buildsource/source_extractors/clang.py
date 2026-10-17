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

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ... import deadline
from ...deadline import run_bounded
from ...extract.compiler_driver import is_intel_sycl_driver
from ...model.execution_cache import memoized, path_witness
from ..build_evidence import CompileUnit
from ..model import LayerConfidence
from ..source_abi import (
    SourceAbiTu,
    SourceEntity,
    coverage_state_for_family,
    default_fact_set,
)
from ._argv import (
    effective_directory,
    resolve_read_files,
    unredact_home,
)
from ._deadline_bound import run_bounded_for_extraction
from .base import SourceExtractionError
from .clang_command import (
    build_clang_command,
    build_clang_macro_command,
)
from .clang_macros import macros_from_preprocessor
from .clang_node_predicates import (
    _SCOPE_NODE_KINDS,
    _ClassifyContext,
    _default_member_access,
    _is_accessible,
    _is_concept_node,
    _is_constexpr_var_node,
    _is_function_node,
    _is_template_node,
    _is_type_node,
    _is_typedef_node,
    _is_variable_node,
    _location,
)
from .clang_nodes import (
    _default_arg_repr,
    _entity_names,
    _entity_ownership,
    _expr_value,
    _has_body,
    _hash,
    _init_expr,
    _mangled,
    _node_file,
    _node_line,
    _param_ids,
    _qualified,
    _signature,
    _subtree_hash,
    _template_params,
)

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
    clang_cache_identity_extra,
)
from .clang_source_edges import build_source_edges

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8). Bump on ANY recipe change so a
#: stale ``--cache-dir`` never reuses an old dump. 0.8: also folds in the
#: resolved compiler override (Codex review). 0.9: the reconstructed command now
#: appends ``-fsycl-host-only`` for a SYCL-enabled Intel oneAPI invocation
#: (Codex review) -- a pre-0.9 cache entry for the same TU was built without
#: that flag and must not be reused as-is. 0.10/0.11: a real build's own
#: recorded ``-fsycl-host-only``/``-fsycl-device-only`` (Intel-only SYCL
#: pass-selector flags carried through via ``abi_relevant_flags``) on a
#: non-Intel invoked binary now degrades that one TU (a clear
#: SourceExtractionError) instead of failing on the raw "unknown argument".
#: 0.10 first shipped this only for ``-fsycl-device-only``, silently
#: STRIPPING a recorded ``-fsycl-host-only`` instead -- confirmed against a
#: real clang install (Codex review) that this was itself wrong: stock
#: clang's bare ``-fsycl`` defines ``__SYCL_DEVICE_ONLY__`` (device context
#: by default), so dropping just the host-only selector silently replayed
#: an explicitly host-pinned TU as device code. 0.11 raises for both
#: selectors symmetrically; a bare, unselected ``-fsycl`` (ambiguous, no
#: explicit pin either way) is left carried through as-is, matching the L2
#: header-AST backend's own established "accepted approximation" for that
#: shape (see ``needs_sycl_host_only``'s own docstring). 0.12:
#: ``_argv._carry_abi_relevant_flags`` (shared by castxml/clang) no longer
#: dedups a repeated flag WITHIN ``abi_relevant_flags`` itself -- a real,
#: layered build config can legitimately record the same toggle-style flag
#: more than once (e.g. ``-fno-sycl -fsycl -fno-sycl``), and the old dedup
#: silently dropped every repeat, corrupting last-flag-wins state for any
#: such pair, not just SYCL (Codex review). 0.13: ``_stamp_fact_set_and_
#: coverage`` now stamps a real ``compiler_family`` (``"intel-llvm"`` for
#: Intel's oneAPI fork, ``"clang"`` unchanged otherwise) instead of always
#: the ``default_fact_set`` default -- a cache entry produced under 0.12 or
#: earlier carries the stale ``"clang"`` label baked into its persisted
#: ``fact_set`` and, on a cache HIT, is returned as-is by
#: ``source_replay._replay_cache_lookup`` without ever re-running
#: ``_stamp_fact_set_and_coverage`` (Codex review, PR #756: a persistent
#: ``--source-abi-cache-dir``/``ABICHECK_L4_CACHE_DIR`` reused across an
#: abicheck upgrade would otherwise silently keep serving the old label
#: forever, since ``compute_tu_cache_key`` doesn't fold ``compiler_family``
#: in on its own -- only the extractor version invalidates it).
CLANG_EXTRACTOR_VERSION = "0.13"


@memoized(maxsize=8, witness=path_witness)
def _clang_compiler_family(clang_bin: str) -> str:
    """The ``compiler_family`` label for *clang_bin* (ADR-038 C.8 fact_set).

    Defaults to ``"clang"`` for an ordinary vanilla/Apple/Debian Clang, but
    reports ``"intel-llvm"`` for Intel's oneAPI DPC++/C++ Compiler fork.
    Primarily resolved the same way ``actions/collect-facts/run.sh``'s
    ``_is_intel_llvm_compiler`` does: probing for the ``__INTEL_LLVM_COMPILER``
    predefined macro (``clang_bin -dM -E -x c++ -``), a vendor-scoped signal
    that recognizes the fork regardless of how the binary is named/invoked
    (a conventional ``clang``/``clang++`` symlink or a custom-named toolchain
    wrapper included) -- unlike a name-based check, which cannot. Cached per
    binary path (``model.execution_cache.memoized``, re-probed when the executable changes; mirroring :func:`_clang_compiler_
    version`'s identical cost shape at the same per-TU call site, so this
    costs one extra subprocess call per unique *clang_bin* for a whole scan,
    not one per TU). Falls back to :func:`abicheck.dumper_clang.
    is_intel_sycl_driver`'s name-based recognition (icx/icpx/dpcpp/dpcpp-cl)
    only if the macro probe itself fails to run at all (compiler not found,
    times out, or rejects ``-dM -E``) -- a real, if weaker, signal is still
    better than silently defaulting to "clang" (Codex review: an earlier
    revision used the name check as primary and never attempted the macro
    probe, missing Intel's fork whenever it's invoked through a
    conventionally-named symlink or wrapper).

    Without this, ``default_fact_set``'s ``compiler_family`` default of
    ``"clang"`` silently collapsed every Intel oneAPI wrapper-collected
    fact_set to the same generic label a vanilla Clang run would carry --
    confirmed on a real ``abicheck-cc icpx`` wrapper pack (source_facts
    recorded ``"compiler_family": "clang", "compiler_version": "22.1.0"``,
    with no trace that the frontend was actually a downstream fork). This
    silence mattered concretely for two reasons: (1) two source_facts.jsonl
    files collected under a vanilla-clang wrapper and an icpx wrapper looked
    fact-set-identical to ``check_fact_set_compatibility``'s rule 2
    (``compiler_family_mismatch`` — a real signal per this codebase's own
    ADR-038 C.8 design, silently unavailable for the one fork most likely to
    have real, documented frontend-behavior differences, per contrib/
    abicheck-clang-plugin/README.md's Intel oneAPI section); and (2) a
    release build without DWARF debug info had no other producer-identity
    signal at all to recover it from (DWARF's own ``DW_AT_producer`` string
    is a separate, independent identity source that isn't always present).
    """
    try:
        r = run_bounded(
            [clang_bin, "-dM", "-E", "-x", "c++", "-"],
            input="",
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return "intel-llvm" if "__INTEL_LLVM_COMPILER" in r.stdout else "clang"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "intel-llvm" if is_intel_sycl_driver(clang_bin) else "clang"


@memoized(maxsize=8, witness=path_witness)
def _clang_compiler_version(clang_bin: str) -> str:
    """``clang -dumpversion`` for *clang_bin*, cached (ADR-038 C.8 fact_set).

    Cached per binary path so a many-TU build pays this subprocess once, not
    once per compile unit. Best-effort: any failure yields ``""`` rather than
    aborting extraction (compiler_version is provenance, not required input).
    """
    try:
        r = run_bounded(
            [clang_bin, "-dumpversion"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


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
    #
    # Round 30 Finding 3 (Codex review, fresh evidence): for an `env -C build`
    # compile unit, clang's own subprocess actually ran from the EFFECTIVE
    # (chdir-composed) directory, not the raw `compile_unit.directory` -- a
    # relative dependency path must resolve against that same effective
    # directory or the real dependency is omitted from the cache fingerprint
    # entirely. Mirrors the identical fix in castxml.py's `_parse_root`.
    tu.read_files = resolve_read_files(
        _collect_files(ast_root),
        effective_directory(compile_unit.argv, compile_unit.directory)
        or compile_unit.directory,
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
            names=_entity_names(name, mangled, node),
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
                names=_entity_names(name, mangled, node),
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
            names=_entity_names(name, mangled, node),
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
            names=_entity_names(name, mangled, node),
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


def _recheck_deadline(source: str, when: str) -> None:
    """Re-check the deadline mid-extraction; folds into SourceExtractionError."""
    try:
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        raise SourceExtractionError(
            f"scan deadline exceeded {when} for {source}"
        ) from exc


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

    def cache_identity_extra(self) -> str:
        return clang_cache_identity_extra(self.clang_bin, self.compiler_binary)

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
                "arguments, inline/template/constexpr bodies), or use --depth build."
            )
        directory = unredact_home(compile_unit.directory)
        source = Path(unredact_home(compile_unit.source))
        if not source.is_absolute() and directory:
            source = Path(directory) / source
        # The EFFECTIVE directory the subprocess actually runs from -- distinct
        # from `directory` above, which stays the raw, un-chdir'd compile-unit
        # directory the `source`/compile-database `file` pair is genuinely
        # anchored to (Codex review, Finding 10, fresh evidence). A leading
        # `env -C DIR` prefix in `compile_unit.argv` means the real build's
        # compiler process ran from `<directory>/DIR`, not bare `directory` --
        # `replay_extra_flags()` (see its own docstring) carries raw, unmodified
        # relative flag values (`-iquote ../include`) straight from
        # `compile_unit.argv`, which only resolve correctly when THIS
        # subprocess's own cwd matches that same effective directory.
        run_directory = effective_directory(compile_unit.argv, directory) or directory

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
            ast_cmd, run_directory, compile_unit.source
        )
        try:
            if ast_path.stat().st_size == 0:
                raise SourceExtractionError(
                    f"clang produced no AST for {compile_unit.source} "
                    f"(exit {ast_rc}): {ast_stderr[:1000]}"
                    + _missing_generated_header_hint(ast_stderr)
                )
            _recheck_deadline(compile_unit.source, "before loading AST")
            try:
                with open(ast_path, "rb") as fh:  # bytes: json detects encoding
                    ast_root = json.load(fh)
            except ValueError as exc:
                # "Extra data" means a second top-level JSON value follows the
                # first — some other flag not already collapsed to a single
                # pass above (`_clang_context_args`'s `-fsycl-host-only`
                # insertion) made this compile emit more than one document to
                # the same stdout stream (e.g. an OpenMP/CUDA offload target
                # flag, or a non-Intel SYCL-capable driver this codebase
                # doesn't yet special-case) — name the likely cause here
                # rather than a bare byte-offset, mirroring the identical hint
                # `dumper_clang_errors._parse_clang_ast_result` gives the L2
                # header-AST backend for the same failure shape.
                hint = ""
                if isinstance(exc, json.JSONDecodeError) and exc.msg == "Extra data":
                    hint = (
                        " -- this looks like more than one JSON document on "
                        "stdout, which happens when a compiler flag makes "
                        "clang run multiple -cc1 passes for one compile (e.g. "
                        "an unpinned SYCL/OpenMP/CUDA offload flag); pin a "
                        "single compilation pass (e.g. -fsycl-host-only) in "
                        "the build's own recorded flags"
                    )
                raise SourceExtractionError(
                    f"clang AST for {compile_unit.source} was not valid JSON: "
                    f"{exc}{hint}"
                ) from exc
        finally:
            ast_path.unlink(missing_ok=True)
        # The JSON load can itself consume the budget (Codex review, round 4).
        _recheck_deadline(compile_unit.source, "after loading AST")
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
        # P1 #17-18: source-graph edges from the SAME already-parsed AST dict —
        # parse_clang_ast_calls()/parse_clang_ast_types() are pure dict-walking
        # functions (no subprocess), so this adds no second frontend pass.
        # Append onto tu.diagnostics directly, not the now-disconnected `diags`
        # list: source_abi_from_clang_ast() above already copied `diags` into
        # tu.diagnostics (list(diagnostics or [])), so a diagnostic appended to
        # `diags` after that point would never reach _stamp_fact_set_and_coverage()
        # below, which reads tu.diagnostics — silently reporting source_edges as
        # empty-confirmed instead of failed on a parser error (Codex review, P2).
        self._attach_source_edges(tu, ast_root, tu.diagnostics)
        # Drop the large AST tree before the macro pass spawns another subprocess,
        # so its memory is reclaimed and doesn't stack with the macro pass.
        del ast_root
        self._attach_macros(
            tu, compile_unit, source, run_directory, effective_public_roots
        )
        self._stamp_fact_set_and_coverage(tu)
        return tu

    def _attach_source_edges(
        self, tu: SourceAbiTu, ast_root: dict[str, Any], diags: list[str]
    ) -> None:
        """Populate ``tu.source_edges`` from the in-memory clang AST (P1 #17-18).

        Thin wrapper: the actual parsing lives in ``clang_source_edges.py``
        (split out to keep this module under the AI-readiness line-count cap).
        """
        tu.source_edges = build_source_edges(ast_root, diags)

    def _stamp_fact_set_and_coverage(self, tu: SourceAbiTu) -> None:
        """ADR-038 C.8: record this TU's canonical fact-set identity + per-family
        coverage. Always attempts every family (no user-selectable mode).
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
        # Both families are derived from the same (possibly ast_recovered
        # -truncated) AST dict the AST-derived families above already
        # account for -- a recovered parse with no edge-parser exception of
        # its own must still downgrade these two, or a partially-collected
        # AST could advertise them as complete/empty-confirmed (Codex
        # review, P2).
        edges_diag = ast_recovered or any(
            d.startswith("source_edges unavailable") for d in tu.diagnostics
        )
        coverage["source_edges"] = coverage_state_for_family(
            entities_present=bool(tu.source_edges), family_diagnostics_seen=edges_diag
        )
        coverage["read_files"] = coverage_state_for_family(
            entities_present=bool(tu.read_files), family_diagnostics_seen=ast_recovered
        )
        tu.coverage = coverage
        tu.fact_set = default_fact_set(
            producer="abicheck-cc-clang-extractor",
            producer_version=CLANG_EXTRACTOR_VERSION,
            compiler_version=_clang_compiler_version(self.clang_bin),
            compiler_family=_clang_compiler_family(self.clang_bin),
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
        # Bound by min(self.timeout, active --budget) and process-group-safe on
        # timeout, same fix as the L2 header-AST subprocess (Codex review, PR
        # #591, round 7).
        cmd = [unredact_home(tok) for tok in cmd]
        return run_bounded_for_extraction(
            cmd,
            timeout=self.timeout,
            tool_label="clang",
            unit_label=source_label,
            capture_output=True,
            text=True,
            cwd=directory or None,
        )

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
        # See _run: run_bounded_for_extraction bounds this the same way.
        cmd = [unredact_home(tok) for tok in cmd]
        fd, name = tempfile.mkstemp(prefix="abicheck-l4-ast-", suffix=".json")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                proc = run_bounded_for_extraction(
                    cmd,
                    timeout=self.timeout,
                    tool_label="clang",
                    unit_label=source_label,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    cwd=directory or None,
                )
            stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
            return path, stderr, proc.returncode
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
        #
        # Round 30 Finding 3 (Codex review, fresh evidence): resolve against
        # the EFFECTIVE (chdir-composed) directory, mirroring the AST pass
        # above and castxml.py's identical fix -- the macro pass's own
        # subprocess ran from the same effective cwd, not the raw
        # `compile_unit.directory`, when a leading `env -C DIR` prefix was
        # recorded.
        resolved = resolve_read_files(
            set(macro_files),
            effective_directory(compile_unit.argv, compile_unit.directory)
            or compile_unit.directory,
        )
        tu.read_files = sorted(set(tu.read_files) | set(resolved))
