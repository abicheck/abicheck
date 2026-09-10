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

"""ADR-068's audit-gate axis (2026-09-10 amendment): the orthogonal exit
contribution that lets a ``compare --no-baseline`` CI job gate on a real
audit finding, closing the gap ``docs/contribute/known-gaps.md``'s
"no way to gate a CI job on an audit finding" entry recorded.

**What this reproduces, and what it deliberately does not.** Legacy
``scan``'s audit mode derived a *verdict* from its own findings
(``cli_scan_baseline.py``'s ``compute_verdict``-over-``verdict_scored_
changes`` path) and mapped that verdict to an exit code the same way a real
two-sided comparison does: an ``API_BREAK_KINDS``-classified finding exits
``2``, a ``BREAKING_KINDS``-classified finding exits ``4``, and a
``RISK_KINDS``-classified finding (advisory-only, ADR-028 D3 / ADR-035 D1)
exits ``0`` on its own. ADR-068 D2 forbids an audit from ever emitting ``2``
or ``4`` -- those are the *compatibility* family's own codes, and an audit
reports no compatibility verdict -- so this axis reproduces exactly the
same **partition** (a finding whose *effective* verdict is
``Verdict.BREAKING``/``Verdict.API_BREAK`` gates; ``Verdict.
COMPATIBLE_WITH_RISK``/``Verdict.COMPATIBLE`` do not) through its own code,
:data:`AUDIT_GATE_EXIT_CODE`, folded with ``max`` like every other
orthogonal axis in this codebase (`contract_coverage_exit.py`,
`depth_evidence_contract.py`) -- it can raise a clean ``0``, and it can
never lower, or be confused for, a compatibility ``2``/``4``.

**Reads the policy-effective verdict, never the raw ``ChangeKind``
category, on purpose.** An earlier revision of this module read
:data:`~abicheck.policy.classification.BREAKING_KINDS`/
:data:`~abicheck.policy.classification.API_BREAK_KINDS` membership directly
off each finding's raw ``change.kind`` -- correct on the un-policied G20
corpus, but wrong the moment a run supplies a ``--policy`` document:
``policy_file.py``'s ``overrides:``/``reclassify:`` blocks can promote a
``RISK``-classified kind to ``Verdict.BREAKING`` (or demote a normally
breaking kind), and every other consumer of a finding's classification in
this codebase (the JSON/Markdown/SARIF/JUnit renderers, the two-sided
gate) reads that *effective*, policy-resolved verdict, not the raw kind.
Gating on the raw kind meant an explicitly-selected, trusted policy's
promotion of a finding to breaking was silently invisible to this axis --
a ``--policy`` naming a kind as breaking, whose committed snapshot carries
exactly that finding, unsuppressed, could still exit ``0`` (Codex security
review, P1: :file:`SECURITY.md` treats analyzed binaries as untrusted and
policies as trusted, so the trusted signal must be the one honored). This
module now reads each finding's already-resolved
:attr:`~abicheck.report.finding.ReportFinding.verdict` -- the same value
:func:`abicheck.policy.severity.effective_verdict_for_change` computes for
every other renderer, "read, don't re-derive" per this repository's own
known-gaps convention -- rather than importing ``ReportFinding`` itself
(which would invert the ``policy -> report`` dependency direction; this
module stays duck-typed over any object carrying a ``.verdict``).

**Why this reads ``Verdict`` directly rather than routing through
``severity.py``'s coarser category model.** ``severity.py``'s own
``IssueCategory``/``SeverityConfig`` split findings into four buckets, and
by its own module docstring ``potential_breaking`` is **``API_BREAK_KINDS ∪
RISK_KINDS``** -- the two are deliberately merged into one severity bucket
there, because a severity preset's whole point is "how strict should
*review-worthy* findings be", not "does this specific finding require
recompilation". Routing this axis through that bucket would gate exactly
the case the task that produced this module named as the regression to
avoid: ``case143_audit_accidental_export``'s ``RISK``-classified finding
(effective verdict ``Verdict.COMPATIBLE_WITH_RISK``) would gate identically
to ``case148``/``case149``'s ``API_BREAK``-classified ones the moment
``potential_breaking`` reached ``error``. So this axis compares
``Verdict.BREAKING``/``Verdict.API_BREAK`` directly instead -- the same
five-way ``Verdict`` enum every other verdict-level consumer of the
taxonomy uses, just not folded through the coarser four-bucket severity
model.

**Opt-in, not on by default.** Every existing ``compare --no-baseline``
invocation's exit code must stay unchanged (a real migration/compatibility
cost per this repository's own "don't change public interfaces without an
ADR and migration" rule) -- so this axis only ever contributes when the
invocation explicitly asked for it. Activation reuses the existing
``--severity-preset`` option (previously a hard usage error under
``--no-baseline``, naming this exact gap) rather than inventing a new flag:
passing any preset other than ``info-only`` opts the run into gating,
exactly mirroring what the flag already means on a two-sided ``compare`` --
"I want this run to gate on its findings". ``info-only`` stays a deliberate
no-gate request, the same as it is for a two-sided run. See the ADR-068
amendment (2026-09-10) for the full reasoning and the rejected
alternative (a bespoke ``--audit-gate`` flag).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .classification import Verdict

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "AUDIT_GATE_EXIT_CODE",
    "SEVERITY_PRESET_DISABLES_AUDIT_GATE",
    "audit_gate_enabled_for_severity_preset",
    "audit_gate_exit_contribution",
    "fold_audit_gate_exit",
]

#: This axis's own exit code. Chosen from the codes ``compare``/
#: ``scan --against`` already use (``0, 1, 2, 4, 5, 6, 7, 8, 64`` --
#: surveyed against `docs/reference/exit-codes.md` and `severity.py`'s own
#: `_CATEGORY_EXIT_CODES`/`analysis_assurance`/`contract_coverage_exit`/
#: `depth_evidence_contract` modules at the time this was added): ``3`` is
#: the one integer in that low range no `compare`/`scan --against` axis
#: currently emits (`1`/`2`/`4`/`5` are taken by severity/compatibility/
#: budget, `6`/`7`/`8` by not-comparable/evidence-contract/removed-required-
#: library). Deliberately *not* `2` -- ADR-068 D2 reserves that for a real
#: compatibility source-break, which an audit structurally cannot report.
AUDIT_GATE_EXIT_CODE = 3

#: The one ``--severity-preset`` value that does **not** opt an audit into
#: gating -- it is the explicit "don't gate anything" request on a two-sided
#: `compare` too (`severity.INFO_ONLY_PRESET` sets every category to
#: `SeverityLevel.INFO`), and this axis honors that same intent rather than
#: treating "a preset was named" as unconditional activation.
SEVERITY_PRESET_DISABLES_AUDIT_GATE = "info-only"


def audit_gate_enabled_for_severity_preset(severity_preset: str | None) -> bool:
    """Does *severity_preset* opt a ``--no-baseline`` audit into gating?

    ``None`` (the flag was not given at all) and
    :data:`SEVERITY_PRESET_DISABLES_AUDIT_GATE` both answer ``False``;
    every other accepted preset name (``default``, ``strict`` today)
    answers ``True``. The one place this question is answered, so the CLI
    layer and any future typed-API caller cannot drift on what "opted in"
    means.
    """
    return (
        severity_preset is not None
        and severity_preset != SEVERITY_PRESET_DISABLES_AUDIT_GATE
    )


#: The effective verdicts that gate. Mirrors legacy ``scan``'s own
#: ``BREAKING_KINDS | API_BREAK_KINDS`` partition, but stated at the
#: ``Verdict`` level -- the level every policy override/reclassify rule
#: ultimately resolves to -- rather than the raw per-kind taxonomy, so a
#: policy-promoted finding is caught the same way an un-promoted one is.
_GATING_VERDICTS = (Verdict.BREAKING, Verdict.API_BREAK)


def _gates(verdict: Verdict) -> bool:
    """Would legacy ``scan``'s verdict computation have gated on a finding
    whose *effective*, policy-resolved verdict is *verdict*?

    ``True`` for :data:`Verdict.BREAKING`/:data:`Verdict.API_BREAK`,
    ``False`` for everything else (:data:`Verdict.COMPATIBLE_WITH_RISK`,
    :data:`Verdict.COMPATIBLE`, and the unreachable :data:`Verdict.
    NO_CHANGE`).
    """
    return verdict in _GATING_VERDICTS


def audit_gate_exit_contribution(findings: Iterable[Any], *, enabled: bool) -> int:
    """This axis's own exit contribution: :data:`AUDIT_GATE_EXIT_CODE` or ``0``.

    *findings* is any iterable of objects carrying a policy-effective
    ``.verdict`` -- a :class:`~abicheck.report.finding.ReportFinding`, as
    resolved by :func:`abicheck.policy.severity.effective_verdict_for_change`
    (via :func:`abicheck.report.finding.build_report_findings`). Reading
    that already-resolved field, rather than a finding's raw
    ``change.kind``, is what lets a ``--policy`` override/``reclassify:``
    rule that promotes a finding to breaking be honored here exactly as it
    is by every other renderer (see the module docstring's Codex-review
    note) -- a bare object with no ``.verdict`` (e.g. a raw ``Change``) is
    skipped rather than mis-read, so a caller that forgets to resolve
    first fails to gate loudly in its own tests rather than silently
    reading a category the raw object was never classified against.

    ``0`` whenever *enabled* is ``False`` (the default): an audit that never
    asked to gate reports exactly the same exit code it always has --
    reading, never re-deriving, is what keeps every pre-existing
    ``compare --no-baseline`` invocation unchanged. When enabled, the first
    gating finding is sufficient; this is a floor, not a count.
    """
    if not enabled:
        return 0
    for finding in findings:
        verdict = getattr(finding, "verdict", None)
        if isinstance(verdict, Verdict) and _gates(verdict):
            return AUDIT_GATE_EXIT_CODE
    return 0


def fold_audit_gate_exit(base: int, contribution: int) -> int:
    """*base* raised to this axis's floor -- the same ``max`` discipline
    every other orthogonal axis in this codebase folds with. Never lowers
    *base*, and *contribution* is always either ``0`` or
    :data:`AUDIT_GATE_EXIT_CODE` -- never ``2``/``4``, so it can never be
    mistaken for, or override, the compatibility family's own codes.
    """
    return max(base, contribution)
