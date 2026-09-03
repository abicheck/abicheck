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
`service_scan.py`/`service_compare_pipeline.py`/`service_dump_pipeline.py`
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
``.service_scan``, ``.header_utils``, ``.errors``, ``.model`` -- none of which
import ``.service`` or this module back.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compile_context import CompileContext
from .errors import SnapshotError, ValidationError
from .header_utils import (
    cache_relevant_operand_paths,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from .service_scan import expand_header_inputs

if TYPE_CHECKING:
    from .model import AbiSnapshot


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
) -> AbiSnapshot:
    """Build and embed the header-only (L2) semantic graph (ADR-041 addendum).

    A no-op when ``header_graph`` was not requested or no headers were parsed.
    Calls the same ``dumper._clang_header_dump`` the main clang-frontend
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
    calls ``_clang_header_dump`` at all. Mirrors ``_dump_elf``'s own header-expansion
    (``expand_header_inputs`` — a ``headers`` entry may be a directory) and
    inferred-include-root derivation (``resolve_inferred_header_roots`` — an
    umbrella header's relative ``#include``s need the same auto-added ``-I``/
    ``-isystem`` search dirs the main dump computes) so this second pass sees
    the identical resolved input the main dump already parsed successfully,
    rather than the raw, unexpanded arguments (Codex review: without this, a
    header *directory* input made ``_clang_header_dump`` write an invalid
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
    if not header_graph or not headers:
        return snap
    from .buildsource.header_graph import (
        HEADER_INCLUDE_GRAPH_PASS,
        ClangHeaderIncludeExtractor,
        build_header_only_graph,
    )
    from .buildsource.include_graph import augment_graph_with_includes
    from .buildsource.model import (
        CoverageStatus,
        DataLayer,
        LayerConfidence,
        LayerCoverage,
    )
    from .buildsource.pack import BuildSourcePack
    from .dumper import _clang_header_dump, _resolve_clang_bin
    from .dumper_clang_streaming import suppress_streaming_prune

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
            # different `_clang_header_dump` cache key -- silently missing
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
            # AST cache key explicitly — _clang_header_dump's disk cache
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
        with suppress_streaming_prune():
            ast_root, _resolved_kind, _resolved_force_cpp = _clang_header_dump(
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
    except (SnapshotError, ValidationError):
        ast_root = None
    graph = build_header_only_graph(
        snap,
        ast_root,
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
        # Resolve the same clang driver `_clang_header_dump` above used
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
    pack = BuildSourcePack(root=Path(""), source_graph=graph)
    # Populate the manifest coverage row the normal collect/embed path always
    # sets (inline.build_inline_coverage's L5 row) — otherwise the pack's
    # default empty ``coverage`` reads as "L5 not collected" to
    # cli_buildsource_helpers._layer_presence/_optional_coverage even though
    # source_graph is populated, making coverage/asymmetry reporting
    # misleading (Codex review). L3/L4 stay honestly NOT_COLLECTED — neither
    # a build nor an L4 source-ABI replay ran in a header-only world.
    pack.manifest.coverage = [
        LayerCoverage(
            layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if graph.edges else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED
            if graph.edges
            else LayerConfidence.UNKNOWN,
        ),
    ]
    snap.build_source = pack
    # ADR-063 Phase 3 (D5): one shared SourceGraphSummary instance for both
    # the L5 builder above (`graph`, already `pack.source_graph`) and the
    # public-surface evidence graph -- never two independently-constructed
    # summary objects that happen to agree, which is exactly the drift this
    # phase's shared-assembly design exists to rule out. `snap.surface_graph`
    # is the same object `pack.source_graph` already holds; the codec (
    # `storage/surface_graph_codec.py`) relies on that identity to dedup the
    # embedded copy on encode and restore it on decode.
    #
    # Deliberately NOT populated with compare/surface_graph.py's own
    # declaration/type/header/symbol facts here: `_attach_header_graph` runs
    # unconditionally on essentially every real dump (G31 Phase A). Paying
    # `build_public_surface_facts`'s per-declaration walk on every dump
    # regressed the header-graph attach-cost perf gate by 47-96% at
    # realistic sizes (caught by CI on this phase's own PR). An earlier
    # revision of ADR-063 Phase 3 D5's traversal migration deferred that
    # populate step to a later enrichment call instead, keyed off this same
    # graph object -- but a further review round found the graph's own
    # cross-producer evidence-merge precedence could let a stale or
    # adversarial persisted fact outrank a fresh recomputation, so the final
    # design (`policy.public_surface_closure.py`'s
    # `_resolve_public_surface_from_snapshot`) does not read or enrich this
    # graph at all: it calls `compare/surface_graph.py`'s
    # `referenced_identifiers_by_node()`, a pure function of the snapshot's
    # own current declarations, computed fresh on every public/export-domain
    # surface query (Codex review, PR #979) -- see that module's own
    # docstring for the full security history.
    snap.surface_graph = graph
    return snap
