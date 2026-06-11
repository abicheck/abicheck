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

"""`collect-evidence` command (ADR-028 D6, ADR-029).

Collects an optional EvidencePack from an existing build tree *without
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
from typing import TYPE_CHECKING

import click

from . import __version__ as _abicheck_version
from .cli import main
from .evidence.build_evidence import BuildEvidence
from .evidence.model import (
    CoverageStatus,
    EvidenceConfidence,
    EvidenceLayer,
    ExtractorRecord,
    LayerCoverage,
)
from .evidence.pack import EvidencePack
from .evidence.redaction import DEFAULT_REDACTION
from .evidence.source_replay import REPLAY_SCOPES

if TYPE_CHECKING:
    from .checker_types import Change
    from .evidence.source_abi import SourceAbiSurface
    from .evidence.source_extractors import (
        CastxmlSourceExtractor,
        ClangSourceExtractor,
    )
    from .evidence.source_graph import SourceGraphSummary
    from .model import AbiSnapshot


@main.command("collect-evidence")
@click.option("--binary", "binary", type=click.Path(path_type=Path), default=None,
              help="Built shared library this evidence describes (recorded as provenance).")
@click.option("-H", "--headers", "headers", multiple=True, type=click.Path(path_type=Path),
              help="Public header file or directory (recorded as provenance; repeat).")
@click.option("--build-dir", "build_dir", type=click.Path(path_type=Path), default=None,
              help="Build directory to inspect (CMake File API reply, Ninja query).")
@click.option("--compile-db", "compile_db", type=click.Path(path_type=Path), default=None,
              help="Path to compile_commands.json (or a directory containing it).")
@click.option("-p", "compile_db_p", type=click.Path(path_type=Path), default=None,
              help="Alias for --compile-db (build dir or file).")
@click.option("--cmake", "--cmake-file-api", "cmake", is_flag=True, default=False,
              help="Collect CMake File API facts from --build-dir (reads the reply directory; no build).")
@click.option("--ninja", "ninja", is_flag=True, default=False,
              help="Collect Ninja compile/graph facts from --build-dir via `ninja -t` queries.")
@click.option("--ninja-compdb", "ninja_compdb", type=click.Path(path_type=Path), default=None,
              help="Pre-captured `ninja -t compdb` output (for hermetic CI / no live ninja).")
@click.option("--bazel-cquery", "bazel_cquery", type=click.Path(path_type=Path), default=None,
              help="Pre-captured `bazel cquery --output=jsonproto` output (configured target graph).")
@click.option("--bazel-aquery", "bazel_aquery", type=click.Path(path_type=Path), default=None,
              help="Pre-captured `bazel aquery --output=jsonproto` output (compile/link action graph).")
@click.option("--make-dry-run", "make_dry_run", type=click.Path(path_type=Path), default=None,
              help="Pre-captured `make -n`/`--trace` transcript (reduced-confidence compile units).")
@click.option("--read-compiler-record", "read_compiler_record", is_flag=True, default=False,
              help="Recover compiler provenance from --binary (.GCC.command.line / DWARF DW_AT_producer).")
@click.option("--build-system", "build_system", default="generic", show_default=True,
              type=click.Choice(["generic", "cmake", "ninja", "bazel", "make"], case_sensitive=False),
              help="Build system hint for the compile-DB adapter.")
@click.option("--source-abi", "source_abi", is_flag=True, default=False,
              help="Collect L4 source ABI replay (parses sources/headers). REQUIRES clang "
                   "(or castxml/an Android dump); without the tool this fails gracefully and "
                   "source-only checks stay disabled.")
@click.option("--source-abi-extractor", "source_abi_extractor", default="clang", show_default=True,
              type=click.Choice(["clang", "castxml", "android"], case_sensitive=False),
              help="L4 backend: clang (inline/template/constexpr bodies + default args), "
                   "castxml (declarations/types/const values only), or android (reuse a "
                   "pre-captured header-abi .lsdump/.sdump).")
@click.option("--source-abi-scope", "source_abi_scope", default="target", show_default=True,
              type=click.Choice(list(REPLAY_SCOPES), case_sensitive=False),
              help="Which translation units to replay (ADR-030 D7): off | headers-only | "
                   "changed | target | full.")
@click.option("--source-abi-target", "source_abi_target", default="", help="Target id to scope replay to (e.g. target://libfoo).")
@click.option("--changed-path", "changed_paths", multiple=True, type=str,
              help="Changed file path for --source-abi-scope changed (repeat).")
@click.option("--android-dump", "android_dump", type=click.Path(path_type=Path), default=None,
              help="Pre-captured Android header-abi .lsdump/.sdump JSON (for --source-abi-extractor android).")
@click.option("--source-abi-cache", "source_abi_cache", type=click.Path(path_type=Path), default=None,
              help="Directory for the per-TU source ABI dump cache (ADR-030 D8).")
@click.option("--clang-bin", "clang_bin", default="clang", show_default=True, help="clang binary to use for source ABI replay.")
@click.option("--source-graph", "source_graph", default="off", show_default=True,
              type=click.Choice(["off", "summary"], case_sensitive=False),
              help="Collect an L5 source graph (ADR-031). 'summary' folds the L3 "
                   "build evidence into a compact target/source/header/option graph "
                   "for graph-to-graph comparison and finding localization.")
@click.option("--call-graph", "call_graph", is_flag=True, default=False,
              help="Add approximate direct-call edges to the L5 source graph via "
                   "clang AST (ADR-031 D4, phase 6). REQUIRES clang++; without it "
                   "the graph is collected without call edges. Implies --source-graph summary.")
@click.option("--include-graph", "include_graph", is_flag=True, default=False,
              help="Add compile-unit include edges to the L5 graph via `clang -MM` "
                   "(ADR-031 D3). REQUIRES clang++. Implies --source-graph summary.")
@click.option("--kythe-entries", "kythe_entries", type=click.Path(path_type=Path), default=None,
              help="Pre-captured Kythe entries JSON to fold into the L5 graph "
                   "(ADR-031 D5; non-executing). Implies --source-graph summary.")
@click.option("--codeql-results", "codeql_results", type=click.Path(path_type=Path), default=None,
              help="Pre-captured CodeQL call-graph query result JSON to fold into "
                   "the L5 graph (ADR-031 D5; non-executing). Implies --source-graph summary.")
@click.option("--extractor-manifest", "extractor_manifests", multiple=True,
              type=click.Path(path_type=Path),
              help="Register an external CLI evidence extractor by manifest path "
                   "(ADR-032 D3; trusted-by-operator, never auto-discovered). Repeat "
                   "for several. Its declared actions are intersected with the actions "
                   "enabled for this run (see --allow-build-query).")
@click.option("--source-root", "source_root", type=click.Path(path_type=Path), default=None,
              help="Source checkout root, supplied to external extractors that reference "
                   "the {source_root} placeholder (ADR-032 D3).")
@click.option("--allow-build-query", "allow_build_query", is_flag=True, default=False,
              help="Permit extractors to query the build system (ninja -t, bazel "
                   "cquery/aquery, CMake File API regeneration). Off by default: only "
                   "reading existing build outputs is allowed (ADR-032 D5).")
@click.option("--collection-mode", "collection_mode", default="permissive", show_default=True,
              type=click.Choice(["permissive", "strict", "audit"], case_sensitive=False),
              help="How extractor failures are handled (ADR-032 D9): permissive "
                   "(failures degrade coverage, collection continues), strict (a "
                   "failed/invalid extractor exits non-zero), audit (preserve raw "
                   "artifacts + full diagnostics).")
@click.option("-o", "--output", "output", type=click.Path(path_type=Path), required=True,
              help="Output evidence-pack directory.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def collect_evidence_cmd(
    binary: Path | None,
    headers: tuple[Path, ...],
    build_dir: Path | None,
    compile_db: Path | None,
    compile_db_p: Path | None,
    cmake: bool,
    ninja: bool,
    ninja_compdb: Path | None,
    bazel_cquery: Path | None,
    bazel_aquery: Path | None,
    make_dry_run: Path | None,
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
    """Collect an optional source/build EvidencePack from an existing build tree.

    \b
    Examples:
      abicheck collect-evidence --compile-db build/compile_commands.json -o libfoo.evidence/
      abicheck collect-evidence -p build/ --headers include/ -o libfoo.evidence/
      abicheck collect-evidence --build-dir build --cmake --ninja -o libfoo.evidence/

    The resulting directory attaches to a snapshot with `abicheck dump --evidence`.
    """
    effective_compile_db = compile_db or compile_db_p
    extractors: list[ExtractorRecord] = []
    merged = BuildEvidence()

    _run_adapters(
        merged, extractors,
        compile_db=effective_compile_db,
        build_dir=build_dir,
        cmake=cmake,
        ninja=ninja,
        ninja_compdb=ninja_compdb,
        bazel_cquery=bazel_cquery,
        bazel_aquery=bazel_aquery,
        make_dry_run=make_dry_run,
        binary=binary,
        read_compiler_record=read_compiler_record,
        build_system=build_system,
        verbose=verbose,
    )

    # External CLI extractors (ADR-032 D3): explicitly-registered subprocess
    # adapters, run under the resolved action ceiling (D5). Their normalized
    # build_evidence is folded into `merged` so it shares coverage and the pack.
    if extractor_manifests:
        _run_external_extractors(
            merged, extractors,
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
            merged, extractors,
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
        merged, extractors,
        source_graph=source_graph,
        call_graph=call_graph,
        include_graph=include_graph,
        kythe_entries=kythe_entries,
        codeql_results=codeql_results,
        surface=surface,
        clang_bin=clang_bin,
    )

    pack = EvidencePack.empty(
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
        merged.compile_units or merged.targets or merged.toolchains
        or merged.link_units or merged.build_options
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
        pack, merged, output,
        has_build=has_build,
        source_abi=source_abi,
        source_detail=source_detail,
        graph=graph,
        graph_detail=graph_detail,
    )


def _collect_source_graph(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    source_graph: str,
    call_graph: bool,
    include_graph: bool,
    kythe_entries: Path | None,
    codeql_results: Path | None,
    surface: SourceAbiSurface | None,
    clang_bin: str,
) -> tuple[SourceGraphSummary | None, str]:
    """Build the optional L5 source graph and fold in any requested augmentations.

    Any graph-augmenting option (call/include graph, Kythe/CodeQL ingest) implies
    graph collection. Returns ``(graph, graph_detail)``; ``graph`` is ``None`` when
    no graph was requested.
    """
    if (call_graph or include_graph or kythe_entries or codeql_results) and source_graph == "off":
        source_graph = "summary"
    if source_graph != "summary":
        return None, ""

    from .evidence.source_graph import build_source_graph
    # Fold the L4 surface in too when it was collected (--source-abi), so the
    # graph carries the public-reachability + source↔binary slices.
    graph = build_source_graph(merged, source_abi=surface)
    if call_graph:
        _collect_call_graph(graph, merged, extractors, clang_bin=clang_bin)
    if include_graph:
        _collect_include_graph(graph, merged, extractors, clang_bin=clang_bin)
    if kythe_entries or codeql_results:
        _ingest_graph_backends(graph, extractors,
                               kythe_entries=kythe_entries, codeql_results=codeql_results)
    graph.finalize()
    graph_detail = (
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges "
        f"({graph.coverage.get('targets', 0)} targets, "
        f"{graph.coverage.get('compile_units', 0)} compile units, "
        f"{graph.coverage.get('source_decls', 0)} source decls, "
        f"{graph.coverage.get('call_edges', {}).get('count', 0)} call edges, "
        f"{graph.coverage.get('include_edges', {}).get('count', 0)} include edges)"
    )
    extractors.append(ExtractorRecord(
        name="source_graph:summary",
        status="ok" if graph.nodes else "partial",
        detail=graph_detail if graph.nodes else "no build evidence to fold into a graph",
    ))
    return graph, graph_detail


def _enforce_strict_mode(
    extractors: list[ExtractorRecord], merged: BuildEvidence, collection_mode: str
) -> None:
    """Fail the command if strict mode is set and any extractor is incomplete (ADR-032 D9).

    Both a failed row and a skipped one (e.g. an extractor gated out by the action
    ceiling, so its requested evidence is absent) count — strict requires the
    evidence to be present. Called *before* the success output so a strict run
    never prints "Evidence pack written" and then exits non-zero.
    """
    if collection_mode != "strict":
        return
    incomplete = [e for e in extractors if e.status in ("failed", "skipped")]
    if not incomplete:
        return
    names = ", ".join(sorted(f"{e.name}:{e.status}" for e in incomplete))
    for diag in merged.diagnostics:
        click.echo(f"  note: {diag}", err=True)
    raise click.ClickException(
        f"strict collection mode: {len(incomplete)} extractor(s) did not "
        f"produce valid evidence ({names}). Fix the inputs/tools, grant the "
        "needed actions, or use --collection-mode permissive."
    )


def _echo_collection_summary(
    pack: EvidencePack,
    merged: BuildEvidence,
    output: Path,
    *,
    has_build: bool,
    source_abi: bool,
    source_detail: str,
    graph: SourceGraphSummary | None,
    graph_detail: str,
) -> None:
    """Print the per-layer summary for a successfully written evidence pack."""
    click.echo(f"Evidence pack written to {output}")
    click.echo(f"  content hash: {pack.content_hash()}")
    if has_build:
        click.echo(
            f"  L3 build context: {len(merged.compile_units)} compile units, "
            f"{len(merged.targets)} targets, {len(merged.toolchains)} toolchains"
        )
    else:
        click.echo("  L3 build context: not collected (no adapters produced facts)")
    if source_abi:
        click.echo(f"  L4 source ABI replay: {source_detail}")
    if graph is not None:
        click.echo(f"  L5 source graph: {graph_detail or 'empty (no build evidence)'}")
    for diag in merged.diagnostics:
        click.echo(f"  note: {diag}", err=True)


def _run_adapters(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    compile_db: Path | None,
    build_dir: Path | None,
    cmake: bool,
    ninja: bool,
    ninja_compdb: Path | None,
    bazel_cquery: Path | None,
    bazel_aquery: Path | None,
    make_dry_run: Path | None,
    binary: Path | None,
    read_compiler_record: bool,
    build_system: str,
    verbose: bool,
) -> None:
    """Run the requested build-evidence adapters and fold them into *merged*."""
    # Import adapters lazily so `collect-evidence --help` stays cheap.
    from .evidence.adapters import (
        BazelAdapter,
        CMakeFileApiAdapter,
        CompileDbAdapter,
        MakeAdapter,
        NinjaAdapter,
    )

    if compile_db is not None:
        try:
            ev = CompileDbAdapter(compile_db, build_system=build_system).collect()
            merged.merge(ev)
            extractors.append(ExtractorRecord(
                name="compile_commands",
                status="ok",
                inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                detail=f"{len(ev.compile_units)} compile units",
            ))
        except (OSError, ValueError) as exc:
            extractors.append(ExtractorRecord(
                name="compile_commands", status="failed", inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                detail=str(exc),
            ))
            merged.diagnostics.append(f"compile_commands: {exc}")

    if cmake:
        if build_dir is None:
            raise click.UsageError("--cmake requires --build-dir.")
        ev = CMakeFileApiAdapter(build_dir).collect()
        merged.merge(ev)
        extractors.append(ExtractorRecord(
            name="cmake_file_api", status="ok" if ev.targets else "partial",
            inputs=[DEFAULT_REDACTION.path(str(build_dir))],
            detail=f"{len(ev.targets)} targets, {len(ev.toolchains)} toolchains",
        ))

    if ninja or ninja_compdb is not None:
        if build_dir is None and ninja_compdb is None:
            raise click.UsageError("--ninja requires --build-dir (or pass --ninja-compdb).")
        adapter = NinjaAdapter(build_dir, compdb=ninja_compdb)
        ev = adapter.collect()
        merged.merge(ev)
        extractors.append(ExtractorRecord(
            name="ninja", status="ok" if ev.compile_units else "partial",
            inputs=[DEFAULT_REDACTION.path(str(build_dir or ninja_compdb))],
            detail=f"{len(ev.compile_units)} compile units",
        ))

    if bazel_cquery is not None or bazel_aquery is not None:
        ev = BazelAdapter(cquery=bazel_cquery, aquery=bazel_aquery).collect()
        merged.merge(ev)
        inputs = [DEFAULT_REDACTION.path(str(p)) for p in (bazel_cquery, bazel_aquery) if p is not None]
        extractors.append(ExtractorRecord(
            name="bazel", status="ok" if (ev.targets or ev.compile_units or ev.link_units) else "partial",
            inputs=inputs,
            detail=(
                f"{len(ev.targets)} targets, {len(ev.compile_units)} compile units, "
                f"{len(ev.link_units)} link units"
            ),
        ))

    if make_dry_run is not None:
        # Only a pre-captured transcript — the Make adapter never runs make,
        # because `make -n` still executes `+` recipes and `$(shell …)`.
        ev = MakeAdapter(build_dir, dry_run=make_dry_run).collect()
        merged.merge(ev)
        extractors.append(ExtractorRecord(
            name="make", status="ok" if ev.compile_units else "partial",
            inputs=[DEFAULT_REDACTION.path(str(make_dry_run))],
            detail=f"{len(ev.compile_units)} compile units (reduced confidence)",
        ))

    if read_compiler_record:
        if binary is None:
            raise click.UsageError("--read-compiler-record requires --binary.")
        from .evidence.compiler_record import extract_compiler_record
        ev = extract_compiler_record(binary)
        merged.merge(ev)
        extractors.append(ExtractorRecord(
            name="compiler_record",
            status="ok" if (ev.toolchains or ev.compile_units) else "partial",
            inputs=[DEFAULT_REDACTION.path(str(binary))],
            detail=f"{len(ev.toolchains)} toolchains, {len(ev.compile_units)} compile units",
        ))


def _run_external_extractors(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    manifests: tuple[Path, ...],
    pack_root: Path,
    binary: Path | None,
    build_dir: Path | None,
    source_root: Path | None,
    compile_db: Path | None,
    allow_build_query: bool,
    collection_mode: str,
    verbose: bool,
) -> None:
    """Run explicitly-registered external CLI extractors (ADR-032 D3/D5/D9).

    Each manifest is loaded from the operator-provided path (never auto-
    discovered). The run-permitted action set starts at ``inspect`` and adds
    ``query_build_system`` only with ``--allow-build-query``; a manifest that
    needs an action outside that set is recorded as skipped rather than run
    (its declared actions are a ceiling intersected with what the run allows).
    Normalized ``build_evidence`` outputs are folded into *merged*; failures are
    captured as extractor rows so the collection-mode policy (D9) can act on them.
    """
    from .evidence.build_evidence import BuildEvidence as _BuildEvidence
    from .evidence.extractor import (
        CollectionAction,
        CollectionContext,
        CollectionMode,
    )
    from .evidence.extractor_manifest import (
        ManifestError,
        load_extractor_manifest,
        run_external_extractor,
    )

    run_permitted = {CollectionAction.INSPECT}
    if allow_build_query:
        run_permitted.add(CollectionAction.QUERY_BUILD_SYSTEM)

    pack_root.mkdir(parents=True, exist_ok=True)

    for manifest_path in manifests:
        try:
            manifest = load_extractor_manifest(manifest_path)
        except ManifestError as exc:
            extractors.append(ExtractorRecord(
                name=f"external:{manifest_path.name}", status="failed",
                inputs=[DEFAULT_REDACTION.path(str(manifest_path))], detail=str(exc),
            ))
            merged.diagnostics.append(f"extractor manifest {manifest_path}: {exc}")
            continue

        context = CollectionContext(
            binary_paths=[binary] if binary else [],
            build_root=build_dir,
            source_root=source_root,
            compile_db=compile_db,
            allowed_actions=set(run_permitted),
            collection_mode=CollectionMode(collection_mode),
            redaction_policy=DEFAULT_REDACTION,
        )
        # An extractor gated out by the action ceiling comes back as a 'skipped'
        # record (run_external_extractor decides via discover()), so there is no
        # permission exception for the caller to handle here.
        _norm, record = run_external_extractor(manifest, context, pack_root)

        extractors.append(record)
        if record.status != "ok":
            merged.diagnostics.append(
                f"{manifest.name}: {record.detail or 'extractor did not complete'}"
            )
            _purge_external_outputs(pack_root, manifest)
            continue

        # Reject output kinds collect-evidence cannot fold yet — only
        # build_evidence is wired into the pack here. A manifest that advertises
        # a source_abi / source_graph_summary output would otherwise be recorded
        # ok while its evidence is silently dropped (and pack.write() removes the
        # canonical source/graph files), so the requested evidence is absent even
        # though the extractor "succeeded" (Codex P2). Fail loudly instead.
        unsupported = sorted({o.kind for o in manifest.outputs if o.kind != "build_evidence"})
        if unsupported:
            record.status = "failed"
            record.detail = record.detail or f"unsupported output kind(s): {', '.join(unsupported)}"
            # The outputs are about to be purged from the pack, so the ledger row
            # must not keep advertising their (now-removed) paths (Codex P2).
            record.artifacts = []
            merged.diagnostics.append(
                f"{manifest.name}: output kind(s) {', '.join(unsupported)} are not yet "
                "supported by collect-evidence (only build_evidence is folded into the pack)"
            )
            _purge_external_outputs(pack_root, manifest)
            continue

        # Fold any normalized build_evidence outputs into the merged L3 evidence.
        # `validate` only proved each file is JSON; it may still be structurally
        # invalid BuildEvidence (e.g. a compile unit missing its id), which
        # BuildEvidence.from_dict surfaces as KeyError/TypeError. Parse *all*
        # declared outputs first and merge only if every one is valid — so a
        # later malformed output never leaves an earlier one's evidence merged
        # from an extractor we then mark failed (D8: invalid output must not
        # influence collected facts). A failure downgrades the ledger row, never
        # crashes the command (D9 permissive), and makes strict mode reject it.
        import json as _json
        parsed: list[_BuildEvidence] = []
        fold_ok = True
        for output in manifest.outputs:
            if output.kind != "build_evidence":
                continue
            be_path = pack_root / output.path
            try:
                parsed.append(_BuildEvidence.from_dict(
                    _json.loads(be_path.read_text(encoding="utf-8"))
                ))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                fold_ok = False
                record.status = "failed"
                record.detail = record.detail or f"invalid build_evidence output: {exc}"
                # _purge_external_outputs (below) removes these files, so the
                # failed ledger row must not keep advertising stale paths to a
                # missing/replaced artifact (Codex P2).
                record.artifacts = []
                merged.diagnostics.append(
                    f"{manifest.name}: could not fold {output.path}: {exc}"
                )
                break
        if fold_ok:
            for build_evidence in parsed:
                merged.merge(build_evidence)
        else:
            _purge_external_outputs(pack_root, manifest)


def _purge_external_outputs(pack_root: Path, manifest: object) -> None:
    """Remove a failed external extractor's normalized outputs from the pack.

    A failed/skipped extractor must be isolated from the collected pack: its
    normalized output files (and its ``normalized/<name>/`` subtree) would
    otherwise be hashed into ``EvidencePack`` ``manifest.artifacts`` and the
    content hash, so an invalid output would change pack identity and publish a
    digest for evidence that was never folded (Codex P2). Raw artifacts under
    ``raw/`` are *not* removed — they are provenance-only, never hashed, and are
    what audit mode preserves for debugging.
    """
    import shutil

    name = getattr(manifest, "name", "")
    for output in getattr(manifest, "outputs", []):
        try:
            (pack_root / output.path).unlink()
        except OSError:
            pass
    norm_dir = pack_root / "normalized" / name
    if norm_dir.is_dir():
        shutil.rmtree(norm_dir, ignore_errors=True)


def _collect_call_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    clang_bin: str,
) -> None:
    """Run the Clang call extractor over the build and fold edges into *graph*.

    Best-effort (ADR-031 D4 / ADR-028 D3): a missing clang or a parse failure
    records a partial/failed extractor row and leaves the graph without call
    edges — it never aborts collection. Re-finalizes the graph so the content
    hash and coverage counts reflect the added edges.
    """
    from .evidence.call_graph import ClangCallGraphExtractor, augment_graph_with_calls

    # clang_bin defaults to "clang" (the L4 extractor's tool); the call
    # extractor needs a C++ driver, so prefer clang++ unless the user pointed
    # --clang-bin at a specific clang.
    extractor = ClangCallGraphExtractor(clang_bin=clang_bin if clang_bin != "clang" else "clang++")
    if not extractor.available():
        extractors.append(ExtractorRecord(
            name="call_graph:clang", status="failed",
            detail=f"{extractor.clang_bin} not found in PATH; graph collected without call edges",
        ))
        return
    edges = extractor.extract_from_build(merged)
    added = augment_graph_with_calls(graph, edges)
    graph.finalize()
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"call_graph: {diag}")
    extractors.append(ExtractorRecord(
        name="call_graph:clang",
        status="ok" if added else "partial",
        detail=f"{added} call edges from {len(merged.compile_units)} compile units",
    ))


def _collect_include_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    clang_bin: str,
) -> None:
    """Run `clang -MM` over the build and fold include edges into *graph* (D3).

    Best-effort like the call extractor: a missing clang records a failed row
    and leaves the graph without include edges, never aborting collection.
    """
    from .evidence.include_graph import (
        ClangIncludeExtractor,
        augment_graph_with_includes,
    )

    extractor = ClangIncludeExtractor(clang_bin=clang_bin if clang_bin != "clang" else "clang++")
    if not extractor.available():
        extractors.append(ExtractorRecord(
            name="include_graph:clang", status="failed",
            detail=f"{extractor.clang_bin} not found in PATH; graph collected without include edges",
        ))
        return
    includes = extractor.extract_from_build(merged)
    added = augment_graph_with_includes(graph, includes)
    graph.finalize()
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"include_graph: {diag}")
    extractors.append(ExtractorRecord(
        name="include_graph:clang",
        status="ok" if added else "partial",
        detail=f"{added} include edges from {len(includes)} compile units",
    ))


def _ingest_graph_backends(
    graph: SourceGraphSummary,
    extractors: list[ExtractorRecord],
    *,
    kythe_entries: Path | None,
    codeql_results: Path | None,
) -> None:
    """Fold pre-captured Kythe/CodeQL exports into *graph* (ADR-031 D5).

    Non-executing (ADR-028 D6): reads the provided JSON exports only. A malformed
    or missing file records a failed extractor row and is skipped.
    """
    import json as _json

    from .evidence.graph_backends import (
        ingest_codeql_call_results,
        ingest_kythe_entries,
    )

    def _load(path: Path, name: str) -> object | None:
        try:
            return _json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            extractors.append(ExtractorRecord(
                name=name, status="failed",
                inputs=[DEFAULT_REDACTION.path(str(path))], detail=str(exc),
            ))
            return None

    if kythe_entries is not None:
        data = _load(kythe_entries, "graph_backend:kythe")
        if data is not None:
            entries = data if isinstance(data, list) else (
                data.get("entries", []) if isinstance(data, dict) else []
            )
            added = ingest_kythe_entries(graph, entries, ref=DEFAULT_REDACTION.path(str(kythe_entries)))
            extractors.append(ExtractorRecord(
                name="graph_backend:kythe", status="ok" if added else "partial",
                inputs=[DEFAULT_REDACTION.path(str(kythe_entries))], detail=f"{added} edges ingested",
            ))

    if codeql_results is not None:
        data = _load(codeql_results, "graph_backend:codeql")
        if isinstance(data, dict):
            added = ingest_codeql_call_results(graph, data, ref=DEFAULT_REDACTION.path(str(codeql_results)))
            extractors.append(ExtractorRecord(
                name="graph_backend:codeql", status="ok" if added else "partial",
                inputs=[DEFAULT_REDACTION.path(str(codeql_results))], detail=f"{added} edges ingested",
            ))


def _build_coverage(
    merged: BuildEvidence,
    has_build: bool,
    surface: SourceAbiSurface | None = None,
    source_detail: str = "",
    graph: SourceGraphSummary | None = None,
    graph_detail: str = "",
) -> list[LayerCoverage]:
    """Build the L3/L4/L5 coverage rows for the pack manifest (ADR-028 D7)."""
    if has_build:
        systems = sorted({g.kind for g in merged.generators}) or ["generic"]
        l3 = LayerCoverage(
            layer=EvidenceLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=EvidenceConfidence.HIGH if merged.targets else EvidenceConfidence.REDUCED,
            detail=(
                f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
                f"{len(merged.targets)} targets"
            ),
        )
    else:
        l3 = LayerCoverage(layer=EvidenceLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED)
    # L4 is PRESENT when at least one TU parsed into the surface, PARTIAL when
    # replay ran but every TU failed/was empty (e.g. clang missing), else
    # NOT_COLLECTED. The surface keeps decls/types only when extraction worked.
    if surface is not None:
        # PRESENT when the surface actually carries reachable entities; PARTIAL
        # when replay ran but yielded nothing (tool missing, all TUs failed, or
        # no public surface matched) — never silently NOT_COLLECTED, so the
        # capability report can explain the gap.
        any_entities = bool(
            surface.reachable_declarations or surface.reachable_types
            or surface.reachable_macros or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        if any_entities:
            l4 = LayerCoverage(
                layer=EvidenceLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PRESENT,
                confidence=EvidenceConfidence.HIGH, detail=source_detail,
            )
        else:
            l4 = LayerCoverage(
                layer=EvidenceLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PARTIAL,
                confidence=EvidenceConfidence.REDUCED, detail=source_detail,
            )
    else:
        l4 = LayerCoverage(layer=EvidenceLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED)
    # L5 is PRESENT when the graph carries edges; PARTIAL when a graph was built
    # but had no build evidence to fold (so it is empty), else NOT_COLLECTED.
    if graph is not None:
        if graph.edges:
            l5 = LayerCoverage(
                layer=EvidenceLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT,
                confidence=EvidenceConfidence.REDUCED, detail=graph_detail,
            )
        else:
            l5 = LayerCoverage(
                layer=EvidenceLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PARTIAL,
                confidence=EvidenceConfidence.UNKNOWN,
                detail=graph_detail or "no build evidence to fold into a graph",
            )
    else:
        l5 = LayerCoverage(layer=EvidenceLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED)
    return [l3, l4, l5]


def _exported_symbols_from_binary(binary: Path | None) -> list[str]:
    """Best-effort exported (mangled) symbol names from ``binary`` for D5 linking.

    Used so the source-decl → binary-symbol mapping (and
    ``source_decl_binary_symbol_mismatch``) is populated. Failures are swallowed
    (returns ``[]``): the other eight source findings do not need symbols, so a
    binary that cannot be parsed must not block L4 collection.
    """
    if binary is None or not Path(binary).is_file():
        return []
    try:
        from .service import detect_binary_format, run_dump

        fmt = detect_binary_format(Path(binary))
        if not fmt:
            return []
        snap = run_dump(Path(binary), fmt)
    except Exception:  # noqa: BLE001 - best-effort; never fail collection on this
        return []
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    return sorted(syms)


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
    from .evidence.source_abi import SourceAbiSurface
    from .evidence.source_replay import (
        SourceAbiCache,
        public_header_roots_for,
        run_source_replay,
    )

    exported = _exported_symbols_from_binary(binary)
    library = str(binary) if binary else ""
    # Header roots: explicit --headers win; else pull from the build targets.
    roots = [str(h) for h in headers] or public_header_roots_for(merged, target_id)

    if extractor == "android":
        return _collect_source_abi_android(
            android_dump, extractors, target_id=target_id,
            exported=exported, library=library, roots=roots,
        )

    impl: ClangSourceExtractor | CastxmlSourceExtractor
    if extractor == "clang":
        from .evidence.source_extractors import ClangSourceExtractor
        impl = ClangSourceExtractor(clang_bin=clang_bin)
        tool_name = clang_bin
    else:
        from .evidence.source_extractors import CastxmlSourceExtractor
        impl = CastxmlSourceExtractor()
        tool_name = "castxml"

    if not merged.compile_units:
        extractors.append(ExtractorRecord(
            name=f"source_abi:{extractor}", status="partial",
            detail="no compile units in build evidence; collect L3 first (e.g. --compile-db)",
        ))
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            "skipped: no L3 build context (need compile units to replay)",
        )
    if not impl.available():
        extractors.append(ExtractorRecord(
            name=f"source_abi:{extractor}", status="failed",
            detail=f"{tool_name} not found in PATH; source-only checks disabled",
        ))
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            f"unavailable: {tool_name} not on PATH — source-only checks disabled "
            "(macros, default args, inline/template/constexpr bodies). Install "
            f"{tool_name} or omit --source-abi.",
        )

    cache = SourceAbiCache(cache_dir) if cache_dir else None
    surface, diagnostics = run_source_replay(
        merged, impl, scope=scope, changed_paths=changed_paths,
        target_id=target_id, library=library, exported_symbols=exported,
        public_header_roots=roots, cache=cache,
    )
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    extractors.append(ExtractorRecord(
        name=f"source_abi:{extractor}",
        status="ok" if parsed else "partial",
        detail=f"scope={scope}, {parsed}/{selected} TUs parsed, {len(diagnostics)} failures",
    ))
    return surface, (
        f"{extractor} extractor, scope={scope}: parsed {parsed}/{selected} TUs, "
        f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types, "
        f"{len(surface.reachable_inline_bodies)} inline bodies, "
        f"{len(surface.reachable_templates)} templates"
        + (f", {len(diagnostics)} TU(s) failed (partial coverage)" if diagnostics else "")
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
    from .evidence.source_abi import SourceAbiSurface
    from .evidence.source_extractors import (
        AndroidHeaderAbiAdapter,
        SourceExtractionError,
    )
    from .evidence.source_link import link_source_abi

    if android_dump is None:
        raise click.UsageError(
            "--source-abi-extractor android requires --android-dump <file.lsdump|.sdump>."
        )
    adapter = AndroidHeaderAbiAdapter()
    try:
        tu = adapter.load(android_dump, target_id=target_id, public_header_roots=roots)
    except SourceExtractionError as exc:
        extractors.append(ExtractorRecord(
            name="source_abi:android", status="failed",
            inputs=[DEFAULT_REDACTION.path(str(android_dump))], detail=str(exc),
        ))
        return SourceAbiSurface(library=library, target_id=target_id), f"failed: {exc}"
    surface = link_source_abi(
        [tu], exported_symbols=exported, library=library, target_id=target_id,
    )
    extractors.append(ExtractorRecord(
        name="source_abi:android", status="ok",
        inputs=[DEFAULT_REDACTION.path(str(android_dump))],
        detail=f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types",
    ))
    return surface, (
        f"android dump: {len(surface.reachable_declarations)} decls, "
        f"{len(surface.reachable_types)} types"
    )


# ── Attach / compare integration (ADR-028 D6, D7; ADR-029 D9) ─────────────────


def attach_evidence_pack(snap: AbiSnapshot, evidence_dir: Path) -> None:
    """Attach an EvidencePack reference to *snap* (ADR-028 D8).

    Loads the pack manifest, computes its content hash, and stores only the
    lightweight reference on the snapshot. Raises a Click error if the directory
    is not a valid pack.
    """
    snap.evidence_pack = _load_pack_or_raise(evidence_dir).to_ref(path_hint=str(evidence_dir))


def collect_compare_evidence(
    old_evidence: Path | None,
    new_evidence: Path | None,
    evidence_mode: str,
    new_snapshot: AbiSnapshot,
    old_snapshot: AbiSnapshot | None = None,
) -> tuple[list[Change], list[dict[str, object]]]:
    """Load packs, diff their build evidence, echo coverage, return findings.

    Per ADR-028 D3 the build-context findings are folded into the ordinary
    verdict pipeline as ``extra_changes`` and never override artifact-backed
    verdicts. The D7 coverage table is printed to stderr here (covers every
    output format) and also returned as serialized rows so the JSON report can
    carry a structured ``evidence_coverage`` block. Returns
    ``(changes, coverage_rows)``.

    When ``old_snapshot`` is supplied, the base and target coverage are compared
    layer-by-layer: if the base was analyzed with evidence the target lacks
    (e.g. a full base scan vs a binary+headers-only target), a single
    ``EVIDENCE_COVERAGE_ASYMMETRIC`` finding spells out exactly which pieces the
    target is missing so the degraded comparison is never silent.
    """
    from .evidence.build_diff import check_header_parse_drift, diff_build_evidence

    if old_evidence is None and new_evidence is None:
        if evidence_mode != "off":
            click.echo(
                f"Note: --evidence-mode {evidence_mode} requested but no evidence "
                "packs were provided; inline collection for this mode is not yet "
                "available. Use `abicheck collect-evidence` + --old/--new-evidence.",
                err=True,
            )
        return [], []

    old_pack = _load_pack_or_raise(old_evidence) if old_evidence else None
    new_pack = _load_pack_or_raise(new_evidence) if new_evidence else None

    changes: list[Change] = []
    old_build = old_pack.build_evidence if old_pack else None
    new_build = new_pack.build_evidence if new_pack else None
    if old_build is not None and new_build is not None:
        changes.extend(diff_build_evidence(old_build, new_build))
    # Header-parse-context drift only applies when the new snapshot actually
    # carries a public-header AST (L2). A binary-only compare has no header
    # parse context that could have drifted, so the finding would be misleading.
    new_has_headers = bool(
        new_snapshot.from_headers and not new_snapshot.from_headers_inferred
    )
    if new_build is not None and new_has_headers:
        changes.extend(check_header_parse_drift(
            new_build,
            headers_parsed_with_context=new_snapshot.parsed_with_build_context,
        ))

    if old_snapshot is not None:
        changes.extend(
            _detect_coverage_asymmetry(old_snapshot, old_pack, new_snapshot, new_pack)
        )

    # L4 source ABI replay diff (ADR-030 D6): both packs must carry a source
    # surface. Per ADR-028 D3 these are ordinary API_BREAK/RISK findings folded
    # into the verdict pipeline — never sole authority for a BREAKING verdict.
    old_surface = old_pack.source_abi if old_pack else None
    new_surface = new_pack.source_abi if new_pack else None
    if old_surface is not None and new_surface is not None:
        from .evidence.source_diff import diff_source_abi
        changes.extend(diff_source_abi(old_surface, new_surface))

    # L5 source graph diff (ADR-031 D6): both packs must carry a graph summary.
    # Per ADR-028 D3 / ADR-031 D6 these are ordinary RISK findings folded into
    # the verdict pipeline — they explain and prioritize, never sole authority.
    old_graph = old_pack.source_graph if old_pack else None
    new_graph = new_pack.source_graph if new_pack else None
    if old_graph is not None and new_graph is not None:
        from .evidence.source_graph import diff_source_graph_findings
        changes.extend(diff_source_graph_findings(old_graph, new_graph))

    src_pack = new_pack or old_pack
    coverage = list(src_pack.manifest.coverage) if src_pack else []
    if not coverage:
        coverage = [
            LayerCoverage(layer=layer.value, status=CoverageStatus.NOT_COLLECTED)
            for layer in (EvidenceLayer.L3_BUILD, EvidenceLayer.L4_SOURCE_ABI, EvidenceLayer.L5_SOURCE_GRAPH)
        ]
    intrinsic = _intrinsic_coverage(new_snapshot)
    _echo_coverage(intrinsic, coverage)
    _echo_capabilities(intrinsic, coverage)
    coverage_rows: list[dict[str, object]] = [c.to_dict() for c in (*intrinsic, *coverage)]
    return changes, coverage_rows


def _load_pack_or_raise(evidence_dir: Path) -> EvidencePack:
    try:
        return EvidencePack.load(evidence_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"Invalid evidence pack at {evidence_dir}: {exc}") from exc


def _intrinsic_coverage(snap: AbiSnapshot) -> list[LayerCoverage]:
    """Derive L0/L1/L2 coverage rows from a snapshot (ADR-028 D7)."""
    def row(layer: str, present: bool, detail: str) -> LayerCoverage:
        return LayerCoverage(
            layer=layer,
            status=CoverageStatus.PRESENT if present else CoverageStatus.NOT_COLLECTED,
            confidence=EvidenceConfidence.HIGH if present else EvidenceConfidence.UNKNOWN,
            detail=detail,
        )

    has_debug = bool(snap.dwarf or snap.dwarf_advanced)
    has_headers = bool(snap.from_headers and not snap.from_headers_inferred)
    return [
        row("L0", bool(snap.elf or snap.pe or snap.macho), snap.platform or ""),
        row("L1", has_debug, "DWARF" if has_debug else ""),
        row("L2", has_headers, "header-scoped" if has_headers else ""),
    ]


# Human-readable layer names, ordered shallow→deep, shared by the coverage
# table and the asymmetry finding so both speak the same vocabulary.
_LAYER_NAMES: dict[str, str] = {
    "L0": "L0 binary metadata", "L1": "L1 debug info", "L2": "L2 public header AST",
    "L3_build": "L3 build context", "L4_source_abi": "L4 source ABI replay",
    "L5_source_graph": "L5 source graph summary",
}


def _echo_coverage(intrinsic: list[LayerCoverage], optional: list[LayerCoverage]) -> None:
    """Print the D7 evidence-coverage table to stderr (all output formats)."""
    click.echo("Evidence coverage:", err=True)
    for cov in [*intrinsic, *optional]:
        extra = ""
        if cov.status != CoverageStatus.NOT_COLLECTED:
            extra = f", {cov.confidence.value} confidence"
            if cov.detail:
                extra += f": {cov.detail}"
        click.echo(f"  {_LAYER_NAMES.get(cov.layer, cov.layer):<26} {cov.status.value}{extra}", err=True)


def _layer_presence(snap: AbiSnapshot, pack: EvidencePack | None) -> dict[str, bool]:
    """Map every evidence layer id → present? for one side of the compare.

    L0/L1/L2 are intrinsic to the snapshot; L3/L4/L5 come from the pack manifest
    coverage (with the loaded ``build_evidence`` object treated as authoritative
    proof that L3 is present even if the manifest row is stale).
    """
    present = {
        row.layer: row.status != CoverageStatus.NOT_COLLECTED
        for row in _intrinsic_coverage(snap)
    }
    by_layer = {c.layer: c.present for c in (pack.manifest.coverage if pack else [])}
    for layer in (EvidenceLayer.L3_BUILD, EvidenceLayer.L4_SOURCE_ABI, EvidenceLayer.L5_SOURCE_GRAPH):
        present[layer.value] = by_layer.get(layer.value, False)
    if pack is not None and pack.build_evidence is not None:
        present[EvidenceLayer.L3_BUILD.value] = True
    return present


def _detect_coverage_asymmetry(
    old_snap: AbiSnapshot,
    old_pack: EvidencePack | None,
    new_snap: AbiSnapshot,
    new_pack: EvidencePack | None,
) -> list[Change]:
    """Flag layers the base was analyzed with but the target lacks (ADR-028 D7).

    A full base scan (binary + debug + headers + build + sources) compared
    against a binary+headers-only target is a legitimate, supported comparison —
    but it is *degraded*: the layers the target is missing cannot prove or
    disprove changes, so the verdict is scoped to what both sides share. Rather
    than let that happen silently, emit one ``EVIDENCE_COVERAGE_ASYMMETRIC``
    RISK finding naming exactly which pieces the target is missing.

    Only the base→target degradation direction is reported (target missing what
    the base had). A target that is *richer* than the base does not undermine
    the comparison, so it is not flagged here.
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    old_present = _layer_presence(old_snap, old_pack)
    new_present = _layer_presence(new_snap, new_pack)
    missing = [
        layer
        for layer in _LAYER_NAMES
        if old_present.get(layer) and not new_present.get(layer)
    ]
    if not missing:
        return []

    human = ", ".join(_LAYER_NAMES[m] for m in missing)
    return [
        Change(
            kind=ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC,
            symbol="evidence:coverage",
            description=(
                f"Base was analyzed with evidence the target lacks ({human}). "
                "The comparison is scoped to the layers both sides share, so "
                "changes only those missing layers could prove are NOT reported "
                "and this verdict must not be read as a full-coverage result. "
                "Re-scan the target with the same inputs (e.g. -g for debug "
                "info, collect-evidence for build/source context) to restore "
                "full coverage."
            ),
            old_value=human,
            new_value="not collected on target",
        )
    ]


