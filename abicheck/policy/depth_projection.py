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
snapshot actually carries DWARF debug info — see
:func:`_snapshot_has_native_debug_info` — so a headers-only snapshot with no
DWARF strips down to L0 (no structural facts survive at all) exactly the way
the reference implementation's own L0 branch does, while a DWARF-informed
one keeps layout/signatures at ``binary`` the way a real DWARF-informed
binary dump would (Codex review, PR #1020: an earlier version of this
module fixed every ``binary``-rung projection to the reference
implementation's L1 branch unconditionally, so a purely header-derived
snapshot with no DWARF at all still carried full ``types``/``enums``/
function-signature data through a ``binary``-depth projection and could
still emit e.g. ``type_field_type_changed``).

**Deliberately in scope** (the same fields the tier-accuracy gate's
validated ``project()`` degrades, plus the ``BuildSourcePack`` L3-L5 split
that synthetic corpus never populates, plus three fields a second review
round found this module itself needed to close — see below):
``functions``/``variables`` visibility+origin (a ``Visibility.HIDDEN``
— non-exported — declaration is dropped entirely, not promoted to
``ELF_ONLY``: see :func:`_strip_header_and_above_evidence`'s own
docstring), ``types``/``enums`` (kept only when DWARF specifically
observed that declaration by name — :func:`_dwarf_confirmed_names`, real
per-record evidence, not a whole-snapshot guess), ``typedefs`` (always
cleared — this codebase's model has no per-name DWARF lookup for them at
all), ``constants``, ``python_api``, ``from_headers``, ``semantic_ir`` and
``surface_graph`` (both L2+ header-AST/header-graph facts, gated the same
as ``from_headers`` — ``_attach_header_graph``'s own docstring: "the
header-only (L2) semantic graph", not an L4/L5 fact despite a first
version of this module gating it to ``source`` on that wrong assumption),
``contract`` (an ADR-050 ``ExtractionContract`` computed from the same
header-scope/compile-context inputs this module discards — left alone, it
can still make ``checker.compare()`` raise a scope/profile mismatch error
from two sides' *original* header scopes even though a binary-only
comparison was never going to look at either), ``build_mode``, and
``build_source`` (nulled below ``build``; degraded to its L3-only
``build_evidence`` — ``source_abi``/``source_graph`` cleared — between
``build`` and ``source``). Callers that resolve build-info/source packs
*out-of-band* (a real, pre-built pack directory, not embedded inline) must
project or withhold those packs themselves before diffing them — this
module only ever sees what already lives on the snapshot object it was
handed; see ``cli_compare_helpers.run_compare``'s own comment on its
``prepare_embedded_build_source`` call for the call-site half of that fix.

