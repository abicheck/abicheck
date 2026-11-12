"""A present assurance block with no usable status never passes the gate.

``member_assurance_fields`` turned an empty or ``None`` status into
``"complete"`` (``... or "complete"``), so a single such member gave
``require_complete`` an exit contribution of 0 (CodeRabbit review). Oracle:
the gate rule itself -- only a member whose own status is ``complete``, or
one with no assurance block at all, may contribute 0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.workflows.release_assurance_members import release_assurance_from_results


def _diff(block: object) -> SimpleNamespace:
    return SimpleNamespace(library="libx.so", analysis_assurance=block)


@pytest.mark.parametrize("status", ["", None, 0, [], "partial", "failed", "bogus"])
def test_unusable_or_incomplete_status_blocks(status: object) -> None:
    block = SimpleNamespace(status=status, notes=())
    decision = release_assurance_from_results([_diff(block)], require_complete=True)
    assert decision.exit_contribution == 1
    assert decision.status != "complete"


@pytest.mark.parametrize("block", [None, SimpleNamespace(status="complete", notes=())])
def test_complete_or_absent_block_passes(block: object) -> None:
    decision = release_assurance_from_results([_diff(block)], require_complete=True)
    assert decision.exit_contribution == 0
