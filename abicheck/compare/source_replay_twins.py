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

"""The one table of source-replay twins: which L4/L5 source-replay kinds
restate an observation the header tier (or the binary tier) already reports.

The L4 source replay (``buildsource.source_diff``) and the L5 graph findings
(``buildsource.source_graph_findings``) tag every finding ``source_only``
(``buildsource.evidence_report``). Several of their kinds observe the same
declaration change as a header-tier kind, under a different ``ChangeKind`` and
different wording, so the exact and identity keys in
:mod:`abicheck.compare.finding_dedup` never see the pair as equal.

Every replay kind must be accounted for in exactly one of three places:

* :data:`REPLAY_TWINS` -- a *duplicate*: the replay copy is dropped when a
  header-tier (or binary-tier) finding names the same entity, one for one.
* :data:`DISTINCT_FACETS` -- a *different facet* of the same edit: both kinds
  survive, and the reason says why.
* :data:`REPLAY_ONLY` -- no header-tier counterpart exists in this tree; the
  reason says why.

``tests/test_source_replay_twin_table.py`` enforces that with an AST scan of
:data:`REPLAY_EMITTER_MODULES`, so a new replay kind cannot slip past the
dedup pass unclassified.

The header tier is the owner in every duplicate pair: it is artifact-anchored
(ADR-028 D3) and carries the declaration's own facts, which the replay copy
does not. The one exception is a verdict: when the replay copy's default verdict
is strictly more severe (a default-argument change is an API break in the
replay and only a compatible note in the header tier), the replay copy is the
survivor, so a twin never weakens the verdict. A duplicate is matched one for one by name, so an extra replay
finding with no twin stays visible, and every dropped copy is recorded as
``deduplicated`` by the disposition pipeline (ADR-067).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from operator import attrgetter
from typing import Literal

from ..demangle import demangle
from ..model.change import Change
from ..model.change_catalog.assembled import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
)
from ..model.change_catalog.kinds import ChangeKind

#: The evidence bucket the L4 source replay tags its findings with (set by
#: ``buildsource.evidence_report`` before the disposition pipeline runs).
SOURCE_REPLAY_EVIDENCE = "source_only"

#: Modules whose ``ChangeKind`` references are the source-replay emissions
#: this table must cover: the L4 surface diff and the L5 graph findings, the
#: two producers ``evidence_report`` tags ``source_only``.
REPLAY_EMITTER_MODULES: tuple[str, ...] = (
    "abicheck/buildsource/source_diff.py",
    "abicheck/buildsource/source_graph_findings.py",
)

KeyOf = Callable[[Change], str | None]


_symbol: KeyOf = attrgetter("symbol")


def _binary_symbol_of_mapping(change: Change) -> str | None:
    """A mapping finding's old binary symbol: the export it used to map to."""
    old = change.old_value
    return old if isinstance(old, str) else None


def _inline_removal_name(change: Change) -> str:
    """The bare qualified name a function-level finding is about.

    The header tier names the function by its mangled symbol. Its demangled form
    (``demo::f(int)``) is filled in by enrichment that runs only after this step,
    so it is demangled here. The source replay names the function by qualified
    name (``demo::f``). Stripping the parameter list lines the two up.
    """
    name = change.demangled_symbol or demangle(change.symbol) or change.symbol
    return name.split("(", 1)[0]


def _typedef_header_name(change: Change) -> str:
    """The qualified name a header-tier typedef finding is about.

    The header tier names the alias by its bare spelling (``old_t``) and keeps
    the namespace in its entity id; the replay names it by qualified name
    (``demo::old_t``). Rendering the entity id gives the replay's spelling. A
    finding with no entity id falls back to its symbol, so it can still pair
    with a replay finding that spells the same name.
    """
    from ..model.semantic_ir_legacy_adapter import render_display_name_or_leaf

    if change.entity_id is None:
        return change.symbol
    return render_display_name_or_leaf(change.entity_id)


