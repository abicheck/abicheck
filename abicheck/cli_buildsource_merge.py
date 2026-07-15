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

    The authoritative export set is the platform **dynamic symbol table**
    (``elf.symbols`` / ``pe.exports`` / ``macho.exports``), which lists every
    exported symbol as its raw linker name. When one is present it is used
    **alone**: the modeled ``functions``/``variables`` lists are a *narrower*,
    DWARF-shaped view that (a) covers only a fraction of the exports — feeding
    only those collapsed symbol matching to a handful of hits (the plugin/
    ``merge`` regression) — and (b) can carry non-ABI ctor/dtor linkage tags
    (GCC's unified ``C4``/``D4``) that are **not** real exports; unioning them in
    would let a source decl mangled ``C4`` exact-match a phantom and inflate
    ``exported_symbols``/``matched_symbols`` with a name the binary never exported
    (Codex review). The modeled mangled names are therefore only a *fallback* for
    backends that expose no raw table at all (a source-only snapshot, or a format
    whose export table did not parse).
    """
    raw: set[str] = set()
    have_raw_table = False
    elf = getattr(snap, "elf", None)
    if elf is not None:
        have_raw_table = True
        # Only DEFAULT-versioned ELF exports enter the relink set. A name that
        # exists *solely* as a non-default version alias (`foo@VER` with no
        # `foo@@VER`) cannot be linked against by an unversioned consumer, so
        # including it would let the L4 mapping mark a header decl backed only by
        # such an alias as "exported" — and the crosscheck's two-way reconciliation
        # would then wrongly suppress the `public_not_exported` finding the consumer
        # would actually hit as an undefined symbol (Codex review). Mirrors
        # `crosscheck._exported_symbol_names`. `is_default` is True for unversioned
        # symbols, so plain (non-versioned) libraries are unaffected.
        raw |= {
            s.name
            for s in getattr(elf, "symbols", ())
            if getattr(s, "name", "") and getattr(s, "is_default", True)
        }
    pe = getattr(snap, "pe", None)
    if pe is not None:
        have_raw_table = True
        raw |= {e.name for e in getattr(pe, "exports", ()) if getattr(e, "name", "")}
    macho = getattr(snap, "macho", None)
    if macho is not None:
        have_raw_table = True
        raw |= {e.name for e in getattr(macho, "exports", ()) if getattr(e, "name", "")}
    raw.discard("")
    if have_raw_table:
        # A parsed platform table is authoritative EVEN WHEN EMPTY — a hidden-only
        # library genuinely exports nothing, so its DWARF-modeled `functions` are
        # *not* exports and must not be relinked as if they were (Codex review).
        return tuple(sorted(raw))
    # No platform table parsed at all (a source-only snapshot): the modeled
    # mangled names are the only available fallback.
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    syms.discard("")
    return tuple(sorted(syms))


def _ingest_inputs_pack_snapshot(path: Path) -> AbiSnapshot:
    """Ingest a Flow-2 ``abicheck_inputs/`` directory into a source-side snapshot.

    The build-emitted normalized facts (ADR-035 D5) become a binary-less
    ``AbiSnapshot`` carrying the embedded L3/L4/L5 ``build_source`` pack, so the
    existing ``merge`` fold combines them with the artifact-side dump — no
    compiler frontend is re-run.
    """
    from .buildsource.inputs_pack import ingest_inputs_pack
    from .model import AbiSnapshot

    ingested = ingest_inputs_pack(path)
    snap = AbiSnapshot(
        library=ingested.manifest.library or path.name,
        version=ingested.manifest.version,
    )
    snap.build_source = ingested.pack
    return snap


def _merge_load_snapshots(inputs: tuple[Path, ...]) -> list[tuple[Path, AbiSnapshot]]:
    """Load and validate all input snapshots, raising clean Click errors on failure.

    An input may be a ``.abi.json`` dump or a Flow-2 ``abicheck_inputs/``
    directory (ADR-035 D5); the latter is ingested into a source-side snapshot so
    build-emitted facts ride the existing fold.
    """
    from .buildsource.inputs_pack import is_inputs_pack
    from .serialization import load_snapshot

    if len(inputs) < 2:
        raise click.UsageError("merge needs at least two inputs.")
    snaps: list[tuple[Path, AbiSnapshot]] = []
    for path in inputs:
        try:
            if path.is_dir():
                if not is_inputs_pack(path):
                    raise click.ClickException(
                        f"{path.name} is a directory but not an abicheck_inputs/ pack "
                        f"(no manifest.json with kind: abicheck_inputs)."
                    )
                snaps.append((path, _ingest_inputs_pack_snapshot(path)))
            else:
                snaps.append((path, load_snapshot(path)))
        except click.ClickException:
            raise
        except Exception as exc:  # malformed/corrupted input → clean error
            raise click.ClickException(
                f"could not read input {path.name}: {exc}"
            ) from exc
    return snaps


def _merge_pick_base(snaps: list[tuple[Path, AbiSnapshot]]) -> tuple[Path, AbiSnapshot]:
    """Return the (path, snapshot) pair that carries binary metadata (L0), else the first."""
    return next(
        (
            (p, s)
            for p, s in snaps
            if s.elf is not None or s.pe is not None or s.macho is not None
        ),
        snaps[0],
    )


def _merge_fold_packs(
    snaps: list[tuple[Path, AbiSnapshot]],
) -> tuple[BuildSourcePack | None, int]:
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
    winners = (
        _resolve_conflict_winners(combined, conflicts) if combined is not None else {}
    )
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
        from .buildsource.inline import build_inline_coverage
        from .buildsource.source_graph import (
            build_source_graph,
            mark_source_edges_extractor_coverage,
        )
        from .buildsource.source_link import relink_surface_exports

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


def _merge_attach_combined(
    combined: BuildSourcePack,
    base: AbiSnapshot,
    output: Path,
) -> None:
    """Relink source-ABI surface against binary exports (A1) and attach combined to base."""
    base_exports = _exported_symbols_from_snapshot(base)
    _relink_combined_against_exports(combined, base_exports)
    _warn_if_source_surface_empty(combined, base_exports)
    base.build_source = combined
    base.build_source_pack = combined.to_ref(path_hint=str(output))


def embed_inputs_pack(
    snap: AbiSnapshot, inputs_path: Path, output: Path | None
) -> None:
    """Fold a Flow-2 ``abicheck_inputs/`` pack into a binary dump inline.

    ``abicheck dump <binary> --inputs ./abicheck_inputs/`` performs, in one
    command, exactly the fold that ``abicheck merge <binary>.json
    ./abicheck_inputs/`` does: ingest the build-emitted pack (no frontend re-run),
    combine its L3/L4/L5 facts into *snap*, and relink the source surface against
    the binary's exports. This removes the separate ``merge`` step for the common
    single-artifact plugin/wrapper flow (``merge`` remains for multi-input folds).
    """
    ingested = _ingest_inputs_pack_snapshot(inputs_path)
    combined = _combine_packs(snap.build_source, ingested.build_source)
    if combined is None:
        return
    base_exports = _exported_symbols_from_snapshot(snap)
    _relink_combined_against_exports(combined, base_exports)
    _warn_if_source_surface_empty(combined, base_exports)
    snap.build_source = combined
    snap.build_source_pack = combined.to_ref(path_hint=str(output) if output else "")


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
                detail = f" ({cov.detail})" if cov.detail else ""
                click.echo(f"  {cov.layer}: {cov.status.value}{detail}", err=True)