**Deliberately out of scope, not silently assumed handled**: platform
container facts (``elf``/``pe``/``macho``/``dwarf``/``dwarf_advanced``
themselves are never cleared — only what they *justify keeping* in
``functions``/``types``/... changes), ``kabi``/``sycl``/``python_ext``/
``numpy_capi``, ``dependency_info``, ``fact_provenance``/``ast_*``/
entity-id maps, and ``elf_only_mode`` (left exactly as resolved except
where the no-DWARF branch sets it ``True`` itself, matching the reference
implementation's identical choice at its own L0). Function/variable
*signatures* (return type, params, value) stay gated on whole-snapshot
DWARF presence, not per-declaration — see
:func:`_snapshot_has_native_debug_info`'s own docstring for exactly why:
this codebase's model has no per-function DWARF-confirmation fact for a
projection to read back, so getting this fully precise is real,
separately-justified future work (it needs a new fact the dumper doesn't
record yet), not a residual of this module's own scope. A future extension
of this module's scope generally is the same: real, separately-justified
work, not a residual of this docstring's own account.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

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


def _snapshot_has_native_debug_info(snap: AbiSnapshot) -> bool:
    """Whether *snap* carries DWARF debug info independent of any header AST.

    The same signal ``confidence.py``/``analysis_assurance.py`` already use
    for "does this snapshot have debug info" (``dwarf is not None and
    dwarf.has_dwarf``) — not restated, reused, so this module cannot silently
    disagree with those about what counts. Deliberately does not also probe
    PE/Mach-O-specific debug carriers (PDB, ...): matching that same existing
    precedent exactly rather than inventing a wider, unvalidated heuristic —
    a real PDB-aware extension is separately-justified future work, not a
    residual of this function's own scope.

    **Whole-snapshot, not per-declaration** — deliberately, and only for the
    two fact families this codebase's model has no per-name DWARF lookup
    for at all: function/variable signatures. ``types``/``enums`` *do* have
    one (``DwarfMetadata.structs``/``.enums``, keyed by name) and are
    filtered per-record instead — see :func:`_dwarf_confirmed_names`. For
    functions/variables, this whole-snapshot gate matches the one
    already-validated reference implementation of this idea,
    ``scripts/check_tier_accuracy.py``'s ``project()``, exactly: its own L1
    branch keeps every function's signature uniformly once *any* DWARF is
    present, with no per-function check either — this codebase's dumper
    merge process does not track "was this specific function's signature
    confirmed by its own DWARF subprogram DIE, or only by headers" anywhere
    a projection could read it back. A real per-function extension is
    separately-justified future work (it would need a new fact the dumper
    doesn't currently record), not a residual of this function's own scope
    (Codex review, PR #1020, third round).
    """
    return snap.dwarf is not None and snap.dwarf.has_dwarf


def _dwarf_confirmed_names(snap: AbiSnapshot) -> tuple[frozenset[str], frozenset[str]]:
    """Struct and enum names DWARF specifically observed, respectively.

    ``DwarfMetadata.structs``/``.enums`` are real per-name facts — unlike
    the whole-snapshot ``has_dwarf`` flag, they answer "was *this*
    declaration confirmed by DWARF" precisely, so an uninstantiated
    header-only record sitting alongside unrelated real DWARF content is
    correctly excluded rather than swept in by the coarser flag (Codex
    review, PR #1020, third round — a first cut of this module used
    ``_snapshot_has_native_debug_info`` for these two families too, which
    over-retains exactly this case). Empty when *snap* has no ``dwarf`` at
    all, which is what makes the no-DWARF-whatsoever case (this function's
    old, coarser job) fall out of the same per-record filter for free.
    """
    dwarf = snap.dwarf
    if dwarf is None:
        return frozenset(), frozenset()
    return frozenset(dwarf.structs), frozenset(dwarf.enums)


def _strip_header_and_above_evidence(snap: AbiSnapshot) -> None:
    """Blank every L2+ (header-AST) fact on *snap*, in place.

    ``types``/``enums`` keep only the entries DWARF itself specifically
    observed (:func:`_dwarf_confirmed_names`) — real per-record evidence,
    not a whole-snapshot guess. ``typedefs`` are always cleared: this
    codebase's model has no per-name DWARF lookup for them at all (no
    ``DwarfMetadata.typedefs``), so there is no evidence-backed way to keep
    any of them below `headers`. Function/variable *signatures*
    (return type, params, value) stay gated on whole-snapshot DWARF
    presence — see :func:`_snapshot_has_native_debug_info`'s own docstring
    for exactly why that one stays coarser. A function/variable with
    ``Visibility.HIDDEN`` (a real, non-exported header-only declaration,
    never a fact a binary-only view could see at all) is dropped from the
    snapshot entirely rather than promoted to ``ELF_ONLY`` — promoting it
    would misrepresent an unexported symbol as binary-visible and could
    manufacture a false ``*_removed_elf_only`` finding for a declaration no
    real binary-only dump would ever have seen as a symbol in the first
    place (Codex review, PR #1020, third round).
    """
    snap.functions = [f for f in snap.functions if f.visibility != Visibility.HIDDEN]
    snap.variables = [v for v in snap.variables if v.visibility != Visibility.HIDDEN]
    for f in snap.functions:
        f.visibility = Visibility.ELF_ONLY
        f.origin = ScopeOrigin.UNKNOWN
    for v in snap.variables:
        v.visibility = Visibility.ELF_ONLY
        v.origin = ScopeOrigin.UNKNOWN
    confirmed_structs, confirmed_enums = _dwarf_confirmed_names(snap)
    snap.types = [t for t in snap.types if t.name in confirmed_structs]
    snap.enums = [e for e in snap.enums if e.name in confirmed_enums]
    for t in snap.types:
        t.origin = ScopeOrigin.UNKNOWN
    for e in snap.enums:
        e.origin = ScopeOrigin.UNKNOWN
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

    if not _snapshot_has_native_debug_info(snap):
        for f in snap.functions:
            f.return_type = "?"
            f.params = []
        for v in snap.variables:
            v.type = "?"
            v.is_const = False
            v.value = None
        snap.elf_only_mode = True


def _project_build_source_pack(
    pack: BuildSourcePack, rank: int, build_rank: int, source_rank: int
) -> BuildSourcePack | None:
    """Degrade one already-resolved ``BuildSourcePack`` to *rank*, in place.

    Shared by :func:`project_snapshot_to_depth` (for a snapshot's embedded
    ``build_source``) and :func:`project_build_source_pack_to_depth` (for a
    pack resolved out-of-band from an explicit ``--build-info``/``--sources``
    path) so the L3-L5 capping rule is stated once rather than duplicated
    between the two callers. Below ``build``, the whole pack carries no
    evidence an explicit ``--depth`` asked for and is dropped entirely;
    between ``build`` and ``source``, the L3 ``build_evidence`` payload
    survives but the L4 source-ABI replay and L5 source-graph payloads are
    cleared; at or above ``source``, *pack* is returned unchanged.
    """
    if rank < build_rank:
        return None
    if rank < source_rank:
        pack.source_abi = None
        pack.source_graph = None
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