#: One row per check category: (label, evidence layer that enables it, the
#: question it answers, and why it is off when that layer is absent). This is the
#: "what is and is not being checked, and why" report (ADR-028 D7): the tiers run
#: from a bare binary up through debug symbols, headers, build data, and sources.
_CHECK_CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
    ("Symbol presence & linkage (added/removed/SONAME)", "L0",
     "from the binary's dynamic symbol table",
     "needs the built binary"),
    ("Type layout, members, vtables, signatures", "L1",
     "from DWARF/PDB debug info",
     "no debug info: checks limited to symbol-level, not struct/member/layout"),
    ("API decls absent from the symbol table; public-surface scoping", "L2",
     "from the public header AST",
     "no headers: header-only/inline-API declarations are invisible"),
    ("Build-flag & toolchain drift (visibility, std, ABI flags)", "L3_build",
     "from build-system data (compile DB / CMake / Ninja / Bazel)",
     "no build data: flag/toolchain regressions are not detected"),
    ("Macros, default args, inline/template/constexpr bodies", "L4_source_abi",
     "from source ABI replay (requires a source extractor: clang, castxml, or android)",
     "no source replay evidence: source-only API changes are not detected"),
    ("Impact / call / reachability graph", "L5_source_graph",
     "from the source graph summary",
     "no graph evidence: cross-symbol impact is not analyzed"),
)


