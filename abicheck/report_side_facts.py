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

"""ADR-061 Phase 2 item 5's ``DiffResult`` field mixin.

``checker_types.py`` is on ``architecture/debt.yaml``'s no-growth ledger
(frozen at its pre-ADR-061 line count, "cannot move safely without a
behavior-preserving vertical slice") -- it cannot take three new fields'
worth of declarations and docstrings directly, which is what "move
responsibility instead of raising the baseline" (the gate's own error
text) means here: the fields live in this new, unfrozen leaf module as a
plain dataclass, and ``DiffResult`` inherits it, so ``checker_types.py``
itself only grows by the one-line base-class change. Every field is
``kw_only=True``, so inheritance order carries no positional-argument
consequence for ``DiffResult``'s existing constructor callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReportSideFacts:
    """Report-only facts a ``compare`` run resolves once and attaches to
    its ``DiffResult`` before rendering, so a JSON builder in
    :mod:`abicheck.reporter`/:mod:`abicheck.reporter_contract_blocks` reads
    them directly instead of a renderer re-parsing its own already-rendered
    JSON text to splice them in afterwards (the "post-render mutation" ADR-061
    Phase 2 item 5 names). None of these three ever feed a verdict, a gate,
    or an exit code -- they are pure report content.
    """

    # The evidence depth each side of the comparison actually reached (one
    # of ``checker_types.EVIDENCE_DEPTH_VALUES`` -- "binary"/"headers"/
    # "build"/"source"), resolved by ``cli_compare_helpers`` from the real snapshot
    # plus any out-of-band ``--build-info``/``--sources`` pack (mirrors
    # ``analysis_assurance``'s own identical out-of-band-pack resolution).
    # ``None`` for every caller other than ``compare``'s own JSON path --
    # nothing else populates these today.
    old_evidence_depth: str | None = field(default=None, kw_only=True)
    new_evidence_depth: str | None = field(default=None, kw_only=True)
    # The ``--audit-suppressions`` ledger (``suppression.SuppressionAudit``),
    # attached by ``cli_compare_helpers._attach_suppression_audit`` before
    # rendering. Typed ``object`` rather than the real type: ``suppression.py``
    # imports ``Change`` from ``checker_types.py``, so ``checker_types.py``
    # importing ``SuppressionAudit`` back would be circular -- the same
    # reason ``DiffResult.contract_context``/``analysis_assurance`` are typed
    # ``object`` too. ``None`` for every run that did not pass
    # ``--audit-suppressions``. The markdown/text/review rendering of this
    # same ledger stays a post-render append
    # (``cli_compare_fold._fold_suppression_audit_into_text``'s remaining
    # job): that section must NOT be demangled the way the rest of a
    # ``--demangle`` markdown/review report is, which folding it into
    # ``reporter_markdown.to_markdown``'s own line list (ahead of that
    # function's single, whole-report demangle pass) cannot honor.
    suppression_audit: object | None = field(default=None, kw_only=True)
