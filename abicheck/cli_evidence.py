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
rebuilding*. The pack augments an ABI snapshot with L3 build-context evidence
(compile DB / CMake File API / Ninja). Per ADR-028 D6 this command never runs
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

if TYPE_CHECKING:
    from .checker_types import Change
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
@click.option("--build-system", "build_system", default="generic", show_default=True,
              type=click.Choice(["generic", "cmake", "ninja", "bazel", "make"], case_sensitive=False),
              help="Build system hint for the compile-DB adapter.")
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
    build_system: str,
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
        build_system=build_system,
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
    has_build = bool(merged.compile_units or merged.targets or merged.toolchains)
    if has_build:
        pack.build_evidence = merged
    pack.manifest.coverage = _build_coverage(merged, has_build)
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
    build_system: str,
    verbose: bool,
) -> None:
    """Run the requested build-evidence adapters and fold them into *merged*."""
    # Import adapters lazily so `collect-evidence --help` stays cheap.
    from .evidence.adapters import (
        CMakeFileApiAdapter,
        CompileDbAdapter,
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


def _build_coverage(merged: BuildEvidence, has_build: bool) -> list[LayerCoverage]:
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
    return [
        l3,
        LayerCoverage(layer=EvidenceLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=EvidenceLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]


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
) -> tuple[list[Change], list[dict[str, object]]]:
    """Load packs, diff their build evidence, echo coverage, return findings.

    Per ADR-028 D3 the build-context findings are folded into the ordinary
    verdict pipeline as ``extra_changes`` and never override artifact-backed
    verdicts. The D7 coverage table is printed to stderr here (covers every
    output format) and also returned as serialized rows so the JSON report can
    carry a structured ``evidence_coverage`` block. Returns
    ``(changes, coverage_rows)``.
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

    src_pack = new_pack or old_pack
    coverage = list(src_pack.manifest.coverage) if src_pack else []
    if not coverage:
        coverage = [
            LayerCoverage(layer=layer.value, status=CoverageStatus.NOT_COLLECTED)
            for layer in (EvidenceLayer.L3_BUILD, EvidenceLayer.L4_SOURCE_ABI, EvidenceLayer.L5_SOURCE_GRAPH)
        ]
    intrinsic = _intrinsic_coverage(new_snapshot)
    _echo_coverage(intrinsic, coverage)
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


def _echo_coverage(intrinsic: list[LayerCoverage], optional: list[LayerCoverage]) -> None:
    """Print the D7 evidence-coverage table to stderr (all output formats)."""
    names = {
        "L0": "L0 binary metadata", "L1": "L1 debug info", "L2": "L2 public header AST",
        "L3_build": "L3 build context", "L4_source_abi": "L4 source ABI replay",
        "L5_source_graph": "L5 source graph summary",
    }
    click.echo("Evidence coverage:", err=True)
    for cov in [*intrinsic, *optional]:
        extra = ""
        if cov.status != CoverageStatus.NOT_COLLECTED:
            extra = f", {cov.confidence.value} confidence"
            if cov.detail:
                extra += f": {cov.detail}"
        click.echo(f"  {names.get(cov.layer, cov.layer):<26} {cov.status.value}{extra}", err=True)
