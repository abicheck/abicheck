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
]
