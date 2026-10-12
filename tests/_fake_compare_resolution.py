"""A stand-in for ``cli_resolve._resolve_compare_pair``'s answer.

The native ``compare`` CLI resolves a pair through ``_resolve_compare_pair``
and classifies it through the shared ``workflows.compare_classify`` core,
which needs the ``CompareRequest`` the pair was resolved from as well as the
two snapshots. A test that fakes resolution returns this pair instead of a
bare ``(old, new)`` tuple.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abicheck.service_compare_pipeline import ResolvedComparePair
from abicheck.workflows.contracts import CompareRequest
from abicheck.workflows.request_inputs import InputSpec


def fake_resolved_pair(
    old: Any, new: Any | None = None
) -> tuple[CompareRequest, ResolvedComparePair]:
    """``(request, pair)`` resolving to *old*/*new* (*new* defaults to *old*).

    The CLI states the collect mode and the operands itself, so the pair's
    side evidence is never read and stays ``None``.
    """
    request = CompareRequest(
        old=InputSpec(path=Path("old.abi.json")),
        new=InputSpec(path=Path("new.abi.json")),
    )
    pair = ResolvedComparePair(
        old=old,
        new=old if new is None else new,
        old_fmt=None,
        new_fmt=None,
        old_evidence=None,  # type: ignore[arg-type]
        new_evidence=None,  # type: ignore[arg-type]
    )
    return request, pair