def _echo_capabilities(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> None:
    """Print exactly which check categories are enabled — and why others are not.

    Driven by the evidence coverage (ADR-028 D7): each check category is gated on
    one evidence layer, so the user sees, for the inputs they actually provided
    (binary only → +debug → +headers → +build data → +sources), which checks ran
    and the concrete reason each disabled one is off.
    """
    # Only a PRESENT layer enables its checks: a PARTIAL layer (e.g. L4 when clang
    # was missing or every TU failed, so no entities were extracted) ran but
    # produced nothing, and must read as [off], not [on] (CodeRabbit review).
    present = {
        c.layer
        for c in (*intrinsic, *optional)
        if c.status == CoverageStatus.PRESENT
    }
    click.echo("Checks enabled for this scan (and why others are not):", err=True)
    for label, layer, how, why_off in _CHECK_CAPABILITIES:
        if layer in present:
            click.echo(f"  [on]  {label} — {how}", err=True)
        else:
            click.echo(f"  [off] {label} — {why_off}", err=True)


# ── compare-graph: structural graph-to-graph diff (ADR-031 D6, D8) ────────────


def _load_source_graph(path: Path) -> SourceGraphSummary:
    """Load a source graph summary from a JSON file or an evidence-pack dir.

    Accepts either ``…/graph/source_graph_summary.json`` directly or a pack
    directory (the graph is read from its manifest layout). Raises a Click error
    when neither yields a graph so the failure is actionable.
    """
    import json as _json

    from .evidence.source_graph import SourceGraphSummary

    if path.is_dir():
        pack = _load_pack_or_raise(path)
        if pack.source_graph is None:
            raise click.ClickException(
                f"Evidence pack at {path} has no L5 source graph "
                "(collect it with `collect-evidence --source-graph summary`)."
            )
        return pack.source_graph
    if not path.is_file():
        raise click.ClickException(f"No source graph summary at {path}.")
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read source graph at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"{path} must contain a JSON object.")
    # SourceGraphSummary.from_dict is intentionally forgiving (it defaults a
    # missing nodes/edges to empty), so guard here: an unrelated JSON file (e.g.
    # a pack manifest) would otherwise load as an empty graph and report a bogus
    # diff instead of an actionable error.
    if not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
        raise click.ClickException(
            f"{path} is not a source graph summary "
            "(expected top-level 'nodes' and 'edges' lists)."
        )
    return SourceGraphSummary.from_dict(data)


