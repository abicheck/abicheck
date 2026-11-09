"""The exact ADR-090 answer of a comparison whose observed verdict is a claim.

A test about another axis (suppression, roll-ups, export-table reads) still
knows, per cell, which compared sides carry type-level evidence (debug info or
a header AST). From that it states the one answer ADR-090 allows:

* no side lacks it: the report reads the observed verdict, carries no
  ``unconfirmed`` block, and the run exits 0;
* some side lacks it: the report reads ``UNCONFIRMED``, its ``unconfirmed``
  block keeps the observed verdict and names exactly those sides, the exit
  reason is ``unconfirmed_compatibility`` alone, and the run exits 1.

There is no "0 or 1" branch: each cell's evidence decides which answer it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["assert_claim_outcome"]


def assert_claim_outcome(
    code: int,
    report: Mapping[str, Any],
    *,
    observed: str,
    lacking: Sequence[str],
) -> None:
    """Assert the exact answer for a run whose observed verdict is *observed*.

    *lacking* is the ordered list of compared sides (``"old"``, ``"new"``)
    that carry no type-level evidence in this cell; empty when both do.
    """
    context = {
        "code": code,
        "verdict": report.get("verdict"),
        "exit": report.get("exit"),
    }
    if not lacking:
        assert report["verdict"] == observed, context
        assert "unconfirmed" not in report, report.get("unconfirmed")
        assert code == 0, context
        return
    block = report["unconfirmed"]
    assert report["verdict"] == "UNCONFIRMED", context
    assert block["observed_verdict"] == observed, block
    assert block["sides_without_type_evidence"] == list(lacking), block
    assert report["exit"]["reasons"] == ["unconfirmed_compatibility"], context
    assert code == 1, context
