# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""CLI helpers for data-source diagnostics."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from .buildsource.model import LayerCoverage
    from .buildsource.pack import BuildSourcePack


def print_data_sources(
    so_path: Path,
    has_headers: bool,
    build_source_path: Path | None = None,
    sources_path: Path | None = None,
) -> None:
    """Print data source diagnostic information for a binary."""
    from .binary_utils import detect_binary_format, normalize_binary_input
    from .dwarf_snapshot import show_data_sources

    normalized_path, binary_fmt = normalize_binary_input(so_path)
    if binary_fmt is None:
        binary_fmt = detect_binary_format(normalized_path)
    elf_meta = None
    dwarf_meta = None
    build_source_pack = None

    if binary_fmt == "elf":
        from .dwarf_unified import parse_dwarf
        from .elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(normalized_path)
        dwarf_meta, _ = parse_dwarf(normalized_path)

    if build_source_path is not None or sources_path is not None:
        from .buildsource.inline import is_pack_dir
        from .buildsource.pack import BuildSourcePack

        def load_pack(path: Path, label: str) -> BuildSourcePack | None:
            """Load a build-source pack when the input is already collected."""
            if not is_pack_dir(path):
                click.echo(
                    f"{label} input: {path} "
                    "(not collected in --show-data-sources; run dump without "
                    "--show-data-sources to embed inline L3/L4/L5 facts)",
                    err=True,
                )
                return None
            try:
                return BuildSourcePack.load(path)
            except Exception as exc:
                raise click.ClickException(
                    f"Invalid {label} build-source pack: {exc}"
                ) from exc

        build_info_pack = (
            load_pack(build_source_path, "Build-info")
            if build_source_path is not None else None
        )
        sources_pack = (
            load_pack(sources_path, "Sources")
            if sources_path is not None else None
        )

        build_source_pack = _combine_diagnostic_packs(build_info_pack, sources_pack)

    click.echo(
        show_data_sources(
            normalized_path, elf_meta, dwarf_meta, has_headers, build_source_pack
        )
    )


def _combine_diagnostic_packs(
    build_info_pack: BuildSourcePack | None,
    sources_pack: BuildSourcePack | None,
) -> BuildSourcePack | None:
    """Combine split build-info and source packs for diagnostic reporting."""
    from .buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from .buildsource.pack import BuildSourcePack

    if build_info_pack is None:
        return sources_pack
    if sources_pack is None:
        return build_info_pack

    combined = BuildSourcePack.empty(Path(""))
    combined.build_evidence = build_info_pack.build_evidence or sources_pack.build_evidence
    combined.source_abi = sources_pack.source_abi or build_info_pack.source_abi
    combined.source_graph = sources_pack.source_graph or build_info_pack.source_graph
    combined.manifest = copy.deepcopy(build_info_pack.manifest)

    layer_payload = {
        DataLayer.L3_BUILD.value: "build_evidence",
        DataLayer.L4_SOURCE_ABI.value: "source_abi",
        DataLayer.L5_SOURCE_GRAPH.value: "source_graph",
    }

    def row_for(layer: str, *packs: BuildSourcePack) -> LayerCoverage | None:
        """Return coverage from the pack that supplied the requested layer."""
        payload_attr = layer_payload.get(layer)
        for pack in packs:
            if payload_attr and not getattr(pack, payload_attr):
                continue
            row = next(
                (c for c in pack.manifest.coverage if c.layer == layer),
                None,
            )
            if row is not None:
                return copy.deepcopy(row)
        return None

    coverage = [
        copy.deepcopy(c)
        for c in build_info_pack.manifest.coverage
        if c.layer not in {
            DataLayer.L3_BUILD.value,
            DataLayer.L4_SOURCE_ABI.value,
            DataLayer.L5_SOURCE_GRAPH.value,
        }
    ]
    for layer, present, packs in (
        (
            DataLayer.L3_BUILD.value,
            combined.build_evidence is not None,
            (build_info_pack, sources_pack),
        ),
        (
            DataLayer.L4_SOURCE_ABI.value,
            combined.source_abi is not None,
            (sources_pack, build_info_pack),
        ),
        (
            DataLayer.L5_SOURCE_GRAPH.value,
            combined.source_graph is not None,
            (sources_pack, build_info_pack),
        ),
    ):
        row = row_for(layer, *packs)
        if row is None:
            row = LayerCoverage(
                layer=layer,
                status=CoverageStatus.PRESENT if present else CoverageStatus.NOT_COLLECTED,
            )
        coverage.append(row)
    combined.manifest.coverage = coverage
    return combined
