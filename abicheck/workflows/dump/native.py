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

"""Native-binary dump orchestration: ``service.run_dump`` and the ELF extractor.

``_run_dump_uncached`` resolves the header backend, runs the hybrid
two-backend path when selected, and otherwise dispatches the primary
extraction through the binary-format seam in
:mod:`abicheck.workflows.dump.formats` (ELF/PE/Mach-O adapters) before
running the shared post-extraction tail (metadata attach, header-only
graph, clang layout, closure-identity renumbering).

:func:`extract_elf` is the ELF adapter's extractor. It stays here, in a flat
legacy module, until ``dumper.py`` has an owning layer: a migrated
``workflows`` module may not import an unclassified one. PE and Mach-O
extraction live in :mod:`abicheck.workflows.dump.pe`/``.macho``.

To substitute an extractor, replace its entry in :data:`FORMAT_ADAPTERS`
rather than patching a name in this module.
"""

from __future__ import annotations

import functools
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...buildsource.source_inputs import granting_live_source_licence
from ...dry_run_estimate import expand_header_inputs
from ...errors import (
    AbicheckError,
    SnapshotError,
    UnsupportedArtifactError,
    ValidationError,
)
from ...extract.headers.clang.layout_tool import attach_clang_layout
from ...extract.metadata_attach import (
    try_attach_numpy_capi_surface,
    try_attach_python_api_surface,
    try_attach_python_ext_metadata,
    try_attach_sycl_metadata,
)
from ...header_utils import (
    cache_relevant_operand_paths,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from ...model import AbiSnapshot
from ...service_header_graph_attach import (
    _HEADER_GRAPH_ENABLED,
    _HEADER_GRAPH_INCLUDES_ENABLED,
    _attach_header_graph,
    prefetch_graph_if_useful,
    prefetch_settled_on_failure,
)
from ...storage import closure_identity
from ...workflows.dump.formats import (
    DEFAULT_ADAPTERS,
    BinaryFormatAdapter,
    NativeExtractRequest,
    emit_notice,
)
from ...workflows.dump.hybrid import compose_hybrid
from ...workflows.run_dump_scope import (
    run_dump_header_roots,
    wrap_run_dump_with_dependency_scope,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...compile_context import CompileContext
    from ...dump_manifest import DumpManifest


# The facade's logger name, kept from before the split.
_logger = logging.getLogger("abicheck.service")


def _run_dump_uncached(
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
    compile: CompileContext | None = None,
    notify: Callable[[str], None] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Extract an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    ``public_headers`` / ``public_header_dirs`` tag declaration provenance
    (ADR-024 Phase 1) on all three formats: ELF threads them into
    :func:`dumper.dump` (which runs ``apply_provenance``), PE/Mach-O apply them
    via :func:`_apply_native_provenance`. A no-op when no header set is supplied.
    ``debug_format`` forces the ELF debug format. ``notify`` receives
    user-facing progress notes (see :func:`abicheck.service.resolve_input`).

    ``public_include_search_dirs`` (PE/Mach-O and the ``hybrid`` merge only;
    mirrors ``dumper.dump``'s own parameter of the same name for ELF) is the
    caller's own genuinely explicit ``-I``/``--include`` list, distinct from
    ``includes`` -- which a caller may have already widened with auto-derived
    directories (e.g. an umbrella ``-H`` header's own directory, seeded purely
    so its relative ``#include``s resolve) before calling this function. When
    given, it -- not the possibly-widened ``includes`` -- is what reaches
    :func:`_apply_native_provenance`/the header-only graph attach, so an
    auto-derived directory can never silently promote a private sibling
    header to ``PUBLIC_HEADER`` on these two formats the way it once did for
    ELF (Codex review, PR #839 round 9). Omitted (``None``, the default),
    every existing caller's behavior is unchanged: ``includes`` itself is
    used, same as before this parameter existed.

    The header-only (L2) semantic graph
    (:func:`abicheck.buildsource.header_graph.build_header_only_graph`, ADR-041
    addendum) — a smaller, build-free alternative to the L4/L5 build-integrated
    graph, available uniformly across all three binary formats — is always
    attempted (G29 Phase A: no longer flag-gated). A no-op when no headers were
    parsed; degrades to a graph with declaration-visibility nodes only (no
    type/call edges) when clang is unavailable. The include-file extension
    (:class:`abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor`,
    adding ``COMPILE_UNIT_INCLUDES_FILE`` edges from each top-level header to
    everything it transitively includes) is also always attempted.

    Raises:
        SnapshotError: If the binary cannot be parsed.
        ValidationError: For invalid arguments (missing exports, bad include dirs,
            or a non-``None`` ``dump_manifest`` for a non-ELF binary).
    """
    if dump_manifest is not None and binary_fmt != "elf":
        raise ValidationError(
            f"dump_manifest is not yet supported for {binary_fmt.upper()} "
            "binaries; use a single-header dump for this format."
        )

    _headers = headers or []
    _includes = includes or []
    # See this function's own docstring: falls back to `_includes` when the
    # caller doesn't distinguish an explicit -I list from a widened one.
    _public_include_search_dirs = (
        list(public_include_search_dirs)
        if public_include_search_dirs is not None
        else _includes
    )
    # Every format's own main pass normalizes `lang` to only ever force a
    # language explicitly requested, letting auto-detection run otherwise
    # (including for the default "c++") -- `_cache_key` hashes the raw
    # `lang` value, so `_attach_header_graph`'s own clang_header_dump call
    # must pass this identical normalized value, or it hashes a different
    # key than the main pass just used, permanently missing the AST memo
    # for the default (non-explicit-"c") workload (Codex review). ELF does
    # this in `extract_elf` below (case-sensitive `lang == "c"`); PE/Mach-O do
    # it in `service_header_scoped._try_header_scoped_dump` -- reached
    # whenever headers are given, the only case this graph attach does
    # anything at all -- with a case-*insensitive* `lang.lower() == "c"`,
    # so the two branches deliberately differ (Codex review, twice: the
    # first pass wrongly assumed PE/Mach-O never normalized `lang` at all).
    #
    # G31 Phase C follow-up: `lang_explicit` (from `DumpRequest.lang_explicit`/
    # `CompareRequest.lang_explicit`) widens the "force" condition beyond a
    # bare `lang == "c"` -- a genuinely explicit request forces whatever
    # language the caller named (not just "c"), on both this graph pass and
    # `extract_elf`/`_try_header_scoped_dump`'s own primary pass below, so the
    # two can never silently disagree about which language mode parsed the
    # library's own headers (AGENTS.md "dump --lang c++ is silently
    # discarded ..." known gap). `False` (the default) is a no-op: identical
    # to the pre-existing behavior above.
    _header_graph_lang = (
        (lang if (lang_explicit or lang == "c") else None)
        if binary_fmt == "elf"
        else (lang if (lang_explicit or lang.lower() == "c") else None)
    )
    # An explicit --ast-frontend on the compile context wins over the bare
    # header_backend arg (the latter is the compare-path default carrier).
    # .lower() (Codex review): compile.frontend="AUTO" is an accepted,
    # case-insensitive spelling that must mean "no override" -- else a
    # pinned, already-resolved header_backend (service_dump_pipeline.
    # ResolvedDumpRequest.effective_header_backend) is silently discarded
    # in favor of re-resolving "AUTO" against a live env read below.
    eff_backend = (
        compile.frontend
        if (compile is not None and compile.frontend.lower() != "auto")
        else header_backend
    )

    from ...extract.header_ast_backend import _resolve_header_backend

    request = NativeExtractRequest(
        path=path,
        version=version,
        headers=_headers,
        includes=_includes,
        lang=lang,
        lang_explicit=lang_explicit,
        header_backend=eff_backend,
        compile=compile,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        public_include_search_dirs=_public_include_search_dirs,
        include_labels=include_labels,
        pdb_path=pdb_path,
        dwarf_only=dwarf_only,
        debug_roots=debug_roots,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        debug_format=debug_format,
        symbols_only=symbols_only,
        debug_presence_only=debug_presence_only,
        dump_manifest=dump_manifest,
        notify=notify,
    )
    if _resolve_header_backend(eff_backend) == "hybrid":
        # G28 Phase 3: the Tier-2 hybrid entry point the CLI routes through
        # (dumper.dump() has its own path for direct Python-API callers):
        # one castxml leg and one clang leg of the same request, merged.
        # Each leg skips the header-only graph -- it would be seeded from
        # only that backend's declarations -- and the graph is attached once
        # below, to the union. No attach_clang_layout here: each leg's own
        # tail already ran it, so re-running it on the merge backfills
        # nothing.
        merged = compose_hybrid(
            functools.partial(
                _extract_and_finish,
                binary_fmt,
                header_graph_lang=_header_graph_lang,
                skip_header_graph_attach=True,
            ),
            request,
            header_roots=run_dump_header_roots(
                headers, dump_manifest, public_headers, public_header_dirs
            ),
        )
        # dwarf_only/symbols_only mean "ignore headers entirely", same as the
        # ELF tail's own _attach_header_graph call (Codex review).
        return _attach_header_graph(
            merged,
            _HEADER_GRAPH_ENABLED and not dwarf_only and not symbols_only,
            _HEADER_GRAPH_INCLUDES_ENABLED and not dwarf_only and not symbols_only,
            _headers,
            _includes,
            _header_graph_lang,
            compile,
            public_headers,
            public_header_dirs,
            include_search_dirs=_public_include_search_dirs,
        )

    return _extract_and_finish(
        binary_fmt,
        request,
        header_graph_lang=_header_graph_lang,
        skip_header_graph_attach=False,
    )


def _extract_and_finish(
    binary_fmt: str,
    request: NativeExtractRequest,
    *,
    header_graph_lang: str | None,
    skip_header_graph_attach: bool,
) -> AbiSnapshot:
    """One single-backend dump: the format's adapter, then its tail.

    The ELF tail attaches SYCL/Python/NumPy metadata, the header-only graph
    and the clang layout; PE and Mach-O share :func:`_finish_native_snapshot`.
    Both renumber anonymous closure identities exactly once, at the end.
    """
    from ...extract.header_ast_backend import _resolve_header_backend
    from ...storage.header_ast_cache import ast_memoize_scope

    adapter = FORMAT_ADAPTERS.get(binary_fmt)
    if adapter is None:
        raise UnsupportedArtifactError(f"Unsupported binary format: {binary_fmt}")
    path = request.path
    _headers = request.headers
    _includes = request.includes
    lang = request.lang
    compile = request.compile
    public_headers = request.public_headers
    public_header_dirs = request.public_header_dirs
    _public_include_search_dirs = request.public_include_search_dirs
    dwarf_only = request.dwarf_only
    symbols_only = request.symbols_only
    eff_backend = request.header_backend
    _header_graph_lang = header_graph_lang
    _skip_header_graph_attach = skip_header_graph_attach
    if binary_fmt == "elf":
        _graph_wanted = not (_skip_header_graph_attach or dwarf_only or symbols_only)
        _prefetched_graph = prefetch_graph_if_useful(
            _HEADER_GRAPH_ENABLED and _graph_wanted,
            _resolve_header_backend(eff_backend),
            _headers,
            _includes,
            _header_graph_lang,
            compile,
        )
        # See the hybrid-path scope above -- but only worth opening when
        # _attach_header_graph below will actually run: it no-ops on
        # `_skip_header_graph_attach`/`dwarf_only`/`symbols_only` and on
        # empty `_headers`, which `dump_manifest` guarantees (mutually
        # exclusive with `headers`, api_types.py). Opening it unconditionally
        # would veto the opt-in streaming pruner for a manifest dump's own
        # TU parses too whenever they share this thread (single TU /
        # `ABICHECK_TU_JOBS=1`) -- protecting a memo nothing will ever read
        # (Codex review, PR #840).
        # defer_closure_identity_renumbering (Codex review, fresh evidence):
        # attach_clang_layout below independently derives a base's name from
        # clang's still-`:line:col`-form spelling, so pre-renumbering here
        # (as extract_elf's own dump() otherwise would) leaves `base_offsets`
        # keyed differently than the already-`#N` `bases` -- and renumbering
        # twice isn't safe either, since a second pass only sees the surviving
        # raw markers and assigns them ordinals from that narrower view.
        # Suppressed for this whole branch, renumbered once at the end.
        with (
            ast_memoize_scope() if _headers and _graph_wanted else nullcontext(),
            closure_identity.defer_closure_identity_renumbering(),
            prefetch_settled_on_failure(_prefetched_graph),
        ):
            snap = adapter.extract(request)
        try_attach_sycl_metadata(snap, path)
        try_attach_python_ext_metadata(snap)
        try_attach_python_api_surface(snap)
        try_attach_numpy_capi_surface(snap, path)
        # dwarf_only/symbols_only mean "ignore headers entirely" -- extract_elf
        # above already honors both, so the header-graph attach must not
        # silently re-parse those headers and attach L2 build_source evidence
        # to what the caller explicitly requested as DWARF-only/symbols-only
        # (Codex review).
        snap = _attach_header_graph(
            snap,
            _HEADER_GRAPH_ENABLED and _graph_wanted,
            _HEADER_GRAPH_INCLUDES_ENABLED and _graph_wanted,
            _headers,
            _includes,
            _header_graph_lang,
            compile,
            public_headers,
            public_header_dirs,
            # `_public_include_search_dirs`, not `_includes` (Codex review,
            # fresh evidence): `_includes` can already be build/source-
            # evidence-widened, and this graph attach's own node-visibility
            # classification must agree with the primary parse's
            # declaration-provenance classification above, not silently
            # re-widen it.
            include_search_dirs=_public_include_search_dirs,
            prefetched=_prefetched_graph,
        )
        snap = attach_clang_layout(
            snap, _headers, _includes, lang=lang, compile=compile
        )
        return closure_identity.renumber_anonymous_closure_identities(snap)
    # PE and Mach-O: see the ELF branch's comment above -- the same
    # base_offsets/bases spelling mismatch, since both extractors already
    # renumber too early.
    with (
        ast_memoize_scope(),
        closure_identity.defer_closure_identity_renumbering(),
    ):
        snap = adapter.extract(request)
    return _finish_native_snapshot(
        snap,
        path=path,
        headers=_headers,
        includes=_includes,
        lang=lang,
        header_graph_lang=_header_graph_lang,
        compile=compile,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        skip_header_graph=_skip_header_graph_attach or symbols_only,
        public_include_search_dirs=_public_include_search_dirs,
    )


def _finish_native_snapshot(
    snap: AbiSnapshot,
    *,
    path: Path,
    headers: list[Path],
    includes: list[Path],
    lang: str,
    header_graph_lang: str | None,
    compile: CompileContext | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    skip_header_graph: bool,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Shared post-dump tail for the PE and Mach-O branches of ``run_dump``.

    Both formats finish a dump identically — native provenance, the optional
    Python/NumPy surface attachments, the header-only (L2) graph, then the
    clang layout backfill, then a single closure-identity renumbering pass —
    and the two branches only differ in which ``_dump_*`` produced *snap*.
    Kept as one function so a new post-processing step cannot be added to
    one format and silently forgotten on the other (CodeFactor: duplicate
    code). The ELF branch deliberately stays separate: it also attaches
    SYCL metadata and honors ``dwarf_only``, neither of which applies here.

    Callers are expected to have produced *snap* under
    :func:`closure_identity.defer_closure_identity_renumbering` --
    renumbered here exactly once, after ``attach_clang_layout``, so a base's
    offset lands under the same ordinal ``bases`` gets, not disagreeing.

    ``skip_header_graph`` folds the caller's own reasons to suppress the graph
    (a ``hybrid`` leg's ``skip_header_graph_attach``, ``symbols_only``)
    into one flag; the global enablement switches stay this function's business.

    ``public_include_search_dirs`` (see ``_run_dump_uncached``'s own docstring):
    when given, used instead of ``includes`` for both the flat-snapshot
    provenance widening and the header-graph attach, so an already-widened
    ``includes`` (auto-derived directories included) can never leak into
    either. Defaults to ``includes`` itself, unchanged from before this
    parameter existed.
    """
    _public_dirs = (
        public_include_search_dirs
        if public_include_search_dirs is not None
        else includes
    )
    snap = _apply_native_provenance(
        snap, public_headers, public_header_dirs, _public_dirs
    )
    try_attach_python_ext_metadata(snap)
    try_attach_python_api_surface(snap)
    try_attach_numpy_capi_surface(snap, path)
    snap = _attach_header_graph(
        snap,
        _HEADER_GRAPH_ENABLED and not skip_header_graph,
        _HEADER_GRAPH_INCLUDES_ENABLED and not skip_header_graph,
        headers,
        includes,
        header_graph_lang,
        compile,
        public_headers,
        public_header_dirs,
        include_search_dirs=_public_dirs,
    )
    snap = attach_clang_layout(snap, headers, includes, lang=lang, compile=compile)
    return closure_identity.renumber_anonymous_closure_identities(snap)


@functools.wraps(_run_dump_uncached)  # name lookup below so patching sticks
def _call_run_dump_uncached(*args: Any, **kwargs: Any) -> AbiSnapshot:
    return _run_dump_uncached(*args, **kwargs)


run_dump = granting_live_source_licence(
    wrap_run_dump_with_dependency_scope(_call_run_dump_uncached)
)  # source-read licence: granted at the one shared live extraction every front end funnels through, never in a front end's own wrapper (see buildsource.source_inputs.granting_live_source_licence)
# CodeRabbit: both functools.wraps() above copy __name__ down the chain from _run_dump_uncached, so run_dump.__name__ read as "_run_dump_uncached" -- wrong for any introspecting caller. __signature__ is unaffected.
run_dump.__name__ = "run_dump"
run_dump.__qualname__ = "run_dump"


def _apply_native_provenance(
    snap: AbiSnapshot,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Tag declaration provenance on a PE/Mach-O snapshot (ADR-024 Phase 1).

    Mirrors the ELF path (``dumper.create_snapshot``), which always runs
    ``apply_provenance`` and, since the same PR's ELF-side fix, folds the
    caller's ``-I`` roots in too. A no-op when no public-header set is
    supplied — every origin stays ``UNKNOWN`` and behaviour is unchanged.
    Without ``include_search_dirs`` here, a declaration reached only
    transitively through PE/Mach-O's own ``-I`` (never itself named as a
    root) stayed ``PRIVATE_HEADER`` and could be excluded from the public
    surface — the exact false-clean result the ELF fix closed, left open on
    these two formats (Codex review, fresh evidence).
    """
    from ...workflows.snapshot_factory import finish_provenance

    return finish_provenance(
        snap,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
    )


def extract_elf(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    lang_explicit: bool = False,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    notify: Callable[[str], None] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Dump an ELF binary to an ABI snapshot.

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024). They are threaded into :func:`dumper.dump`, which runs
    ``apply_provenance`` over the parsed surface — the same call the ``dump`` CLI
    makes (``cli_dump_helpers._run_elf_dump``). Without this thread-through the
    ELF service path leaves every origin ``UNKNOWN``, silently disabling the
    provenance-gated cross-checks on the ``scan`` entry point.

    ``dump_manifest`` (ADR-050 D3) is a parsed multi-TU manifest replacing
    *headers* for this dump; threaded straight into :func:`dumper.dump`,
    which enforces the mutual-exclusivity rule against *headers*/
    *public_headers*/*public_header_dirs*.

    ``public_include_search_dirs`` is the caller's genuinely explicit ``-I``
    list, kept separate from *includes* -- which can already be widened by
    the time it reaches here (Codex review) -- so provenance widening never
    uses a build-derived directory. Falls back to ``list(includes)`` when
    omitted (unchanged prior behavior).
    """
    from ...dumper import dump

    # P1.1 (ADR-021a): a resolved detached debug artifact (--debug-root /
    # --debuginfod) was previously only used for a CLI log line -- the
    # DWARF parse always read `path` itself, so a stripped .so stayed
    # L0-only after abicheck reported finding the debug file. Resolve here
    # (gated as the CLI is) and thread it to dumper.dump instead of `path`.
    debug_info_path: Path | None = None
    if (
        not symbols_only
        and not debug_presence_only
        and (debug_roots or enable_debuginfod)
    ):
        from ...debug_resolver import resolve_debug_info

        artifact = resolve_debug_info(
            path,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
        )
        if artifact is not None and artifact.dwarf_path is not None:
            resolved_dwarf = artifact.dwarf_path.resolve()
            if resolved_dwarf != path.resolve():
                debug_info_path = artifact.dwarf_path
                message = f"Debug info for {path.name}: {artifact.source}"
                if notify is not None:
                    notify(message)
                else:
                    _logger.info(message)

    from ...compile_context import CompileContext

    cc = compile if compile is not None else CompileContext()
    resolved_headers = expand_header_inputs(headers) if headers else []
    if not resolved_headers and symbols_only and dump_manifest is None:
        emit_notice(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Using exported symbols only for binary-depth scan.",
        )
    elif not resolved_headers and not dwarf_only and dump_manifest is None:
        emit_notice(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Will use DWARF debug info if available, else symbols-only mode.",
        )
    if resolved_headers and not dwarf_only:
        for inc in includes:
            if not inc.exists() or not inc.is_dir():
                raise ValidationError(
                    f"Include directory not found or not a directory: {inc}"
                )
    elif includes and not dwarf_only and dump_manifest is None:
        emit_notice(notify, "Warning: --include paths are ignored without headers.")

    # P3: auto-add the public-header roots to the search path. Same bucket
    # selection as the dump CLI path (resolve_inferred_header_roots): plain
    # -I with no compile-context includes, else -isystem (below build-
    # context dirs, above system dirs) -- keeps priority without dropping
    # the root below system headers.
    eff_includes = list(includes)
    eff_tokens: tuple[str, ...] = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    if resolved_headers and not dwarf_only:
        inc_extra, deferred = resolve_inferred_header_roots(
            headers,
            list(includes),
            gcc_options=cc.gcc_options,
            gcc_option_tokens=cc.gcc_option_tokens,
        )
        eff_includes += inc_extra
        eff_tokens = cc.gcc_option_tokens + tuple(deferred)
        # Deferred roots ride in gcc_option_tokens (-isystem), not extra_includes,
        # so hash them into the AST cache key explicitly, folding in any
        # include-search dir in cc.gcc_option_tokens itself so this PRIMARY
        # parse's key stays aligned with _attach_header_graph's own fold above
        # (Codex review).
        deferred_dirs = tuple(
            deferred_token_dirs(deferred)
        ) + cache_relevant_operand_paths(cc.gcc_option_tokens)

    compiler = "cc" if lang == "c" else "c++"
    try:
        return dump(
            so_path=path,
            headers=resolved_headers,
            extra_includes=eff_includes,
            # Provenance widening gets ONLY the caller's own explicit -I
            # list -- see dump()'s own docstring note on
            # `public_include_search_dirs` (real regression: `eff_includes`
            # also carries `inc_extra`'s auto-added umbrella-header
            # directory, which can hold a genuinely private sibling header).
            # Prefer the caller's own separately-threaded, genuinely
            # explicit list over this function's own `includes` parameter
            # (which can already be build/source-evidence-widened by the
            # time it reaches here -- Codex review, fresh evidence; see
            # this function's own docstring).
            public_include_search_dirs=(
                list(public_include_search_dirs)
                if public_include_search_dirs is not None
                else list(includes)
            ),
            version=version,
            compiler=compiler,
            gcc_path=cc.gcc_path,
            gcc_prefix=cc.gcc_prefix,
            gcc_options=cc.gcc_options,
            gcc_option_tokens=eff_tokens,
            sysroot=cc.sysroot,
            nostdinc=cc.nostdinc,
            # G31 Phase C follow-up: an explicit request (`lang_explicit`)
            # forces `lang` here regardless of value, matching this call's
            # own `_header_graph_lang` sibling in `run_dump` above -- both
            # must agree on the same explicit-vs-auto-detected decision
            # (AGENTS.md "dump --lang c++ is silently discarded ..." known
            # gap). `lang_explicit=False` (the default) is a no-op: identical
            # to the pre-existing "force only bare 'c'" behavior.
            lang=lang if (lang_explicit or lang == "c") else None,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            header_backend=header_backend,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_hash_dirs=deferred_dirs,
            debug_info_path=debug_info_path,
            extra_include_labels=include_labels,
            dump_manifest=dump_manifest,
            frontend_context=cc.frontend_context,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to dump '{path}': {exc}") from exc


class ElfAdapter:
    """The ELF :class:`~abicheck.workflows.dump.formats.BinaryFormatAdapter`.

    Defined here, beside :func:`extract_elf`, rather than in ``formats``:
    ``formats`` importing this module back would close an import cycle.
    """

    format = "elf"

    def extract(self, request: NativeExtractRequest) -> AbiSnapshot:
        r = request
        return extract_elf(
            r.path,
            r.headers,
            r.includes,
            r.version,
            r.lang,
            lang_explicit=r.lang_explicit,
            dwarf_only=r.dwarf_only,
            debug_roots=r.debug_roots,
            enable_debuginfod=r.enable_debuginfod,
            debuginfod_url=r.debuginfod_url,
            debug_format=r.debug_format,
            symbols_only=r.symbols_only,
            debug_presence_only=r.debug_presence_only,
            header_backend=r.header_backend,
            compile=r.compile,
            public_headers=r.public_headers,
            public_header_dirs=r.public_header_dirs,
            notify=r.notify,
            include_labels=r.include_labels,
            dump_manifest=r.dump_manifest,
            public_include_search_dirs=r.public_include_search_dirs,
        )


#: The binary-format registry ``run_dump`` dispatches through, keyed by
#: ``detect_binary_format``'s spelling. Replace an entry to substitute an
#: extractor (``tests/_dump_format_fakes.py`` does so for one ``with`` block).
FORMAT_ADAPTERS: dict[str, BinaryFormatAdapter] = {
    **DEFAULT_ADAPTERS,
    "elf": ElfAdapter(),
}