#: Header or binary kinds that report a declaration whose binary symbol is gone.
#: ``SOURCE_DECL_BINARY_SYMBOL_MISMATCH`` and ``SOURCE_TO_BINARY_MAPPING_CHANGED``
#: restate that loss from the source side, whichever of these the binary tier
#: chose (deleted, removed, or no longer exported).
_BINARY_LOSS_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_DELETED,
        ChangeKind.FUNC_DELETED_DWARF,
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        ChangeKind.VAR_REMOVED,
        ChangeKind.VAR_VISIBILITY_CHANGED,
    }
)


@dataclass(frozen=True)
class ReplayTwin:
    """One duplicate pair: a replay kind and the header/binary kinds it repeats.

    ``replay_key`` and ``header_key`` derive the name each side is matched on.
    Either may return ``None`` for a finding it cannot name, which then never
    pairs.

    ``owner`` names the survivor when the pair matches: ``"header"`` (the
    default, see :func:`drop_replay_twins`) or ``"replay"``, for a pair whose
    replay kind names the observation more precisely than the header kind does
    (an uninstantiated template is not a hidden function).
    """

    replay: ChangeKind
    header: frozenset[ChangeKind]
    replay_key: KeyOf
    header_key: KeyOf
    owner: Literal["header", "replay"] = "header"


@dataclass(frozen=True)
class DistinctFacet:
    """A replay kind that reports a different facet of an edit a header kind
    also reports. Both survive; ``reason`` says why they are not duplicates."""

    replay: ChangeKind
    header: frozenset[ChangeKind]
    reason: str


REPLAY_TWINS: tuple[ReplayTwin, ...] = (
    # The header-tier constant owns a constexpr value change (qualified name on
    # both sides). L4 emits CONSTEXPR_VALUE_CHANGED only for C++ constexpr
    # variables (clang_node_predicates: VarDecl with ``constexpr``).
    ReplayTwin(
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
        frozenset({ChangeKind.CONSTANT_CHANGED}),
        _symbol,
        _symbol,
    ),
    # A header-only inline function removal, reported by both tiers.
    ReplayTwin(
        ChangeKind.INLINE_FUNCTION_REMOVED,
        frozenset({ChangeKind.INLINE_FUNCTION_REMOVED}),
        _inline_removal_name,
        _inline_removal_name,
    ),
    # A template removed from the headers with no instantiation in any binary.
    # The header tier reports the same removal as a hidden (inline) function,
    # which is not what was removed, so the replay's kind survives.
    ReplayTwin(
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        frozenset({ChangeKind.INLINE_FUNCTION_REMOVED}),
        _inline_removal_name,
        _inline_removal_name,
        owner="replay",
    ),
    # A default-argument edit: the header tier reports it per parameter
    # (PARAM_DEFAULT_VALUE_CHANGED, mangled symbol); the replay per function
    # (qualified name). Both reduce to the function's bare name.
    ReplayTwin(
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
        frozenset({ChangeKind.PARAM_DEFAULT_VALUE_CHANGED}),
        _inline_removal_name,
        _inline_removal_name,
    ),
    # A typedef removed or retargeted: header kind on the bare alias name (the
    # namespace lives in the entity id), replay kind on the qualified name.
    ReplayTwin(
        ChangeKind.PUBLIC_TYPEDEF_REMOVED,
        frozenset({ChangeKind.TYPEDEF_REMOVED}),
        _symbol,
        _typedef_header_name,
    ),
    ReplayTwin(
        ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
        frozenset({ChangeKind.TYPEDEF_BASE_CHANGED}),
        _symbol,
        _typedef_header_name,
    ),
    # A declaration whose binary symbol is gone, and the L5 mapping change for
    # the same loss: both restate the binary-tier removal of one mangled symbol.
    ReplayTwin(
        ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
        _BINARY_LOSS_KINDS,
        _symbol,
        _symbol,
    ),
    ReplayTwin(
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
        _BINARY_LOSS_KINDS,
        _binary_symbol_of_mapping,
        _symbol,
    ),
)