@main.command("compare-graph")
@click.argument("old", type=click.Path(path_type=Path))
@click.argument("new", type=click.Path(path_type=Path))
@click.option("--format", "fmt", default="text", show_default=True,
              type=click.Choice(["text", "json"], case_sensitive=False),
              help="Output format for the structural graph diff.")
def compare_graph_cmd(old: Path, new: Path, fmt: str) -> None:
    """Compare two L5 source graph summaries (ADR-031 D6, D8).

    \b
    OLD and NEW may each be a `graph/source_graph_summary.json` file or an
    evidence-pack directory produced by `collect-evidence --source-graph summary`.

    The diff is structural — which nodes/edges entered or left the graph. Per
    ADR-028 D3 / ADR-031 D6 it *explains and prioritizes* impact; it never, on
    its own, decides or suppresses an artifact-proven ABI break.
    """
    import json as _json

    from .evidence.source_graph import diff_source_graph, diff_source_graph_findings

    old_graph = _load_source_graph(old)
    new_graph = _load_source_graph(new)
    delta = diff_source_graph(old_graph, new_graph)
    findings = diff_source_graph_findings(old_graph, new_graph)

    if fmt == "json":
        payload = delta.to_dict()
        payload["findings"] = [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "old_value": c.old_value,
                "new_value": c.new_value,
            }
            for c in findings
        ]
        click.echo(_json.dumps(payload, indent=2, sort_keys=True))
        return

    if not delta.changed:
        click.echo("Source graphs are structurally identical.")
        click.echo(f"  graph_id: {old_graph.graph_id or old_graph.compute_graph_id()}")
        return

    click.echo("Source graph structural diff:")
    click.echo(
        f"  nodes: +{len(delta.added_nodes)} / -{len(delta.removed_nodes)}    "
        f"edges: +{len(delta.added_edges)} / -{len(delta.removed_edges)}"
    )
    for node in delta.added_nodes:
        click.echo(f"  + node [{node.kind}] {node.label or node.id}")
    for node in delta.removed_nodes:
        click.echo(f"  - node [{node.kind}] {node.label or node.id}")
    for edge in delta.added_edges:
        click.echo(f"  + edge {edge.kind}: {edge.src} -> {edge.dst}")
    for edge in delta.removed_edges:
        click.echo(f"  - edge {edge.kind}: {edge.src} -> {edge.dst}")

    if findings:
        # Graph-derived RISK findings (ADR-031 D6): explanation/prioritization,
        # never a standalone ABI-break verdict (ADR-028 D3).
        click.echo(f"\nGraph-derived risk findings ({len(findings)}):")
        for c in findings:
            click.echo(f"  [{c.kind.value}] {c.symbol}: {c.description}")


