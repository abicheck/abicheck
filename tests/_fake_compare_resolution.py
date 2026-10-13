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

from abicheck.service_compare_evidence import SideEvidence
from abicheck.service_compare_pipeline import ResolvedComparePair
from abicheck.workflows.contracts import CompareRequest
from abicheck.workflows.request_inputs import InputSpec


def fake_resolved_pair(
    old: Any,
    new: Any | None = None,
    *,
    collect_mode: str = "off",
    operands: tuple[Path, Path] = (Path("old.abi.json"), Path("new.abi.json")),
) -> tuple[CompareRequest, ResolvedComparePair]:
    """``(request, pair)`` resolving to *old*/*new* (*new* defaults to *old*).

    Classification reads the compared files off the request (*operands*: a
    fake standing in for the CLI's resolution passes the two operand paths
    it was handed, so the report's file metadata reads real files) and only
    the collect mode off each side's evidence (*collect_mode*, ``off`` like
    a bare snapshot comparison).
    """
    request = CompareRequest(
        old=InputSpec(path=Path(operands[0])),
        new=InputSpec(path=Path(operands[1])),
    )
    pair = ResolvedComparePair(
        old=old,
        new=old if new is None else new,
        old_fmt=None,
        new_fmt=None,
        old_evidence=_evidence(collect_mode),
        new_evidence=_evidence(collect_mode),
    )
    return request, pair


def _evidence(collect_mode: str) -> SideEvidence:
    return SideEvidence(
        headers=[], compile=None, collect_mode=collect_mode, dump_manifest=None
    )
