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
    }
    has_build = bool(
        merged.compile_units or merged.targets or merged.toolchains
        or merged.link_units or merged.build_options
    )
    if has_build:
        pack.build_evidence = merged
    if surface is not None:
        pack.source_abi = surface
    pack.manifest.coverage = _build_coverage(merged, has_build, surface, source_detail)
    pack.write()

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


def _build_coverage(
    merged: BuildEvidence,
    has_build: bool,
    surface: SourceAbiSurface | None = None,
    source_detail: str = "",
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
    return [
        l3,
        l4,
        LayerCoverage(layer=EvidenceLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]


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
