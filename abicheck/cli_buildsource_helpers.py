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

"""Plain helper functions extracted from ``cli_buildsource``.

These cover the ``merge`` sub-command as well as the ``compare`` build-source
integration (embedded-evidence diffing, layer-coverage reporting, capability
reporting) and the source-graph load/localize helpers. They were extracted from
``cli_buildsource.py`` to keep that module under the 2000-line hard cap. They
must NOT import from ``abicheck.cli_buildsource`` or ``abicheck.cli`` (that would
create an import cycle rejected by the CI gate) — this is a leaf module.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .buildsource import evidence_report as _evidence_report
from .buildsource.build_evidence import l3_coverage_fields
from .buildsource.model import (
    CoverageStatus,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .buildsource.pack import BuildSourcePack
from .cli_buildsource_merge import (
    _exported_symbols_from_snapshot as _exported_symbols_from_snapshot,
    _ingest_inputs_pack_snapshot as _ingest_inputs_pack_snapshot,
    _merge_attach_combined as _merge_attach_combined,
    _merge_fold_packs as _merge_fold_packs,
    _merge_handle_conflicts as _merge_handle_conflicts,
    _merge_load_snapshots as _merge_load_snapshots,
    _merge_pick_base as _merge_pick_base,
    _merge_print_summary as _merge_print_summary,
)
from .errors import SnapshotError
from .workflows.extraction import DEFAULT_REDACTION, pack_content_hash

if TYPE_CHECKING:
    from .buildsource.build_evidence import BuildEvidence
    from .buildsource.source_abi import SourceAbiSurface
    from .checker_types import Change, DiffResult
    from .model import AbiSnapshot
    from .model.source_graph import SourceGraphSummary
    from .policy_file import PolicyFile


# ADR-061 Phase 3: these moved to `buildsource.evidence_report`, which is now
# their single definition. The leading-underscore spellings survive as aliases
# because they are the names `cli_buildsource` re-exports and several tests
# import; a second copy here is exactly what the move existed to remove.
_CHECK_CAPABILITIES = _evidence_report.CHECK_CAPABILITIES
_LAYER_NAMES = _evidence_report.LAYER_NAMES
_detect_coverage_asymmetry = _evidence_report.detect_coverage_asymmetry
_intrinsic_coverage = _evidence_report.intrinsic_coverage
_layer_presence = _evidence_report.layer_presence
_optional_coverage = _evidence_report.optional_coverage


def _echo(message: str) -> None:
    """The CLI's sink for `buildsource.evidence_report`'s report lines.

    stderr, deliberately: the D7 coverage/capability report must cover every
    output format without polluting a ``--format json`` stdout that a consumer
    pipes into a parser.
    """
    click.echo(message, err=True)


def _resolve_side_pack(
    build_info: Path | None,
    sources: Path | None,
    snap: AbiSnapshot | None,
) -> BuildSourcePack | None:
    """CLI adapter over ``buildsource.evidence_report.resolve_side_pack``.

    Translates the engine's ``SnapshotError`` into a plain ``ClickException``
    (**exit 1** -- operational, not a usage error: the command line was
    well-formed and the pack was not), the same translation
    :func:`_load_pack_or_raise` makes for the single-pack case. Message
    unchanged. Pinned by ``tests/test_evidence_report_contract.py``.

    Also supplies the stderr sink for a Flow-2 pack's non-fatal validation
    findings, which the engine returns through a callback rather than
    printing itself.
    """
    from .buildsource.evidence_report import resolve_side_pack

    try:
        return resolve_side_pack(build_info, sources, snap, on_warning=_echo)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def diff_embedded_build_source(
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    collect_mode: str,
    new_snapshot: AbiSnapshot,
    old_snapshot: AbiSnapshot | None = None,
    policy_file: PolicyFile | None = None,
    *,
    quiet: bool = False,
) -> tuple[list[Change], list[dict[str, object]], dict[str, object]]:
    """CLI adapter over ``buildsource.evidence_report.diff_embedded_build_source``.

    Supplies the stderr sink the engine deliberately does not own, and keeps the
    ``quiet`` keyword for the CLI callers that already pass it (``quiet=True``
    simply supplies no sink). See that function for the behaviour.
    """
    from .buildsource.evidence_report import diff_embedded_build_source as _diff

    try:
        return _diff(
            old_build_info,
            new_build_info,
            old_sources,
            new_sources,
            collect_mode,
            new_snapshot,
            old_snapshot,
            policy_file,
            on_output=None if quiet else _echo,
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def prepare_embedded_build_source(
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    collect_mode: str,
    extra_changes: list[Change] | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    policy_file: PolicyFile | None = None,
    *,
    quiet: bool = False,
) -> tuple[
    list[Change] | None, list[dict[str, object]], dict[str, object], list[Change]
]:
    """CLI adapter over ``buildsource.evidence_report.prepare_embedded_build_source``.

    Same stderr sink and same ``SnapshotError`` -> ``ClickException`` (exit 1)
    translation as :func:`diff_embedded_build_source` above.
    """
    from .buildsource.evidence_report import prepare_embedded_build_source as _prepare

    try:
        return _prepare(
            old_snapshot,
            new_snapshot,
            collect_mode,
            extra_changes,
            old_build_info,
            new_build_info,
            old_sources,
            new_sources,
            policy_file,
            on_output=None if quiet else _echo,
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def attach_evidence_metrics(
    result: DiffResult,
    metrics: dict[str, object],
    injected_changes: list[Change],
    *,
    quiet: bool = False,
) -> None:
    """CLI adapter over ``buildsource.evidence_report.attach_evidence_metrics``."""
    from .buildsource.evidence_report import attach_evidence_metrics as _attach

    _attach(
        result, metrics, injected_changes, on_output=None if quiet else _echo
    )


def echo_evidence_metrics(metrics: dict[str, object]) -> None:
    """Print the ADR-033 D6/D9 metrics summary to stderr.

    The engine renders the lines (``evidence_policy.evidence_metrics_lines``);
    this is the CLI's stream. Kept as a named function because it is the CLI's
    own spelling of that report and is exercised directly by
    ``tests/test_build_source_cli.py``.
    """
    from .buildsource.evidence_policy import evidence_metrics_lines

    for line in evidence_metrics_lines(metrics):
        _echo(line)



def _load_pack_or_raise(evidence_dir: Path) -> BuildSourcePack:
    """CLI adapter over ``buildsource.pack_load.load_pack_or_raise``.

    Translates the engine's ``SnapshotError`` into a plain ``ClickException``
    (**exit 1** -- operational, not a usage error: the command line was
    well-formed and the pack was not). Message unchanged.
    """
    from .workflows.extraction import load_pack_or_raise

    try:
        return load_pack_or_raise(evidence_dir)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _is_inputs_pack_dir(path: Path | None) -> bool:
    """Alias for ``buildsource.inputs_pack.is_inputs_pack_dir`` (ADR-035 D5),
    which has owned it since ADR-061 Phase 3."""
    from .workflows.extraction import is_inputs_pack_dir

    return is_inputs_pack_dir(path)


def _load_inputs_pack_or_raise(
    path: Path, *, exported_symbols: Iterable[str] = ()
) -> BuildSourcePack:
    """CLI adapter over ``buildsource.pack_load.load_inputs_pack_or_raise``.

    Same exit-1 translation as :func:`_load_pack_or_raise`, plus the stderr
    sink for the loader's non-fatal findings -- the engine returns those
    through a callback rather than owning a stream.
    """
    from .workflows.extraction import load_inputs_pack_or_raise

    try:
        return load_inputs_pack_or_raise(
            path,
            exported_symbols=exported_symbols,
            on_warning=lambda message: click.echo(message, err=True),
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_side_pack_input(
    path: Path | None, *, exported_symbols: Iterable[str] = ()
) -> BuildSourcePack | None:
    """Load a compare-side out-of-band pack, auto-detecting its pack kind."""
    if path is None:
        return None
    if _is_inputs_pack_dir(path):
        return _load_inputs_pack_or_raise(path, exported_symbols=exported_symbols)
    return _load_pack_or_raise(path)


def _echo_coverage(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> None:
    """Print the D7 evidence-coverage table to stderr (all output formats)."""
    from .buildsource.evidence_report import coverage_lines

    for line in coverage_lines(intrinsic, optional):
        _echo(line)


def _echo_compare_side_coverage(
    old_intrinsic: list[LayerCoverage],
    old_optional: list[LayerCoverage],
    new_intrinsic: list[LayerCoverage],
    new_optional: list[LayerCoverage],
) -> None:
    """Print old/new layer coverage so mixed-evidence compares are explicit."""
    from .buildsource.evidence_report import compare_side_coverage_lines

    for line in compare_side_coverage_lines(
        old_intrinsic, old_optional, new_intrinsic, new_optional
    ):
        _echo(line)


def _echo_capabilities(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> None:
    """Print which check categories are enabled -- and why others are not."""
    from .buildsource.evidence_report import capability_lines

    for line in capability_lines(intrinsic, optional):
        _echo(line)


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
        p02 = l3_coverage_fields(merged)  # P0.2 root-target-scoping fields
        detail = (
            f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
            f"{len(merged.targets)} targets" + p02.pop("detail_suffix")
        )
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.HIGH
            if merged.targets
            else LayerConfidence.REDUCED,
            detail=detail,
            **p02,
        )
    else:
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
        )
    # L4 is PRESENT when at least one TU parsed into the surface, PARTIAL when
    # replay ran but every TU failed/was empty (e.g. clang missing), else
    # NOT_COLLECTED. The surface keeps decls/types only when extraction worked.
    if surface is not None:
        # PRESENT when the surface actually carries reachable entities; PARTIAL
        # when replay ran but yielded nothing (tool missing, all TUs failed, or
        # no public surface matched) — never silently NOT_COLLECTED, so the
        # capability report can explain the gap.
        any_entities = bool(
            surface.reachable_declarations
            or surface.reachable_types
            or surface.reachable_macros
            or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        if any_entities:
            l4 = LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value,
                status=CoverageStatus.PRESENT,
                confidence=LayerConfidence.HIGH,
                detail=source_detail,
            )
        else:
            l4 = LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.REDUCED,
                detail=source_detail,
            )
    else:
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        )
    # L5 is PRESENT when the graph carries edges; PARTIAL when a graph was built
    # but had no build evidence to fold (so it is empty), else NOT_COLLECTED.
    if graph is not None:
        if graph.edges:
            l5 = LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value,
                status=CoverageStatus.PRESENT,
                confidence=LayerConfidence.REDUCED,
                detail=graph_detail,
            )
        else:
            l5 = LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.UNKNOWN,
                detail=graph_detail or "no build evidence to fold into a graph",
            )
    else:
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED
        )
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


def _collect_source_graph(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    source_graph: str,
    changed_paths: tuple[str, ...],
    kythe_entries: Path | None,
    codeql_results: Path | None,
    codeql_extends_results: Path | None,
    surface: SourceAbiSurface | None,
    clang_bin: str,
) -> tuple[SourceGraphSummary | None, str]:
    """Build the optional L5 source graph and fold in any requested augmentations.

    Kythe/CodeQL ingestion (pre-captured, non-executing) implies graph
    collection — their JSON is useless without a graph to fold into. Returns
    ``(graph, graph_detail)``; ``graph`` is ``None`` when no graph was
    requested.

    Call/type/include-graph edges are **not** separate opt-in flags here —
    they fold automatically whenever both ``--source-abi`` (L4) and
    ``--source-graph summary`` (L5) are active, mirroring exactly the inline
    ``dump --sources`` path's own automatic gate
    (``inline._build_inline_graph``'s ``with_call_graph``). The two paths
    used to diverge: this one required explicit ``--call-graph``/
    ``--include-graph`` flags with no inline-path equivalent, which read as
    dead CLI surface on the recommended path and a hidden, easy-to-miss
    requirement on this one. Sharing ``inline_graph_fold``'s fold functions
    (rather than this module's own now-removed near-duplicates) keeps the two
    paths from drifting again.
    """
    if (
        kythe_entries or codeql_results or codeql_extends_results
    ) and source_graph == "off":
        source_graph = "summary"
    if source_graph != "summary":
        return None, ""

    from .buildsource.source_graph import build_source_graph

    # Fold the L4 surface in too when it was collected (--source-abi), so the
    # graph carries the public-reachability + source↔binary slices.
    graph = build_source_graph(merged, source_abi=surface)
    if surface is not None:
        from .workflows.extraction import (
            fold_call_graph,
            fold_callback_graph,
            fold_include_graph,
            fold_macro_graph,
            fold_override_graph,
            fold_template_graph,
            fold_type_graph,
            fold_virtual_dispatch_graph,
        )

        fold_call_graph(graph, merged, clang_bin, extractors, changed_paths)
        fold_type_graph(graph, merged, clang_bin, extractors, changed_paths)
        fold_include_graph(graph, merged, clang_bin, extractors, changed_paths)
        # G29 Phase 5 item 1 (Codex review, fresh evidence): this out-of-band
        # `collect --source-abi --source-graph summary` path previously
        # folded only three of the four clang-backed passes `inline.
        # _build_inline_graph`'s own `with_call_graph` block runs together --
        # an otherwise-equivalent collected pack silently carried no template
        # nodes/edges/coverage stamp at all.
        fold_template_graph(graph, merged, clang_bin, extractors, changed_paths)
        # ADR-041 P2 item 1 / G29 Phase 5 items 2/3 (Codex review, fresh
        # evidence): this collect path had fallen behind
        # `inline_graph_fold.fold_semantic_graphs`'s own list a third time --
        # override/virtual-dispatch/macro folding were still missing here,
        # so an otherwise-equivalent collected pack silently carried no
        # METHOD_POSSIBLE_OVERRIDE/VIRTUAL_CALL_MAY_DISPATCH_TO/
        # TYPE_HAS_VTABLE/MACRO_CONTROLS_DECL/DECL_USES_MACRO edges (or
        # their coverage stamps) at all, regardless of what `--source-abi`
        # collected. `fold_override_graph` must run before
        # `fold_virtual_dispatch_graph` (the latter reads the former's
        # already-folded edges, per `fold_semantic_graphs`' own ordering
        # contract), and `fold_virtual_dispatch_graph` takes no
        # clang/scoping arguments of its own (see its docstring).
        fold_override_graph(graph, merged, clang_bin, extractors, changed_paths)
        fold_virtual_dispatch_graph(graph)
        fold_macro_graph(graph, merged, clang_bin, extractors, changed_paths)
        # G29 Phase 5 item 4: mirrors the same recurring gap this collect
        # path's own comments above already document (this out-of-band path
        # falling behind `inline_graph_fold.fold_semantic_graphs`'s own call
        # list) -- fold this in the same commit that adds
        # `fold_callback_graph` rather than a fourth follow-up fix.
        fold_callback_graph(graph, merged, clang_bin, extractors, changed_paths)
    # fold_archive_graph needs no clang/L4 surface (unlike the three passes
    # above) -- it runs unconditionally whenever the graph carries a
    # static_library node, mirroring inline._build_inline_graph's identical
    # unconditional call (Codex review, fresh evidence: this collect path
    # never called it at all, so a collected pack's static_library nodes
    # never got archive-member/symbol-definition edges or a coverage stamp,
    # regardless of whether --source-abi was given).
    from .workflows.extraction import fold_archive_graph

    fold_archive_graph(graph, merged, extractors)
    if kythe_entries or codeql_results or codeql_extends_results:
        _ingest_graph_backends(
            graph,
            extractors,
            kythe_entries=kythe_entries,
            codeql_results=codeql_results,
            codeql_extends_results=codeql_extends_results,
        )
    graph.finalize()
    graph_detail = (
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges "
        f"({graph.coverage.get('targets', 0)} targets, "
        f"{graph.coverage.get('compile_units', 0)} compile units, "
        f"{graph.coverage.get('source_decls', 0)} source decls, "
        f"{graph.coverage.get('call_edges', {}).get('count', 0)} call edges, "
        f"{graph.coverage.get('include_edges', {}).get('count', 0)} include edges)"
    )
    extractors.append(
        ExtractorRecord(
            name="source_graph:summary",
            status="ok" if graph.nodes else "partial",
            detail=graph_detail
            if graph.nodes
            else "no build evidence to fold into a graph",
        )
    )
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
    pack: BuildSourcePack,
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
    click.echo(f"  content hash: {pack_content_hash(pack)}")
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


#: ``collect --from`` adapter specs (ADR-037 CLI consolidation). The six former
#: per-adapter flags (``--cmake``/``--ninja`` live toggles + ``--ninja-compdb``/
#: ``--bazel-cquery``/``--bazel-aquery``/``--make-dry-run`` pre-captured paths)
#: collapse onto one repeatable ``--from adapter[=path]``. Live adapters take no
#: ``=path`` (they read ``--build-dir``); pre-captured ones require one.
_FROM_LIVE_ADAPTERS: frozenset[str] = frozenset({"cmake", "ninja"})
#: pre-captured adapter name → the ``_run_adapters`` kwarg it feeds.
_FROM_PATH_ADAPTERS: dict[str, str] = {
    "ninja-compdb": "ninja_compdb",
    "bazel-cquery": "bazel_cquery",
    "bazel-aquery": "bazel_aquery",
    "make": "make_dry_run",
}


def parse_from_specs(specs: tuple[str, ...]) -> dict[str, object]:
    """Parse ``collect --from adapter[=path]`` specs into ``_run_adapters`` kwargs.

    Returns a dict with ``cmake``/``ninja`` bools and ``ninja_compdb``/
    ``bazel_cquery``/``bazel_aquery``/``make_dry_run`` paths (None when unset).
    Raises :class:`click.UsageError` on an unknown adapter, a live adapter given
    a ``=path``, a pre-captured adapter given no path, or the same adapter passed
    twice (so a repeated ``--from`` never silently last-wins). Pure (no I/O) so it
    is unit-tested directly.
    """
    out: dict[str, object] = {
        "cmake": False,
        "ninja": False,
        "ninja_compdb": None,
        "bazel_cquery": None,
        "bazel_aquery": None,
        "make_dry_run": None,
    }
    valid = sorted(_FROM_LIVE_ADAPTERS | set(_FROM_PATH_ADAPTERS))
    seen: set[str] = set()
    for spec in specs:
        name, sep, value = spec.partition("=")
        name = name.strip()
        if name in seen:
            raise click.UsageError(
                f"--from {name} was given more than once; pass each adapter "
                "at most once."
            )
        if name in _FROM_LIVE_ADAPTERS:
            if sep:
                raise click.UsageError(
                    f"--from {name} is a live adapter and takes no '=path' "
                    "(it reads --build-dir)."
                )
            out[name] = True
        elif name in _FROM_PATH_ADAPTERS:
            if not value:
                raise click.UsageError(
                    f"--from {name} requires a pre-captured path "
                    f"(e.g. --from {name}=path)."
                )
            out[_FROM_PATH_ADAPTERS[name]] = Path(value)
        else:
            raise click.UsageError(
                f"--from: unknown adapter {name!r}; expected one of {valid}."
            )
        seen.add(name)
    return out


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
    record_bazel_inputs: bool,
    verbose: bool,
) -> None:
    """Run the requested build-evidence adapters and fold them into *merged*."""
    # Import adapters lazily so `collect --help` stays cheap.
    from .buildsource.adapters import (
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
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="ok",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=f"{len(ev.compile_units)} compile units",
                )
            )
        except (OSError, ValueError) as exc:
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=str(exc),
                )
            )
            merged.diagnostics.append(f"compile_commands: {exc}")

    if cmake:
        if build_dir is None:
            raise click.UsageError("--cmake requires --build-dir.")
        ev = CMakeFileApiAdapter(build_dir).collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="cmake_file_api",
                status="ok" if ev.targets else "partial",
                inputs=[DEFAULT_REDACTION.path(str(build_dir))],
                detail=f"{len(ev.targets)} targets, {len(ev.toolchains)} toolchains",
            )
        )

    if ninja or ninja_compdb is not None:
        if build_dir is None and ninja_compdb is None:
            raise click.UsageError(
                "--ninja requires --build-dir (or pass --ninja-compdb)."
            )
        adapter = NinjaAdapter(build_dir, compdb=ninja_compdb)
        ev = adapter.collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="ninja",
                status="ok" if ev.compile_units else "partial",
                inputs=[DEFAULT_REDACTION.path(str(build_dir or ninja_compdb))],
                detail=f"{len(ev.compile_units)} compile units",
            )
        )

    if bazel_cquery is not None or bazel_aquery is not None:
        ev = BazelAdapter(
            workspace=build_dir,
            cquery=bazel_cquery,
            aquery=bazel_aquery,
            record_inputs=record_bazel_inputs,
        ).collect()
        merged.merge(ev)
        inputs = [
            DEFAULT_REDACTION.path(str(p))
            for p in (bazel_cquery, bazel_aquery)
            if p is not None
        ]
        extractors.append(
            ExtractorRecord(
                name="bazel",
                status="ok"
                if (ev.targets or ev.compile_units or ev.link_units)
                else "partial",
                inputs=inputs,
                detail=(
                    f"{len(ev.targets)} targets, {len(ev.compile_units)} compile units, "
                    f"{len(ev.link_units)} link units"
                ),
            )
        )

    if make_dry_run is not None:
        # Only a pre-captured transcript — the Make adapter never runs make,
        # because `make -n` still executes `+` recipes and `$(shell …)`.
        ev = MakeAdapter(build_dir, dry_run=make_dry_run).collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="make",
                status="ok" if ev.compile_units else "partial",
                inputs=[DEFAULT_REDACTION.path(str(make_dry_run))],
                detail=f"{len(ev.compile_units)} compile units (reduced confidence)",
            )
        )

    if read_compiler_record:
        if binary is None:
            raise click.UsageError("--read-compiler-record requires --binary.")
        from .workflows.extraction import extract_compiler_record

        ev = extract_compiler_record(binary)
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="compiler_record",
                status="ok" if (ev.toolchains or ev.compile_units) else "partial",
                inputs=[DEFAULT_REDACTION.path(str(binary))],
                detail=f"{len(ev.toolchains)} toolchains, {len(ev.compile_units)} compile units",
            )
        )


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
    from .buildsource.build_evidence import BuildEvidence as _BuildEvidence
    from .workflows.extraction import (
        CollectionAction,
        CollectionContext,
        CollectionMode,
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
            extractors.append(
                ExtractorRecord(
                    name=f"external:{manifest_path.name}",
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(manifest_path))],
                    detail=str(exc),
                )
            )
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

        # Reject output kinds collect cannot fold yet — only
        # build_evidence is wired into the pack here. A manifest that advertises
        # a source_abi / source_graph_summary output would otherwise be recorded
        # ok while its evidence is silently dropped (and pack_io.write() removes the
        # canonical source/graph files), so the requested evidence is absent even
        # though the extractor "succeeded" (Codex P2). Fail loudly instead.
        unsupported = sorted(
            {o.kind for o in manifest.outputs if o.kind != "build_evidence"}
        )
        if unsupported:
            record.status = "failed"
            record.detail = (
                record.detail or f"unsupported output kind(s): {', '.join(unsupported)}"
            )
            # The outputs are about to be purged from the pack, so the ledger row
            # must not keep advertising their (now-removed) paths (Codex P2).
            record.artifacts = []
            merged.diagnostics.append(
                f"{manifest.name}: output kind(s) {', '.join(unsupported)} are not yet "
                "supported by collect (only build_evidence is folded into the pack)"
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
                parsed.append(
                    _BuildEvidence.from_dict(
                        _json.loads(be_path.read_text(encoding="utf-8"))
                    )
                )
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
    otherwise be hashed into ``BuildSourcePack`` ``manifest.artifacts`` and the
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


def _ingest_graph_backends(
    graph: SourceGraphSummary,
    extractors: list[ExtractorRecord],
    *,
    kythe_entries: Path | None,
    codeql_results: Path | None,
    codeql_extends_results: Path | None,
) -> None:
    """Fold pre-captured Kythe/CodeQL exports into *graph* (ADR-031 D5).

    Non-executing (ADR-028 D6): reads the provided JSON exports only. A malformed
    or missing file records a failed extractor row and is skipped.
    """
    import json as _json

    from .workflows.extraction import (
        ingest_codeql_call_results,
        ingest_codeql_extends_results,
        ingest_kythe_entries,
    )

    def _load(path: Path, name: str) -> object | None:
        try:
            parsed: object = _json.loads(Path(path).read_text(encoding="utf-8"))
            return parsed
        except (OSError, ValueError) as exc:
            extractors.append(
                ExtractorRecord(
                    name=name,
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(path))],
                    detail=str(exc),
                )
            )
            return None

    if kythe_entries is not None:
        data = _load(kythe_entries, "graph_backend:kythe")
        if data is not None:
            entries = (
                data
                if isinstance(data, list)
                else (data.get("entries", []) if isinstance(data, dict) else [])
            )
            added = ingest_kythe_entries(
                graph, entries, ref=DEFAULT_REDACTION.path(str(kythe_entries))
            )
            extractors.append(
                ExtractorRecord(
                    name="graph_backend:kythe",
                    status="ok" if added else "partial",
                    inputs=[DEFAULT_REDACTION.path(str(kythe_entries))],
                    detail=f"{added} edges ingested",
                )
            )

    if codeql_results is not None:
        data = _load(codeql_results, "graph_backend:codeql")
        if isinstance(data, dict):
            added = ingest_codeql_call_results(
                graph, data, ref=DEFAULT_REDACTION.path(str(codeql_results))
            )
            extractors.append(
                ExtractorRecord(
                    name="graph_backend:codeql",
                    status="ok" if added else "partial",
                    inputs=[DEFAULT_REDACTION.path(str(codeql_results))],
                    detail=f"{added} edges ingested",
                )
            )

    if codeql_extends_results is not None:
        data = _load(codeql_extends_results, "graph_backend:codeql_extends")
        if data is not None:
            if isinstance(data, dict):
                added = ingest_codeql_extends_results(
                    graph, data, ref=DEFAULT_REDACTION.path(str(codeql_extends_results))
                )
                extractors.append(
                    ExtractorRecord(
                        name="graph_backend:codeql_extends",
                        status="ok" if added else "partial",
                        inputs=[DEFAULT_REDACTION.path(str(codeql_extends_results))],
                        detail=f"{added} edges ingested",
                    )
                )
            else:
                # Codex review: valid JSON that isn't an object (e.g. a bare
                # array) used to leave no record at all, silently hiding that
                # the requested backend was never ingested.
                extractors.append(
                    ExtractorRecord(
                        name="graph_backend:codeql_extends",
                        status="failed",
                        inputs=[DEFAULT_REDACTION.path(str(codeql_extends_results))],
                        detail='expected a JSON object with a top-level "#select"',
                    )
                )
