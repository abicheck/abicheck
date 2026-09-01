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
"""Detect ``scan``'s own abort envelope for the sticky PR comment.

Split from :mod:`abicheck.pr_comment_scan` -- that module sits at its own
ADR-061 no-growth debt budget with zero line slack (``architecture/
debt.yaml``), the same reason several other ADR-064 abort-report helpers
this session landed in their own leaf modules
(``abicheck.workflows.scan_abort_result``).
"""

from __future__ import annotations

from typing import Any


def scan_abort_incomplete_reason(
    diff: dict[str, Any], report: dict[str, Any]
) -> str | None:
    """A human-readable reason when *diff* is ``scan``'s own abort envelope
    (``BUDGET_OVERFLOW``/``EVIDENCE_CONTRACT_ERROR``, ADR-064 stage 1b's
    native-CLI abort report, ``cli_scan._emit_scan_abort_report``), else
    ``None``.

    That envelope shapes ``diff`` as ``{"exit": {...}}`` -- no
    ``findings``/``additions``/``quality``/``reason`` key at all, since no
    comparison ever ran. Without this check, :func:`~abicheck.
    pr_comment_scan.from_scan` read the empty buckets that shape produces as
    an ordinary, clean, zero-findings comparison and rendered "No ABI
    changes" for a scan that aborted before comparing anything at all
    (Codex review, fresh evidence: reachable for ``EVIDENCE_CONTRACT_ERROR``
    through the GitHub Action, and for either sentinel through
    ``cli_pr_comment`` directly; under the default ``--on=changes`` this
    could even delete a prior sticky failure comment because the model
    reported zero changes). The caller treats a non-``None`` result the same
    way it already treats a ``NOT_COMPARABLE`` ``diff["reason"]`` -- a
    single, blocking "analysis incomplete" finding.
    """
    if not diff or not set(diff) <= {"exit"}:
        return None
    exit_block = diff.get("exit")
    reasons = exit_block.get("reasons") if isinstance(exit_block, dict) else None
    category = str(reasons[0]) if isinstance(reasons, list) and reasons else None
    verdict_raw = report.get("verdict")
    label = category or (
        str(verdict_raw) if isinstance(verdict_raw, str) else "aborted"
    )
    return f"scan aborted before completing a comparison ({label})"
