# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Cap an already-resolved snapshot's evidence to what an explicit ``--depth`` requested.

``workflows.artifact.execute.enforce_requested_depth`` has long enforced
``--depth`` as a **floor**: it fails a run when the resolved evidence falls
short of what was requested. It never enforced a **ceiling** — an input that
is an already-serialized JSON snapshot (or, equivalently, a freshly-resolved
one whose extraction happened to reach further than asked) still carries
every richer fact it embeds, so ``compare old.json new.json --depth binary``
still emits real header-derived findings and can still publish ``BREAKING``
even though only binary-level evidence was requested. That gap is long
documented (``enforce_requested_depth``'s own docstring, and
``docs/contribute/known-gaps.md``'s "``--depth`` is a floor for live
extraction, not a ceiling for a pre-built snapshot" entry) — this module is
the "real, separate design question" that entry named but did not attempt:
"a comparison-time projection — resolve the snapshot as today, then filter
what ``checker.compare()`` is allowed to see down to the requested rung,
keeping the resolved ``AbiSnapshot`` itself untouched."

:func:`project_snapshot_to_depth` is that filter. It is pure (returns a deep
copy; never mutates its argument) and degrades exactly the fact families
:mod:`abicheck.evidence_depth`'s own rank table already gates, mirrored from
the one already-validated reference implementation of this exact idea —
``scripts/check_tier_accuracy.py``'s ``project()``, which the per-tier
accuracy gate runs against a real, if synthetic, labelled corpus. That
function's ``Tier`` axis (L0-L3) is finer than the public ``EvidenceDepth``
ladder (``binary``/``headers``/``build``/``source``, ``BINARY`` covering both
L0 and L1: "no L2 AST", not "no debug info"); this module maps onto the
coarser public ladder a caller actually requests through ``--depth``,
picking L0 or L1 **per snapshot** (not fixed to L1) based on whether *that*
snapshot's structural facts are genuinely DWARF/symbol-sourced — see
:func:`_structural_facts_are_dwarf_confirmed` — so a headers-derived
snapshot strips down to L0 (no structural facts survive at all) exactly the
way the reference implementation's own L0 branch does, while a genuinely
DWARF/symbols-only one keeps layout/signatures at ``binary`` the way a real
DWARF-informed binary dump would (Codex review, PR #1020: an earlier
version of this module fixed every ``binary``-rung projection to the
reference implementation's L1 branch unconditionally, so a purely
header-derived snapshot with no DWARF at all still carried full
``types``/``enums``/function-signature data through a ``binary``-depth
projection and could still emit e.g. ``type_field_type_changed``; a later
version kept a *header-derived* ``RecordType`` wholesale whenever DWARF
merely confirmed a struct by *name*, still letting an uncorroborated
header-only field-type change through — see
:func:`_structural_facts_are_dwarf_confirmed`'s own docstring for why
``from_headers`` is now part of the gate, not just ``dwarf.has_dwarf``).

**Deliberately in scope** (the same fields the tier-accuracy gate's
validated ``project()`` degrades, plus the ``BuildSourcePack`` L3-L5 split
that synthetic corpus never populates, plus several fields later review
rounds found this module itself needed to close — see below):
``functions``/``variables`` visibility+origin (a ``Visibility.HIDDEN``
— non-exported — declaration is dropped entirely, and a declaration with
no confirmed export-table entry is dropped too, not promoted to
``ELF_ONLY``: see :func:`_strip_header_and_above_evidence`'s own
docstring), ``types``/``enums``/``typedefs`` (kept wholesale only when
:func:`_structural_facts_are_dwarf_confirmed`, else fully cleared —
genuine DWARF-visible struct/enum layout changes on a header-derived
snapshot are still caught independently through the untouched
``snap.dwarf`` fields, never through these), ``constants``, ``python_api``,
``from_headers``, ``semantic_ir`` and ``surface_graph`` (both L2+
header-AST/header-graph facts, gated the same as ``from_headers`` —
``_attach_header_graph``'s own docstring: "the header-only (L2) semantic
graph", not an L4/L5 fact despite a first version of this module gating it
to ``source`` on that wrong assumption), ``contract`` (an ADR-050
``ExtractionContract`` computed from the same header-scope/compile-context
inputs this module discards — left alone, it can still make
``checker.compare()`` raise a scope/profile mismatch error from two sides'
*original* header scopes even though a binary-only comparison was never
going to look at either), ``build_mode``, and ``build_source`` (nulled
below ``build``; degraded to its L3-only ``build_evidence`` —
``source_abi``/``source_graph`` cleared, and their own
``BuildSourceManifest.coverage`` rows demoted to ``NOT_COLLECTED`` so a
report can't still claim that evidence backed this run — between ``build``
and ``source``). Callers that resolve build-info/source packs
*out-of-band* (a real, pre-built pack directory, not embedded inline) must
project or withhold those packs themselves before diffing them — this
module only ever sees what already lives on the snapshot object it was
handed; see ``cli_compare_helpers.run_compare``'s own comment on its
``prepare_embedded_build_source`` call for the call-site half of that fix.

**Deliberately out of scope, not silently assumed handled**: platform
container facts (``elf``/``pe``/``macho``/``dwarf``/``dwarf_advanced``
themselves are never cleared — only what they *justify keeping* in
``functions``/``types``/... changes, and what :func:`_exported_symbol_names`
reads back out of them), ``kabi``/``sycl``/``python_ext``/``numpy_capi``,
``dependency_info``, ``fact_provenance``/``ast_*``/entity-id maps, and
``elf_only_mode`` (left exactly as resolved except where the no-DWARF
branch sets it ``True`` itself, matching the reference implementation's
identical choice at its own L0). A future extension of this module's scope
is real, separately-justified work, not a residual of this docstring's own
account.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from ..buildsource.model import CoverageStatus, DataLayer, LayerCoverage
from ..evidence_depth import DEPTH_RANK
from ..model import ScopeOrigin, Visibility

if TYPE_CHECKING:
    from ..buildsource.pack import BuildSourcePack
    from ..model import AbiSnapshot

__all__ = [
    "project_build_source_pack_to_depth",
    "project_pair_to_depth",
    "project_snapshot_to_depth",
]


def _structural_facts_are_dwarf_confirmed(snap: AbiSnapshot) -> bool:
    """Whether *snap*'s ``types``/``enums``/``typedefs``/function-variable
    signatures are genuinely DWARF/symbol-sourced, not header-parsed.

    Requires both ``snap.dwarf.has_dwarf`` (the same signal
    ``confidence.py``/``analysis_assurance.py`` already use for "does this
    snapshot have debug info" — not restated, reused) **and**
    ``not snap.from_headers``. ``AbiSnapshot.from_headers``'s own field
    comment states the reason precisely: "DWARF-derived declarations
    populate the SAME functions/types lists [as header-derived ones] but
    must NOT be mistaken for header-level evidence." A header-derived
    ``RecordType``'s numeric *layout* (size/offset) may be backfilled from
    DWARF when the header backend can't compute it itself
    (``dumper_layout_backfill.py``), but its field-level *type spelling*
    stays whatever the header AST parsed — DWARF confirming a struct's
    *name* says nothing about whether that struct's *fields* agree with the
    header's own spelling (Codex review, PR #1020, fourth round — a third
    cut of this module kept a header-derived ``RecordType`` wholesale
    whenever DWARF merely confirmed the struct by name, which still let an
    uncorroborated header-only field-type change through; a per-record
    ``DwarfMetadata.structs``/``.enums`` name check was one level too
    narrow, not a fix). The same reasoning applies to function/variable
    signatures: this codebase's dumper merge process has no per-declaration
    "was this signature confirmed by its own DWARF DIE, or only by
    headers" fact either.

    A genuinely DWARF/symbols-only dump (``from_headers`` is ``False``) has
    no such ambiguity: ``dwarf_snapshot.py``'s own DWARF-only extraction
    path populates ``types``/``enums``/``typedefs``/function-variable
    signatures directly from DWARF DIEs, so every one of those fields is
    real DWARF evidence there, safely kept wholesale. On a header-derived
    snapshot, a real binary-visible struct/enum layout change is still
    caught independently: ``diff_platform._diff_dwarf`` reads
    ``snap.dwarf.structs``/``.enums`` directly, which this module never
    clears, and degrades gracefully with no header model at all ("If the
    header model is absent... fall back to comparing all DWARF types",
    that function's own comment) — function/variable signature changes have
    no such independent DWARF-native detector, a real, separately-justified
    gap (it would need a new per-declaration confirmation fact the dumper
    does not currently record), not a residual of this function's own
    scope. Deliberately does not also probe PE/Mach-O-specific debug
    carriers (PDB, ...) — matching the existing ``confidence.py``/
    ``analysis_assurance.py`` precedent exactly rather than inventing a
    wider, unvalidated heuristic; a real PDB-aware extension is
    separately-justified future work too.
    """
    return snap.dwarf is not None and snap.dwarf.has_dwarf and not snap.from_headers


def _exported_symbol_names(snap: AbiSnapshot) -> frozenset[str] | None:
    """*snap*'s raw platform export-table names, or ``None`` with no table at all.

    A small, local copy of the same "raw export table" read every other
    consumer of this idea already keeps its own independent copy of
    (``buildsource.crosscheck_base._exported_symbol_names``,
    ``buildsource.snapshot_exports.exported_symbols_from_snapshot``,
    ``post_manifest._exported_symbol_names``,
    ``diff_unnamed_types._exported_symbol_names`` — see
    ``buildsource/CLAUDE.md``'s own note that unifying these is a
    deliberately separate slice, not folded in here): each has a slightly
    different normalization/fallback need, and a ``policy``-layer caller
    may import ``model``/``compare`` but not ``extract`` (ADR-061), where
    most of those live. Matches ``crosscheck_base``'s own normalization
    exactly (only default-versioned ELF exports; Mach-O's single leading
    underscore stripped) since that is what must line up against
    ``Function.mangled``/``Variable.mangled``'s own spelling.

    ``None`` (never an empty ``frozenset``) when *snap* carries no platform
    export table at all — a caller must treat "cannot confirm" differently
    from "parsed and confirmed empty" (a real hidden-only library genuinely
    exports nothing and that must still read as confirmed-empty, not as
    "no evidence"), so :func:`_strip_header_and_above_evidence` skips this
    check entirely rather than misreading an absent platform block as zero
    exports.
    """
    elf = snap.elf
    if elf is not None:
        return frozenset(s.name for s in elf.symbols if s.name and s.is_default)
    pe = snap.pe
    if pe is not None:
        return frozenset(e.name for e in pe.exports if e.name)
    macho = snap.macho
    if macho is not None:
        return frozenset(
            e.name[1:] if e.name.startswith("_") else e.name
            for e in macho.exports
            if e.name
        )
    return None


def _strip_header_and_above_evidence(snap: AbiSnapshot) -> None:
    """Blank every L2+ (header-AST) fact on *snap*, in place.

    ``types``/``enums``/``typedefs`` and function/variable *signatures*
    (return type, params, value) survive wholesale only when
    :func:`_structural_facts_are_dwarf_confirmed` — see that function's own
    docstring for exactly why ``dwarf.has_dwarf`` alone is not enough.
    Otherwise every one of those is fully cleared, not merely re-scoped.

    A function/variable with ``Visibility.HIDDEN`` (a real, non-exported
    header-only declaration, never a fact a binary-only view could see at
    all) is dropped from the snapshot entirely rather than promoted to
    ``ELF_ONLY`` (Codex review, PR #1020, third round). A surviving
    function/variable is *additionally* required to appear in *snap*'s own
    raw export table (:func:`_exported_symbol_names`, when one exists)
    before promotion to ``ELF_ONLY`` — a header parser's own "declared
    public, without contrary evidence" fallback (e.g. ``dumper_castxml.
    _variable_visibility``'s un-emitted customization-point-object case)
    can mark a declaration ``PUBLIC`` even though the compiler never
    actually emitted a symbol for it, and promoting that declaration to
    ``ELF_ONLY`` would manufacture a false ``*_removed_elf_only``/
    ``*_removed`` finding for a symbol no real binary-only dump would ever
    have seen (Codex review, PR #1020, fourth round). Skipped — no
    filtering beyond the ``HIDDEN`` drop above — when *snap* carries no
    platform export table at all, so a synthetic/incomplete snapshot with
    no platform block populated keeps its prior, looser behavior rather
    than being stripped to nothing.
    """
    dwarf_sourced = _structural_facts_are_dwarf_confirmed(snap)

    snap.functions = [f for f in snap.functions if f.visibility != Visibility.HIDDEN]
    snap.variables = [v for v in snap.variables if v.visibility != Visibility.HIDDEN]
    exported = _exported_symbol_names(snap)
    if exported is not None:
        snap.functions = [f for f in snap.functions if f.mangled in exported]
        snap.variables = [v for v in snap.variables if v.mangled in exported]
    for f in snap.functions:
        f.visibility = Visibility.ELF_ONLY
        f.origin = ScopeOrigin.UNKNOWN
    for v in snap.variables:
        v.visibility = Visibility.ELF_ONLY
        v.origin = ScopeOrigin.UNKNOWN

    if dwarf_sourced:
        for t in snap.types:
            t.origin = ScopeOrigin.UNKNOWN
        for e in snap.enums:
            e.origin = ScopeOrigin.UNKNOWN
    else:
        snap.types = []
        snap.enums = []
        snap.typedefs = {}

    snap.constants = {}
    snap.from_headers = False
    snap.python_api = None
    snap.semantic_ir = None
    # `_attach_header_graph`'s own docstring: "the header-only (L2) semantic
    # graph" -- an L2 fact like the others above, not the L4/L5
    # `build_source.source_graph` this function leaves untouched.
    snap.surface_graph = None
    # ADR-050 `ExtractionContract`: both fingerprints are computed from
    # header-scope/compile-context inputs this function just discarded.
    # Left alone, `checker.compare()`'s `check_contracts_comparable()` can
    # still raise `ScopeMismatchError`/`ProfileMismatchError` from the two
    # sides' *original* header scopes even though the comparison this
    # snapshot now feeds is explicitly binary-only and was never going to
    # look at either scope (Codex review, PR #1020, third round).
    snap.contract = None

    if not dwarf_sourced:
        for f in snap.functions:
            f.return_type = "?"
            f.params = []
        for v in snap.variables:
            v.type = "?"
            v.is_const = False
            v.value = None
        snap.elf_only_mode = True


#: Layer values :func:`_project_build_source_pack` clears between ``build``
#: and ``source`` — shared so the payload fields and their manifest coverage
#: rows can never independently drift.
_L4_L5_LAYER_VALUES = frozenset(
    {DataLayer.L4_SOURCE_ABI.value, DataLayer.L5_SOURCE_GRAPH.value}
)


def _mark_layers_not_collected(
    pack: BuildSourcePack, layer_values: frozenset[str]
) -> None:
    """Rewrite *pack*'s own ``manifest.coverage`` rows for *layer_values*, in place.

    Clearing ``pack.source_abi``/``.source_graph`` alone leaves
    ``pack.manifest.coverage``'s own ``LayerCoverage`` rows still claiming
    ``PRESENT``/``PARTIAL`` for a layer this projection just removed —
    ``evidence_report.optional_coverage()`` returns those rows directly, so
    human output, JSON ``layer_coverage``, and the D9 evidence metrics could
    still claim source-ABI/source-graph evidence backed this comparison even
    though the depth ceiling excluded it from actually being diffed (Codex
    review, PR #1020, fourth round). Demoting to ``NOT_COLLECTED`` (dropping
    every other field — ``detail``/``confidence``/counts) matches the exact
    "nothing collected" row shape every existing producer already
    constructs for a layer it never ran (e.g. ``inline.py``'s own
    ``else: LayerCoverage(layer=..., status=CoverageStatus.NOT_COLLECTED)``
    branches), so a reader can't distinguish "genuinely never collected"
    from "collected, then projected away below the requested depth" — which
    is exactly the honest claim at this depth.
    """
    pack.manifest.coverage = [
        LayerCoverage(layer=row.layer, status=CoverageStatus.NOT_COLLECTED)
        if row.layer in layer_values
        else row
        for row in pack.manifest.coverage
    ]


def _project_build_source_pack(
    pack: BuildSourcePack, rank: int, build_rank: int, source_rank: int
) -> BuildSourcePack | None:
    """Degrade one already-resolved ``BuildSourcePack`` to *rank*, in place.

    Shared by :func:`project_snapshot_to_depth` (for a snapshot's embedded
    ``build_source``) and :func:`project_build_source_pack_to_depth` (for a
    pack resolved out-of-band from an explicit ``--build-info``/``--sources``
    path) so the L3-L5 capping rule is stated once rather than duplicated
    between the two callers. Below ``build``, the whole pack carries no
    evidence an explicit ``--depth`` asked for and is dropped entirely — its
    ``manifest.coverage`` goes with it, so no separate row-demotion is
    needed there. Between ``build`` and ``source``, the L3 ``build_evidence``
    payload survives but the L4 source-ABI replay and L5 source-graph
    payloads are cleared, and their own coverage rows demoted alongside them
    (:func:`_mark_layers_not_collected`); at or above ``source``, *pack* is
    returned unchanged.
    """
    if rank < build_rank:
        return None
    if rank < source_rank:
        pack.source_abi = None
        pack.source_graph = None
        _mark_layers_not_collected(pack, _L4_L5_LAYER_VALUES)
    return pack


def project_snapshot_to_depth(snap: AbiSnapshot, depth: str | None) -> AbiSnapshot:
    """Return a copy of *snap* capped to what an explicit ``--depth`` requested.

    A no-op (returns *snap* itself, not a copy) when *depth* is ``None`` —
    matching ``enforce_requested_depth``'s identical "no explicit depth, no
    enforcement" contract — or when *depth* is not a recognized public rung
    (``evidence_depth.DEPTH_RANK`` doesn't know it; validation elsewhere is
    what rejects that case, this function simply declines to guess).

    Callers should apply this only to the *view* a comparison classifies
    from, after :func:`~abicheck.workflows.artifact.execute.
    enforce_requested_depth` has already confirmed the resolved evidence
    meets *depth* as a floor — this function only ever removes evidence, it
    never restores what was never resolved. It never mutates its argument,
    so a caller that also persists or reuses the original, un-projected
    snapshot (a ``dump`` artifact meant for a later, deeper comparison) is
    unaffected.
    """
    if depth is None:
        return snap
    depth = depth.lower()
    if depth not in DEPTH_RANK:
        return snap
    rank = DEPTH_RANK[depth]
    headers_rank = DEPTH_RANK["headers"]
    build_rank = DEPTH_RANK["build"]
    source_rank = DEPTH_RANK["source"]

    out = copy.deepcopy(snap)
    if rank < headers_rank:
        _strip_header_and_above_evidence(out)
    if rank < build_rank:
        out.build_mode = None
    if out.build_source is not None:
        out.build_source = _project_build_source_pack(
            out.build_source, rank, build_rank, source_rank
        )
    return out


def project_build_source_pack_to_depth(
    pack: BuildSourcePack | None, depth: str | None
) -> BuildSourcePack | None:
    """Return a copy of *pack* capped to what an explicit ``--depth`` requested.

    The out-of-band counterpart to :func:`project_snapshot_to_depth`'s own
    ``build_source`` handling, for a caller that resolves an explicit
    ``--build-info``/``--sources`` pack *itself* rather than reading one
    already embedded on a snapshot object. ``project_snapshot_to_depth`` only
    ever sees what already lives on the snapshot it was handed, so a raw pack
    path threaded straight through to ``prepare_embedded_build_source``
    bypasses it entirely (Codex review, PR #1020, third round) — a caller
    doing that must cap the pack itself with this function before attaching
    or diffing it. A no-op (returns *pack* itself) when *pack* or *depth* is
    ``None``, or *depth* is not a recognized public rung — matching
    :func:`project_snapshot_to_depth`'s identical no-op contract.
    """
    if pack is None or depth is None:
        return pack
    depth = depth.lower()
    if depth not in DEPTH_RANK:
        return pack
    rank = DEPTH_RANK[depth]
    build_rank = DEPTH_RANK["build"]
    source_rank = DEPTH_RANK["source"]
    return _project_build_source_pack(
        copy.deepcopy(pack), rank, build_rank, source_rank
    )


def project_pair_to_depth(
    old: AbiSnapshot, new: AbiSnapshot, depth: str | None
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """:func:`project_snapshot_to_depth` applied to both sides of a comparison.

    The one-line convenience every ``compare_snapshots()`` call site with two
    resolved sides and an optional ``depth`` needs, so each keeps its own
    call site to a single statement rather than two.
    """
    return project_snapshot_to_depth(old, depth), project_snapshot_to_depth(new, depth)
