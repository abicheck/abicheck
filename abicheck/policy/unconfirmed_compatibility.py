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

"""ADR-090: a clean verdict without type-level evidence is ``UNCONFIRMED``.

A comparison is *unconfirmed* when all of these hold (ADR-090 D1):

(a) its change-derived verdict is a compatibility claim -- ``NO_CHANGE``,
    ``COMPATIBLE`` or ``COMPATIBLE_WITH_RISK``. An observed ``API_BREAK`` or
    ``BREAKING`` is never touched;
(b) at least one compared side has no type-level evidence
    (:mod:`abicheck.model.type_evidence`);
(c) the two sides are not one identical input (byte-identical binaries or
    identical persisted snapshot content), which is ``NO_CHANGE`` by
    construction, not an evidence gap.

``UNCONFIRMED`` belongs to the *report's* compatibility vocabulary (the JSON
``verdict``, ``run_outcome.compatibility``, every headline). It is not a
:class:`~abicheck.model.change_catalog.registry.Verdict` member: the
change-kind verdict sets derive from that enum, and ``DiffResult.verdict``
stays the observed, change-derived fact (ADR-090 maintainer answer 2).

The exit axis is ``1``, folded with ``max`` like contract coverage and
analysis assurance, so it never lowers a ``2``/``4``. ``assurance.unconfirmed:
warn`` keeps the label and contributes ``0``. No setting restores the clean
label for a triggered run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.change_catalog.registry import Verdict

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = [
    "COMPATIBILITY_CLAIMS",
    "UNCONFIRMED",
    "UNCONFIRMED_MODES",
    "UNCONFIRMED_REMEDY",
    "is_unconfirmed",
    "report_compatibility",
    "unconfirmed_block",
    "unconfirmed_exit_contribution",
    "unconfirmed_summary",
]

#: The report-vocabulary value for a triggered comparison.
UNCONFIRMED = "UNCONFIRMED"

#: ``assurance.unconfirmed`` values; ``fail`` is the default.
UNCONFIRMED_MODES: tuple[str, ...] = ("fail", "warn")

#: Change-derived verdicts that assert compatibility (D1(a)).
COMPATIBILITY_CLAIMS: frozenset[Verdict] = frozenset(
    {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}
)

#: D5: the two remedies, in this order.
UNCONFIRMED_REMEDY = (
    "supply public headers for both sides (--header old=... --header new=...), "
    "or compare unstripped debug builds (-g, without strip/strip --strip-debug)"
)


def is_unconfirmed(result: DiffResult) -> bool:
    """Whether *result* meets ADR-090 D1 (a), (b) and (c)."""
    # Read defensively: a duck-typed result (a report view, a test double)
    # carrying no fact was never evaluated, so it asserts nothing new.
    facts = getattr(result, "type_evidence", None)
    if facts is None or facts.old is None:
        # Never evaluated (a hand-built result), or a `--no-baseline` audit,
        # which compares nothing and so asserts no compatibility.
        return False
    if result.verdict not in COMPATIBILITY_CLAIMS:
        return False
    lacking = facts.sides_without_type_evidence()
    if not lacking:
        return False
    # D1(c): only one identical input on both sides is NO_CHANGE by
    # construction. A one-sided gap means the effective inputs differ, so an
    # identical-bytes flag cannot exempt it.
    return not (facts.identical_inputs and len(lacking) == 2)


def report_compatibility(result: DiffResult) -> str:
    """The report's compatibility value: ``UNCONFIRMED`` or the verdict."""
    if is_unconfirmed(result):
        return UNCONFIRMED
    verdict = result.verdict
    return verdict.value if hasattr(verdict, "value") else str(verdict)


def unconfirmed_exit_contribution(result: DiffResult) -> int:
    """The ``0``/``1`` exit axis: ``1`` for a triggered ``fail``-mode run."""
    if getattr(result, "unconfirmed_mode", "fail") == "warn":
        return 0
    return 1 if is_unconfirmed(result) else 0


def unconfirmed_summary(result: DiffResult) -> str:
    """One sentence naming the missing evidence and the remedies."""
    facts = result.type_evidence
    sides = facts.sides_without_type_evidence() if facts is not None else ()
    which = " and ".join(sides) if sides else "a compared"
    noun = "sides have" if len(sides) > 1 else "side has"
    return (
        f"compatibility is UNCONFIRMED: the {which} {noun} no type-level "
        f"evidence (no debug info, no header AST), so signature, enum and "
        f"layout changes on exported symbols cannot be seen; "
        f"{result.verdict.value} describes only the symbol table. "
        f"To confirm, {UNCONFIRMED_REMEDY}"
    )


def unconfirmed_block(result: DiffResult) -> dict[str, object] | None:
    """The report's ``unconfirmed`` block, or ``None`` when not triggered."""
    facts = result.type_evidence
    if facts is None or not is_unconfirmed(result):
        return None
    return {
        "observed_verdict": result.verdict.value,
        "sides_without_type_evidence": list(facts.sides_without_type_evidence()),
        "mode": result.unconfirmed_mode,
        "exit_contribution": unconfirmed_exit_contribution(result),
        "remedy": UNCONFIRMED_REMEDY,
    }
