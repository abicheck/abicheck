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

"""ADR-070: turning a release's own member results into the assurance fold.

``policy.release_assurance`` owns the *fold* and deliberately takes plain
``(name, status, notes)`` triples -- it may not import the flat-root
``analysis_assurance`` module, which itself imports from ``policy``. This
module is the adapter that closes that gap from the other side: it reads each
member's real ``AnalysisAssurance`` and hands the fold what it needs.

It lives in ``workflows`` because both of its callers are frontends, and
``frontends -> policy`` is forbidden while ``frontends -> workflows`` is the
sanctioned seam (``architecture/modules.yaml``). Keeping the two reads here
rather than inline in each frontend is also what makes the two operand shapes
provably agree: the live directory fan-out records per-member status into its
``library_results`` dicts, the stored-``BundleFacts`` driver carries real
``DiffResult`` objects on ``BundleDiffResult.per_library``, and both arrive at
one :func:`~abicheck.policy.release_assurance.resolve_release_assurance_decision`
call through the two functions below.

Deliberately a small sibling leaf rather than another name on
``workflows.gate``: a frontend reaching this through that module's
re-export turned a function-local import into a cycle mypy could not order
(``cli.main`` lost its inferred type), because ``gate`` pulls in enough of the
CLI-registration cluster. This module imports only ``policy.release_assurance``
and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.release_assurance import (
    MemberAssurance,
    resolve_release_assurance_decision,
)

if TYPE_CHECKING:
    from ..policy.release_assurance import ReleaseAssuranceDecision

__all__ = [
    "member_assurance_entry_fields",
    "member_assurance_fields",
    "release_assurance_from_entries",
    "release_assurance_from_results",
]

#: What a member whose ``DiffResult`` carries no ``AnalysisAssurance`` at all
#: reports. Fails *open* rather than closed, matching
#: ``analysis_assurance.analysis_assurance_exit_contribution``'s own
#: defensive ``0`` for the same case: ``checker.compare()`` always attaches
#: one, so this is only reachable for a hand-built result in a test, and
#: inventing a shortfall there would turn a missing test fixture into a gate
#: failure. A *real* evidence shortfall is always a real non-``complete``
#: status, never an absent block.
_NO_BLOCK_STATUS = "complete"


def member_assurance_fields(result: Any) -> tuple[str, tuple[str, ...]]:
    """One member's ``(status, notes)``, read off *result*'s own block.

    ``getattr``-guarded the same way
    ``analysis_assurance.analysis_assurance_exit_contribution`` guards its
    own read -- see :data:`_NO_BLOCK_STATUS` for why an absent block is not
    treated as a shortfall.
    """
    aa = getattr(result, "analysis_assurance", None)
    if aa is None:
        return _NO_BLOCK_STATUS, ()
    status = str(getattr(aa, "status", _NO_BLOCK_STATUS) or _NO_BLOCK_STATUS)
    return status, tuple(str(n) for n in (getattr(aa, "notes", ()) or ()))


def release_assurance_from_results(
    results: Any, *, require_complete: bool
) -> ReleaseAssuranceDecision:
    """The fold over a stored-``BundleFacts`` release's ``per_library``
    ``DiffResult``s (ADR-070 D8).

    Member names come from ``DiffResult.library``, which is what every other
    view of that report already keys on.
    """
    members = []
    for diff in results or ():
        status, notes = member_assurance_fields(diff)
        members.append(
            MemberAssurance(
                name=str(getattr(diff, "library", "") or ""),
                status=status,
                notes=notes,
            )
        )
    return resolve_release_assurance_decision(
        tuple(members), require_complete=require_complete
    )


def release_assurance_from_entries(
    library_results: list[dict[str, object]], *, require_complete: bool
) -> ReleaseAssuranceDecision:
    """The fold over the live directory/package fan-out's own per-member
    entries.

    Only entries carrying ``analysis_assurance_status`` participate: the
    fan-out writes that key exactly when ``assurance.require_complete`` is in
    effect (ADR-070 D4), so its absence means "this release was never asked",
    not "this member is clean". An entry for a member that *failed* to compare
    at all carries no such key either, which is correct -- a member that never
    produced a comparison is ADR-065's scope axis, not this one's.
    """
    members = tuple(
        MemberAssurance(
            name=str(entry.get("library", "")),
            status=str(entry.get("analysis_assurance_status", "")),
            notes=tuple(
                str(n) for n in _as_list(entry.get("analysis_assurance_notes"))
            ),
        )
        for entry in library_results
        if isinstance(entry, dict) and "analysis_assurance_status" in entry
    )
    return resolve_release_assurance_decision(
        members, require_complete=require_complete
    )


def _as_list(value: object) -> list[object]:
    """*value* as a list, or ``[]`` -- a type-safe read of an
    ``object``-typed ``library_results`` entry value (the same need
    ``cli_compare_release_helpers._list_len`` exists for)."""
    return list(value) if isinstance(value, list) else []


def member_assurance_entry_fields(result: Any) -> dict[str, object]:
    """The three per-member keys the live fan-out records into its own
    ``library_results`` entry (ADR-070 D6).

    Written only when ``assurance.require_complete`` is in effect, so every
    release report produced without the setting stays byte-identical (D4) --
    the same rule the fan-out's contract-coverage keys follow for
    ``--contract``.

    The *contribution* is recorded alongside the status, not derived later from
    it, so :func:`release_assurance_from_entries`' aggregate is a ``max`` over
    values the scalar rule already decided rather than a second implementation
    of "status != complete" -- the same read-don't-re-derive discipline
    ``pack_application`` follows for the gate config.

    This is the one place the flat-root ``analysis_assurance`` module is read
    for a release member, which is also why it is here: a frontend may not
    import ``policy`` (and that module is ``policy`` by
    ``architecture/modules.yaml``'s ``legacy_paths``), while
    ``frontends -> workflows`` is the sanctioned seam.
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution

    status, notes = member_assurance_fields(result)
    return {
        "analysis_assurance_status": status,
        "analysis_assurance_notes": list(notes),
        "analysis_assurance_exit_contribution": analysis_assurance_exit_contribution(
            result, require_complete=True
        ),
    }
