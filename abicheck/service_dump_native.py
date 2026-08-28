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

"""Native-binary dump orchestration: ``service.run_dump`` and its ELF tail.

Split out of ``service.py`` (ADR-061 "make service.py a thin facade" pass)
as a leaf module, the pattern ``service_metadata_attach``/
``service_header_graph_attach``/``service_header_scoped``/``service_render``/
``service_scan``/``service_compare_pipeline``/``service_dump_pipeline``
already follow. The PE/Mach-O half of the same original block
(``_dump_pe``/``_dump_macho``/``_extract_pdb_debug``) lives one file
further out, in ``service_dump_native_pe`` (imported/re-exported below) —
this module alone, pre-split, was already over the 800-line production cap
a genuinely new file gets no debt-ledger baseline to grow into.
``service.py`` re-exports every public name from both modules, so
``from abicheck.service import run_dump`` (and the several
``_dump_elf``/``_dump_pe``/``_dump_macho``/``_run_dump_uncached`` names
tests patch directly) keep resolving unchanged.

**Test-patch note** (mirrors ``workflows/extraction.py``'s own documented
gotcha): a call from one function below to another resolves against *this
module's* globals, not whatever ``abicheck.service`` re-exports. A test
substituting ``_dump_elf``/``_run_dump_uncached``/``_attach_header_graph``
for a caller defined here must patch ``abicheck.service_dump_native.<name>``,
not ``abicheck.service.<name>``. Same rule one module over for
``_dump_pe``/``_dump_macho`` — see ``service_dump_native_pe.py``.
"""

from __future__ import annotations

