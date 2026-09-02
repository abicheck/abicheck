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
that synthetic corpus never populates): ``functions``/``variables``
visibility+origin, ``types``/``enums``/``typedefs`` (fully stripped, not
merely re-scoped, when no DWARF backs them — see above), ``constants``,
``python_api``, ``from_headers``, ``semantic_ir`` and ``surface_graph``
(both L2+ header-AST/header-graph facts, gated the same as
``from_headers`` — ``_attach_header_graph``'s own docstring: "the
header-only (L2) semantic graph", not an L4/L5 fact despite a first
version of this module gating it to ``source`` on that wrong assumption),
``build_mode``, and ``build_source`` (nulled below ``build``; degraded to
its L3-only ``build_evidence`` — ``source_abi``/``source_graph`` cleared —
between ``build`` and ``source``).

**Deliberately out of scope, not silently assumed handled**: platform
container facts (``elf``/``pe``/``macho``/``dwarf``/``dwarf_advanced``
themselves are never cleared — only what they *justify keeping* in
``functions``/``types``/... changes), ``kabi``/``sycl``/``python_ext``/
``numpy_capi``, ``dependency_info``, ``contract``/``fact_provenance``/
``ast_*``/entity-id maps, and ``elf_only_mode`` (left exactly as resolved
except where the no-DWARF L0 branch below sets it ``True`` itself, matching
the reference implementation's identical choice at its own L0). A future
extension of this module's scope is real, separately-justified work, not a
residual of this docstring's own account.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from ..evidence_depth import DEPTH_RANK
from ..model import ScopeOrigin, Visibility

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["project_pair_to_depth", "project_snapshot_to_depth"]


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
    """
    return snap.dwarf is not None and snap.dwarf.has_dwarf


def _strip_header_and_above_evidence(snap: AbiSnapshot) -> None:
    """Blank every L2+ (header-AST) fact on *snap*, in place.

    When *snap* carries real DWARF debug info, this keeps DWARF-derived
    structural facts (layout, signatures, typedefs) — those are an L1 fact
    independent of whether headers were also parsed — and blanks only what a
    header AST alone contributes: scoping (visibility/origin), macro/
    constexpr constant values, the Python-API stub surface, and the
    header-AST-only ``SemanticIR``.

    When *snap* has no DWARF at all, every one of those structural facts
    came *only* from the header AST — nothing else in the snapshot could
    have produced them — so this additionally strips to L0: no types, no
    enums, no typedefs, and functions/variables degrade to bare symbol
    identity (no signature/type/value evidence), matching
    ``scripts/check_tier_accuracy.py``'s own L0 branch exactly.
    """
    for f in snap.functions:
        f.visibility = Visibility.ELF_ONLY
        f.origin = ScopeOrigin.UNKNOWN
    for v in snap.variables:
        v.visibility = Visibility.ELF_ONLY
        v.origin = ScopeOrigin.UNKNOWN
    for t in snap.types:
        t.origin = ScopeOrigin.UNKNOWN
    for e in snap.enums:
        e.origin = ScopeOrigin.UNKNOWN
    snap.constants = {}
    snap.from_headers = False
    snap.python_api = None
    snap.semantic_ir = None
    # `_attach_header_graph`'s own docstring: "the header-only (L2) semantic
    # graph" -- an L2 fact like the others above, not the L4/L5
    # `build_source.source_graph` this function leaves untouched.
    snap.surface_graph = None

    if not _snapshot_has_native_debug_info(snap):
        snap.types = []
        snap.enums = []
        snap.typedefs = {}
        for f in snap.functions:
            f.return_type = "?"
            f.params = []
        for v in snap.variables:
            v.type = "?"
            v.is_const = False
            v.value = None
        snap.elf_only_mode = True


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
        out.build_source = None
    elif rank < source_rank and out.build_source is not None:
        # Between `build` and `source`: keep the L3 build_evidence payload,
        # drop the L4 source-ABI replay and L5 source-graph payloads.
        out.build_source.source_abi = None
        out.build_source.source_graph = None
    return out


def project_pair_to_depth(
    old: AbiSnapshot, new: AbiSnapshot, depth: str | None
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """:func:`project_snapshot_to_depth` applied to both sides of a comparison.

    The one-line convenience every ``compare_snapshots()`` call site with two
    resolved sides and an optional ``depth`` needs, so each keeps its own
    call site to a single statement rather than two.
    """
    return project_snapshot_to_depth(old, depth), project_snapshot_to_depth(new, depth)
