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

"""``_attach_header_graph``, split out of ``service.py`` purely to stay under
the AI-readiness 2000-line hard cap -- the identical reason `service_render.py`/
`dry_run_estimate.py`/`service_compare_pipeline.py`/`service_dump_pipeline.py`
already moved out of that file (see ``service.py``'s own tail-of-file re-export
block for the established precedent this follows). No behavior change: same
function body, same signature.

Re-exported eagerly as ``service._attach_header_graph`` (not a lazy shim) since
it is patched directly by name in a large number of tests
(``monkeypatch.setattr("abicheck.service._attach_header_graph", ...)``,
``unittest.mock.patch("abicheck.service._attach_header_graph", ...)``) and
imported directly (``from abicheck.service import _attach_header_graph``) --
both keep working unchanged because Python resolves a module-level name via
the module's own ``__dict__`` at call time, regardless of where the name was
originally defined.

No import-cycle risk: this module imports from ``.compile_context``,
``.dry_run_estimate``, ``.header_utils``, ``.errors``, ``.model`` -- none of which
import ``.service`` or this module back.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compile_context import CompileContext
from .dry_run_estimate import expand_header_inputs
from .errors import SnapshotError, ValidationError
from .header_utils import (
    cache_relevant_operand_paths,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)

if TYPE_CHECKING:
    from .model import AbiSnapshot
from .workflows import memory_trace

_log = logging.getLogger(__name__)


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
# ``clang_header_dump``) — bounded by header count, fails soft when clang is
# unavailable, but not yet cheap. Caching this pass is deferred to Phase D.
_HEADER_GRAPH_ENABLED = True
_HEADER_GRAPH_INCLUDES_ENABLED = True


def _attach_header_graph(
    snap: AbiSnapshot,
    header_graph: bool,
    header_graph_includes: bool,
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    include_search_dirs: list[Path] | None = None,
    prefetched: Future[HeaderGraphAst] | None = None,
) -> AbiSnapshot:
    """Build and embed the header-only (L2) semantic graph (ADR-041 addendum).

    A no-op when ``header_graph`` was not requested or no headers were parsed.
    Calls the same ``extract.headers.clang.backend.clang_header_dump`` the main clang-frontend
    snapshot pass already used — reused directly (private only by
    convention; ``dumper.py`` sits at its 2000-line hard cap, so a public
    wrapper is not added there) rather than threading the parser's
    already-consumed AST back out through three format-specific builders.
    When the main snapshot pass ran under ``--ast-frontend clang`` with the
    identical resolved headers/includes, this is no longer a second
    *independent* parse: ``dumper_cache``'s in-process AST memo (G31 Phase C)
    returns the already-parsed dict straight away, skipping a second disk
    read/JSON re-parse. It stays a genuine second ``clang`` invocation only
    when the main pass used ``castxml`` (the default backend), which never
    calls ``clang_header_dump`` at all. Mirrors ``_dump_elf``'s own header-expansion
    (``expand_header_inputs`` — a ``headers`` entry may be a directory) and
    inferred-include-root derivation (``resolve_inferred_header_roots`` — an
    umbrella header's relative ``#include``s need the same auto-added ``-I``/
    ``-isystem`` search dirs the main dump computes) so this second pass sees
    the identical resolved input the main dump already parsed successfully,
    rather than the raw, unexpanded arguments (Codex review: without this, a
    header *directory* input made ``clang_header_dump`` write an invalid
    ``#include`` of the directory path itself and raise, and even a single
    umbrella header with relative includes into a sibling directory could
    fail to resolve, both silently degrading to the declaration-only graph).
    Degrades to a graph with declaration-visibility nodes only (no type/call
    edges) when clang is unavailable or the header parse fails — never aborts
    the dump itself (ADR-028 D3).

    ``header_graph_includes`` additionally folds a per-header include graph
    (:class:`~abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor`) —
    a separate opt-in since it costs one extra ``clang -M`` invocation per
    top-level header, not just the one aggregate pass ``header_graph`` alone
    needs.

    ``include_search_dirs`` is forwarded to
    :func:`build_header_only_graph`'s own parameter of the same name —
    each caller's raw, explicit ``-I`` list (never an auto-derived one),
    matching what ``apply_provenance`` already widened *snap*'s own
    per-declaration ``origin`` with, so the graph's header-level nodes
    agree with the flat snapshot instead of independently reclassifying
    the same header ``private_header`` (Codex review, fresh evidence).
    """
    # Boundary, not a stage of this function: everything the primary dump did
    # (binary + debug + the castxml/clang header parse + metadata attach) is
    # closed here, so its peak is attributed to `dump.primary:done` and the
    # stages below start from a fresh window. It lives at the top of this
    # function rather than at the `_dump_elf` call site because `dumper.py`
    # and `workflows/dump/native.py` are both at their debt-ledger line caps.
    memory_trace.mark("dump.primary:done")
    if not header_graph or not headers:
        return snap
    from .buildsource.header_graph import (
        HEADER_INCLUDE_GRAPH_PASS,
        ClangHeaderIncludeExtractor,
        build_header_only_graph,
    )
    from .buildsource.include_graph import augment_graph_with_includes
    from .dumper import _resolve_clang_bin
    from .model.source_graph_coverage import HEADER_CALL_GRAPH_PASS

    cc = compile if compile is not None else CompileContext()
    _is_c = (lang or "").lower() == "c"
    acquired = (
        prefetched.result()
        if prefetched is not None
        else acquire_header_graph_ast(headers, includes, lang, compile)
    )
    projection = acquired.projection
    ast_failure = acquired.ast_failure
    resolved_headers = acquired.resolved_headers
    eff_includes = acquired.eff_includes
    eff_tokens = acquired.eff_tokens
    memory_trace.mark("dump.header_graph.ast_released")
    graph = build_header_only_graph(
        snap,
        ast_projection=projection,
        public_header_paths=[str(p) for p in (public_headers or [])],
        public_dir_paths=[str(p) for p in (public_header_dirs or [])],
        header_paths=[str(p) for p in resolved_headers],
        include_search_dirs=[str(p) for p in (include_search_dirs or [])],
        # Real per-declaration provenance for a hybrid merge (empty dict on
        # every other snapshot, a harmless no-op there) — G31 Phase C
        # hybrid-graph provenance-tagging; see build_header_only_graph's own
        # docstring and dumper_hybrid.merge_snapshots' "visibility" stamp.
        fact_provenance=snap.fact_provenance,
    )
    # The graph build is the projection's only consumer, so let it go here
    # rather than at function exit. Measured, not tidiness: holding it to
    # the end left this attach retaining ~5 MiB MORE than the code it
    # replaced (three runs above both baseline runs on oneDAL), because the
    # projection's own indexes and edge lists -- 23.6 MiB there -- outlived
    # the only thing that reads them. A reordering whose whole claim is
    # "nothing is held longer than it is needed" has to hold that for its
    # own intermediate too.
    projection = None
    if ast_failure is not None:
        graph.degraded_passes[HEADER_CALL_GRAPH_PASS] = True
        graph.finalize()
    memory_trace.mark("dump.header_graph.build:done")
    if header_graph_includes and resolved_headers and cc.frontend_context == "host":
        # `ClangHeaderIncludeExtractor` drives a plain `clang -M` per header
        # with no `-fsycl`/host-vs-device concept at all (unlike the AST pass
        # just above, which threads `frontend_context` through and is
        # validated against a real DPC++ capture, see sycl_context.py) --
        # for a non-host request it would silently resolve `#ifdef
        # __SYCL_DEVICE_ONLY__`-style guards as host and attach host-only
        # include edges to a device snapshot's graph (Codex review). Skipping
        # it entirely leaves the include-graph pass honestly "not collected"
        # for this snapshot (`_include_graph_covered` false, since neither
        # `extractor_passes` nor `degraded_passes` gets stamped) rather than
        # confidently wrong -- the same host/device tradeoff already made for
        # DWARF layout backfill (dumper._dump_elf) and the clang layout tool.
        #
        # Resolve the same clang driver `clang_header_dump` above used
        # (honoring `--compiler`/`--compiler-prefix`) rather than defaulting to
        # the bare "clang++" — otherwise a hermetic/cross toolchain selected
        # via those flags silently loses every COMPILE_UNIT_INCLUDES_FILE
        # edge (or resolves them against the host's clang instead) even
        # though the semantic header graph just above parsed correctly
        # (Codex review). Non-raising here: an unresolvable driver degrades
        # to ClangHeaderIncludeExtractor's own default, which then reports
        # "not found" via its own .available() check rather than aborting
        # the dump (ADR-028 D3).
        try:
            include_clang_bin = _resolve_clang_bin(
                "cc" if _is_c else "c++", cc.gcc_path, cc.gcc_prefix
            )
        except SnapshotError:
            include_clang_bin = "clang" if _is_c else "clang++"
        include_map, include_diags = ClangHeaderIncludeExtractor(
            clang_bin=include_clang_bin
        ).extract(
            [str(p) for p in resolved_headers],
            [str(p) for p in eff_includes],
            language="C" if _is_c else "CXX",
            sysroot=str(cc.sysroot) if cc.sysroot else None,
            nostdinc=cc.nostdinc,
            gcc_options=cc.gcc_options,
            gcc_option_tokens=eff_tokens,
        )
        if include_map:
            augment_graph_with_includes(graph, include_map)
        # A clean pass with an empty map (a leaf public header with no
        # #include of its own, or every resolved include self-filtered) is
        # a genuine zero, not a failure to collect — stamp the pass so
        # `_include_graph_covered` doesn't mistake it for "never ran" and
        # misreport every header on a later comparison's other side as
        # newly entering the include graph (Codex review). Re-finalize
        # unconditionally (even with an empty map) since `finalize()` derives
        # `coverage["include_edges"]["collected"]` from this same marker —
        # skipping it for the empty-map case left that field stale/false
        # despite `extractor_passes` correctly recording the pass as run
        # (Codex review, follow-up). A *partial* run (one header's `clang -M`
        # failed while another's succeeded) folds real edges for the headers
        # that did parse but must not be confirmed as a clean full pass
        # either — mark it degraded instead, mirroring
        # `inline_graph_fold.fold_include_graph`'s own
        # `elif extractor.diagnostics: degraded_passes[...] = True` branch,
        # so `_include_graph_fully_covered` never trusts the missing portion
        # as evidence a header genuinely stopped being included (Codex
        # review, follow-up).
        if include_diags:
            graph.degraded_passes[HEADER_INCLUDE_GRAPH_PASS] = True
        else:
            graph.extractor_passes[HEADER_INCLUDE_GRAPH_PASS] = True
        graph.finalize()
    memory_trace.mark("dump.header_graph.include_pass:done")
    # ADR-063 Phase 3/10: the header graph lives on `surface_graph` alone.
    # This used to also synthesize `snap.build_source = BuildSourcePack(
    # root=Path(""), source_graph=graph)` (with L3/L4 not-collected and an L5
    # row) so legacy `build_source.source_graph` readers saw it; every such
    # reader now goes through `evidence_depth.resolve_l5_source_graph`, and
    # the coverage rows that pack's manifest carried are derived on read by
    # `evidence_depth.header_graph_coverage`. A real `--sources`/
    # `--build-info` embed later adopts this graph into its own pack
    # (`buildsource/embed.py`'s backfill).
    #
    # Deliberately NOT populated with compare/surface_graph.py's own
    # declaration/type/header/symbol facts here: `_attach_header_graph` runs
    # unconditionally on essentially every real dump (G31 Phase A). Paying
    # `build_public_surface_facts`'s per-declaration walk on every dump
    # regressed the header-graph attach-cost perf gate by 47-96% at
    # realistic sizes, and `policy.public_surface_closure` does not read this
    # graph at all (it recomputes references from the snapshot's own current
    # declarations; Codex review, PR #979).
    snap.surface_graph = graph
    return snap


@dataclass(frozen=True)
class HeaderGraphAst:
    """What the header-graph attach needs from its own clang parse."""

    projection: Any
    ast_failure: str | None
    resolved_headers: list[Path]
    eff_includes: list[Path]
    eff_tokens: tuple[str, ...]


def acquire_header_graph_ast(
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> HeaderGraphAst:
    """The header graph's own clang parse, projected; independent of the snapshot.

    Split out of :func:`_attach_header_graph` so it can start before the
    primary dump finishes (``prefetch_header_graph_ast``): its inputs are the
    headers and compile context, never the snapshot it is later attached to.
    """
    from .buildsource.header_graph_ast_projection import (
        HeaderGraphAstProjection,
        merge_header_graph_ast_projections,
        parse_header_groups_bisecting,
        project_header_graph_ast,
    )
    from .buildsource.header_graph_ast_stream import (
        ClangAstStreamError,
        project_header_graph_ast_file,
    )
    from .buildsource.header_graph_projection_cache import (
        load_cached_projection,
        store_cached_projection,
    )
    from .extract.headers.clang.backend import clang_header_dump
    from .extract.headers.clang.streaming import suppress_streaming_prune
    from .storage.derived_ast import DerivedAstArtifact, derived_ast_scope

    # Everything either projection path may raise on an AST that is readable
    # but not shaped the way the readers assume.
    #
    # The readers walk a tree they trust: `for child in node.get("inner", [])
    # or []` raises `TypeError` when `inner` is a number rather than a list,
    # and a sibling shape mismatch raises `AttributeError`. Neither is
    # hypothetical for a *corrupt cache entry*, and neither was contained --
    # so a bad file on disk aborted the whole dump instead of costing the
    # header graph, which is exactly what ADR-028 D3 ("degrade to no fact,
    # never a wrong fact, never an aborted collection") forbids.
    #
    # `RecursionError` is here for the reason `clang_ast_run` already guards
    # it: a pathologically deep TU exhausts the interpreter stack inside
    # `json.loads`. `ValueError` covers `json.JSONDecodeError`, and `OSError`
    # the file going away between the offer and the read.
    #
    # Hardening every walker against every malformed shape is deliberately
    # *not* the fix: it would touch the hottest code in the extractor to
    # defend against input clang never produces, and would mask real shape
    # bugs. Containing it where both paths converge keeps one rule in one
    # place.
    # Below this document size, the whole-tree parse is simply cheaper and
    # costs nothing worth avoiding, so the stream declines and the caller
    # parses normally.
    #
    # Streaming trades CPU for memory: it decodes every element twice (the
    # readers are two-pass and the second pass needs whole-translation-unit
    # indexes) plus a structural scan, for ~2-3x the parse's wall time. That
    # is a good trade only where the memory exists to be saved. Measured, the
    # two ends are 100x apart: this repository's own header-graph perf
    # fixtures produce 0.2/0.6/2.5 MiB documents at sizes 25/100/400, where
    # the whole document and tree together are a few MiB and there is no
    # memory problem at all, while oneDAL's `libonedal_core.so.2` produces
    # 263 MiB, where the attach peaked at 2.1 GiB. Streaming everything made
    # the small case 44-71% slower for nothing, which the PR-vs-base attach
    # gate correctly rejected.
    #
    # A clang AST tree costs roughly 1.5x its document and peaks at roughly
    # 2.5x (263 MiB document -> ~400 MiB of dicts, ~674 MiB peak, measured),
    # so this threshold reads as "stream once the whole-tree peak would
    # exceed roughly 80 MiB". It sits ~13x above the largest fixture and
    # ~100x below the real case, so neither lands near it by accident.
    # `ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB` overrides it, which is how the
    # tests drive both paths over one document.
    _STREAM_MIN_BYTES = (
        float(os.environ.get("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", "32"))
        * 1024
        * 1024
    )

    _MALFORMED_AST_ERRORS = (
        ClangAstStreamError,
        ValueError,
        OSError,
        RecursionError,
        TypeError,
        AttributeError,
    )

    cc = compile if compile is not None else CompileContext()
    # Case-insensitive, None-safe: PE/Mach-O's own main pass
    # (service_header_scoped._try_header_scoped_dump) treats an uppercase
    # "C" the same as "c" for both compiler selection and the AST cache key;
    # every C/C++ branch below must agree with that, or an explicit
    # lang="C" request silently parses as C++ here and/or misses the memo
    # the main pass wrote (CodeRabbit review, Codex review).
    _is_c = (lang or "").lower() == "c"
    ast_root: dict[str, Any] | None = None
    resolved_headers: list[Path] = []
    eff_includes: list[Path] = list(includes)
    eff_tokens: tuple[str, ...] = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    # Bound before the try, not by the `with` below: every path out of that
    # block has to be able to ask whether a cached projection was used, and
    # the block does not run at all when there are no resolved headers or
    # when the clang acquisition raises. An empty artifact reads as "no
    # cache, nothing to store", which is exactly right for both.
    derived_projection = DerivedAstArtifact()
    # Set when the projection below was computed by *streaming* an AST
    # document rather than read from a sidecar, which is the one case that
    # still owes a cache write. `derived_projection.used` cannot say which
    # of the two happened, and storing unconditionally would rewrite an
    # identical sidecar on every warm run.
    streamed_paths: list[Path] = []

    def _projection_for(
        ast_path: Path, *, tree_in_hand: bool = False
    ) -> HeaderGraphAstProjection | None:
        """Answer the AST acquisition with a projection, or decline.

        Offered two different paths (see `dumper_cache.
        offer_derived_ast_source`): the AST *cache entry*, before anything
        is read, and -- when no entry existed -- the document `clang` just
        wrote. Both are single-document AST dumps, so both are streamable;
        the sidecar is only ever consulted for the first, since a fresh
        temp file has none.

        Declining (``None``) is always safe: the caller then parses the AST
        the way it always did. So every failure here degrades to the old
        cost rather than to a wrong or missing graph (ADR-028 D3) -- an
        unreadable document, a truncated one, a shape the scanner does not
        recognise.
        """
        cached = load_cached_projection(ast_path)
        if cached is not None or tree_in_hand:
            # With the tree already parsed (the clang-frontend memo handoff),
            # projecting it beats streaming the same document off disk.
            return cached
        try:
            if ast_path.stat().st_size < _STREAM_MIN_BYTES:
                return None
        except OSError:
            return None
        try:
            with memory_trace.phase("dump.header_graph.project_streaming"):
                projection = project_header_graph_ast_file(ast_path)
        except _MALFORMED_AST_ERRORS:
            return None
        streamed_paths.append(ast_path)
        return projection

    ast_failure: str | None = None
    projection_from_groups: HeaderGraphAstProjection | None = None
    try:
        resolved_headers = expand_header_inputs(headers)
        if resolved_headers:
            # Root inference reads the RAW `headers` (matching `_dump_elf`'s
            # own `resolve_inferred_header_roots(headers, ...)` call), not
            # `resolved_headers` -- `_implicit_header_includes` treats a
            # directory input as a single root but a directory *expanded*
            # into its individual nested files as one root per subdirectory,
            # so using the expanded list here diverged from the main pass
            # for any directory `-H` input with nested subdirectories,
            # producing a different eff_includes/eff_tokens and therefore a
            # different `clang_header_dump` cache key -- silently missing
            # the in-process AST memo in exactly the large-header-tree case
            # this reuse targets (Codex review).
            inc_extra, deferred = resolve_inferred_header_roots(
                headers,
                list(includes),
                gcc_options=cc.gcc_options,
                gcc_option_tokens=cc.gcc_option_tokens,
            )
            eff_includes = list(includes) + inc_extra
            eff_tokens = cc.gcc_option_tokens + tuple(deferred)
            # The deferred roots ride in gcc_option_tokens (-isystem), not
            # extra_includes, so their contents must also be hashed into the
            # AST cache key explicitly — clang_header_dump's disk cache
            # never inspects option-token content, only extra_includes/
            # extra_hash_dirs, so without this a header changed under an
            # inferred root would reuse a stale cached AST (Codex review;
            # mirrors _dump_elf's own deferred_dirs handling). Also fold in
            # any include-search directory riding in `cc.gcc_option_tokens`
            # itself (an explicit --gcc-options/--compiler-option -I, or —
            # since the P0.3 L3->L2 fold — a compile-DB-derived one), for
            # the identical reason: this second, independent header parse
            # has its own cache key, so a directory the primary snapshot
            # pass already hashes must be hashed here too, or an edit under
            # it would silently reuse a stale cached graph even though the
            # primary snapshot re-parsed correctly (Codex review).
            deferred_dirs = tuple(
                deferred_token_dirs(deferred)
            ) + cache_relevant_operand_paths(cc.gcc_option_tokens)
        # ADR-050 D5 (Codex review): this internal semantic header graph
        # (G29 Phase A) must be built from the SAME frontend_context as the
        # primary snapshot it's attached to -- a device-context dump's
        # embedded graph built from a host parse would combine device
        # declarations with host-only call/type/include edges, feeding
        # crosschecks/diff_source_graph_findings a graph incoherent with
        # what it's describing.
        #
        # `suppress_streaming_prune()` (Codex review, PR #840): this call is
        # a real downstream consumer of the raw AST dict, not just a
        # dependency-filtered snapshot -- `buildsource.call_graph.
        # parse_clang_ast_calls` walks it directly to pre-index every full
        # FunctionDecl/CXXMethodDecl/... node by clang id and declaring file
        # for call-graph edge resolution, so a placeholder the opt-in
        # streaming pruner already collapsed a node into would degrade or
        # drop `DECL_CALLS_DECL` edges. This covers the genuinely-separate-
        # parse case (a memo-hit is separately covered by
        # `_streaming_prune_enabled()`'s own `ast_memoize_active()` check,
        # which applies to the *primary* pass this memo entry came from).
        # The projection cache goes here rather than around this whole
        # block: `derived_ast_scope` offers the AST cache entry's own path to
        # `load_cached_projection` *before* anything is read, so a warm run
        # skips the 822 MiB read and the ~1 GiB of dicts it would become.
        # A miss still records the path, which is how the cold run below
        # knows where to store what it computed.
        with (
            suppress_streaming_prune(),
            memory_trace.phase("dump.header_graph.clang_ast"),
            derived_ast_scope(_projection_for) as derived_projection,
        ):
            ast_root, _resolved_kind, _resolved_force_cpp = clang_header_dump(
                resolved_headers,
                eff_includes,
                compiler="cc" if _is_c else "c++",
                gcc_path=cc.gcc_path,
                gcc_prefix=cc.gcc_prefix,
                gcc_options=cc.gcc_options,
                gcc_option_tokens=eff_tokens,
                sysroot=cc.sysroot,
                nostdinc=cc.nostdinc,
                lang=lang,
                extra_hash_dirs=deferred_dirs,
                frontend_context=cc.frontend_context,
                # This is the *final* consumer of this AST -- writing it into
                # the in-process memo here would have no further same-process
                # reader to hand off to (Codex review). A memo entry the
                # primary snapshot pass already wrote is still read (and
                # popped) above; only the write-back on a miss is suppressed.
                memoize=False,
            )
    except (SnapshotError, ValidationError) as exc:
        # Not silent: the graph below falls back to declaration-only, and
        # the call-graph pass is recorded as *attempted and failed* so the
        # comparison's `graph_completeness` reads "degraded" rather than a
        # clean, fully-covered graph (ADR-028 D3: a failed extractor is an
        # explicit fact, never an empty surface).
        ast_root = None
        ast_failure = (
            str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        )
        _log.warning(
            "header graph: clang AST parse failed; falling back to a "
            "declaration-only graph (call/reference edges not collected): %s",
            ast_failure,
        )
        memory_trace.mark("dump.header_graph.clang_ast:failed")
        if len(resolved_headers) > 1:
            # One header the frontend rejects (a SYCL header needing a flag
            # this host parse lacks, say) must not cost every other header
            # its call/reference edges. Re-parse in halves and keep each
            # group that succeeds; only the headers that fail on their own
            # are lost, and the pass stays marked degraded below.
            def _parse_group(group: list[Path]) -> HeaderGraphAstProjection:
                # Same scope as the batch parse above: the call-graph reader
                # needs the dependency declarations a prune would collapse.
                with suppress_streaming_prune():
                    tree, _kind, _force = clang_header_dump(
                        group,
                        eff_includes,
                        compiler="cc" if _is_c else "c++",
                        gcc_path=cc.gcc_path,
                        gcc_prefix=cc.gcc_prefix,
                        gcc_options=cc.gcc_options,
                        gcc_option_tokens=eff_tokens,
                        sysroot=cc.sysroot,
                        nostdinc=cc.nostdinc,
                        lang=lang,
                        extra_hash_dirs=deferred_dirs,
                        frontend_context=cc.frontend_context,
                        memoize=False,
                    )
                return project_header_graph_ast(tree)

            with memory_trace.phase("dump.header_graph.clang_ast_bisect"):
                parts, failed = parse_header_groups_bisecting(
                    resolved_headers,
                    _parse_group,
                    (SnapshotError, ValidationError, *_MALFORMED_AST_ERRORS),
                )
            if parts:
                projection_from_groups = merge_header_graph_ast_projections(parts)
            names = ", ".join(p.name for p in failed[:5])
            more = ", ..." if len(failed) > 5 else ""
            recovered = (
                "the rest were parsed separately"
                if parts
                else "no header could be recovered"
            )
            ast_failure = (
                f"clang AST parse failed for {len(failed)} of "
                f"{len(resolved_headers)} header(s) ({names}{more}); "
                f"{recovered}: {ast_failure}"
            )
            _log.warning("header graph: %s", ast_failure)
    # Reduce the AST to the four compact projections the graph builder
    # actually reads, then drop the tree BEFORE the graph is allocated, so
    # the two are never resident together.
    #
    # Be precise about what this buys, because the obvious claim is wrong
    # and was measured (`docs/contribute/measurements/
    # header-graph-attach-memory.md`, the 2026-09-19 follow-up). On the real
    # reference library, three runs per side: it does NOT lower the member's
    # peak (2216.3 -> 2215.3 MiB), and steady-state retention once the
    # attach returns is ~8-12 MiB *higher*, not lower. The peak lives inside
    # `json.load`, where the whole document is held as one str while the
    # tree is built from it -- `document + tree`, never `tree + graph`.
    #
    # What it does buy is residency *during* the attach: the graph build
    # goes from +147 MiB to -27 MiB against the AST-parse level, ~175 MiB
    # lower at that point, because the graph lands in arenas the parse
    # already freed. And it makes a future prune of the tree expressible at
    # all -- the projection is exactly the statement of what such a prune
    # would have to preserve, and that prune has a measured 62%-of-tree
    # ceiling where this has none left. Do not cite this as having reduced
    # oneDAL's memory; it did not.
    #
    # Evidence is untouched: the same four pure readers run over the same
    # tree in the same order (`project_header_graph_ast`),
    # `DECL_CALLS_DECL` included.
    projection: HeaderGraphAstProjection | None = None
    if projection_from_groups is not None:
        # The whole-tree parse failed and the per-group re-parse above
        # recovered what it could; no whole tree exists to project.
        projection = projection_from_groups
    elif derived_projection.used:
        # No tree was ever built: the projection came either straight off a
        # sidecar (warm) or from streaming the AST document itself (cold).
        # `ast_root` holds the cache layer's own marker rather than a tree,
        # so it must not be projected -- `used` is the discriminator, never
        # a type check on that marker.
        projection = derived_projection.value
        memory_trace.mark(
            "dump.header_graph.projection_streamed"
            if streamed_paths
            else "dump.header_graph.projection_cache:hit"
        )
        if streamed_paths and derived_projection.cache_path is not None:
            # A streamed projection still owes the sidecar the warm path
            # reads, so the next run skips even the stream. Stored against
            # the AST *cache entry* path, never the streamed document's own
            # -- on a cold run that was a temp file the dump unlinks.
            store_cached_projection(derived_projection.cache_path, projection)
    elif ast_root is not None:
        try:
            with memory_trace.phase("dump.header_graph.project"):
                projection = project_header_graph_ast(ast_root)
        except _MALFORMED_AST_ERRORS:
            # Same containment as the streaming loader above, and for the
            # same reason: the readers walk a tree whose shape they trust,
            # so a document whose `inner` is a number rather than a list
            # raises `TypeError` out of the walk. That is reachable from a
            # corrupt AST cache entry, and letting it escape would abort a
            # whole dump over a bad cache file -- the exact failure ADR-028
            # D3 forbids. Degrade to the declaration-only graph instead.
            projection = None
        if projection is not None and derived_projection.cache_path is not None:
            # Store beside the AST entry this projection was derived from, so
            # the two share one key and one lifetime. Best-effort by design:
            # `store_cached_projection` never raises, because failing to warm
            # a cache must not fail the dump.
            store_cached_projection(derived_projection.cache_path, projection)
    # `ast_root` is the only surviving reference to the tree at this point
    # (`clang_header_dump` is called with `memoize=False`, so nothing was
    # written into the in-process AST memo either), so clearing the name
    # drops it here rather than at function exit. `gc.collect()` is
    # deliberately NOT called: a clang AST is an acyclic dict/list
    # structure, so refcounting frees it immediately, and a collection here
    # would cost a full-heap walk for nothing (the earlier attribution
    # measured `gc.collect()` on this path freeing exactly zero objects).
    ast_root = None
    memory_trace.mark("dump.header_graph.ast_released")
    return HeaderGraphAst(
        projection, ast_failure, resolved_headers, eff_includes, eff_tokens
    )


def prefetch_header_graph_ast(
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> Future[HeaderGraphAst]:
    """Start :func:`acquire_header_graph_ast` on a budgeted thread now.

    Lets the header graph's own clang parse run while the primary castxml
    dump runs. Its thread comes from the process-wide budget
    (``ABICHECK_MAX_THREADS``); with the budget spent it runs inline, which
    is exactly the old, sequential order.
    """
    import contextvars

    from .process_resources import BudgetedExecutor

    pool = BudgetedExecutor(1, thread_name_prefix="abicheck-hgraph")
    ctx = contextvars.copy_context()
    future = pool.submit(
        ctx.run, acquire_header_graph_ast, headers, includes, lang, compile
    )
    future.add_done_callback(lambda _f: pool.shutdown(wait=False))
    return future


#: Longest a failed dump waits for its abandoned header-graph prefetch.
_PREFETCH_SETTLE_TIMEOUT_S = 10.0


@contextmanager
def prefetch_settled_on_failure(
    future: Future[HeaderGraphAst] | None,
) -> Iterator[None]:
    """Wait for *future* to finish before re-raising a failure of the block.

    A prefetch runs beside the primary dump; if that dump fails, nothing will
    ever consume the prefetched graph, but its parse keeps running and keeps
    its thread-budget slot (released only when it completes). Settling it
    here returns the slot before the failing call returns, so a failed dump
    never leaves an orphaned parse behind to starve a later pool.
    """
    try:
        yield
    except Exception:
        # Bounded: a prefetch stuck in a slow parse must not hold the error
        # hostage. Past the bound the slot is returned when the parse ends,
        # exactly the pre-fix behavior, but the common case (a parse that is
        # finishing or already done) is settled before the error propagates.
        if future is not None:
            with suppress(BaseException):
                future.result(timeout=_PREFETCH_SETTLE_TIMEOUT_S)
        raise
    # KeyboardInterrupt / SystemExit propagate immediately: an interrupt must
    # never wait on a background parse (up to clang's own timeout).


def prefetch_graph_if_useful(
    wanted: bool,
    resolved_backend: str,
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> Future[HeaderGraphAst] | None:
    """Start the header graph's clang parse now, when that is worth doing.

    The parse depends only on the headers and compile context, never on the
    snapshot, so under castxml -- which never produces the clang AST the
    graph needs -- it can run alongside the primary dump. Measured on a cold
    header cache: 11-15% off a single-library compare, neutral on a warm
    cache and on a release whose members already fill the cores. Under the
    clang frontend the attach reuses the primary pass's AST instead, so a
    prefetch there would parse the same headers twice; ``None`` then, and
    whenever the graph is not wanted at all.
    """
    if not (wanted and headers and resolved_backend == "castxml"):
        return None
    return prefetch_header_graph_ast(headers, includes, lang, compile)
