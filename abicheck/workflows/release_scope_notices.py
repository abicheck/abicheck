# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release fan-out's notices about members that did not pair.

Derived from the ADR-065 acquisition record and emitted by the release
compare owner (:mod:`abicheck.workflows.release_compare`) as soon as the
record exists. Moved here from ``report/comparison_scope.py`` with that
owner: the notices are run warnings the workflow records in its outcome,
not a projection of a finished report, and they read only the model.
"""

from __future__ import annotations

from ..model.scope_acquisition import AcquisitionState, ScopeAcquisitionRecord

__all__ = ["release_scope_warnings"]


def release_scope_warnings(record: ScopeAcquisitionRecord) -> list[str]:
    """The fan-out's stderr notices about members that did not pair, derived
    from the acquisition record (ADR-065 S4).

    These used to be written from ``_match_release_keys``'s raw
    ``old_keys - new_keys`` set difference -- the last consumer, beside the
    JSON ``unmatched_old`` key, still reading that path by name after S2 took
    exit ``8``, the verdict bump and the Markdown sections off it. Reading
    the record instead is what lets one line distinguish the four states D2
    keeps apart: a *proven* removal (the lacking side's inventory is
    complete) says "removed" and names the proof, an unmatched member says
    "unmatched" and says why the proof is missing, an ``out_of_scope`` member
    is not mentioned at all (it was never selected), and a member whose
    acquisition failed is left to the completeness axis's own diagnostic
    rather than being misreported here as an absence.

    Emitted after the fan-out, since the record does not exist before it;
    ``no comparison completed`` (D7) closes the list when nothing compared.
    """
    proven_removed = {m.member for m in record.proven_removed_members}
    proven_added = {m.member for m in record.proven_added_members}
    messages: list[str] = []
    for m in record.members:
        if m.state is AcquisitionState.OUT_OF_SCOPE:
            continue
        if m.member in proven_removed:
            messages.append(
                f"Warning: library removed: {m.name} -- absent from NEW, whose "
                f"inventory is proven complete ({record.new_inventory.provenance})"
            )
        elif m.member in proven_added:
            messages.append(
                f"Info: library added: {m.name} -- absent from OLD, whose "
                f"inventory is proven complete ({record.old_inventory.provenance})"
            )
        elif m.state is AcquisitionState.NOT_SUPPLIED and m.old_present:
            messages.append(
                f"Warning: library unmatched (no counterpart on NEW): {m.name}"
            )
        elif m.state is AcquisitionState.NOT_SUPPLIED and m.new_present:
            messages.append(
                f"Info: library unmatched (no counterpart on OLD): {m.name}"
            )
        elif m.state is AcquisitionState.EXPECTED_NOT_PRODUCED:
            messages.append(
                f"Warning: library expected but not produced: {m.name}"
                + (f" -- {m.reason}" if m.reason else "")
            )
    if record.no_comparison_completed:
        messages.append(
            "Warning: no matching library pairs found between OLD and NEW inputs -- "
            "no comparison completed."
        )
    return messages
