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
keeping DWARF-derived layout/signatures at the ``binary`` rung the way a
real DWARF-informed binary dump would.

**Deliberately in scope** (the same fields the tier-accuracy gate's
validated ``project()`` degrades, plus the ``BuildSourcePack``/
``surface_graph`` L3-L5 split that synthetic corpus never populates):
``functions``/``variables`` visibility+origin, ``types``/``enums`` origin,
``constants``, ``python_api``, ``from_headers``, ``semantic_ir`` (an L2+
header-AST fact, gated the same as ``from_headers``), ``build_mode``,
``build_source`` (nulled below ``build``; degraded to its L3-only
``build_evidence`` — ``source_abi``/``source_graph`` cleared — between
``build`` and ``source``), and ``surface_graph`` (an L5 fact, nulled below
``source``).

**Deliberately out of scope, not silently assumed handled**: platform
container facts (``elf``/``pe``/``macho``/``dwarf``/``dwarf_advanced``),
``kabi``/``sycl``/``python_ext``/``numpy_capi``, ``typedefs`` (DWARF also
carries typedef DIEs, so — like ``types``/``enums`` — these survive a
``binary``-rung projection the same way the validated reference
implementation keeps them below its own L0), ``dependency_info``,
``contract``/``fact_provenance``/``ast_*``/entity-id maps, and
``elf_only_mode`` (left exactly as resolved — forcing it ``True`` would
misreport a projection of a real DWARF-informed snapshot as symbols-only,
which the reference implementation itself only does at its fully-stripped
L0, not at the L1-equivalent this module's ``binary`` rung maps to). A
future extension of this module's scope is real, separately-justified work,
not a residual of this docstring's own account.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from ..evidence_depth import DEPTH_RANK
from ..model import ScopeOrigin, Visibility

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["project_snapshot_to_depth"]


def _strip_header_and_above_evidence(snap: AbiSnapshot) -> None:
    """Blank every L2+ (header-AST) fact on *snap*, in place.

    Keeps DWARF-derived structural facts (layout, signatures, typedefs) —
    those are an L0/L1 fact regardless of whether headers were also parsed —
    and blanks only what a header AST alone contributes: scoping
    (visibility/origin), macro/constexpr constant values, the Python-API
    stub surface, and the header-AST-only ``SemanticIR``.
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
    if rank < source_rank:
        out.surface_graph = None
    return out
