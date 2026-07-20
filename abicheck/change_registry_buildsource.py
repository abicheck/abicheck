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

"""Build-source (L3/L4/L5) ChangeKind registry entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_coverage.py``/``change_registry_composition.py``. These
entries are spliced into the single ``REGISTRY`` at import time — declaring a
kind here is exactly equivalent to declaring it in ``change_registry.py``.

Covers findings sourced from the optional ``buildsource`` evidence layers
(L3 build evidence, L4 source-ABI replay, L5 semantic graph) rather than
artifact (L0-L2) diffing — see ``abicheck/buildsource/CLAUDE.md``.
"""
from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

BUILDSOURCE_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E("identity_collision_detected", _R,
       impact="Two distinct declarations were linked onto the same L4 identity key "
              "(SourceEntity.identity(): the mangled name, else "
              "qualified_name#signature_hash, else the bare qualified name) — proven "
              "distinct because each carries a different clang-computed USR. The "
              "identity fallback chain accepts this rare collision by design for "
              "unmangled cross-scope declarations (ADR-041 P1 #5); when it happens, "
              "the two declarations were folded together in the linked surface, so "
              "any L4/L5 finding attributed to that identity may actually describe "
              "either one. A source-tooling-confidence risk, never an artifact-proven "
              "ABI break — no action is required unless a finding under that name "
              "looks wrong, in which case treat it as ambiguous between the two USRs "
              "named in the finding detail."),
    _E("compile_context_conflict", _R,
       impact="Two or more L3 compile units attributed to the same build target "
              "carry conflicting ABI-relevant compile contexts — e.g. one unit "
              "built -frtti and another -fno-rtti (or -fexceptions vs "
              "-fno-exceptions), or the same preprocessor define bound to two "
              "different values. Aggregating them into one build context (as a "
              "synthetic public-consumer TU, or by first-match wins) silently "
              "picks one and drops the other, so the recorded L3/L4 facts may "
              "describe a build the shipped library never used (AC-008). A "
              "source-tooling risk, never an artifact-proven ABI break: scope the "
              "evidence to a single build target / link unit (or pass an explicit "
              "compile-DB filter) so one coherent context feeds the analysis."),
    _E("source_surface_dso_mismatch", _R,
       impact="The linked L4 source surface carries reachable declarations but its "
              "decl->export linking matched none of the analyzed binary's exported "
              "symbols. The surface almost certainly describes a different or "
              "shared DSO (e.g. one surface folded from every target's sources and "
              "reused across libraries), so any L4/L5 finding attributed to this "
              "binary may be mis-scoped (AC-009). A source-tooling risk, never an "
              "artifact-proven ABI break: relink/rebuild the source surface "
              "per-DSO against this binary's own exports."),
    # G31 Phase B (ADR-048) — graph-node reconciliation outcomes. See
    # buildsource.graph_reconcile for the matching algorithm and
    # checker_policy.ChangeKind for the authority-rule note.
    _E("declaration_renamed", _R,
       impact="The L5 source graph reconciled an old and a new declaration/type "
              "node as the same real-world entity under a new qualified name "
              "(same declaring file, unambiguous canonical-id/alias/structural "
              "evidence — never a bare short-name guess). Without this "
              "reconciliation the rename would show up as an unrelated "
              "remove-then-add pair in the graph diff. Informational: does not "
              "by itself indicate a break — any artifact-level finding for "
              "either spelling stands on its own evidence."),
    _E("declaration_moved", _R,
       impact="The L5 source graph reconciled an old and a new declaration/type "
              "node as the same real-world entity that moved to a different "
              "declaring file (same qualified name, unambiguous evidence). "
              "Without this reconciliation the move would show up as an "
              "unrelated remove-then-add pair in the graph diff. Informational: "
              "does not by itself indicate a break."),
    _E("declaration_identity_reconciled", _R,
       impact="The L5 source graph reconciled an old and a new declaration/type "
              "node as the same real-world entity where both the qualified "
              "name and the declaring-file evidence changed together (a "
              "combined rename+move, or a canonical-id/alias match with no "
              "clean rename/move split). Without this reconciliation the "
              "change would show up as an unrelated remove-then-add pair in "
              "the graph diff. Informational: does not by itself indicate a "
              "break."),
]