@main.command("explain-finding")
@click.option("--evidence", "evidence", type=click.Path(path_type=Path), required=True,
              help="Evidence-pack directory (or a source_graph_summary.json) to explain through.")
@click.option("--symbol", "symbol", default="", help="Exported (mangled) binary symbol to localize.")
@click.option("--report", "report", type=click.Path(path_type=Path), default=None,
              help="A `compare --format json` report; with --finding-id, resolves the symbol from it.")
@click.option("--finding-id", "finding_id", default="",
              help="Index (or symbol) of a finding in --report to localize.")
@click.option("--format", "fmt", default="text", show_default=True,
              type=click.Choice(["text", "json"], case_sensitive=False))
def explain_finding_cmd(
    evidence: Path, symbol: str, report: Path | None, finding_id: str, fmt: str,
) -> None:
    """Localize a finding through L5 source-graph evidence (ADR-031 D8).

    Given an exported symbol (directly via --symbol, or resolved from a
    `--report` finding via --finding-id), walks the graph to show what produced
    and reaches it: exporting target, source declaration(s), declaring public
    header(s), ABI-relevant build option(s), and static callees. This explains
    and prioritizes; it is never an ABI verdict (ADR-031 D6).
    """
    import json as _json

    from .evidence.source_graph import localize_symbol

    graph = _load_source_graph(evidence)
    if not symbol and report is not None:
        symbol = _resolve_symbol_from_report(report, finding_id)
    if not symbol:
        raise click.ClickException(
            "No symbol to explain: pass --symbol, or --report with --finding-id."
        )

    result = localize_symbol(graph, symbol)
    if fmt == "json":
        click.echo(_json.dumps(result, indent=2, sort_keys=True))
        return

    click.echo(f"Explaining symbol: {symbol}")
    if not result["found"]:
        click.echo("  (symbol not present in the source graph — no localization available)")
    rows = [
        ("exported by target(s)", result["exported_by_targets"]),
        ("source declaration(s)", result["source_declarations"]),
        ("declared in header(s)", result["declared_in_headers"]),
        ("reached by build option(s)", result["reached_by_build_options"]),
        ("static callee(s)", result["static_callees"]),
    ]
    for label, values in rows:
        click.echo(f"  {label}: {', '.join(values) if values else '(none in graph)'}")


def _resolve_symbol_from_report(report: Path, finding_id: str) -> str:
    """Resolve a symbol from a `compare --format json` report finding.

    ``finding_id`` may be a 0-based index into the report's changes, or a symbol
    substring to match. Returns the matched change's ``symbol`` (or "").
    """
    import json as _json

    try:
        data = _json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read report {report}: {exc}") from exc
    changes = data.get("changes") or data.get("findings") or []
    if not isinstance(changes, list):
        return ""
    if finding_id.isdigit():
        idx = int(finding_id)
        if 0 <= idx < len(changes) and isinstance(changes[idx], dict):
            return str(changes[idx].get("symbol", ""))
        return ""
    for change in changes:
        if isinstance(change, dict) and finding_id and finding_id in str(change.get("symbol", "")):
            return str(change.get("symbol", ""))
    return ""