DISTINCT_FACETS: tuple[DistinctFacet, ...] = (
    DistinctFacet(
        ChangeKind.TEMPLATE_BODY_CHANGED,
        frozenset({ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED}),
        "a template-parameter default edit also moves the template's declaration "
        "text, so the replay's body fingerprint moves too; the body fingerprint "
        "and the default facet are kept as two findings by design",
    ),
)

#: Replay kinds with no header-tier or binary-tier counterpart in this tree.
#: Each reason is one line; a kind moves out of here once a counterpart exists.
REPLAY_ONLY: dict[ChangeKind, str] = {
    ChangeKind.CONCEPT_TIGHTENED: "the header tier has no concept-constraint detector",
    ChangeKind.GENERATED_HEADER_CHANGED: "generated-header content is diffed only by the replay",
    ChangeKind.INLINE_BODY_CHANGED: "inline body fingerprints exist only in the replay",
    ChangeKind.ODR_SOURCE_CONFLICT: "cross-TU source ODR disagreement has no header-tier kind",
    ChangeKind.PUBLIC_MACRO_REMOVED: "no header-tier macro detector; macros come from the replay",
    ChangeKind.PUBLIC_MACRO_VALUE_CHANGED: "no header-tier macro detector; macros come from the replay",
    ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE: "replay bookkeeping about its own coverage, not an ABI fact",
    ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL: "L5 graph reachability fact; the header tier has no graph",
    ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED: "L5 call-graph fact; the header tier has no call graph",
    ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED: "L5 ownership fact; the binary tier does not record source owners",
    ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API: "L5 reachability fact for generated headers",
    ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT: "L5 include-graph fact; the header tier has no include graph",
    ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED: "L5 dependency fact; the header tier has no dependency graph",
    ChangeKind.PUBLIC_REACHABILITY_CHANGED: "L5 reachability fact; the header tier has no graph",
    ChangeKind.TARGET_DEPENDENCY_ADDED: "L5 build-target dependency fact; the header tier does not read targets",
}


def _severity(kind: ChangeKind) -> int:
    """The default verdict rank of *kind* (4 = BREAKING ... 1 = COMPATIBLE)."""
    if kind in BREAKING_KINDS:
        return 4
    if kind in API_BREAK_KINDS:
        return 3
    if kind in RISK_KINDS:
        return 2
    return 1


def _drop_one_twin(changes: list[Change], twin: ReplayTwin) -> list[Change]:
    """Collapse each matched pair to one copy, one for one.

    The header copy survives, unless the replay copy states a strictly more
    severe default verdict: a twin never weakens the verdict, so the stronger
    copy is the one kept. Equal severity keeps the header copy.
    """
    header_by_key: dict[str, list[int]] = {}
    for i, c in enumerate(changes):
        if c.kind in twin.header and c.evidence_category != SOURCE_REPLAY_EVIDENCE:
            key = twin.header_key(c)
            if key is not None:
                header_by_key.setdefault(key, []).append(i)
    dropped: set[int] = set()
    for i, c in enumerate(changes):
        if c.kind is not twin.replay or c.evidence_category != SOURCE_REPLAY_EVIDENCE:
            continue
        key = twin.replay_key(c)
        candidates = header_by_key.get(key) if key is not None else None
        if not candidates:
            continue
        j = candidates.pop(0)
        replay_wins = twin.owner == "replay" or (
            _severity(c.kind) > _severity(changes[j].kind)
        )
        dropped.add(j if replay_wins else i)
    return [c for i, c in enumerate(changes) if i not in dropped]


def drop_replay_twins(
    changes: Iterable[Change], twins: tuple[ReplayTwin, ...] = REPLAY_TWINS
) -> list[Change]:
    """Drop each source-replay copy that a header/binary finding already reports.

    Applies every duplicate pair in *twins* in turn. A non-replay finding is
    never dropped, and a replay finding with no twin is kept.
    """
    result = list(changes)
    for twin in twins:
        result = _drop_one_twin(result, twin)
    return result
