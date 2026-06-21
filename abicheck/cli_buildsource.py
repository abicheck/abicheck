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

"""`collect` command (ADR-028 D6, ADR-029).

Collects an optional BuildSourcePack from an existing build tree *without
rebuilding*. The pack augments an ABI snapshot with L3 build-context evidence (compile DB /
CMake File API / Ninja / Bazel / Make dry-run / compiler-recorded metadata).
Per ADR-028 D6 this command never runs
arbitrary build commands: it only reads existing build outputs and build-system
query interfaces. Anything that builds or executes project code is a separate,
explicit opt-in not implemented here.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from . import __version__ as _abicheck_version
from .buildsource.build_evidence import BuildEvidence
from .buildsource.merge_support import (
    _combine_packs,
    _detect_merge_layer_conflicts,
    _filter_pack_layers,
)
from .buildsource.model import (
    ExtractorRecord,
)
from .buildsource.pack import BuildSourcePack
from .buildsource.redaction import DEFAULT_REDACTION
from .buildsource.source_replay import REPLAY_SCOPES
from .cli import main
from .cli_buildsource_helpers import (  # noqa: F401  (re-exported for API stability / tests)
    _build_coverage as _build_coverage,
    _collect_call_graph as _collect_call_graph,
    _collect_include_graph as _collect_include_graph,
    _collect_source_graph as _collect_source_graph,
    _detect_coverage_asymmetry as _detect_coverage_asymmetry,
    _echo_capabilities as _echo_capabilities,
    _echo_collection_summary as _echo_collection_summary,
    _echo_compare_side_coverage as _echo_compare_side_coverage,
    _echo_coverage as _echo_coverage,
    _enforce_strict_mode as _enforce_strict_mode,
    _exported_symbols_from_binary as _exported_symbols_from_binary,
    _exported_symbols_from_snapshot as _exported_symbols_from_snapshot,
    _ingest_graph_backends as _ingest_graph_backends,
    _intrinsic_coverage as _intrinsic_coverage,
    _layer_presence as _layer_presence,
    _load_pack_or_raise as _load_pack_or_raise,
    _merge_attach_combined as _merge_attach_combined,
    _merge_fold_packs as _merge_fold_packs,
    _merge_handle_conflicts as _merge_handle_conflicts,
    _merge_load_snapshots as _merge_load_snapshots,
    _merge_pick_base as _merge_pick_base,
    _merge_print_summary as _merge_print_summary,
    _optional_coverage as _optional_coverage,
    _purge_external_outputs as _purge_external_outputs,
    _resolve_side_pack as _resolve_side_pack,
    _run_adapters as _run_adapters,
    _run_external_extractors as _run_external_extractors,
    attach_evidence_metrics as attach_evidence_metrics,
    diff_embedded_build_source as diff_embedded_build_source,
    parse_from_specs as parse_from_specs,
    prepare_embedded_build_source as prepare_embedded_build_source,
)
from .cli_options import verbose_option

if TYPE_CHECKING:
    from .buildsource.source_abi import SourceAbiSurface
    from .model import AbiSnapshot


@main.command("collect")
@click.option(
    "--binary",
    "binary",
    type=click.Path(path_type=Path),
    default=None,
    help="Built shared library this evidence describes (recorded as provenance).",
)
@click.option(
    "-H",
    "--header",
    "--headers",
    "headers",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Public header file or directory (recorded as provenance; repeat). "
    "`--header` is the canonical spelling (shared with dump/compare); "
    "`--headers` is accepted as a back-compat alias.",
)
@click.option(
    "--build-dir",
    "build_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Build directory to inspect (CMake File API reply, Ninja query).",
)
@click.option(
    "--compile-db",
    "compile_db",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to compile_commands.json (or a directory containing it).",
)
@click.option(
    "-p",
    "compile_db_p",
    type=click.Path(path_type=Path),
    default=None,
    help="Alias for --compile-db (build dir or file).",
)
@click.option(
    "--from",
    "from_adapters",
    multiple=True,
    metavar="ADAPTER[=PATH]",
    help="Build-evidence adapter to run (repeatable). Live (read --build-dir, no "
    "build): `cmake` (CMake File API reply), `ninja` (`ninja -t` queries). "
    "Pre-captured (require a path): `ninja-compdb=<file>`, `bazel-cquery=<file>`, "
    "`bazel-aquery=<file>`, `make=<transcript>`. "
    "E.g. `--from cmake --from bazel-aquery=aquery.json`.",
)
@click.option(
    "--read-compiler-record",
    "read_compiler_record",
    is_flag=True,
    default=False,
    help="Recover compiler provenance from --binary (.GCC.command.line / DWARF DW_AT_producer).",
)
@click.option(
    "--build-system",
    "build_system",
    default="generic",
    show_default=True,
    type=click.Choice(
        ["generic", "cmake", "ninja", "bazel", "make"], case_sensitive=False
    ),
    help="Build system hint for the compile-DB adapter.",
)
@click.option(
    "--source-abi",
    "source_abi",
    is_flag=True,
    default=False,
    help="Collect L4 source ABI replay (parses sources/headers). REQUIRES clang "
    "(or castxml/an Android dump); without the tool this fails gracefully and "
    "source-only checks stay disabled.",
)
@click.option(
    "--source-abi-extractor",
    "source_abi_extractor",
    default="auto",
    show_default=True,
    type=click.Choice(["auto", "clang", "castxml", "android"], case_sensitive=False),
    help="L4 backend: auto (pick the most capable available — clang, else castxml), "
    "clang (inline/template/constexpr bodies + default args), "
    "castxml (declarations/types/const values only), or android (reuse a "
    "pre-captured header-abi .lsdump/.sdump). A requested clang that is not on "
    "PATH falls back to castxml rather than disabling source-only checks.",
)
@click.option(
    "--source-abi-scope",
    "source_abi_scope",
    default="target",
    show_default=True,
    type=click.Choice(list(REPLAY_SCOPES), case_sensitive=False),
    help="Which translation units to replay (ADR-030 D7): off | headers-only | "
    "changed | target | full.",
)
@click.option(
    "--source-abi-target",
    "source_abi_target",
    default="",
    help="Target id to scope replay to (e.g. target://libfoo).",
)
@click.option(
    "--changed-path",
    "changed_paths",
    multiple=True,
    type=str,
    help="Changed file path for --source-abi-scope changed (repeat).",
)
@click.option(
    "--android-dump",
    "android_dump",
    type=click.Path(path_type=Path),
    default=None,
    help="Pre-captured Android header-abi .lsdump/.sdump JSON (for --source-abi-extractor android).",
)
@click.option(
    "--source-abi-cache",
    "source_abi_cache",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory for the per-TU source ABI dump cache (ADR-030 D8).",
)
@click.option(
    "--clang-bin",
    "clang_bin",
    default="clang",
    show_default=True,
    help="clang binary to use for source ABI replay.",
)
@click.option(
    "--source-graph",
    "source_graph",
    default="off",
    show_default=True,
    type=click.Choice(["off", "summary"], case_sensitive=False),
    help="Collect an L5 source graph (ADR-031). 'summary' folds the L3 "
    "build evidence into a compact target/source/header/option graph "
    "for graph-to-graph comparison and finding localization.",
)
@click.option(
    "--call-graph",
    "call_graph",
    is_flag=True,
    default=False,
    help="Add approximate direct-call edges to the L5 source graph via "
    "clang AST (ADR-031 D4, phase 6). REQUIRES clang++; without it "
    "the graph is collected without call edges. Implies --source-graph summary.",
)
@click.option(
    "--include-graph",
    "include_graph",
    is_flag=True,
    default=False,
    help="Add compile-unit include edges to the L5 graph via `clang -M` "
    "(ADR-031 D3). REQUIRES clang++. Implies --source-graph summary.",
)
@click.option(
    "--kythe-entries",
    "kythe_entries",
    type=click.Path(path_type=Path),
    default=None,
    help="Pre-captured Kythe entries JSON to fold into the L5 graph "
    "(ADR-031 D5; non-executing). Implies --source-graph summary.",
)
@click.option(
    "--codeql-results",
    "codeql_results",
    type=click.Path(path_type=Path),
    default=None,
    help="Pre-captured CodeQL call-graph query result JSON to fold into "
    "the L5 graph (ADR-031 D5; non-executing). Implies --source-graph summary.",
)
@click.option(
    "--extractor-manifest",
    "extractor_manifests",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Register an external CLI evidence extractor by manifest path "
    "(ADR-032 D3; trusted-by-operator, never auto-discovered). Repeat "
    "for several. Its declared actions are intersected with the actions "
    "enabled for this run (see --allow-build-query).",
)
@click.option(
    "--source-root",
    "source_root",
    type=click.Path(path_type=Path),
    default=None,
    help="Source checkout root, supplied to external extractors that reference "
    "the {source_root} placeholder (ADR-032 D3).",
)
@click.option(
    "--allow-build-query",
    "allow_build_query",
    is_flag=True,
    default=False,
    help="Permit extractors to query the build system (ninja -t, bazel "
    "cquery/aquery, CMake File API regeneration). Off by default: only "
    "reading existing build outputs is allowed (ADR-032 D5).",
)
@click.option(
    "--collection-mode",
    "collection_mode",
    default="permissive",
    show_default=True,
    type=click.Choice(["permissive", "strict", "audit"], case_sensitive=False),
    help="How extractor failures are handled (ADR-032 D9): permissive "
    "(failures degrade coverage, collection continues), strict (a "
    "failed/invalid extractor exits non-zero), audit (preserve raw "
    "artifacts + full diagnostics).",
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output build-source pack directory.",
)
@verbose_option
def collect_cmd(
    binary: Path | None,
    headers: tuple[Path, ...],
    build_dir: Path | None,
    compile_db: Path | None,
    compile_db_p: Path | None,
    from_adapters: tuple[str, ...],
    read_compiler_record: bool,
    build_system: str,
    source_abi: bool,
    source_abi_extractor: str,
    source_abi_scope: str,
    source_abi_target: str,
    changed_paths: tuple[str, ...],
    android_dump: Path | None,
    source_abi_cache: Path | None,
    clang_bin: str,
    source_graph: str,
    call_graph: bool,
    include_graph: bool,
    kythe_entries: Path | None,
    codeql_results: Path | None,
    extractor_manifests: tuple[Path, ...],
    source_root: Path | None,
    allow_build_query: bool,
    collection_mode: str,
    output: Path,
    verbose: bool,
) -> None:
    """Collect an optional source/build BuildSourcePack from an existing build tree.

    \b
    Examples:
      abicheck collect --compile-db build/compile_commands.json -o libfoo.evidence/
      abicheck collect -p build/ --headers include/ -o libfoo.evidence/
      abicheck collect --build-dir build --from cmake --from ninja -o libfoo.evidence/
      abicheck collect --from bazel-aquery=aquery.json -o libfoo.evidence/

    The resulting directory attaches to a snapshot with `abicheck dump --build-info`/`--sources`.
    """
    effective_compile_db = compile_db or compile_db_p
    extractors: list[ExtractorRecord] = []
    merged = BuildEvidence()
    record_bazel_inputs = include_graph or (
        source_abi
        and _source_abi_scope_needs_include_map(source_abi_scope, list(changed_paths))
    )
    # Collapse the unified `--from adapter[=path]` specs into the per-adapter
    # kwargs the engine still takes (ADR-037 CLI consolidation).
    adapters = parse_from_specs(from_adapters)

    _run_adapters(
        merged,
        extractors,
        compile_db=effective_compile_db,
        build_dir=build_dir,
        cmake=bool(adapters["cmake"]),
        ninja=bool(adapters["ninja"]),
        ninja_compdb=adapters["ninja_compdb"],  # type: ignore[arg-type]
        bazel_cquery=adapters["bazel_cquery"],  # type: ignore[arg-type]
        bazel_aquery=adapters["bazel_aquery"],  # type: ignore[arg-type]
        make_dry_run=adapters["make_dry_run"],  # type: ignore[arg-type]
        binary=binary,
        read_compiler_record=read_compiler_record,
        build_system=build_system,
        record_bazel_inputs=record_bazel_inputs,
        verbose=verbose,
    )

    # External CLI extractors (ADR-032 D3): explicitly-registered subprocess
    # adapters, run under the resolved action ceiling (D5). Their normalized
    # build_evidence is folded into `merged` so it shares coverage and the pack.
    if extractor_manifests:
        _run_external_extractors(
            merged,
            extractors,
            manifests=extractor_manifests,
            pack_root=output,
            binary=binary,
            build_dir=build_dir,
            source_root=source_root,
            compile_db=effective_compile_db,
            allow_build_query=allow_build_query,
            collection_mode=collection_mode,
            verbose=verbose,
        )

    surface: SourceAbiSurface | None = None
    source_detail = ""
    if source_abi:
        surface, source_detail = _collect_source_abi(
            merged,
            extractors,
            extractor=source_abi_extractor,
            scope=source_abi_scope,
            target_id=source_abi_target,
            changed_paths=list(changed_paths),
            android_dump=android_dump,
            cache_dir=source_abi_cache,
            clang_bin=clang_bin,
            headers=headers,
            binary=binary,
            verbose=verbose,
        )

    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph=source_graph,
        call_graph=call_graph,
        include_graph=include_graph,
        kythe_entries=kythe_entries,
        codeql_results=codeql_results,
        surface=surface,
        clang_bin=clang_bin,
    )

    pack = BuildSourcePack.empty(
        output,
        abicheck_version=_abicheck_version,
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    # Redact home/workspace prefixes from provenance paths before persisting,
    # consistent with how the rest of the evidence model redacts paths.
    red = DEFAULT_REDACTION
    pack.manifest.extractors = extractors
    pack.manifest.inputs = {
        "binary": red.path(str(binary)) if binary else None,
        "headers": [red.path(str(h)) for h in headers],
        "build_dir": red.path(str(build_dir)) if build_dir else None,
        "collection_mode": collection_mode,
    }
    has_build = bool(
        merged.compile_units
        or merged.targets
        or merged.toolchains
        or merged.link_units
        or merged.build_options
    )
    if has_build:
        pack.build_evidence = merged
    if surface is not None:
        pack.source_abi = surface
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = _build_coverage(
        merged, has_build, surface, source_detail, graph, graph_detail
    )
    pack.write()

    _enforce_strict_mode(extractors, merged, collection_mode)
    _echo_collection_summary(
        pack,
        merged,
        output,
        has_build=has_build,
        source_abi=source_abi,
        source_detail=source_detail,
        graph=graph,
        graph_detail=graph_detail,
    )


def _include_map_for_replay(
    merged: BuildEvidence, clang_bin: str, scope: str
) -> dict[str, list[str]] | None:
    """Per-TU include graph ``{compile_unit_id: [included_path]}`` for replay scoping.

    Runs ``clang -MM`` over the build (ADR-031 D3) so ``headers-only``/``changed``
    replay can scope precisely (ADR-030 follow-up #4). Returns ``None`` when clang
    is unavailable or yields nothing, so :func:`run_source_replay` falls back to
    the target-ownership heuristics — collection never blocks on it.
    """
    from .buildsource.include_graph import (
        ClangIncludeExtractor,
        include_map_from_recorded_inputs,
    )

    recorded = include_map_from_recorded_inputs(merged)
    # Bazel-recorded action inputs are an over-approximation, not textual
    # includes. They are useful for changed-path fanout, but not exact enough
    # for headers-only set-cover selection.
    if recorded and scope != "headers-only":
        return recorded
    extractor = ClangIncludeExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        return None
    includes = extractor.extract_from_build(merged)
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"source_abi_include_graph: {diag}")
    return includes or None


_SOURCE_ABI_DIRECT_SOURCE_EXTS = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".cu",
    ".m",
    ".mm",
)


def _source_abi_scope_needs_include_map(scope: str, changed_paths: list[str]) -> bool:
    """Whether L4 replay scoping needs depfile include extraction.

    Header-aware scopes benefit from the include graph, but a changed path that
    is only a source file is selected directly by ``CompileUnit.source`` in
    ``select_compile_units``. Building depfiles for every TU in that case is pure
    overhead on large projects.
    """
    if scope == "headers-only":
        return True
    if scope != "changed":
        return False
    return not all(
        path.lower().replace("\\", "/").endswith(_SOURCE_ABI_DIRECT_SOURCE_EXTS)
        for path in changed_paths
    )


def _collect_source_abi(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    extractor: str,
    scope: str,
    target_id: str,
    changed_paths: list[str],
    android_dump: Path | None,
    cache_dir: Path | None,
    clang_bin: str,
    headers: tuple[Path, ...],
    binary: Path | None,
    verbose: bool,
) -> tuple[SourceAbiSurface | None, str]:
    """Run L4 source ABI replay and return ``(surface, human-readable detail)``.

    Never raises on a missing tool: a clang-less environment yields a partial
    surface and a clear note, keeping artifact tiers authoritative (ADR-028 D3).
    """
    from .buildsource.source_abi import SourceAbiSurface
    from .buildsource.source_replay import (
        SourceAbiCache,
        public_header_roots_for,
        run_source_replay,
    )

    exported = _exported_symbols_from_binary(binary)
    library = str(binary) if binary else ""

    # A no-op replay scope selects zero translation units by design ("off", or
    # "changed" with no --changed-path), so it needs neither L3 build evidence,
    # an Android dump, nor a source-ABI frontend. Resolve it first — before the
    # Android branch and the clang/castxml probe — so a no-op scope never
    # false-fails --collection-mode strict on a missing dump/frontend it would
    # not use. (ADR-030 D7; Codex review.)
    if scope == "off" or (scope == "changed" and not changed_paths):
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="partial",
                detail=f"scope {scope!r} selects no translation units; nothing to replay",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            f"no-op: scope {scope!r} selects no translation units",
        )

    # Header roots: explicit --headers win; else pull from the build targets.
    roots = [str(h) for h in headers] or public_header_roots_for(merged, target_id)

    if extractor == "android":
        return _collect_source_abi_android(
            android_dump,
            extractors,
            target_id=target_id,
            exported=exported,
            library=library,
            roots=roots,
        )

    from .buildsource.source_extractors import select_source_backend

    # Evaluate the available front-ends and pick a path (ADR-030 D3): "auto"
    # picks the most capable available backend; an explicitly-requested clang
    # that is absent falls back to castxml instead of disabling source checks.
    choice, impl = select_source_backend(extractor, clang_bin=clang_bin)
    if impl is None or choice.selected is None:
        detail = "; ".join(f"{n}: {why}" for n, why in choice.skipped) or choice.reason
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"no usable source-ABI backend ({detail}); source-only checks disabled",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            "unavailable: no source-ABI front-end on PATH (clang/castxml) — "
            "source-only checks disabled. Install clang or castxml.",
        )

    extractor = choice.selected
    tool_name = clang_bin if choice.selected == "clang" else "castxml"
    # Surface the decision and the chosen backend's capability gaps so a
    # construct it cannot observe (e.g. concept tightening or constructor
    # mangling under castxml) is logged rather than silently invisible.
    merged.diagnostics.append(f"source_abi: {choice.reason}")
    if choice.capability_gaps:
        merged.diagnostics.append(f"source_abi: {choice.gap_note()}")

    if not merged.compile_units:
        # The user explicitly asked for a unit-consuming L4 scope but there is
        # no L3 build context to replay, so nothing is produced. Record this as
        # "skipped" (not "partial") so --collection-mode strict fails loud
        # instead of silently passing on an empty requested layer; permissive
        # mode is unaffected and still exits 0. (No-op scopes already returned
        # above, before backend resolution.)
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="skipped",
                detail="no compile units in build evidence; collect L3 first (e.g. --compile-db)",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            "skipped: no L3 build context (need compile units to replay)",
        )
    if not impl.available():
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"{tool_name} not found in PATH; source-only checks disabled",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            f"unavailable: {tool_name} not on PATH — source-only checks disabled "
            "(macros, default args, inline/template/constexpr bodies). Install "
            f"{tool_name} or omit --source-abi.",
        )

    # For the scopes that benefit (ADR-030 follow-up #4), build a per-TU include
    # graph from compiler depfiles and feed it to replay so headers-only does a
    # minimal set cover and changed maps a header to exactly the TUs that include
    # it. The extractor degrades to {} when clang is absent → heuristic fallback,
    # so this never blocks collection. `target`/`full` ignore the include map.
    include_map = (
        _include_map_for_replay(merged, clang_bin, scope)
        if _source_abi_scope_needs_include_map(scope, changed_paths)
        else None
    )
    cache = SourceAbiCache(cache_dir) if cache_dir else None
    surface, diagnostics = run_source_replay(
        merged,
        impl,
        scope=scope,
        changed_paths=changed_paths,
        target_id=target_id,
        library=library,
        exported_symbols=exported,
        public_header_roots=roots,
        cache=cache,
        include_map=include_map,
    )
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    detail = (
        f"scope={scope}, {parsed}/{selected} TUs parsed, {len(diagnostics)} failures"
    )
    if cache is not None and cache.hit_rate is not None:  # ADR-033 D9 cache_hit_rate
        detail += f", cache_hit_rate={cache.hit_rate:.0%} ({cache.hits}/{cache.hits + cache.misses})"
    extractors.append(
        ExtractorRecord(
            name=f"source_abi:{extractor}",
            status="ok" if parsed else "partial",
            detail=detail,
        )
    )
    return surface, (
        f"{extractor} extractor, scope={scope}: parsed {parsed}/{selected} TUs, "
        f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types, "
        f"{len(surface.reachable_inline_bodies)} inline bodies, "
        f"{len(surface.reachable_templates)} templates"
        + (
            f", {len(diagnostics)} TU(s) failed (partial coverage)"
            if diagnostics
            else ""
        )
    )


def _collect_source_abi_android(
    android_dump: Path | None,
    extractors: list[ExtractorRecord],
    *,
    target_id: str,
    exported: list[str],
    library: str,
    roots: list[str],
) -> tuple[SourceAbiSurface | None, str]:
    """Normalize a pre-captured Android header-abi dump into a linked surface (D9)."""
    from .buildsource.source_abi import SourceAbiSurface
    from .buildsource.source_extractors import (
        AndroidHeaderAbiAdapter,
        SourceExtractionError,
    )
    from .buildsource.source_link import link_source_abi

    if android_dump is None:
        raise click.UsageError(
            "--source-abi-extractor android requires --android-dump <file.lsdump|.sdump>."
        )
    adapter = AndroidHeaderAbiAdapter()
    try:
        tu = adapter.load(android_dump, target_id=target_id, public_header_roots=roots)
    except SourceExtractionError as exc:
        extractors.append(
            ExtractorRecord(
                name="source_abi:android",
                status="failed",
                inputs=[DEFAULT_REDACTION.path(str(android_dump))],
                detail=str(exc),
            )
        )
        return SourceAbiSurface(library=library, target_id=target_id), f"failed: {exc}"
    surface = link_source_abi(
        [tu],
        exported_symbols=exported,
        library=library,
        target_id=target_id,
    )
    extractors.append(
        ExtractorRecord(
            name="source_abi:android",
            status="ok",
            inputs=[DEFAULT_REDACTION.path(str(android_dump))],
            detail=f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types",
        )
    )
    return surface, (
        f"android dump: {len(surface.reachable_declarations)} decls, "
        f"{len(surface.reachable_types)} types"
    )


# ── Attach / compare integration (ADR-028 D6, D7; ADR-029 D9) ─────────────────


def embed_build_source(
    snap: AbiSnapshot,
    build_info: Path | None,
    sources: Path | None,
    *,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    clang_bin: str = "clang",
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    changed_paths: tuple[str, ...] = (),
    extractor: str = "auto",
) -> None:
    """Embed build-info / source facts inline in *snap* (single-artifact UX).

    *collect_mode* is the ADR-033 D2 CI evidence mode selecting which layers and
    replay scope to collect: ``build`` captures L3 build context only, ``off``
    embeds nothing, the source/graph modes collect L3+L4+L5 at the matching scope.

    Source-tree-centric inputs (ADR-028..033 amendment): ``sources`` is a source
    checkout — L4 source ABI replay and the L5 graph are run *inline* and
    embedded; ``build_info`` is an optional build dir / ``compile_commands.json``
    / pre-captured pack supplying L3. A ``compile_commands.json`` inside the
    source tree is auto-discovered when ``build_info`` is omitted.

    For back-compatibility a path that is itself a pack directory produced by
    ``abicheck collect`` (it has a ``manifest.json``) is loaded as that pack
    instead of being collected inline.

    The combined facts ride inside the ``.abi.json`` so a later
    ``compare old.json new.json`` works with no out-of-band directories. Also
    records the matching content-addressed ``build_source_pack`` reference.
    """
    from .buildsource.inline import (
        collect_inline_pack,
        discover_build_config,
        is_pack_dir,
        load_build_config,
    )
    from .buildsource.source_replay import collection_for_ci_mode

    scope, layers = collection_for_ci_mode(collect_mode)
    if not layers:  # 'off' (or an unknown mode) embeds nothing
        return

    bi_is_pack = is_pack_dir(build_info)
    src_is_pack = is_pack_dir(sources)
    bi_pack = (
        _load_pack_or_raise(build_info)
        if (bi_is_pack and build_info is not None)
        else None
    )
    src_pack = (
        _load_pack_or_raise(sources) if (src_is_pack and sources is not None) else None
    )

    raw_build_info = None if (build_info is None or bi_is_pack) else build_info
    raw_sources = None if (sources is None or src_is_pack) else sources

    inline_pack: BuildSourcePack | None = None
    if raw_build_info is not None or raw_sources is not None:
        cfg_path = build_config or discover_build_config(raw_sources)
        # Only an explicit --config is operator-supplied/trusted for
        # subprocess execution. Auto-discovered source-tree configs may be
        # attacker-controlled; their non-executable settings are still honored.
        cfg_trusted_for_query = build_config is not None
        try:
            cfg = load_build_config(cfg_path) if cfg_path is not None else None
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        # CLI overrides (no config file needed): --build-query / --build-compile-db
        # win over the .abicheck.yml values when supplied.
        if build_query is not None or build_compile_db is not None:
            import dataclasses

            from .buildsource.inline import BuildConfig

            cfg = cfg or BuildConfig()
            cfg = dataclasses.replace(
                cfg,
                query=build_query if build_query is not None else cfg.query,
                compile_db=build_compile_db
                if build_compile_db is not None
                else cfg.compile_db,
            )
        # A1: plumb the binary's L0 exports (already parsed into this snapshot)
        # into the inline replay, so the linked source surface knows which decls
        # map to exports and the provenance/mapping checks have a signal. Empty in
        # the source-only `dump --sources` flow (no binary) — then A1 stays inert.
        exported = _exported_symbols_from_snapshot(snap)
        inline_pack = collect_inline_pack(
            sources=raw_sources,
            build_info=raw_build_info,
            build_config=cfg,
            allow_build_query=allow_build_query,
            build_config_trusted_for_query=cfg_trusted_for_query,
            base_build=bi_pack.build_evidence if bi_pack else None,
            clang_bin=clang_bin,
            extractor=extractor,
            scope=scope,
            layers=layers,
            exported_symbols=exported,
            changed_paths=changed_paths,
        )
        # P09: don't fail *silently* when a source/build tree yields no compile DB.
        # Autotools `configure` (and a bare checkout) emit no compile_commands.json,
        # so L3/L4/L5 collect nothing — previously with no explanation. Warn with an
        # actionable hint (unless a build.query diagnostic already explains it).
        _ev = inline_pack.build_evidence if inline_pack is not None else None
        _has_l3 = _ev is not None and bool(_ev.compile_units)
        _has_query_note = inline_pack is not None and any(
            e.name == "build_query" for e in inline_pack.manifest.extractors
        )
        if not _has_l3 and bi_pack is None and not _has_query_note:
            _tree = raw_sources if raw_sources is not None else raw_build_info
            _deeper = "/L4/L5" if ("L4" in layers or "L5" in layers) else ""
            click.echo(
                f"warning: no compile_commands.json found under {_tree} "
                "(looked in: ., build, builddir, out, _build, cmake-build-debug); "
                f"L3{_deeper} not collected. Generate one — CMake: configure with "
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON; Meson: emitted by `meson setup`; "
                "Autotools/Make: run `bear -- make` — or pass "
                "--build-info <dir|compile_commands.json>.",
                err=True,
            )

    # Pre-captured packs must also honour the collect-mode layer set (Codex).
    bi_pack = _filter_pack_layers(bi_pack, layers)
    src_pack = _filter_pack_layers(src_pack, layers)

    # --build-info (pack) wins L3, --sources (pack) wins L4/L5, the inline pack
    # backfills both; coverage is rebuilt per layer from the supplying pack.
    merged = _combine_packs(bi_pack, src_pack, inline_pack)
    if merged is None:
        return
    snap.build_source = merged
    # Provenance hint: prefer the source input, else build-info.
    hint = str(sources) if sources is not None else str(build_info)
    snap.build_source_pack = merged.to_ref(path_hint=hint)


def dump_source_only(
    sources: Path | None,
    build_info: Path | None,
    version: str,
    output: Path | None,
    build_config: Path | None,
    allow_build_query: bool,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    extractor: str = "auto",
) -> None:
    """Write a binary-less snapshot carrying only the embedded build/source facts.

    The parallel-baseline flow: ``dump --sources <tree>`` / ``--build-info <path>``
    with no ``SO_PATH`` collects L3/L4/L5 inline and embeds them in an otherwise
    empty snapshot, to be combined with an artifact-side dump via ``merge``. A
    bare ``dump`` (no binary and no source/build inputs) errors clearly here.
    """
    from .cli import _stamp_provenance, _write_snapshot_output
    from .model import AbiSnapshot

    if sources is None and build_info is None:
        raise click.UsageError(
            "dump requires a binary (SO_PATH), or --sources/--build-info for a "
            "source-only snapshot."
        )
    # Library name from the source/build input so the snapshot is identifiable;
    # `merge` keeps the artifact side as the base regardless.
    hint = sources if sources is not None else build_info
    library = hint.name if hint is not None else "source"
    snap = AbiSnapshot(library=library, version=version)
    _stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write_snapshot_output(
        snap,
        output,
        build_info,
        sources,
        build_config,
        allow_build_query,
        collect_mode,
        build_query=build_query,
        build_compile_db=build_compile_db,
        extractor=extractor,
    )


@main.command("merge")
@click.argument(
    "inputs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output combined baseline snapshot (.abi.json).",
)
@click.option(
    "--on-conflict",
    "on_conflict",
    type=click.Choice(["warn", "error"]),
    default="warn",
    show_default=True,
    help="What to do when two inputs supply the SAME layer (L3/L4/L5) "
    "with DIFFERING facts: `warn` keeps first-wins and records a "
    "diagnostic; `error` exits non-zero (good for baseline generation).",
)
@verbose_option
def merge_cmd(
    inputs: tuple[Path, ...], output: Path, on_conflict: str, verbose: bool
) -> None:
    """Combine independently-produced dumps into one self-contained baseline.

    \b
    Each INPUT is a `.abi.json` produced by `abicheck dump`, OR a Flow-2
    `abicheck_inputs/` directory the product build emitted (ADR-035 D5). The
    realistic flow is one artifact-side dump plus one source-side input prepared
    in parallel:

    \b
      abicheck dump libfoo.so -H include/   -o libfoo.bin.json   # L0/L1/L2
      abicheck dump --sources ./libfoo-src/ -o libfoo.src.json   # L3/L4/L5
      abicheck merge libfoo.bin.json libfoo.src.json -o libfoo.baseline.json

    \b
    A build that emits normalized facts can skip the source-side replay entirely
    and drop an `abicheck_inputs/` pack instead — abicheck ingests it without
    re-running a frontend:

    \b
      abicheck merge libfoo.bin.json ./abicheck_inputs/ -o libfoo.baseline.json

    The binary-bearing snapshot becomes the base (its ABI surface is kept); every
    input's embedded `build_source` facts are folded together per layer (each
    layer should come from exactly one input) and embedded in the output, so
    `compare old new` carries L3/L4/L5 with no out-of-band directories.
    """
    from .serialization import snapshot_to_json

    snaps = _merge_load_snapshots(inputs)
    base_path, base = _merge_pick_base(snaps)

    # A2: detect layer conflicts before folding (see _detect_merge_layer_conflicts).
    conflicts = _detect_merge_layer_conflicts(snaps)
    combined, contributors = _merge_fold_packs(snaps)

    _merge_handle_conflicts(conflicts, combined, on_conflict)

    if combined is None:
        click.echo(
            "Note: no input carried embedded build_source facts; the merged "
            "baseline is the base snapshot's ABI surface only.",
            err=True,
        )
    else:
        _merge_attach_combined(combined, base, output)

    output.write_text(snapshot_to_json(base), encoding="utf-8")
    _merge_print_summary(base_path, contributors, len(snaps), combined, output)


# ── Back-compat re-export shim (lazy, to avoid an import cycle) ───────────────
# `_load_source_graph` / `_resolve_symbol_from_report` historically lived here
# (re-exported from `cli_buildsource_helpers`, like the block above). They moved
# to `cli_graph` when the `graph` command group was extracted. A *static*
# `from .cli_graph import ...` would form a `cli_buildsource → cli_graph → cli →
# … → cli_buildsource` import cycle (the AI-readiness gate rejects it), so this
# module-level `__getattr__` (PEP 562) resolves them lazily via
# `importlib.import_module` — a runtime call, not a static import edge. It
# preserves the historical path `from abicheck.cli_buildsource import
# _load_source_graph` without coupling the two modules. New code should import
# from `cli_graph` directly.
_GRAPH_REEXPORTS = frozenset({"_load_source_graph", "_resolve_symbol_from_report"})


def __getattr__(name: str) -> Any:
    if name in _GRAPH_REEXPORTS:
        import importlib

        return getattr(importlib.import_module("abicheck.cli_graph"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
