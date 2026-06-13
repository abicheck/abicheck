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

"""Helper functions for the ``merge`` sub-command of ``cli_buildsource``.

These were extracted from ``cli_buildsource.py`` to keep that module under the
2000-line hard cap.  They must NOT import from ``abicheck.cli_buildsource``
(that would create an import cycle rejected by the CI gate).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from .buildsource.merge_support import (
    _combine_packs,
    _layer_value,
    _record_merge_conflicts,
    _resolve_conflict_winners,
)
from .buildsource.model import DataLayer
from .buildsource.pack import BuildSourcePack

if TYPE_CHECKING:
    from .model import AbiSnapshot


def _exported_symbols_from_snapshot(snap: AbiSnapshot) -> tuple[str, ...]:
    """Exported (mangled) symbol names already parsed into *snap* — no re-dump.

    Used to plumb L0 exports into inline source replay (A1) for the
    ``dump <binary> --sources`` flow. Empty for a source-only snapshot.
    """
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    return tuple(sorted(syms))


def _merge_load_snapshots(inputs: tuple[Path, ...]) -> list[tuple[Path, AbiSnapshot]]:
    """Load and validate all input snapshots, raising clean Click errors on failure."""
    from .serialization import load_snapshot

    if len(inputs) < 2:
        raise click.UsageError("merge needs at least two input snapshots.")
    snaps: list[tuple[Path, AbiSnapshot]] = []
    for path in inputs:
        try:
            snaps.append((path, load_snapshot(path)))
        except Exception as exc:  # malformed/corrupted .abi.json → clean error
            raise click.ClickException(
                f"could not read snapshot {path.name}: {exc}"
            ) from exc
    return snaps


def _merge_pick_base(snaps: list[tuple[Path, AbiSnapshot]]) -> tuple[Path, AbiSnapshot]:
    """Return the (path, snapshot) pair that carries binary metadata (L0), else the first."""
    return next(
        (
            (p, s) for p, s in snaps
            if s.elf is not None or s.pe is not None or s.macho is not None
        ),
        snaps[0],
    )


def _merge_fold_packs(snaps: list[tuple[Path, AbiSnapshot]]) -> tuple[BuildSourcePack | None, int]:
    """Fold every input's embedded build_source pack left-to-right. Returns (combined, contributors)."""
    combined: BuildSourcePack | None = None
    contributors = 0
    for _p, s in snaps:
        if s.build_source is None:
            continue
        contributors += 1
        combined = _combine_packs(combined, s.build_source)
    return combined, contributors


def _merge_handle_conflicts(
    conflicts: dict[str, list[tuple[str, str]]],
    combined: BuildSourcePack | None,
    on_conflict: str,
) -> None:
    """Report layer conflicts to stderr and abort or record them per --on-conflict."""
    if not conflicts:
        return
    # Which input's facts actually survived per layer (_combine_packs is
    # first-wins for L3 but last-wins for L4/L5), so the message is accurate.
    winners = _resolve_conflict_winners(combined, conflicts) if combined is not None else {}
    for layer, entries in sorted(conflicts.items()):
        srcs = ", ".join(f"{name}" for name, _digest in entries)
        kept = f"kept {winners[layer]}" if layer in winners else "kept one input"
        click.echo(
            f"merge conflict: layer {layer} supplied with differing facts by "
            f"multiple inputs ({srcs}); {kept}.",
            err=True,
        )
    if on_conflict == "error":
        raise click.ClickException(
            "merge aborted: conflicting layer facts and --on-conflict=error. "
            "Each layer (L3/L4/L5) should come from exactly one input."
        )
    # warn mode: persist the conflict into the combined pack's extractor
    # ledger (a serialized field, unlike a nonexistent manifest.diagnostics),
    # so the recorded baseline carries the divergence forward.
    if combined is not None:
        _record_merge_conflicts(combined, conflicts, winners)


def _merge_attach_combined(
    combined: BuildSourcePack,
    base: AbiSnapshot,
    output: Path,
) -> None:
    """Relink source-ABI surface against binary exports (A1) and attach combined to base."""
    base_exports = _exported_symbols_from_snapshot(base)
    if base_exports and combined.source_abi is not None and not (
        combined.source_abi.roots.get("exported_symbols")
    ):
        from .buildsource.build_evidence import BuildEvidence
        from .buildsource.source_graph import build_source_graph
        from .buildsource.source_link import relink_surface_exports

        relink_surface_exports(combined.source_abi, base_exports)
        # L5: rebuild source graph so L5 mapping/localization is not inert.
        if combined.source_graph is not None:
            combined.source_graph = build_source_graph(
                combined.build_evidence or BuildEvidence(),
                source_abi=combined.source_abi,
            )
        # Mutating payloads invalidates precomputed artifact digests; clear them.
        combined.manifest.artifacts = []
    base.build_source = combined
    base.build_source_pack = combined.to_ref(path_hint=str(output))


def _merge_print_summary(
    base_path: Path,
    contributors: int,
    total: int,
    combined: BuildSourcePack | None,
    output: Path,
) -> None:
    """Print the post-merge summary to stderr."""
    click.echo(f"Merged baseline written to {output}", err=True)
    click.echo(f"  base ABI surface: {base_path.name}", err=True)
    click.echo(f"  build_source contributors: {contributors}/{total}", err=True)
    if combined is not None:
        for cov in combined.manifest.coverage:
            if _layer_value(cov.layer) in {
                DataLayer.L3_BUILD.value,
                DataLayer.L4_SOURCE_ABI.value,
                DataLayer.L5_SOURCE_GRAPH.value,
            }:
                click.echo(f"  {cov.layer}: {cov.status.value}", err=True)
