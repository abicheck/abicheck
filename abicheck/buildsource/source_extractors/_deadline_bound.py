# Copyright 2026 Nikolay Petrov
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

"""Shared "bound a per-TU subprocess by the scan deadline" helper (ADR-028 D3).

``deadline.run_bounded()`` honors an *active* outer ``--budget`` deadline
verbatim (``bounded_timeout()`` returns what's left of it, not
``min(timeout, left)`` — see its docstring) rather than an extractor's own
local ``timeout=``. Under a generous ``--budget`` larger than a backend's
local per-TU cap, a single hung TU would otherwise consume the *whole*
remaining scan budget before degrading, instead of failing fast after its own
cap and leaving the rest of the budget for the remaining TUs (Codex review,
PR #591, round 7 — the same defect already fixed for the L2 probe, the L3
build-dir/cmake-lock waits, the preprocessor pre-scan, and the include-graph
extractor).

Both L4 source extractors (castxml, clang) are advisory (ADR-028 D3): every
failure here already folds into :class:`SourceExtractionError`, which callers
record as partial per-TU coverage rather than aborting the comparison — so
this never needs to decide "propagate vs. degrade", only which timeout to
message.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ... import deadline
from .base import SourceExtractionError


def run_bounded_for_extraction(
    cmd: list[str],
    *,
    timeout: float,
    tool_label: str,
    unit_label: str,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """``deadline.run_bounded()``, but capped by ``min(timeout, scan remaining)``.

    Nests a ``deadline.deadline_scope()`` narrower than the active outer
    ``--budget`` deadline (if any) so this call's *own* ``timeout`` is what
    actually bounds it, not the (possibly much larger) remaining scan budget.
    Always raises :class:`SourceExtractionError` on either a local timeout or
    scan-deadline exhaustion — never a bare ``TimeoutExpired``/``DeadlineExceeded``.
    """
    scan_remaining = deadline.remaining()
    # Whether the OUTER scan --budget (not this call's own timeout) is what
    # will actually bind the nested scope below — decides how a
    # DeadlineExceeded from it is classified/messaged.
    bound_by_scan_deadline = scan_remaining is not None and scan_remaining < timeout
    effective_timeout = (
        timeout if scan_remaining is None else min(timeout, scan_remaining)
    )
    try:
        with deadline.deadline_scope(effective_timeout):
            return deadline.run_bounded(cmd, timeout=timeout, **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        # Always active for an ordinary (no outer deadline) timeout, since the
        # nested deadline_scope() just entered makes run_bounded() see a
        # deadline and raise DeadlineExceeded instead — a bare TimeoutExpired
        # here always means this call's own timeout, never the scan budget.
        raise SourceExtractionError(
            f"{tool_label} timed out after {timeout:.0f}s on {unit_label}"
        ) from exc
    except deadline.DeadlineExceeded as exc:
        if not bound_by_scan_deadline:
            # The entry-time snapshot said this call's OWN timeout was
            # binding, not the outer scan deadline — but run_bounded's own
            # escalation (SIGTERM -> grace -> SIGKILL, plus a fixed 5s
            # pipe-drain) can push real elapsed time past that snapshot, so
            # re-check the (now-restored) outer deadline directly instead of
            # trusting the stale snapshot alone (same re-check pattern as the
            # include-graph extractor and the preprocessor pre-scan).
            try:
                deadline.check()
            except deadline.DeadlineExceeded:
                pass
            else:
                raise SourceExtractionError(
                    f"{tool_label} timed out after {timeout:.0f}s on {unit_label}"
                ) from exc
        raise SourceExtractionError(
            f"scan deadline exceeded running {tool_label} on {unit_label}"
        ) from exc
