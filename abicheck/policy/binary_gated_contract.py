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

"""ADR-084: which ``API_BREAK`` kinds a binary-only run may keep as ``API_BREAK``.

Every kind in :data:`~abicheck.change_registry.API_BREAK_KINDS` is a claim
about the *source* contract. Some of those kinds can be produced from binary
or debug-info input alone (``type_removed`` from DWARF, ``field_renamed``,
``mandatory_template_param_added`` from exported names). For those, a run
with no header or source evidence used to report ``API_BREAK`` (exit 2) on the
strength of registry membership alone. That is a claim the run cannot back.

The three buckets below partition ``API_BREAK_KINDS`` exactly (the completeness
test in ``tests/test_binary_gated_contract.py`` fails on an unclassified kind):

* :data:`BINARY_GATED` -- producible at binary or debug-info depth. The gate
  in :func:`demote_unproven_binary_api_breaks` keeps such a finding as
  ``API_BREAK`` only when a header or source producer observed its subject
  (:func:`~abicheck.model.source_contract_evidence.has_observed_source_contract_evidence`).
  Otherwise it is demoted to ``COMPATIBLE_WITH_RISK`` and disclosed.
* :data:`HEADER_OR_SOURCE_GATED` -- the producer consumes only header, source
  or build data, so a binary-only run never produces the kind. Not demoted.
  Each entry in :data:`NON_BINARY_REASONS` names the input it needs.
* :data:`OUT_OF_AXIS` -- governed by its own evidence axis (a ``.pyi`` surface,
  a kABI symbol file) or a missing-evidence sentinel that is not a break claim.
  Not demoted; the gate has no opinion on them.

The demotion never removes a finding, and it never overrides a verdict. It
marks the finding with the per-finding ``modulation_rule``/``modulation_reason``
pair the report already discloses, and :func:`binary_gate_rule_provenance`
lets the ADR-067 disposition ledger carry the same rule and reason. The
verdict itself is a cap applied where the policy resolves a category
(``policy.classification.effective_category`` and
``policy.reclassify.effective_verdict_for_change``): a policy's relaxation or
escalation of the kind decides first, and the cap lowers only an ``API_BREAK``
the policy left standing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..change_registry import API_BREAK_KINDS
from ..model.change_catalog.kinds import ChangeKind
from ..model.source_contract_evidence import (
    has_observed_source_contract_evidence,
    observed_source_contract_names,
)
from .binary_gate_marker import BINARY_GATE_RULE_ID, is_binary_gate_demoted
from .rule_provenance import RuleProvenance

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model import AbiSnapshot
    from ..model.change import Change
    from ..model.declarations import Function, Variable
    from ..model.entities import EnumType, RecordType

    _Declaration = Function | Variable | RecordType | EnumType

__all__ = [
    "BINARY_GATED",
    "BINARY_GATE_REASON",
    "BINARY_GATE_RULE_ID",
    "HEADER_OR_SOURCE_GATED",
    "NON_BINARY_REASONS",
    "OUT_OF_AXIS",
    "binary_gate_rule_provenance",
    "demote_unproven_binary_api_breaks",
    "is_binary_gate_demoted",
]

#: The reason a demoted finding carries (``modulation_reason`` and the ledger
#: rule's ``reason``). It states what is missing, not what was wrong.
BINARY_GATE_REASON = (
    "no observed header or source contract evidence for this entity; a "
    "binary-level observation does not establish a source-level break"
)

BINARY_GATED: frozenset[ChangeKind] = frozenset(
    {
        # Probed at binary depth: exported or unexported symbols alone reach
        # API_BREAK today (ADR-084 section "Context").
        ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED,
        ChangeKind.INLINE_FUNCTION_REMOVED,
        ChangeKind.TYPE_REMOVED,
        ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT,
        ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
        # Hard-coded public_reachable before ADR-084; now from observed evidence.
        ChangeKind.STD_REEXPORT_REMOVED,
        ChangeKind.TAG_TYPE_RENAMED,
        # DWARF-level type, field, enum, member and function facts.
        ChangeKind.TYPE_BECAME_ABSTRACT,
        ChangeKind.TYPE_BECAME_FINAL,
        ChangeKind.TYPEDEF_REMOVED,
        ChangeKind.ENUM_BECAME_SCOPED,
        ChangeKind.ENUM_MEMBER_RENAMED,
        ChangeKind.FIELD_ACCESS_CHANGED,
        ChangeKind.FIELD_BECAME_CONST,
        ChangeKind.FIELD_LOST_MUTABLE,
        ChangeKind.FIELD_RENAMED,
        ChangeKind.METHOD_ACCESS_CHANGED,
        ChangeKind.VAR_ACCESS_CHANGED,
        ChangeKind.CTOR_EXPLICIT_ADDED,
        ChangeKind.FUNC_BECAME_INLINE,
        ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_ADDED,
        ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_CHANGED,
        ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED,
        ChangeKind.HIDDEN_FRIEND_REMOVED,
        ChangeKind.REMOVED_CONST_OVERLOAD,
        ChangeKind.SOURCE_LEVEL_KIND_CHANGED,
    }
)

#: Each kind's producer needs header, source or build data, which a
#: binary-only input does not carry. The reason names that input.
NON_BINARY_REASONS: Mapping[ChangeKind, str] = {
    ChangeKind.CONCEPT_TIGHTENED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.CONSTEXPR_VALUE_CHANGED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.DEFAULT_ARGUMENT_CHANGED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.PUBLIC_MACRO_REMOVED: "preprocessor macros: L4 source replay",
    ChangeKind.PUBLIC_MACRO_VALUE_CHANGED: "preprocessor macros: L4 source replay",
    ChangeKind.PUBLIC_TYPEDEF_REMOVED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED: "L4 source replay (buildsource/source_diff)",
    ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH: "header plus build data (cross_source_checks)",
    ChangeKind.ODR_TYPE_VARIANT: "per-TU source ABI (cross_source_checks)",
    ChangeKind.CXX_STANDARD_FLOOR_RAISED: "build configuration (diff_build_config)",
    ChangeKind.CONSTANT_CHANGED: "header macro and constant data (compare/constants)",
    ChangeKind.CONSTANT_REMOVED: "header macro and constant data (compare/constants)",
    ChangeKind.PARAM_DEFAULT_VALUE_REMOVED: "header default-argument spellings (parameter_facts)",
}

#: Kinds governed by their own evidence axis, or a sentinel that is not a
#: break claim. The reason names the axis.
OUT_OF_AXIS_REASONS: Mapping[ChangeKind, str] = {
    ChangeKind.EVIDENCE_REQUIRED_MISSING: "missing-evidence sentinel, not a break claim",
    ChangeKind.KABI_EXPORT_TYPE_CHANGED: "Linux kABI symbol-file axis (diff_kabi)",
    ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_CLASS_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_DEFAULT_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_FUNCTION_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_METHOD_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_OVERLOAD_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_PARAMETER_ADDED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_PARAMETER_REMOVED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_PARAMETER_RENAMED: "Python .pyi surface axis",
    ChangeKind.PYTHON_API_STUB_INVALID: "Python .pyi surface axis",
}

HEADER_OR_SOURCE_GATED: frozenset[ChangeKind] = frozenset(NON_BINARY_REASONS)
OUT_OF_AXIS: frozenset[ChangeKind] = frozenset(OUT_OF_AXIS_REASONS)


@dataclass(frozen=True)
class _SubjectIndex:
    """Declarations of both snapshots, keyed by every spelling a change may use.

    Built once per gate run, and only when a candidate finding exists, so a
    run with no ``BINARY_GATED`` finding pays nothing.
    """

    #: ``spelling -> ((declaration, its snapshot's from_headers), ...)``.
    by_key: Mapping[str, tuple[tuple[_Declaration, bool], ...]]
    #: Whether any declaration on either side, or any public-header entity of
    #: either side's L4 source pack, carries observed header or source
    #: evidence. The fallback for a subject that resolves to no declaration:
    #: in a run that parsed headers, an unresolved name is a spelling the
    #: index cannot match, not proof that nothing was observed.
    run_has_source_evidence: bool
    #: Names of the public-header entities an L4 source pack observed on
    #: either side (:func:`observed_source_contract_names`). A removed
    #: declaration exists only in the old side's declarations, but its source
    #: entity is what the source producer saw in the header.
    source_names: frozenset[str]

    @classmethod
    def of(cls, snapshots: Iterable[AbiSnapshot | None]) -> _SubjectIndex:
        by_key: dict[str, list[tuple[_Declaration, bool]]] = {}
        run_has_source_evidence = False
        source_names: set[str] = set()
        for snapshot in snapshots:
            if snapshot is None:
                continue
            header_parsed = bool(snapshot.from_headers)
            decls = snapshot.declarations
            for group in (decls.functions, decls.variables, decls.types, decls.enums):
                for decl in group:
                    if (
                        not run_has_source_evidence
                        and has_observed_source_contract_evidence(
                            decl, header_parsed=header_parsed
                        )
                    ):
                        run_has_source_evidence = True
                    for key in _keys_of(decl):
                        by_key.setdefault(key, []).append((decl, header_parsed))
            names = observed_source_contract_names(snapshot)
            if names:
                run_has_source_evidence = True
                source_names.update(names)
        return cls(
            by_key={k: tuple(v) for k, v in by_key.items()},
            run_has_source_evidence=run_has_source_evidence,
            source_names=frozenset(source_names),
        )

    def subjects(self, symbol: str) -> tuple[tuple[_Declaration, bool], ...]:
        """Declarations *symbol* names, or whose owner it names (a member)."""
        found = list(self.by_key.get(symbol, ()))
        if "::" in symbol:
            owner = symbol.rsplit("::", 1)[0]
            found.extend(self.by_key.get(owner, ()))
        return tuple(found)

    def observed_in_source(self, symbol: str) -> bool:
        """Whether an L4 source pack saw *symbol*, or its owner, in a public header."""
        if symbol in self.source_names:
            return True
        return "::" in symbol and symbol.rsplit("::", 1)[0] in self.source_names


def _keys_of(decl: object) -> tuple[str, ...]:
    keys: list[str] = []
    for attribute in ("name", "mangled", "qualified_name"):
        value = getattr(decl, attribute, None)
        if isinstance(value, str) and value:
            keys.append(value)
    return tuple(keys)


def _has_source_evidence(change: Change, index: _SubjectIndex) -> bool:
    if not change.symbol:
        return index.run_has_source_evidence
    if index.observed_in_source(change.symbol):
        return True
    subjects = index.subjects(change.symbol)
    if subjects:
        return any(
            has_observed_source_contract_evidence(decl, header_parsed=parsed)
            for decl, parsed in subjects
        )
    return index.run_has_source_evidence


def demote_unproven_binary_api_breaks(
    changes: list[Change],
    old: AbiSnapshot | None,
    new: AbiSnapshot,
) -> list[Change]:
    """Demote each ``BINARY_GATED`` ``API_BREAK`` finding without source evidence.

    A candidate is a finding whose intrinsic kind is in :data:`BINARY_GATED`
    and that carries no per-finding ``effective_verdict`` yet (a pipeline
    modulation that already decided its category keeps it). Its subject is
    resolved on either side; the finding stays ``API_BREAK`` when that
    declaration, or an L4 source pack's public-header entity, has observed
    header or source evidence. An unresolved subject takes the run-level answer
    (see :class:`_SubjectIndex`).

    Marks the demoted findings in place and returns them. Nothing is removed
    from *changes*, and no verdict is written (see the module docstring).
    """
    demoted: list[Change] = []
    index: _SubjectIndex | None = None
    for change in changes:
        kind = getattr(change, "kind", None)
        if kind not in BINARY_GATED or kind not in API_BREAK_KINDS:
            continue
        if getattr(change, "effective_verdict", None) is not None:
            continue
        if index is None:
            index = _SubjectIndex.of((old, new))
        if _has_source_evidence(change, index):
            continue
        change.modulation_reason = BINARY_GATE_REASON
        change.modulation_rule = BINARY_GATE_RULE_ID
        demoted.append(change)
    return demoted


def binary_gate_rule_provenance(change: object) -> RuleProvenance | None:
    """The ADR-067 rule a gate-demoted *change* rests on, or ``None``.

    ``finalize_ledger`` attaches this to the kept record, so the audit names
    the rule and reason for every demotion the report shows as
    ``COMPATIBLE_WITH_RISK``.
    """
    if not is_binary_gate_demoted(change):
        return None
    return RuleProvenance(
        rule_id=BINARY_GATE_RULE_ID,
        reason=BINARY_GATE_REASON,
    )
