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

"""ADR-065 D1's *acquisition state* -- the typed, per-member record of which
members of a comparison scope were expected, which were actually supplied,
and which were compared (S2).

Four things this ADR keeps apart live in four places: the analysis
*boundary* is the typed request, the *selection* and *expected inventory*
are the resolved plan, and the **acquisition state** is this record -- the
typed result's own account, per expected member, separate from any policy
verdict. A *support-promise change* (a member confirmed absent from a
complete inventory) is a fifth thing again: a finding under contract policy
(S3), never inferred from this record alone.

D2 is the rule every reader of this record must honor: **unmatched is not
removed**. A member present on one side and absent on the other is
``NOT_SUPPLIED`` with a recorded reason; it becomes a removal only when the
side that lacks it has a *proven-complete* inventory
(:attr:`ScopeAcquisitionRecord.proven_removed_members`), and an addition
only when the *old* side's inventory is proven complete (the symmetric
rule -- a partial old input cannot prove the member was absent before).
``OUT_OF_SCOPE`` is reserved for members the run did not *select* (D9's
narrow current-artifact inference), and contributes nothing to any verdict
or outcome; a selected member whose counterpart was never produced, or
whose extraction failed, keeps its own state and flows into D6's
incompleteness outcome instead -- never demoted to out-of-scope, never
promoted to a removal.

A leaf ``model`` module: no imports beyond the standard library, so
``policy`` (the completeness axis), ``workflows`` (the release builder), and
``report`` (the rendered section) can all depend on it without a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "SCOPE_ACQUISITION_SCHEMA_VERSION",
    "UNCHECKED_STATES",
    "AcquisitionState",
    "InventoryCompleteness",
    "MemberAcquisition",
    "ScopeAcquisitionRecord",
    "SideInventory",
]

#: Self-contained sub-object version, the same convention
#: ``policy.outcome.RUN_OUTCOME_SCHEMA_VERSION`` uses.
SCOPE_ACQUISITION_SCHEMA_VERSION = "1.0"


class AcquisitionState(str, Enum):
    """Per expected member: what happened to it in *this* run (ADR-065 D1).

    Exactly one state per member; the partitions are pairwise disjoint and
    sum to the expected set, which :class:`ScopeAcquisitionRecord`'s own
    constructor enforces by rejecting a duplicate member key.
    """

    #: Both sides supplied the member and its comparison completed.
    AVAILABLE = "available"
    #: A declared inventory expected the member and no side produced it
    #: (needs a declared inventory -- S3/S4; S2 never emits it).
    EXPECTED_NOT_PRODUCED = "expected_not_produced"
    #: The member was supplied but its extraction/comparison failed --
    #: *also* an operational error (D8).
    FAILED = "failed"
    #: One side supplied the member and the other did not, and nothing
    #: proves the lacking side's inventory complete -- so this is what D2
    #: calls *unmatched*, never removed/added.
    NOT_SUPPLIED = "not_supplied"
    #: An artifact this build cannot analyze (a stored snapshot newer than
    #: this reader, a binary format the dumper has no backend for).
    UNSUPPORTED = "unsupported"
    #: Not selected by this run (D9's narrow current-artifact inference):
    #: contributes nothing to the verdict or to the completeness axis.
    OUT_OF_SCOPE = "out_of_scope"
    #: Could pair with more than one counterpart (D3) -- a diagnostic, not
    #: a guess (S1 emits it; S2's filename matching never produces one).
    AMBIGUOUS = "ambiguous"


#: The states D6 counts as "a selected, expected member that did not reach
#: a completed comparison" -- the incompleteness signal. ``AVAILABLE`` did
#: reach one; ``OUT_OF_SCOPE`` was never selected.
UNCHECKED_STATES: frozenset[AcquisitionState] = frozenset(
    {
        AcquisitionState.EXPECTED_NOT_PRODUCED,
        AcquisitionState.FAILED,
        AcquisitionState.NOT_SUPPLIED,
        AcquisitionState.UNSUPPORTED,
        AcquisitionState.AMBIGUOUS,
    }
)


class InventoryCompleteness(str, Enum):
    """Whether one side's member inventory is *proven* complete for the
    analysis boundary (D2) -- the precondition for reading an unmatched
    member on the other side as a removal/addition.
    """

    #: A full package inventory, a matrix whose expected members all
    #: resolved, a stored package's own declared composition, or an
    #: explicit user statement.
    PROVEN = "proven"
    #: A live directory listing, a direct file pair, an extracted archive
    #: (``package.py`` returns directories, never a declared component
    #: inventory -- S3), or anything else that cannot prove absence.
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class SideInventory:
    """One side's completeness proof and where it came from."""

    completeness: InventoryCompleteness
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return {"completeness": self.completeness.value, "provenance": self.provenance}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SideInventory:
        return cls(
            completeness=InventoryCompleteness(data["completeness"]),
            provenance=str(data.get("provenance", "")),
        )


