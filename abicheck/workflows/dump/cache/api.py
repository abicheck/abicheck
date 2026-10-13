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

"""Public entry point of the dump cache: :func:`cached_run_dump`, its request
type and the cacheability gate. See the package docstring."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ....buildsource.source_inputs import extraction_read_source_inputs
from ....extract.binary_identity import stamp_binary_identity
from ...cache_probe import refuse_dump_while_probing
from .key import (
    _cache_key,
    _dump_cache_extra_key,
    _manifest_all_tus_required,
    _manifest_cache_paths,
    _manifest_frontend_context,
    _manifest_public_headers,
    _manifest_roots,
    compile_context_header_inputs,
)
from .store import _lookup_key, _store_key, _write_enabled

if TYPE_CHECKING:
    from collections.abc import Callable

    from ....model import AbiSnapshot


def _dump_is_cacheable(req: DumpCacheRequest) -> bool:
    """Whether a ``run_dump`` call is safe to serve from the whole-snapshot
    cache (:mod:`abicheck.workflows.dump.cache`).

    Deliberately conservative: only the plain "binary + public headers" shape
    — the dominant release-baseline/CI comparison case a repeated pipeline
    re-extracts identically on every run — is cached. A PDB path, a DWARF
    debug-info root, debuginfod resolution (network-dependent), a forced
    debug format, a symbols-only/debug-presence-only dump, or a
    ``CompileContext`` naming an untrackable header input all change what ``run_dump`` produces in ways not
    folded into the cache key below, so those combinations always fall
    through to a live dump rather than risk serving a stale/mismatched
    snapshot.

    G29 Phase A: the L2 header-only semantic graph (and its include-file
    extension) used to disable caching outright (an opt-in flag meant a cache
    hit from a plain prior run could silently omit the graph a later
    graph-requesting call expected). It is no longer flag-gated — every
    ``run_dump`` call attempts it identically — so that toggle-mismatch risk
    is gone; the graph is a deterministic function of the same
    headers/includes/public-header inputs already covered by the cache key,
    and ``store._store_key``/``_lookup_key`` round-trip the full snapshot
    (including ``build_source``) through JSON, so a cache hit still carries
    the graph. A ``CompileContext`` (``--compiler*``/``--sysroot``/
    ``--nostdinc``/``--ast-frontend``/``--frontend-context``) is cacheable:
    its normalized content is folded into the key
    (:func:`compile_context_cache_field`, :func:`effective_header_backend`),
    and the compiler/clang identities are resolved with its
    ``gcc_path``/``gcc_prefix``. A ``None`` context and an all-default one
    share a key, since they produce identical dumps. The header content a
    context makes reachable (``sysroot``, ``-I``/``-isystem``/``-include``
    and siblings in its compiler options) is content-hashed with the same
    include-tree walk as ``-I`` directories
    (:func:`compile_context_header_inputs`), so a warm hit never serves a
    snapshot parsed from stale headers; a context with an input that walk
    cannot follow is not cached at all.

    A labeled ``--include`` (``include_labels`` non-empty, ADR-050 D1) is
    likewise excluded: the label rides only into ``AbiSnapshot.contract``
    (``comparability.compute_extraction_contract``'s ``IncludeDir.label``),
    not into ``_cache_key``'s hashed inputs — folding it in
    there too is real but unbuilt follow-up work, and the labeled form is a
    narrow escape hatch, not the common path, so falling through to a live
    dump is the conservative, correct default rather than risk serving a
    same-key snapshot whose ``contract`` was fingerprinted under a
    different label.

    A ``dump_manifest`` (ADR-050 D3) is cacheable too, as of G32 Phase E:
    :func:`_manifest_cache_paths` derives headers/includes-shaped path lists
    from the manifest for :func:`abicheck._cache_key`'s own
    content-hash walk to reuse unchanged (an edit to any file a
    manifest-driven dump could actually read still busts the cache), and
    :func:`abicheck.comparability.manifest_tu_scope_field` folds the
    manifest's own structural identity (TU names/order/``required``/
    ``contributes_to_abi``/``project_owned``) into the ``extra`` key
    material below — the part a filesystem content walk structurally cannot
    see (renaming a TU, or flipping ``contributes_to_abi``, changes nothing
    a content hash would notice). See :func:`cached_run_dump`.

    **Only when every translation unit is ``required`` (Codex review, PR
    #636).** A ``required=False`` TU's failure degrades ``run_tu_loop`` to a
    logged diagnostic instead of failing the whole dump (ADR-050 D3) — the
    resulting snapshot then successfully returns with that TU's declarations
    silently absent, by design. A purely *transient* failure (a flaky
    castxml crash, momentary resource contention) produces the identical
    "successful but degraded" snapshot on that one run; caching it would let
    that transient blip stick around indefinitely — served back on every
    subsequent identical-input run — instead of self-healing the next time
    the same manifest happens to dump cleanly. A manifest containing any
    optional TU is therefore never cached at all (the same conservative
    "fall through to a live dump" default every other risky shape above
    already gets), regardless of whether this particular run actually hit a
    failure — cacheability can't depend on a per-run outcome the cache
    lookup happens *before* extraction even runs.
    """
    return (
        req.pdb_path is None
        and not req.dwarf_only
        and not req.debug_roots
        and not req.enable_debuginfod
        and req.debug_format is None
        and not req.symbols_only
        and not req.debug_presence_only
        and _compile_context_is_keyable(req.compile)
        and not req.include_labels
        and (req.dump_manifest is None or _manifest_all_tus_required(req.dump_manifest))
    )


def _compile_context_is_keyable(compile: object | None) -> bool:
    """Whether *compile* can be folded into the whole-snapshot cache key:
    ``None``, or a real ``CompileContext`` whose fields are keyed by
    :func:`compile_context_cache_field` and whose header inputs are all
    content-trackable (:func:`compile_context_header_inputs`). Anything else
    falls through to a live dump."""
    from ....compile_context import CompileContext

    if compile is None:
        return True
    return (
        isinstance(compile, CompileContext)
        and compile_context_header_inputs(compile) is not None
    )


def _absolute_paths(paths: list[Path] | None) -> list[Path] | None:
    """*paths* made absolute, so ``inc/a.h`` and its absolute spelling of one
    file share a cache key. Applied to the dump's inputs, not only the key: a
    snapshot records the spelling it was given (surface-graph node ids), so a
    key-only fix would let a warm hit return the other spelling's strings.
    ``abspath``, not ``resolve()``: a symlinked install prefix keeps its name.
    """
    return None if paths is None else [Path(os.path.abspath(p)) for p in paths]


@dataclass(frozen=True)
class DumpCacheRequest:
    """One ``run_dump`` call, normalised for the cache: every argument its output
    depends on, with paths made absolute by :func:`_absolute_paths`.

    ``notify`` (a progress sink) is deliberately absent: it never reaches the
    snapshot. :func:`_cache_key_for` must read every other field, and the
    property test in ``tests/test_dump_cache_key_property.py`` holds it to that.
    """

    path: Path
    binary_fmt: str
    headers: list[Path] | None
    includes: list[Path] | None
    version: str
    lang: str
    lang_explicit: bool
    pdb_path: Path | None
    dwarf_only: bool
    debug_roots: list[Path] | None
    enable_debuginfod: bool
    debuginfod_url: str | None
    debug_format: str | None
    symbols_only: bool
    debug_presence_only: bool
    public_headers: list[Path] | None
    public_header_dirs: list[Path] | None
    header_backend: str
    compile: object | None
    include_labels: dict[Path, str] | None
    dump_manifest: object | None
    include_dependencies: bool
    public_include_search_dirs: list[Path] | None


def _cache_key_for(req: DumpCacheRequest) -> str:
    """The whole-snapshot key for *req*, or ``""`` when it cannot be cached (an
    uncacheable shape, or an input the key cannot see). An empty key is never
    looked up or stored."""
    if not _dump_is_cacheable(req):
        return ""
    public_headers: list[Path] | None
    public_header_dirs: list[Path] | None
    if req.dump_manifest is not None:
        # ADR-050 D3/D6 (G32 Phase E): a manifest-driven dump passes no
        # `headers` at all (dump()'s own mutual-exclusivity check enforces
        # that) -- derive content-hashable path lists from the manifest itself
        # instead, and fold the manifest's own structural identity (a content
        # walk over those same paths cannot see a TU rename or a flipped
        # required/contributes_to_abi/project_owned flag) in as additional
        # `extra` key material.
        from ....compare.manifest_tu_scope import manifest_tu_scope_field

        manifest = req.dump_manifest
        cache_headers, cache_includes = _manifest_cache_paths(manifest)
        # ADR-050 D5 (G32 Phase D): the manifest's own frontend_context
        # ("host"/"device") is authoritative and selects a genuinely different
        # AST (dumper_manifest.resolve_header_ast_result) -- manifest_tu_scope_field
        # only covers per-TU fields, so without folding this manifest-level field
        # in too, two manifests differing only in frontend_context would hash
        # identically and silently share a cached host/device snapshot (Codex
        # review).
        manifest_extra = (
            f"{manifest_tu_scope_field(manifest)}\x00"
            f"{_manifest_frontend_context(manifest)}\x00"
            f"{_manifest_roots(manifest)}"
        )
        uses_ast = True
        # dumper.dump() replaces the caller-supplied public_headers/
        # public_header_dirs with the manifest's own public_header_paths/
        # public_header_dirs whenever a manifest is given, so the key must
        # classify the same way (Codex review, PR #636).
        public_headers, public_header_dirs = _manifest_public_headers(manifest)
    else:
        # The dump pipeline independently infers an umbrella header's include
        # root; fold the same roots into the whole-snapshot key so transitive
        # edits bust it.
        from ....header_utils import resolve_inferred_header_roots

        headers = req.headers or []
        includes = req.includes or []
        inferred_roots, _ = resolve_inferred_header_roots(headers, includes)
        cache_headers = headers
        cache_includes = includes + inferred_roots
        manifest_extra = ""
        uses_ast = bool(headers)
        public_headers = req.public_headers
        public_header_dirs = req.public_header_dirs

    # Header inputs the compile context adds (sysroot, -I/-isystem/-include in
    # its compiler options) reach the parser too: fold their content into the
    # same walk, so an edit under any of them busts the key.
    cc_inputs = compile_context_header_inputs(req.compile)
    if cc_inputs is None:  # guarded by _dump_is_cacheable; defensive
        return ""
    cache_headers = [*cache_headers, *cc_inputs[0]]
    cache_includes = [*cache_includes, *cc_inputs[1]]

    base = _dump_cache_extra_key(
        req.binary_fmt,
        req.header_backend,
        public_headers,
        public_header_dirs,
        req.lang,
        uses_ast=uses_ast,
        lang_explicit=req.lang_explicit,
        public_include_search_dirs=req.public_include_search_dirs,
        compile=req.compile,
    )
    # NUL-joined (see _dump_cache_extra_key's own docstring on why NUL is the
    # only collision-safe choice). The dependency-scope mode is appended
    # unconditionally, even for a header-less dump where it has no effect on the
    # output -- correctness for the header-derived case requires the split, and
    # an up-front "will this dump have headers" check isn't worth its cost here.
    scope_extra = "filtered" if not req.include_dependencies else "full"
    extra = f"{base}\x00{manifest_extra}\x00{scope_extra}"
    return _cache_key(
        req.path, cache_headers, cache_includes, req.version, req.lang, extra=extra
    )


def cached_run_dump(
    run_dump: Callable[..., AbiSnapshot],
    path: Path,
    binary_fmt: str,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    lang_explicit: bool = False,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: object | None = None,
    notify: Callable[[str], None] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: object | None = None,
    include_dependencies: bool = True,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """``run_dump(...)``, transparently served from the whole-snapshot cache
    when the call shape is cacheable (:func:`_dump_is_cacheable`) -- avoiding a
    repeat binary/header extraction entirely (no castxml/DWARF/ELF work, no
    model construction) on a hit. Falls through to a live ``run_dump`` call on a
    miss or an uncacheable shape, then populates the cache.

    ``run_dump`` is passed in by the caller (``workflows.dump.native``) rather
    than imported here, so this module never imports the dump that calls it.

    ``include_dependencies`` is folded into the key (see :func:`_cache_key_for`):
    a filtered and an unfiltered dump of a header-derived binary/headers pair are
    genuinely different snapshot content (``dumper_scoping.resolve_dependency_scope``),
    so they must never share an entry.
    """
    path = Path(os.path.abspath(path))
    req = DumpCacheRequest(
        path=path,
        binary_fmt=binary_fmt,
        headers=_absolute_paths(headers),
        includes=_absolute_paths(includes),
        version=version,
        lang=lang,
        lang_explicit=lang_explicit,
        pdb_path=pdb_path,
        dwarf_only=dwarf_only,
        debug_roots=debug_roots,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        debug_format=debug_format,
        symbols_only=symbols_only,
        debug_presence_only=debug_presence_only,
        public_headers=_absolute_paths(public_headers),
        public_header_dirs=_absolute_paths(public_header_dirs),
        header_backend=header_backend,
        compile=compile,
        include_labels=include_labels,
        dump_manifest=dump_manifest,
        include_dependencies=include_dependencies,
        public_include_search_dirs=_absolute_paths(public_include_search_dirs),
    )

    def _dump_uncached() -> AbiSnapshot:
        """The real dump with this call's normalised arguments, cache aside --
        one spelling for the uncacheable and the miss path alike."""
        return run_dump(
            req.path,
            req.binary_fmt,
            req.headers,
            req.includes,
            req.version,
            req.lang,
            lang_explicit=req.lang_explicit,
            pdb_path=req.pdb_path,
            dwarf_only=req.dwarf_only,
            debug_roots=req.debug_roots,
            enable_debuginfod=req.enable_debuginfod,
            debuginfod_url=req.debuginfod_url,
            debug_format=req.debug_format,
            symbols_only=req.symbols_only,
            debug_presence_only=req.debug_presence_only,
            public_headers=req.public_headers,
            public_header_dirs=req.public_header_dirs,
            header_backend=req.header_backend,
            compile=req.compile,
            notify=notify,
            include_labels=req.include_labels,
            dump_manifest=req.dump_manifest,
            include_dependencies=req.include_dependencies,
            public_include_search_dirs=req.public_include_search_dirs,
        )

    # An uncacheable shape derives the empty key: no lookup, no store, and no
    # key-side tool execution (the gate in _cache_key_for runs first).
    initial_key = _cache_key_for(req)
    cached = _lookup_key(initial_key, path) if initial_key else None
    if cached is not None:
        # A cache hit stands in for the live extraction it replaces, so it needs
        # the source-read licence stamped here: `run_dump` grants it (see
        # `buildsource.source_inputs.granting_live_source_licence`), but this path
        # never calls `run_dump` and the flag is runtime-only. Granting it is sound:
        # the key hashes the live inputs' content, so the hit is a provenance check.
        # Same predicate the shared dump applies: a headerless DWARF hit is no
        # more entitled to re-read DW_AT_decl_file paths (Codex review, P2).
        stamp_binary_identity(cached, path)
        if extraction_read_source_inputs(cached):
            cached.live_source_evidence = True
        return cached
    refuse_dump_while_probing()
    snap = _dump_uncached()
    # Re-derive after the dump: a tool that appeared or changed mid-run must
    # miss, not store under the key it was looked up with.
    final_key = _cache_key_for(req) if initial_key else ""
    if initial_key and initial_key == final_key and _write_enabled():
        _store_key(snap, initial_key, path)
    return snap
