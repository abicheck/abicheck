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

"""``FindingEvolution`` -- ADR-068 D3 / plan §5 prerequisite P2.

A one-sided (per-snapshot) check like ``buildsource.crosscheck``'s
``private_header_leak`` has no OLD/NEW pair of its own -- it diffs one
snapshot's evidence sources against each other. Migrating such a check onto
``compare``'s OLD-vs-NEW pipeline (plan §3 rows 3-5) needs a vocabulary for
what happened to *that check's own finding* between the two sides, so a
pre-existing problem is never silently reported as newly introduced merely
because one side's evidence happened to be weaker.

Four states, matching ADR-068 D3's table exactly:

======================  ===============================================
State                   Meaning
======================  ===============================================
``introduced``          Absent on OLD (with OLD evidence sufficient to
                        say so), present on NEW.
``resolved``            Present on OLD, absent on NEW (with NEW evidence
                        sufficient to say so).
``persistent``          Present on both sides.
``not_evaluated``       The side needed to answer "was this already
                        there" (or "is this now gone") could not --
                        its evidence was insufficient, not merely silent.
======================  ===============================================

**``not_evaluated`` is mandatory, not a convenience** (plan §5 P2, ADR-068
D3): a private-header leak present in *both* releases must never read as
``introduced`` just because the baseline snapshot lacked header evidence to
prove it was already there. That would be a manufactured finding --
vision.md's "weaker evidence narrows conclusions" rule applied to this one
axis. See :mod:`abicheck.compare.finding_evolution` for the matcher that
actually computes this state from two independent per-side check runs.

Model-layer (ADR-061 D1): this module imports nothing internal, per
``architecture/modules.yaml``'s ``model`` layer contract (``may_import: []``).

Authority is unchanged (ADR-028 D3 / ADR-035 D1): a ``Change`` carrying an
``evolution`` value keeps whatever ``ChangeKind`` category it always had
(``RISK``/``API_BREAK`` for every cross-source check) -- ``evolution`` is
additive metadata about *when* the finding was observed, never a promotion
of *what* it is.
"""

from __future__ import annotations

from enum import Enum


class FindingEvolution(str, Enum):
    """One check's OLD-vs-NEW status for one identity.

    A ``str`` mixin, matching the existing ``Verdict``/``ChangeKind``
    convention: ``.value`` is the exact wire spelling used in JSON reports.
    """

    #: Absent on OLD (with OLD evidence sufficient to say so), present on NEW.
    INTRODUCED = "introduced"
    #: Present on OLD, absent on NEW (with NEW evidence sufficient to say so).
    RESOLVED = "resolved"
    #: Present on both sides.
    PERSISTENT = "persistent"
    #: The side needed to decide "new" vs "pre-existing" lacked the evidence
    #: to answer -- never conflate this with "clean".
    NOT_EVALUATED = "not_evaluated"


__all__ = ["FindingEvolution"]