@dataclass(frozen=True)
class MemberAcquisition:
    """One expected member's acquisition state, with the evidence behind it."""

    #: The release-matching key (``_canonical_library_key``), stable across
    #: sides.
    member: str
    state: AcquisitionState
    old_present: bool
    new_present: bool
    #: Human-readable why (an error message, "no counterpart on NEW", ...).
    reason: str = ""
    #: The user-facing name (a real basename) when it differs from *member*.
    display_name: str = ""

    @property
    def name(self) -> str:
        return self.display_name or self.member

    def to_dict(self) -> dict[str, Any]:
        return {
            "member": self.member,
            "name": self.name,
            "state": self.state.value,
            "old_present": self.old_present,
            "new_present": self.new_present,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemberAcquisition:
        member = str(data["member"])
        name = str(data.get("name", member))
        return cls(
            member=member,
            state=AcquisitionState(data["state"]),
            old_present=bool(data.get("old_present", False)),
            new_present=bool(data.get("new_present", False)),
            reason=str(data.get("reason", "")),
            display_name=name if name != member else "",
        )


@dataclass(frozen=True)
class ScopeAcquisitionRecord:
    """The whole run's acquisition record: every expected member exactly
    once, plus each side's inventory proof and how the selection was made.

    ``selection`` names the selection rule the run applied:
    ``"direct_pair"`` (two files, one pair -- the scalar case),
    ``"all_expected"`` (every discovered member on either side is selected),
    or ``"current_artifact"`` (D9: NEW supplied exactly one artifact with
    exactly one OLD counterpart, so the other OLD members are
    ``OUT_OF_SCOPE``).
    """

    members: tuple[MemberAcquisition, ...]
    old_inventory: SideInventory
    new_inventory: SideInventory
    selection: str
    selection_reason: str = ""
    schema_version: str = field(default=SCOPE_ACQUISITION_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for m in self.members:
            if m.member in seen:
                raise ValueError(
                    f"scope acquisition record lists member {m.member!r} twice -- "
                    "the per-member partitions must be pairwise disjoint"
                )
            seen.add(m.member)

    # -- partitions -------------------------------------------------------

    @property
    def expected_members(self) -> tuple[MemberAcquisition, ...]:
        """Every *selected* expected member (``OUT_OF_SCOPE`` excluded)."""
        return tuple(
            m for m in self.members if m.state is not AcquisitionState.OUT_OF_SCOPE
        )

    @property
    def completed_members(self) -> tuple[MemberAcquisition, ...]:
        return tuple(m for m in self.members if m.state is AcquisitionState.AVAILABLE)

    @property
    def unchecked_members(self) -> tuple[MemberAcquisition, ...]:
        """D6's incompleteness signal: selected, expected, never compared --
        **minus** the members a proven-complete inventory already answers
        for. A proven removal/addition (D2) is a *finding* about the
        release, not a gap in what this run could check, so it never
        makes the scope read incomplete; an unmatched member under an
        unproven inventory does."""
        answered = {m.member for m in self.proven_removed_members} | {
            m.member for m in self.proven_added_members
        }
        return tuple(
            m
            for m in self.members
            if m.state in UNCHECKED_STATES and m.member not in answered
        )

    @property
    def out_of_scope_members(self) -> tuple[MemberAcquisition, ...]:
        return tuple(
            m for m in self.members if m.state is AcquisitionState.OUT_OF_SCOPE
        )

    def members_in(self, state: AcquisitionState) -> tuple[MemberAcquisition, ...]:
        return tuple(m for m in self.members if m.state is state)

    def counts(self) -> dict[str, int]:
        """``{state value: count}`` over every state, zeros included, so a
        reader can check the partition sums to the expected set."""
        out = {s.value: 0 for s in AcquisitionState}
        for m in self.members:
            out[m.state.value] += 1
        return out

    # -- outcomes ---------------------------------------------------------

    @property
    def no_comparison_completed(self) -> bool:
        """D7: the selected scope produced no valid comparison at all."""
        return not self.completed_members

    @property
    def is_incomplete(self) -> bool:
        """D6: at least one selected member went unchecked, or nothing
        completed (an empty selection is not a fully checked scope)."""
        return bool(self.unchecked_members) or self.no_comparison_completed

    @property
    def proven_removed_members(self) -> tuple[MemberAcquisition, ...]:
        """D2: selected OLD-only members whose absence the NEW side's
        proven-complete inventory establishes -- the only input the
        removal finding and exit ``8`` may read."""
        if self.new_inventory.completeness is not InventoryCompleteness.PROVEN:
            return ()
        return tuple(
            m for m in self.expected_members if m.old_present and not m.new_present
        )

    @property
    def proven_added_members(self) -> tuple[MemberAcquisition, ...]:
        """The symmetric rule: NEW-only members, provable only against a
        proven-complete OLD inventory."""
        if self.old_inventory.completeness is not InventoryCompleteness.PROVEN:
            return ()
        return tuple(
            m for m in self.expected_members if m.new_present and not m.old_present
        )

    # -- codec ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selection": self.selection,
            "selection_reason": self.selection_reason,
            "old_inventory": self.old_inventory.to_dict(),
            "new_inventory": self.new_inventory.to_dict(),
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScopeAcquisitionRecord:
        raw_members = data.get("members", [])
        if not isinstance(raw_members, list):
            raise ValueError("scope acquisition record: 'members' must be a list")
        return cls(
            members=tuple(MemberAcquisition.from_dict(m) for m in raw_members),
            old_inventory=SideInventory.from_dict(data["old_inventory"]),
            new_inventory=SideInventory.from_dict(data["new_inventory"]),
            selection=str(data.get("selection", "all_expected")),
            selection_reason=str(data.get("selection_reason", "")),
            schema_version=str(
                data.get("schema_version", SCOPE_ACQUISITION_SCHEMA_VERSION)
            ),
        )