import functools
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import qualified_name_segments
from .clang_layout_tool import attach_clang_layout
from .dumper_scoping import wrap_run_dump_with_dependency_scope
from .errors import AbicheckError, SnapshotError, ValidationError
from .header_utils import (
    cache_relevant_operand_paths,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from .model import AbiSnapshot
from .service_header_graph_attach import _attach_header_graph
from .service_metadata_attach import (
    _try_attach_numpy_capi_surface,
    _try_attach_python_api_surface,
    _try_attach_python_ext_metadata,
    _try_attach_sycl_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .compile_context import CompileContext
    from .dump_manifest import DumpManifest

# Deliberately the *parent* module's logger name, not this module's: these
# functions logged under "abicheck.service" before the split, and callers
# (and tests capturing caplog) rely on that name — same convention
# ``service_metadata_attach.py`` already documents for the identical reason.
_logger = logging.getLogger("abicheck.service")

# G29 Phase A: the L2 header-only semantic graph (ADR-041 addendum) and its
# include-file extension used to be strictly opt-in via ``--header-graph``/
# ``--header-graph-includes``. They are now always attempted whenever headers
# are available (``_attach_header_graph`` itself still no-ops without parsed
# headers, and degrades to a declaration-only graph when clang is
# unavailable) — no public flag controls this anymore; see
# ``docs/contribute/plans/g31-header-graph-default-on-followup.md``.
# TODO(header-graph-phase-D): ``header_graph_includes`` runs one extra
# ``clang -M`` pass per top-level header on every dump/compare with no
# caching of its own (only the aggregate AST pass is disk-cached via
# ``_clang_header_dump``) — bounded by header count, fails soft when clang is
# unavailable, but not yet cheap. Caching this pass is deferred to Phase D.
_HEADER_GRAPH_ENABLED = True
_HEADER_GRAPH_INCLUDES_ENABLED = True


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
    _skip_header_graph_attach: bool = False,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Extract an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    ``_skip_header_graph_attach`` is a private, internal-only knob (not
    public API, not CLI-reachable) used solely by this function's own
    ``header_backend="hybrid"`` recursion below: each single-backend
    sub-dump would otherwise redundantly attach its own header-only graph
    (seeded from only that one backend's declarations) before the merge
    throws it away, wasting a whole extra clang AST pass per sub-dump. The
    graph is instead attached exactly once, after the merge, to the union of
    both backends' declarations.

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
            "binaries (ADR-050 D3); use a single-header dump for this format."
        )
    from . import dumper_cache

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
    # `lang` value, so `_attach_header_graph`'s own _clang_header_dump call
    # must pass this identical normalized value, or it hashes a different
    # key than the main pass just used, permanently missing the AST memo
    # for the default (non-explicit-"c") workload (Codex review). ELF does
    # this in `_dump_elf` below (case-sensitive `lang == "c"`); PE/Mach-O do
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
    # `_dump_elf`/`_try_header_scoped_dump`'s own primary pass below, so the
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

    from .dumper import _resolve_header_backend

    if _resolve_header_backend(eff_backend) == "hybrid":
        # G28 Phase 3: the real Tier-2 hybrid entry point the CLI routes
        # through (dumper.dump() has its own, simpler recursion for direct
        # Python-API callers) — recurse into run_dump() once per real
        # backend, forcing frontend via a *replaced* CompileContext (frozen
        # dataclass) so it wins eff_backend's precedence check regardless of
        # header_backend, then merge; only the merge step is new.
        from dataclasses import replace as _dc_replace

        from .compile_context import CompileContext
        from .dumper_hybrid import merge_snapshots

        def _forced_compile(frontend: str) -> CompileContext:
            return (
                _dc_replace(compile, frontend=frontend)
                if compile is not None
                else CompileContext(frontend=frontend)
            )

        common_kwargs: dict[str, Any] = {
            "headers": headers,
            "includes": includes,
            "version": version,
            "lang": lang,
            "lang_explicit": lang_explicit,
            "pdb_path": pdb_path,
            "dwarf_only": dwarf_only,
            "debug_roots": debug_roots,
            "enable_debuginfod": enable_debuginfod,
            "debuginfod_url": debuginfod_url,
            "debug_format": debug_format,
            "symbols_only": symbols_only,
            "debug_presence_only": debug_presence_only,
            "public_headers": public_headers,
            "public_header_dirs": public_header_dirs,
            "public_include_search_dirs": public_include_search_dirs,
            # The header-graph attach is deliberately SKIPPED on either
            # recursive sub-dump below (each would otherwise attach its OWN
            # graph, seeded from only ITS OWN backend's declarations, before
            # the merge throws it away) — attached once, after the merge, to
            # the union of both backends' declarations instead (see the
            # _attach_header_graph call below; Codex review; G29 Phase A:
            # ``_skip_header_graph_attach`` replaces the old "just don't
            # forward header_graph=True" mechanism now that the attach is
            # unconditional rather than flag-gated).
            "notify": notify,
            "_skip_header_graph_attach": True,
            "include_labels": include_labels,
            "dump_manifest": dump_manifest,
        }
        # In-process AST memoization (G31 Phase C) is only worthwhile inside
        # this scope: the _attach_header_graph call below is a real
        # downstream consumer, unlike a direct dumper.dump() caller with no
        # such follow-up (Codex review) -- see dumper_cache.ast_memoize_scope.
        #
        # defer_closure_identity_renumbering (Codex review): this recursion
        # is the same shape dumper_hybrid.run_hybrid_dump merges, and
        # without it reproduces the bug that fix closed -- each recursive
        # run_dump() independently renumbers its own closure markers before
        # the merge, desynchronizing the two backends' ordinals for one
        # closure. Suppressed here, renumbered once on the merged result.
        with (
            dumper_cache.ast_memoize_scope(),
            qualified_name_segments.defer_closure_identity_renumbering(),
        ):
            castxml_snap = run_dump(
                path,
                binary_fmt,
                header_backend="castxml",
                compile=_forced_compile("castxml"),
                **common_kwargs,
            )
            clang_snap = run_dump(
                path,
                binary_fmt,
                header_backend="clang",
                compile=_forced_compile("clang"),
                **common_kwargs,
            )
        merged = qualified_name_segments.renumber_anonymous_closure_identities(
            merge_snapshots(castxml_snap, clang_snap)
        )
        # No attach_clang_layout call here: clang_snap's own recursive call
        # above already got it (the ELF/PE/Mach-O tail below always calls it),
        # so re-running it on merged would backfill nothing (review finding).
        # dwarf_only/symbols_only mean "ignore headers entirely", same as the
        # ELF tail's own _attach_header_graph call below (Codex review).
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

    if binary_fmt == "elf":
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
        # (as _dump_elf's own dump() otherwise would) leaves `base_offsets`
        # keyed differently than the already-`#N` `bases` -- and renumbering
        # twice isn't safe either, since a second pass only sees the surviving
        # raw markers and assigns them ordinals from that narrower view.
        # Suppressed for this whole branch, renumbered once at the end.
        with (
            dumper_cache.ast_memoize_scope()
            if _headers and not _skip_header_graph_attach and not dwarf_only and not symbols_only
            else nullcontext()
        ), qualified_name_segments.defer_closure_identity_renumbering():
            snap = _dump_elf(
                path,
                _headers,
                _includes,
                version,
                lang,
                lang_explicit=lang_explicit,
                dwarf_only=dwarf_only,
                debug_roots=debug_roots,
                enable_debuginfod=enable_debuginfod,
                debuginfod_url=debuginfod_url,
                debug_format=debug_format,
                symbols_only=symbols_only,
                debug_presence_only=debug_presence_only,
                header_backend=eff_backend,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                notify=notify,
                include_labels=include_labels,
                dump_manifest=dump_manifest,
                public_include_search_dirs=_public_include_search_dirs,
            )
        _try_attach_sycl_metadata(snap, path)
        _try_attach_python_ext_metadata(snap)
        _try_attach_python_api_surface(snap)
        _try_attach_numpy_capi_surface(snap, path)
        # dwarf_only/symbols_only mean "ignore headers entirely" -- _dump_elf
        # above already honors both, so the header-graph attach must not
        # silently re-parse those headers and attach L2 build_source evidence
        # to what the caller explicitly requested as DWARF-only/symbols-only
        # (Codex review).
        snap = _attach_header_graph(
            snap,
            _HEADER_GRAPH_ENABLED
            and not _skip_header_graph_attach
            and not dwarf_only
            and not symbols_only,
            _HEADER_GRAPH_INCLUDES_ENABLED
            and not _skip_header_graph_attach
            and not dwarf_only
            and not symbols_only,
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
        )
        snap = attach_clang_layout(
            snap, _headers, _includes, lang=lang, compile=compile
        )
        return qualified_name_segments.renumber_anonymous_closure_identities(snap)
    if binary_fmt == "pe":
        # See the ELF branch's comment above -- same base_offsets/bases
        # spelling mismatch, since _dump_pe already renumbers too early.
        with (
            dumper_cache.ast_memoize_scope(),
            qualified_name_segments.defer_closure_identity_renumbering(),
        ):
            snap = _dump_pe(
                path,
                version,
                headers=_headers,
                includes=_includes,
                lang=lang,
                lang_explicit=lang_explicit,
                pdb_path=pdb_path,
                header_backend=eff_backend,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                include_labels=include_labels,
            )
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
    if binary_fmt == "macho":
        # See the ELF/PE branches' own comments above -- same mismatch.
        with (
            dumper_cache.ast_memoize_scope(),
            qualified_name_segments.defer_closure_identity_renumbering(),
        ):
            snap = _dump_macho(
                path,
                version,
                headers=_headers,
                includes=_includes,
                header_backend=eff_backend,
                lang=lang,
                lang_explicit=lang_explicit,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                include_labels=include_labels,
            )
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
    raise ValidationError(f"Unsupported binary format: {binary_fmt}")


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
    :func:`qualified_name_segments.defer_closure_identity_renumbering` --
    renumbered here exactly once, after ``attach_clang_layout``, so a base's
    offset lands under the same ordinal ``bases`` gets, not disagreeing.

    ``skip_header_graph`` folds the caller's own reasons to suppress the graph
    (the ``hybrid`` recursion's ``_skip_header_graph_attach``, ``symbols_only``)
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
    snap = _apply_native_provenance(snap, public_headers, public_header_dirs, _public_dirs)
    _try_attach_python_ext_metadata(snap)
    _try_attach_python_api_surface(snap)
    _try_attach_numpy_capi_surface(snap, path)
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
    return qualified_name_segments.renumber_anonymous_closure_identities(snap)


@functools.wraps(_run_dump_uncached)  # name lookup below so patching sticks
def _call_run_dump_uncached(*args: Any, **kwargs: Any) -> AbiSnapshot:
    return _run_dump_uncached(*args, **kwargs)


run_dump = wrap_run_dump_with_dependency_scope(_call_run_dump_uncached)
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
    from .provenance import apply_provenance

    return apply_provenance(
        snap,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
    )


def _emit(notify: Callable[[str], None] | None, message: str) -> None:
    """Send a user-facing progress note to *notify*, or the logger if unset."""
    if notify is not None:
        notify(message)
    else:
        _logger.warning(message)


def _dump_elf(
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
    from .dumper import dump

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
        from .debug_resolver import resolve_debug_info

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

    from .compile_context import CompileContext

    cc = compile if compile is not None else CompileContext()
    resolved_headers = expand_header_inputs(headers) if headers else []
    if not resolved_headers and symbols_only and dump_manifest is None:
        _emit(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Using exported symbols only for binary-depth scan.",
        )
    elif not resolved_headers and not dwarf_only and dump_manifest is None:
        _emit(
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
        _emit(notify, "Warning: --include paths are ignored without headers.")

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


# PE/Mach-O dump (``_dump_pe``/``_dump_macho``) and the PDB-debug helper they
# share (``_extract_pdb_debug``) live in the sibling module
# ``service_dump_native_pe`` -- split out purely to stay under the
# AI-readiness 800-line production cap for a *new* file (which, unlike this
# already-baselined module's own predecessor in ``service.py``, has no
# debt-ledger entry to grow into). Re-exported here so
# ``_run_dump_uncached``'s own bare-name calls below keep resolving, and so
# ``from abicheck.service_dump_native import _dump_pe`` (and, via
# ``service.py``'s own re-export, ``from abicheck.service import
# _dump_pe``) keep working unchanged.
from .service_dump_native_pe import (  # noqa: E402
    _dump_macho as _dump_macho,
    _dump_pe as _dump_pe,
    _extract_pdb_debug as _extract_pdb_debug,
)

# expand_header_inputs is the scan-engine's own header expansion helper,
# re-exported through ``service_scan`` -- imported lazily below to avoid a
# module-load-time cycle (``service_scan`` -> ... -> this module's own
# siblings), matching how ``service.py`` itself deferred this before the
# split.
from .service_scan import expand_header_inputs  # noqa: E402
