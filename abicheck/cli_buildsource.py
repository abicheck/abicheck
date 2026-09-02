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

"""Build-source pack attach/embed integration (ADR-028 D6, ADR-029).

The standalone `collect`/`merge` commands were removed in the ADR-043 CLI
reset; this module now only holds `embed_build_source()`/`dump_source_only()`
(the inline collection `dump --sources`/`--build-info` drives) plus the
back-compat re-exports for the library functions that survived the command
deletion. Per ADR-028 D6 nothing here runs arbitrary build commands: it only
reads existing build outputs and build-system query interfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.model import DataLayer
from .buildsource.pack import BuildSourcePack
from .cli_buildsource_helpers import (  # noqa: F401  (re-exported for API stability / tests)
    _build_coverage as _build_coverage,
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
    _is_inputs_pack_dir as _is_inputs_pack_dir,
    _layer_presence as _layer_presence,
    _load_inputs_pack_or_raise as _load_inputs_pack_or_raise,
    _load_pack_or_raise as _load_pack_or_raise,
    _merge_attach_combined as _merge_attach_combined,
    _merge_fold_packs as _merge_fold_packs,
    _merge_handle_conflicts as _merge_handle_conflicts,
    _merge_load_snapshots as _merge_load_snapshots,
    _merge_pick_base as _merge_pick_base,
    _merge_print_summary as _merge_print_summary,
    _optional_coverage as _optional_coverage,
    _resolve_side_pack as _resolve_side_pack,
    _run_adapters as _run_adapters,
    _run_external_extractors as _run_external_extractors,
    attach_evidence_metrics as attach_evidence_metrics,
    diff_embedded_build_source as diff_embedded_build_source,
    parse_from_specs as parse_from_specs,
    prepare_embedded_build_source as prepare_embedded_build_source,
    purge_external_outputs as purge_external_outputs,
)
from .errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from .api_types import DumpRequest
    from .model import AbiSnapshot
    from .service_dump_pipeline import ResolvedDumpRequest


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
    build_targets: tuple[str, ...] = (),
    changed_paths: tuple[str, ...] = (),
    extractor: str = "auto",
    public_headers: tuple[str, ...] = (),
    public_header_dirs: tuple[str, ...] = (),
    defer_cleanup: list[Callable[[], None]] | None = None,
    quiet: bool = False,
) -> None:
    """CLI adapter over :func:`abicheck.buildsource.embed.embed_build_source`.

    The operation moved to the engine in ADR-061 Phase 3; this keeps the CLI's
    two obligations, both of which the engine must not own:

    * **Exit codes.** ``ValidationError`` (a malformed ``.abicheck.yml``) is a
      usage error -> ``click.UsageError``, which ``cli.main`` remaps to 64.
      ``SnapshotError`` (an invalid pack) is operational -> a plain
      ``ClickException``, exit 1. Collapsing the two would tell a CI consumer
      the invocation was wrong when the data was.
    * **The stream.** ``quiet`` is preserved as this layer's spelling; it
      simply decides whether a stderr writer is handed to the engine.
    """
    from .workflows.extraction import embed_build_source as _embed

    try:
        _embed(
            snap,
            build_info,
            sources,
            build_config=build_config,
            allow_build_query=allow_build_query,
            clang_bin=clang_bin,
            collect_mode=collect_mode,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=build_targets,
            changed_paths=changed_paths,
            extractor=extractor,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            defer_cleanup=defer_cleanup,
            on_warning=None
            if quiet
            else (lambda message: click.echo(message, err=True)),
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


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
    build_targets: tuple[str, ...] = (),
    extractor: str = "auto",
    depth: str | None = None,
    include_dependencies: bool = False,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    snapshot_compression: str = "auto",
    project_snapshot_dir: Path | None = None,
) -> None:
    """Write a binary-less snapshot carrying only the embedded build/source facts.

    The parallel-baseline flow: ``dump --sources <tree>`` / ``--build-info <path>``
    with no ``SO_PATH`` collects L3/L4/L5 inline and embeds them in an otherwise
    empty snapshot, to be combined with an artifact-side dump via ``merge``. A
    bare ``dump`` (no binary and no source/build inputs) errors clearly here.

    ``include_dependencies`` is forwarded to ``_write_snapshot_output`` for
    consistency, though it never has any effect here in practice: this
    path's snapshot starts with no functions/variables at all (only L3/L4/L5
    facts get embedded), and ``scope_snapshot_excluding_dependencies`` is a
    no-op on a snapshot with no header-derived declarations
    (``from_headers`` stays ``False``) either way.

    ``gcc_path``/``gcc_prefix`` are the dump's own ``--compiler``/
    ``--compiler-prefix`` (there is no header AST here to have already resolved a
    ``CompileContext`` from — a source-only dump has no ``-H`` headers
    either), forwarded to ``_write_snapshot_output`` so L4 source-ABI replay
    honors the same compiler override a binary dump would.
    """
    from .cli import _stamp_provenance
    from .model import AbiSnapshot
    from .workflows.extraction import resolve_source_frontend_clang_bin

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
        build_targets=build_targets,
        extractor=extractor,
        depth=depth,
        include_dependencies=include_dependencies,
        clang_bin=resolve_source_frontend_clang_bin(
            gcc_path, gcc_prefix, exclude_cl_style=False
        ),
        snapshot_compression=snapshot_compression,
        project_snapshot_dir=project_snapshot_dir,
    )


# ---------------------------------------------------------------------------
# Snapshot output (dump's write path)
#
# Relocated here from ``cli.py`` (which sits within a handful of lines of the
# AI-readiness 2000-line hard cap) as one cohesive cluster: writing a snapshot
# *is* the step that folds this module's own ``embed_build_source`` /
# ``embed_inputs_pack`` payloads in and then enforces the requested evidence
# depth, and ``embed_build_source``'s caller here already imported
# ``_write_snapshot_output`` back out of ``cli``. ``cli`` re-exports all three
# names, so ``abicheck.cli._write_snapshot_output`` -- which several tests and
# ``dump_cmd`` itself use -- keeps resolving unchanged. This module, not a new
# one: a *new* module reaching ``service``/``buildsource`` would join the
# allowlisted CLI import-cycle SCC, which CLAUDE.md "M1-3" forbids extending.
# ---------------------------------------------------------------------------


def _layer_payload_empty(pack: BuildSourcePack, key: str) -> bool:
    """True when *key*'s embedded payload carries no facts.

    A coverage row can read ``PARTIAL``/``PRESENT`` while the payload is empty —
    e.g. ``_run_inline_source_abi`` returns an empty ``SourceAbiSurface()`` when
    clang is unavailable after L3 was found. The status alone then hides the
    miss, so we inspect the actual payload (Codex review, PR #422).
    """
    if key == "L3":
        be = pack.build_evidence
        return be is None or (not be.targets and not be.compile_units)
    if key == "L4":
        sa = pack.source_abi
        return sa is None or not any(sa.reachable_buckets().values())
    if key == "L5":
        sg = pack.source_graph
        return sg is None or not sg.nodes
    return False


def _missing_requested_evidence_layers(
    pack: BuildSourcePack | None, collect_mode: str
) -> list[str]:
    """Layers the *collect_mode* asked for but that came back empty.

    Maps the ADR-033 evidence mode to its expected L3/L4/L5 layers and checks the
    embedded pack. A layer is reported missing when its coverage row is
    ``NOT_COLLECTED`` (or absent) **or** when its embedded payload carries no
    facts despite a ``PARTIAL``/``PRESENT`` status — the latter catches a
    requested extractor that ran but produced nothing (e.g. clang unavailable).
    Returns [] when nothing was requested or every requested layer has facts.
    """
    if pack is None:
        return []
    from .buildsource.model import CoverageStatus
    from .workflows.extraction import collection_for_ci_mode

    _layer_for = {
        "L3": DataLayer.L3_BUILD,
        "L4": DataLayer.L4_SOURCE_ABI,
        "L5": DataLayer.L5_SOURCE_GRAPH,
    }
    _, layers = collection_for_ci_mode(collect_mode)
    missing: list[str] = []
    for key in layers:
        layer = _layer_for.get(key)
        if layer is None:
            continue
        cov = pack.manifest.coverage_for(layer)
        if (
            cov is None
            or cov.status == CoverageStatus.NOT_COLLECTED
            or _layer_payload_empty(pack, key)
        ):
            missing.append(layer.value)
    return missing


def build_source_already_satisfies(snap: AbiSnapshot, collect_mode: str) -> bool:
    """True when *snap* already carries every layer *collect_mode* asks for.

    The idempotence predicate behind :func:`_write_snapshot_output`'s
    check-before-embed guard (CLI cleanup phase two, PR 3A blocker 5,
    sub-issue 3). The ``dump`` CLI embeds L3-L5 at *write* time, while the
    typed pipeline (``service_dump_pipeline.execute_dump_request`` →
    ``service_input_resolution._resolve_side_snapshot_impl``) embeds at
    *resolve* time — so any future migration that routes the real ``dump`` run
    through the typed executor would otherwise embed twice, re-running L4
    source-ABI replay (a real compiler invocation per translation unit, not a
    cheap recomputation) and overwriting the pack the first embed produced.

    Stated in this module's own existing vocabulary rather than a second depth
    ladder: :func:`_missing_requested_evidence_layers` already answers "which
    layers did *collect_mode* ask for that this pack does not have", and it is
    the same function the G21.7 fail-loud warning below already trusts, so the
    guard and the warning cannot disagree about what "satisfied" means. Its
    ``pack is None -> []`` case is *not* satisfaction (nothing was embedded at
    all), hence the explicit ``build_source is not None`` half — without it a
    bare snapshot would read as already-complete and skip the embed entirely.

    ``collect_mode="off"`` with a pack present is deliberately treated as
    satisfied: nothing was requested, so re-running the embed could only
    replace an existing pack with a weaker one.

    A no-op for every caller that exists today — nothing in the ``dump`` CLI
    populates ``build_source`` before the write step — which is exactly what
    makes it safe to land ahead of the migration it exists for.
    """
    if snap.build_source is None:
        return False
    return not _missing_requested_evidence_layers(snap.build_source, collect_mode)


def _classify_missing_layers(
    pack: BuildSourcePack | None, missing: list[str]
) -> tuple[list[str], list[str]]:
    """Split *missing* layer values into (absent, ran_but_empty).

    ``absent`` — the layer never ran (no coverage row, or NOT_COLLECTED): the
    actionable fix is a compile DB / an installed frontend. ``ran_but_empty`` —
    a coverage row exists (PARTIAL/PRESENT) but the payload linked no facts: the
    fix is scoping/roots, not installing tools. Distinguishing the two stops the
    warning from telling users to install clang/castxml when those already ran.
    With no pack (or an unknown layer), default to ``absent`` so the legacy
    "not collected" wording still appears.
    """
    if pack is None:
        return list(missing), []
    from .buildsource.model import CoverageStatus

    by_value = {layer.value: layer for layer in DataLayer}
    absent: list[str] = []
    ran_empty: list[str] = []
    for value in missing:
        layer = by_value.get(value)
        cov = pack.manifest.coverage_for(layer) if layer is not None else None
        if cov is not None and cov.status != CoverageStatus.NOT_COLLECTED:
            ran_empty.append(value)
        else:
            absent.append(value)
    return absent, ran_empty


def _write_snapshot_output(
    snap: AbiSnapshot,
    output: Path | None,
    build_info: Path | None = None,
    sources: Path | None = None,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    build_targets: tuple[str, ...] = (),
    extractor: str = "auto",
    inputs_pack: Path | None = None,
    depth: str | None = None,
    include_dependencies: bool = False,
    header_roots: tuple[Path, ...] = (),
    clang_bin: str = "clang",
    snapshot_compression: str = "auto",
    public_headers: tuple[Path, ...] = (),
    public_header_dirs: tuple[Path, ...] = (),
    project_snapshot_dir: Path | None = None,
) -> None:
    """Serialize snapshot and write to file or stdout.

    *project_snapshot_dir*, when given, additionally writes this dump as a
    real, directory-backed `ProjectSnapshot` package there (ADR-062/
    ADR-063 Phase 8's storage-v2 wiring) -- alongside, never instead of,
    whatever `-o`/`--output`/stdout write this function already performs;
    every existing invocation that omits it is completely unaffected. See
    `project_snapshot_store.write_legacy_snapshot_package`.

    When *build_info* and/or *sources* are given, their normalized L3/L4/L5 facts
    are collected (inline from a source tree / build dir, or loaded from a pack
    directory) and embedded in the snapshot first (single-artifact UX) so a later
    ``compare old.json new.json`` needs no out-of-band packs. *collect_mode* (the
    ADR-033 D2 CI evidence mode) selects which layers and replay scope to collect:
    ``build`` captures L3 build context only, ``off`` collects nothing.
    *build_query* / *build_compile_db* / *build_targets* are the CLI equivalents of
    the ``.abicheck.yml`` ``build.query`` / ``build.compile_db`` / ``build.targets``
    keys — *build_targets* (P0.2) scopes Bazel evidence collection to the given
    root target(s) and their transitive deps instead of a workspace-wide query.
    *extractor* is the L4 source-ABI
    frontend — the same ``--ast-frontend`` knob that drives the L2 header AST
    (ADR-037 D8): one frontend choice across both pipeline stages. *clang_bin* is
    the caller-resolved L4 replay compiler (forwarded to ``embed_build_source``).
    *depth* is the raw ``--depth`` CLI value (``None`` when not passed); when given,
    ``check_requested_depth_satisfied`` raises if the snapshot did not actually reach
    it. Unless *include_dependencies* is set (``dump --include-system-declarations``),
    toolchain/system-header declarations are excluded from the snapshot right before
    serialization by default, once every embed step above has had its chance to fill
    in the snapshot — see ``dumper_scoping.py`` for what "dependency" means here.
    *header_roots* is the actual ``-H``/``--header`` input the dump was invoked with,
    forwarded to ``scope_snapshot_excluding_dependencies`` so a header that IS one of
    those roots (or lives under one) is never treated as a dependency just because it
    happens to sit under a system prefix (e.g. an installed library dumped via its real
    ``/usr/include`` path).

    *public_headers*/*public_header_dirs* are this dump's own public-header
    roots, forwarded to ``embed_build_source`` as ``public_header_roots`` --
    what L4 source-ABI replay classifies a declaration's declaring header as
    public or private against. Without them the replay runs with an *empty*
    root set, so every declaration classifies private and the linked surface
    reaches nothing: measured on a real ``dump lib.so -H api.h --sources .
    --build-info db.json --depth source``, the written snapshot recorded ``0/2
    symbols matched``, ``reachable_declarations=0`` and ``fact_family_states:
    empty-confirmed`` where the identical inputs through ``compare``'s
    implicit-dump operand or the typed ``DumpRequest`` API record ``1/2``
    matched and a real ``source_decl_to_binary_symbol`` mapping. The layer was
    present and honestly reported "partial", but every L4-derived finding was
    silently inert for a ``dump``-produced baseline. Found while measuring
    whole-snapshot parity between this write path and ``execute_dump_request``
    for CLI cleanup phase two's PR 3A -- see the plan's own note.
    """
    from .cli_dump_helpers import (
        check_requested_depth_satisfied,
        fold_dump_provenance_into_dict,
        write_snapshot_and_report,
    )
    from .serialization import snapshot_to_dict

    if (build_info is not None or sources is not None) and not (
        # PR 3A blocker 5, sub-issue 3: check before embedding. See
        # `build_source_already_satisfies`' own docstring -- a snapshot that
        # already carries every layer this collect mode asks for must not have
        # L4 source-ABI replay run over it a second time.
        build_source_already_satisfies(snap, collect_mode)
    ):
        from .cli_buildsource import embed_build_source

        embed_build_source(
            snap,
            build_info,
            sources,
            build_config=build_config,
            allow_build_query=allow_build_query,
            collect_mode=collect_mode,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=build_targets,
            extractor=extractor,
            clang_bin=clang_bin,
            public_headers=tuple(str(p) for p in public_headers),
            public_header_dirs=tuple(str(p) for p in public_header_dirs),
        )
        # G21.7: fail loud — if a requested evidence layer came back empty, say so
        # prominently instead of leaving it buried in the coverage rows. Permissive
        # by design (a warning, not an error): --collection-mode strict on
        # `collect` remains the hard-fail path (ADR-028 D3).
        # Through the ``cli`` module (which re-exports both) so a monkeypatch on
        # ``abicheck.cli._missing_requested_evidence_layers`` /
        # ``._classify_missing_layers`` is still honoured after the relocation --
        # the same resolution the neighbouring ``cli._normalize_binary_input``
        # calls use, and what several existing tests patch.
        from . import cli as _cli

        missing = _cli._missing_requested_evidence_layers(
            snap.build_source, collect_mode
        )
        if missing:
            absent, ran_empty = _cli._classify_missing_layers(
                snap.build_source, missing
            )
            parts: list[str] = []
            if absent:
                # Genuinely absent: no extractor / no compile DB / layer never ran.
                parts.append(
                    f"not collected: {', '.join(absent)} — supply "
                    "--build-info (a compile_commands.json or build dir, e.g. from "
                    "`bear -- make`), or install the clang/castxml source frontend"
                )
            if ran_empty:
                # Ran but produced/linked nothing — do NOT tell the user to install
                # tools they already have; point at the real cause in the coverage
                # rows (usually a public-header-roots or snapshot/source mismatch).
                parts.append(
                    f"collected but linked no facts: {', '.join(ran_empty)} — the "
                    "extractor ran but matched nothing; see the coverage rows for "
                    "the reason (commonly a public-header-roots mismatch, an "
                    "unseeded `--depth source` that selected 0 TUs — use "
                    "--changed-path/--since to seed a changed scope — or the "
                    "snapshot binary not matching --sources; a '0/N symbols "
                    "matched' means source decls did not link to the binary's "
                    "exports)"
                )
            click.echo(
                "Warning: requested evidence layer(s) " + "; ".join(parts) + ".",
                err=True,
            )
    # A build-emitted Flow-2 pack (--inputs) folds straight into the dump — the
    # plugin/wrapper flow in one command, no separate `merge` (after any inline
    # --sources/--build-info embed, so both fact sources combine).
    if inputs_pack is not None:
        from .cli_buildsource_merge import embed_inputs_pack

        embed_inputs_pack(snap, inputs_pack, output)
    # CLI-audit P1: an *explicitly* requested --depth that was not actually
    # reached is a hard failure, not a warning — see
    # check_requested_depth_satisfied's docstring. Checked last, after every
    # embed step above has had its chance to fill in build_source.
    check_requested_depth_satisfied(depth, snap)
    from .workflows.extraction import resolve_dependency_scope

    snap = resolve_dependency_scope(snap, include_dependencies, header_roots)
    # ADR-059: one payload dict, one JSON encode -- previously this built a
    # full JSON *string* via snapshot_to_json(), then fold_dump_provenance_
    # into_json() re-parsed and re-serialized that entire string just to
    # attach one key. For a 100+ MB snapshot that's a second full parse and
    # a second full encode for no reason; fold_dump_provenance_into_dict()
    # does the same augmentation on the already-decoded payload dict.
    payload = snapshot_to_dict(snap)
    payload, resolved_depth_label = fold_dump_provenance_into_dict(payload, depth, snap)
    if project_snapshot_dir is not None:
        _write_project_snapshot_package(payload, project_snapshot_dir, snap.library)
    if output:
        write_snapshot_and_report(
            payload, output, snapshot_compression, resolved_depth_label
        )
    else:
        import json

        click.echo(json.dumps(payload, indent=2))


def _write_project_snapshot_package(
    payload: dict[str, Any], project_snapshot_dir: Path, library: str
) -> None:
    """The `--project-snapshot-dir` half of `_write_snapshot_output` --
    split out purely to keep that function's own body from growing further
    (it already sits well within this module's architecture-gate budget,
    but a fourth, unrelated concern inline would not).

    *library* becomes the package's single `ArtifactRef.artifact_id` --
    matching `import_legacy_snapshot`'s own "one artifact per snapshot"
    A1.3 shape a real `dump` invocation always produces. `dump_source_only`'s
    own `library` field can be an empty string (`--sources .`, or any other
    source path whose `Path.name` is empty), and `ArtifactRef` rejects an
    empty `artifact_id` outright -- falls back to `"source"` in that case
    (the same fallback `dump_source_only` itself already uses for `library`
    when *no* `--sources`/`--build-info` hint exists at all) rather than
    letting `--project-snapshot-dir` turn a `--sources .` invocation that
    would otherwise succeed into a hard failure (Codex review). Any other
    `SnapshotError`/`OSError`/`ValueError` from the write (a bad path, a
    permission error, a payload this build's own current schema version
    cannot re-import -- should never happen for a payload this same build
    just produced, but checked exactly as strictly as the legacy `-o` write
    path already is) is translated into the identical `click.ClickException`
    contract `write_snapshot_and_report` already applies to the legacy write.
    """
    from .errors import SnapshotError
    from .serialization import SCHEMA_VERSION
    from .workflows.storage import write_legacy_snapshot_package

    try:
        write_legacy_snapshot_package(
            payload,
            project_snapshot_dir,
            artifact_id=library or "source",
            max_known_schema_version=SCHEMA_VERSION,
        )
    except (SnapshotError, OSError, ValueError) as exc:
        raise click.ClickException(
            f"Cannot write project snapshot package to {project_snapshot_dir}: {exc}"
        ) from exc
    click.echo(f"Project snapshot package written to {project_snapshot_dir}", err=True)


def resolve_dump_request_for_cli(request: DumpRequest) -> ResolvedDumpRequest:
    """:func:`~abicheck.service_dump_pipeline.resolve_dump_request`, with the
    CLI's error contract.

    The Tier-2 pipeline signals a bad request as
    :class:`~abicheck.errors.ValidationError` or, since ADR-063 Phase 4's
    pre-flight `AnalysisPlan` check, :class:`~abicheck.errors.PlanningError`;
    a Click front end owes the user a ``UsageError`` (exit 64) for either
    instead. Translated here, at the boundary, rather than inside the
    pipeline — the same Tier-1/Tier-2 separation ``embed_side_build_source``
    observes in the other direction.

    Worth being explicit about what this can newly reject on the ``--dry-run``
    path, since that path's own contract is "never raises on anything but a
    usage error": :meth:`DumpRequest.validate` front-runs a check
    ``dumper.dump()`` already performs at *runtime* — a ``--dump-manifest``
    combined with ``-I``/``--include``, which declares two conflicting public
    surfaces. That combination previously dry-ran as a clean report and then
    failed during the real extraction. Reporting it as a usage error in both
    places is a strictly better answer, and it stays inside the dry-run
    contract because it is precisely a usage error.

    Lives here, not in `cli_dump_request.py` (which builds the `DumpRequest`
    this consumes), so that leaf module stays a leaf: this function's own
    `service_dump_pipeline` import is the one edge that needs a module
    already inside the by-design CLI-registration SCC
    (`IMPORT_CYCLE_ALLOWLIST` in `scripts/check_ai_readiness.py`), which
    `cli_buildsource` already is.
    """
    from .errors import PlanningError, ValidationError
    from .service_dump_pipeline import resolve_dump_request

    try:
        return resolve_dump_request(request)
    except (ValidationError, PlanningError) as exc:
        # PlanningError (ADR-063 Phase 4) is a bad-input combination, the
        # same usage-error contract as ValidationError.
        raise click.UsageError(str(exc)) from exc


# ── Back-compat re-export shims (lazy) ─────────────────────────────────────
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

# `_purge_external_outputs` was `cli_buildsource_helpers._purge_external_outputs`
# (a private helper, but one this module has always re-exported "for API
# stability / tests" per AGENTS.md's "Moving helpers out of a module that
# re-exports them?" guidance) before it moved to `buildsource/pack_shape.py`
# as the public `purge_external_outputs` (ADR-061). Resolved the same lazy
# way as the `cli_graph` names above -- not because of an import cycle here,
# but so this compatibility path keeps tracking whatever
# `cli_buildsource_helpers.purge_external_outputs` currently resolves to
# (including a test's `monkeypatch.setattr` on that name) rather than
# freezing a snapshot of it at this module's own import time the way a plain
# assignment would (Codex review).
_HELPERS_REEXPORTS = frozenset({"_purge_external_outputs"})


def __getattr__(name: str) -> Any:
    if name in _GRAPH_REEXPORTS:
        import importlib

        return getattr(importlib.import_module("abicheck.cli_graph"), name)
    if name in _HELPERS_REEXPORTS:
        import importlib

        return importlib.import_module(
            "abicheck.cli_buildsource_helpers"
        ).purge_external_outputs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
