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

"""ADR-044 ChangeKind registry extension entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_coverage.py``/``change_registry_composition.py``. These
entries are spliced into the single ``REGISTRY`` at import time — declaring a
kind here is exactly equivalent to declaring it in ``change_registry.py``.
Covers both the P0 slice's suppression diagnostic and the P1 call-graph
overlay kind.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_B = Verdict.BREAKING
_E = ChangeKindMeta

SUPPRESSION_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "suppression_would_hide_public_break",
        _R,
        impact="A namespace/source_location suppression rule matched this change, "
        "but it was not applied because the change is reachable from the "
        "public ABI surface (ADR-044) — suppressing it would hide a real "
        "break rather than internal noise. Review the finding; if the "
        "suppression is intentional even though the symbol is "
        "public-reachable, add `allow_public_break: true` to that rule.",
    ),
    _E(
        "suppression_reachability_unknown",
        _R,
        impact="A suppression rule using `reachability: proven-unreachable-only` "
        "matched this change, but it was not applied because graph "
        "coverage was insufficient to prove the change unreachable from "
        "the public ABI surface — the change stays in the report instead "
        "of being silently hidden by absence-of-evidence. Add "
        "`allow_unknown_reachability: true` to the rule to suppress it "
        "anyway once you have manually confirmed it is safe.",
    ),
    _E(
        "internal_symbol_required_by_public_api",
        _B,
        impact="An internal-namespaced decl (e.g. ::detail::, ::impl::, "
        "::internal::) that already changed in an artifact-proven "
        "breaking way (e.g. func_removed) is called or referenced from "
        "a public entry point over the optional L5 source/call graph "
        "(--sources/--build-info/--header-graph). Although the symbol "
        "is conceptually internal, it is part of the effective public "
        "ABI: an application built against the old public entry point "
        "can fail to resolve it at load time. Call-graph analogue of "
        "INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API (ADR-044 P1 items 1-2), for "
        "the pure-call shape that walk's layout-only reachability model "
        "cannot see (no field/base/signature evidence, only a call "
        "edge).",
    ),
]
