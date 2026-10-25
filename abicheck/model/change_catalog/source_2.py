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

"""Ordinal part 2 of ``source.py``'s entry list -- not a separate owner.

``source.py`` remains the taxonomy and the single public name
(``SOURCE_ENTRIES``, which appends this slice); see its docstring for the
taxonomy's scope. Split by declaration-order position purely to keep each
file under ADR-061's 800-line ceiling, the same shape as ``platform_{1,2,3}.py``.
"""

from __future__ import annotations

from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

SOURCE_ENTRIES_2: list[ChangeKindMeta] = [
    _E(
        "source_surface_dso_mismatch",
        _R,
        impact="The linked L4 source surface carries reachable declarations but its "
        "decl->export linking matched none of the analyzed binary's exported "
        "symbols. The surface almost certainly describes a different or "
        "shared DSO (e.g. one surface folded from every target's sources and "
        "reused across libraries), so any L4/L5 finding attributed to this "
        "binary may be mis-scoped (AC-009). A source-tooling risk, never an "
        "artifact-proven ABI break: relink/rebuild the source surface "
        "per-DSO against this binary's own exports.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "source_to_binary_mapping_changed",
        _R,
        impact="A declaration present in both versions now maps to a different "
        "exported binary symbol (or its source↔symbol mapping changed) "
        "without a clear artifact ABI diff. A surface/mapping consistency "
        "risk to investigate, surfaced by comparing source graph summaries.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
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
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "suppression_would_hide_public_break",
        _R,
        impact="A namespace/source_location suppression rule matched this change, "
        "but it was not applied because the change is reachable from the "
        "public ABI surface — suppressing it would hide a real "
        "break rather than internal noise. Review the finding; if the "
        "suppression is intentional even though the symbol is "
        "public-reachable, add `allow_public_break: true` to that rule.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "target_dependency_added",
        _R,
        impact="The library gained an inter-target build/link dependency (a new "
        "TARGET_DEPENDS_ON edge in the build graph). The shipped artifact may "
        "now require an additional library at load time, so a deployment that "
        "only shipped the old dependency set can fail to resolve at runtime, "
        "and the added dependency's own ABI now transitively affects "
        "consumers. A packaging/deployment risk to review; the artifact's "
        "DT_NEEDED diff proves any concrete new load-time dependency.",
        entity=_ENT.BUILD,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.ADDED,
    ),
    _E(
        "template_body_changed",
        _R,
        impact="The implementation of an uninstantiated public template changed. "
        "No binary symbol exists to compare, "
        "so this is invisible to artifact comparison; consumers that "
        "instantiate the template pick up the new body on recompile. A "
        "source-visible risk surfaced only by source replay.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
    ),
    _E(
        "undocumented_export_ratio_increased",
        _C,
        impact="The fraction of exported symbols with no public-header declaration "
        "(EXPORT_ONLY origin) rose between versions — a packaging-hygiene "
        "regression: a symbol was exported without a corresponding public "
        "header. Informational; computed unconditionally -- "
        "--surface-metrics is accepted "
        "for compatibility but no longer changes whether this finding "
        "is emitted.",
        description_template="undocumented-export ratio rose: {old} → {new} (symbols exported without a public header)",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "uninstantiated_template_removed",
        _A,
        impact="A public template that was never instantiated into a binary symbol "
        "was removed from the headers. Source that instantiates it no longer "
        "compiles; there is no binary footprint, so only source replay sees "
        "it. A source/API break.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.REMOVED,
    ),
    _E(
        "unversioned_exported_symbol",
        _R,
        impact="The library defines a symbol-versioning scheme (a version script /"
        " .gnu.version_d table) yet exports this symbol without a version node. "
        "Unversioned exports cannot be evolved compatibly later — consumers bind "
        "to the bare name with no version guarantee, so a future versioned "
        "release silently changes what they resolve to. Add the symbol to the "
        "version script (or hide it if it is not public API). A single-release "
        "hygiene risk, never on its own an artifact-proven ABI break.",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
]
