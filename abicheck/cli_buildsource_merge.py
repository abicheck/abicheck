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

"""Plain helper functions for the ``merge`` sub-command.

These cover loading/validating input snapshots, folding their embedded
``build_source`` packs left-to-right, handling layer conflicts, relinking the
combined source-ABI surface against binary exports, and printing the post-merge
summary. They were extracted from ``cli_buildsource_helpers.py`` to keep that
module under the file-size cap. This is a leaf module: it must NOT import from
``abicheck.cli_buildsource_helpers``, ``abicheck.cli_buildsource`` or
``abicheck.cli`` (that would create an import cycle rejected by the CI gate).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from .buildsource.merge_support import (
    _combine_packs,
    _layer_value,
)
from .buildsource.model import DataLayer
from .buildsource.pack import BuildSourcePack
from .evidence_depth import embedded_evidence_pack

if TYPE_CHECKING:
    from .model import AbiSnapshot


def _exported_symbols_from_snapshot(snap: AbiSnapshot) -> tuple[str, ...]:
    """Alias for ``buildsource.snapshot_exports.exported_symbols_from_snapshot``,
    which has owned this since ADR-061 Phase 3."""
    from .workflows.extraction import exported_symbols_from_snapshot

    return exported_symbols_from_snapshot(snap)


def _ingest_inputs_pack_snapshot(path: Path) -> AbiSnapshot:
    """Ingest a Flow-2 ``abicheck_inputs/`` directory into a source-side snapshot.

    The build-emitted normalized facts (ADR-035 D5) become a binary-less
    ``AbiSnapshot`` carrying the embedded L3/L4/L5 ``build_source`` pack, so the
    existing ``merge`` fold combines them with the artifact-side dump — no
    compiler frontend is re-run.
    """
    from .model import AbiSnapshot
    from .workflows.extraction import ingest_inputs_pack

    ingested = ingest_inputs_pack(path)
    snap = AbiSnapshot(
        library=ingested.manifest.library or path.name,
        version=ingested.manifest.version,
    )
    snap.build_source = ingested.pack
    return snap


def _relink_combined_against_exports(
    combined: BuildSourcePack, base_exports: tuple[str, ...]
) -> None:
    """Relink a combined pack's L4 surface + L5 graph against a binary's exports.

    Shared by ``merge`` (folding independently-produced dumps) and ``dump
    --inputs`` (folding a Flow-2 pack straight into the artifact dump): a
    source-only pack carries no ``exported_symbols`` root, so map its decls/types
    to the real binary exports here and rebuild the L4/L5 coverage rows. Mutates
    *combined* in place; a no-op when there are no exports or the surface is
    already export-linked.
    """
    if (
        base_exports
        and combined.source_abi is not None
        and not (combined.source_abi.roots.get("exported_symbols"))
    ):
        from .buildsource.build_evidence import BuildEvidence
        from .workflows.extraction import (
            build_inline_coverage,
            build_source_graph,
            mark_source_edges_extractor_coverage,
            relink_surface_exports,
        )

        relink_surface_exports(combined.source_abi, base_exports)
        # L5: rebuild source graph so L5 mapping/localization is not inert.
        if combined.source_graph is not None:
            combined.source_graph = build_source_graph(
                combined.build_evidence or BuildEvidence(),
                source_abi=combined.source_abi,
            )
            # This rebuild starts a fresh graph with no extractor_passes of its
            # own (a Flow-2 ingest's own call to this helper doesn't survive a
            # rebuild) -- reapply it so a confirmed-complete source_edges
            # rollup still reads as coverage here, not just at initial ingest
            # (Codex review).
            mark_source_edges_extractor_coverage(
                combined.source_graph, combined.source_abi
            )
            # build_source_graph() already called finalize() once (computing
            # graph.coverage's call_edges/type_edges/reference_edges
            # "collected" flags from extractor_passes as of that moment);
            # mark_source_edges_extractor_coverage() above mutates
            # extractor_passes afterward, so finalize() must re-run or the
            # serialized coverage summary still says "collected: false" even
            # though the pass flags are now true (Codex review; mirrors
            # ingest_inputs_pack's own re-finalize after the same call).
            combined.source_graph.finalize()
        extractors = tuple(combined.manifest.extractors)
        has_build = combined.build_evidence is not None and bool(
            combined.build_evidence.compile_units or combined.build_evidence.targets
        )
        managed_layers = {
            DataLayer.L3_BUILD.value,
            DataLayer.L4_SOURCE_ABI.value,
            DataLayer.L5_SOURCE_GRAPH.value,
        }
        preserved = [
            cov
            for cov in combined.manifest.coverage
            if _layer_value(cov.layer) not in managed_layers
        ]
        combined.manifest.coverage = [
            *preserved,
            *build_inline_coverage(
                combined.build_evidence or BuildEvidence(),
                has_build,
                combined.source_abi,
                combined.source_graph,
                extractors,
            ),
        ]
        # Mutating payloads invalidates precomputed artifact digests; clear them.
        combined.manifest.artifacts = []


def embed_inputs_pack(
    snap: AbiSnapshot, inputs_path: Path, output: Path | None
) -> None:
    """Fold a Flow-2 ``abicheck_inputs/`` pack into a binary dump inline.

    ``abicheck dump <binary> --build-info ./abicheck_inputs/`` (the pack is
    auto-detected by its manifest) ingests the build-emitted pack in one
    command, with no frontend re-run: combine its L3/L4/L5 facts into *snap*,
    and relink the source surface against the binary's exports.
    """
    from .workflows.extraction import pack_to_ref

    ingested = _ingest_inputs_pack_snapshot(inputs_path)
    combined = _combine_packs(
        embedded_evidence_pack(snap), embedded_evidence_pack(ingested)
    )
    if combined is None:
        return
    base_exports = _exported_symbols_from_snapshot(snap)
    _relink_combined_against_exports(combined, base_exports)
    _warn_if_source_surface_empty(combined, base_exports)
    snap.build_source = combined
    snap.build_source_pack = pack_to_ref(
        combined, path_hint=str(output) if output else ""
    )


def _warn_if_source_surface_empty(
    combined: BuildSourcePack, base_exports: tuple[str, ...]
) -> None:
    """Project-level source-pack usefulness signal (ADR-038 Caveat A).

    The Clang facts plugin can only detect an empty public surface *per TU*, and
    a single internal-only TU legitimately produces none — so the per-TU
    diagnostic is necessarily fuzzy. ``merge`` is the first point that sees the
    *whole* assembled surface, so it is where the authoritative call can be made:
    if the binary exports symbols but the folded source surface carries **zero**
    public entities across every TU, the producer's public-roots almost certainly
    did not match how the headers resolve (the pack is empty). Emit one clear
    warning here rather than leaving the user with a silently source-less
    baseline.
    """
    surface = getattr(combined, "source_abi", None)
    if surface is None or not base_exports:
        return
    entities = (
        len(surface.reachable_declarations)
        + len(surface.reachable_types)
        + len(surface.reachable_macros)
        + len(surface.reachable_templates)
        + len(surface.reachable_inline_bodies)
    )
    decls = len(surface.reachable_declarations)
    cov = surface.coverage or {}
    matched = int(cov.get("matched_symbols", 0) or 0) + int(
        cov.get("synthesized_symbols_matched", 0) or 0
    )
    exported = int(cov.get("exported_symbols", 0) or 0)
    if entities == 0:
        click.echo(
            "warning: merged source pack carries no public entities though the "
            f"binary exports {len(base_exports)} symbol(s). The producer's "
            "public-roots / ABICHECK_CC_HEADERS likely did not match how the "
            "public headers resolve (verify with `clang -H`); the baseline has no "
            "L4/L5 source evidence. See docs: user-guide/producing-source-facts.",
            err=True,
        )
    elif decls == 0:
        click.echo(
            "warning: merged source pack carries public macros/types but no public "
            f"function/variable declarations while the binary exports {len(base_exports)} "
            "symbol(s). This usually means the selected compile unit did not include "
            "the library's public API declarations, or public-roots is too broad/narrow; "
            "symbol matching will be ineffective.",
            err=True,
        )
    elif exported > 0 and matched == 0:
        click.echo(
            "warning: merged source pack carries public declarations but matched "
            f"0/{exported} exported symbol(s). Check that the facts pack was built "
            "from the same target/configuration as the binary and that public-roots "
            "points at the headers used by that target.",
            err=True,
        )

