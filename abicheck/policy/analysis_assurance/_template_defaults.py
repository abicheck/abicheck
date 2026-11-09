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

"""Assurance gap for template-parameter defaults (ADR-085 D3).

A class-template pattern carries its parameters' default spellings only when
the header backend recorded them (``RecordType.template_param_defaults_fact``,
ADR-085 D1). castxml cannot record them, and a pre-v62 snapshot predates the
field. For such a pattern a default-argument change is not assessed, so a run
over it must say so instead of reading as a clean comparison.

This is a coverage gap, not a verdict: the compare engine still answers only
from the facts each side recorded. It reports reduced assurance on the one
template-defaults axis, the way ``_graph_completeness`` reports a partial L5
pass, so the rollup reads ``partial`` rather than ``complete``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...model.availability import FactStatus
from ...model.template_default_table import split_template_default_key

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding

__all__ = [
    "TEMPLATE_DEFAULTS_DIMENSION",
    "template_default_findings",
]

TEMPLATE_DEFAULTS_DIMENSION = "template parameter defaults"


def _unrecorded_pattern_count(snap: AbiSnapshot) -> int:
    """Class-template patterns of *snap* whose defaults are not PRESENT.

    The clang walk marks every declaration nested inside a pattern as a
    pattern too. A record whose qualified name sits under another pattern's
    name is such a descendant, not a template of its own, so it is skipped.
    """
    patterns = [rec for rec in snap.declarations.types if rec.is_template_pattern]
    names = {(rec.qualified_name or rec.name) for rec in patterns}
    count = 0
    for rec in patterns:
        qualified = rec.qualified_name or rec.name
        if any(_is_nested_in(qualified, parent) for parent in names):
            continue
        fact = rec.template_param_defaults_fact
        if fact is None or fact.status is not FactStatus.PRESENT:
            count += 1
    return count


def _unrecorded_function_pattern_count(snap: AbiSnapshot) -> tuple[int, int]:
    """Function-template patterns of *snap* whose defaults were not recorded.

    A declaration whose fact is absent or NOT_COLLECTED is an ordinary function
    (no template pattern to record), so it does not count. A pattern the clang
    backend saw but could not spell a default of carries a non-PRESENT fact
    (UNSUPPORTED); one whose extraction failed carries FAILED. Returns
    ``(unspellable, failed)``; redeclarations share one identity and count once.
    """
    unspellable: set[object] = set()
    failed: set[object] = set()
    for fn in snap.declarations.functions:
        fact = fn.template_param_defaults_fact
        if fact is None or fact.status in (
            FactStatus.NOT_COLLECTED,
            FactStatus.PRESENT,
        ):
            continue
        key = fn.entity_id if fn.entity_id is not None else id(fn)
        (unspellable if fact.status is FactStatus.UNSUPPORTED else failed).add(key)
    return len(unspellable), len(failed)


def _is_nested_in(qualified: str, parent: str) -> bool:
    """Whether *qualified* is a member of *parent*, on ``::`` boundaries."""
    parts, owner = qualified.split("::"), parent.split("::")
    return len(parts) > len(owner) and parts[: len(owner)] == owner


#: Families a table entry can name; the clang backend's own pattern facts
#: carry class and function templates, so only these two are table-only there.
_ALL_FAMILIES = frozenset({"class", "function", "alias", "variable"})
_UNMODELLED_BY_CLANG = frozenset({"alias", "variable"})


def _unknown_table_count(
    table: dict[str, tuple[str | None, ...] | None], families: frozenset[str]
) -> int:
    """Templates of *families* in the template-default table whose defaults
    are not known (a default the AST walk could not spell, or declarations
    that disagree)."""
    return sum(
        1
        for key, row in table.items()
        if row is None and split_template_default_key(key)[0] in families
    )


def _table_note(label: str, count: int) -> str:
    return (
        "template-parameter defaults (a default could not be spelled): "
        f"{count} template(s) in the public headers of the {label} snapshot "
        "carry a default the clang AST walk could not spell or key (an "
        "expression such as sizeof(T), declarations that disagree, or a member "
        "template of an explicit or partial specialization), so a "
        "default-argument change in them is not assessed"
    )


def _castxml_header_note(label: str) -> str:
    return (
        "template-parameter defaults (castxml cannot record them): its clang "
        f"join did not run, and the public headers of the {label} snapshot "
        "declare template-parameter defaults that castxml does not record, so a "
        "default-argument change in them is not assessed"
    )


def _clang_alias_variable_note(label: str) -> str:
    return (
        "template-parameter defaults (the clang backend does not model alias "
        f"or variable templates): the public headers of the {label} snapshot "
        "declare an alias or variable template with a default template "
        "argument, so a default-argument change in one is not assessed"
    )


def _side_note(label: str, snap: AbiSnapshot, count: int) -> str:
    if snap.ast_producer == "castxml":
        reason = "castxml cannot record them"
    else:
        reason = (
            "not recorded by this snapshot (it predates schema v62, or a "
            "default in the pattern could not be spelled)"
        )
    return (
        f"template-parameter defaults ({reason}): {count} class template "
        f"pattern(s) in the {label} snapshot carry no recorded defaults, so "
        "a default-argument change in them is not assessed"
    )


def _function_side_note(label: str, count: int, reason: str) -> str:
    return (
        f"template-parameter defaults ({reason}): "
        f"{count} function template pattern(s) in the {label} snapshot carry "
        "no recorded defaults, so a default-argument change in them is not "
        "assessed"
    )


def template_default_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """Findings for the template-defaults axis of the rollup.

    *gap* is True when either side has at least one class- or function-template
    pattern without recorded defaults, is a castxml run whose headers declare a
    template parameter default castxml does not record, or is a clang run whose
    headers declare an alias or variable template with a default, which the
    clang backend does not model (ADR-085 D3). *notes*
    names each such side. A snapshot with neither contributes nothing.
    """
    findings: list[UnobservedFinding] = []
    sides: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    for label, snap in sides:
        if snap is None:
            continue
        notes: list[str] = []
        table = snap.cxx_template_defaults
        if snap.ast_producer == "castxml":
            if table is not None:
                # The clang join read every family: only a template whose
                # defaults it could not spell is unassessed.
                unknown = _unknown_table_count(table, _ALL_FAMILIES)
                if unknown:
                    notes.append(_table_note(label, unknown))
                findings.extend(_as_findings(label, notes))
                continue
            if snap.template_default_header_scan:
                notes.append(_castxml_header_note(label))
                findings.extend(_as_findings(label, notes))
                continue
        if snap.ast_producer == "clang":
            if table is not None:
                unknown = _unknown_table_count(table, _UNMODELLED_BY_CLANG)
                if unknown:
                    notes.append(_table_note(label, unknown))
            elif snap.template_default_header_scan:
                notes.append(_clang_alias_variable_note(label))
        count = _unrecorded_pattern_count(snap)
        if count:
            notes.append(_side_note(label, snap, count))
        unspellable, failed = _unrecorded_function_pattern_count(snap)
        if unspellable:
            notes.append(
                _function_side_note(
                    label, unspellable, "a default could not be spelled"
                )
            )
        if failed:
            notes.append(_function_side_note(label, failed, "their extraction failed"))
        findings.extend(_as_findings(label, notes))
    return findings


def _as_findings(label: Side, notes: list[str]) -> list[UnobservedFinding]:
    return [UnobservedFinding(TEMPLATE_DEFAULTS_DIMENSION, label, n) for n in notes]
