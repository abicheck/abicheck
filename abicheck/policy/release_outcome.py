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

"""The release verdict/outcome fold -- one ordering, one fold.

A directory/package ``compare`` reports one ``verdict`` for many members
plus its release-global (bundle, probe-matrix) comparisons. This module is
the single owner of how those verdicts combine:

* :data:`RELEASE_VERDICT_ORDER` -- the one ranking. Real ``Verdict`` values
  rank ``NO_CHANGE < COMPATIBLE < COMPATIBLE_WITH_RISK < API_BREAK <
  BREAKING``; the operational sentinel ``ERROR`` ranks above every real
  verdict, and ADR-050 D2's ``not_comparable`` above even ``ERROR`` (a
  comparison that could not establish what changed dominates a crash).
  Any other string (``unsupported``, ``failed``) ranks as ``NO_CHANGE``.
* :func:`worse_release_verdict` -- one step of the "worst verdict wins"
  rollup, for a caller that folds members as they stream in.
* :func:`fold_release_outcome` -- the whole fold: the reported verdict, the
  release-global verdict alone, and ``run_outcome.compatibility`` (the
  worst *real* verdict, sentinels excluded, ``None`` when nothing real was
  observed -- never the ``NO_CHANGE`` floor, which would claim a clean
  completed comparison for a run that compared nothing).

Two derived rankings live here too, so no other module restates the order:
:func:`compatibility_rank` (strict, real verdicts only -- reclassification's
"never weaken" guard) and :func:`consumer_verdict_rank` (``--used-by``'s
worst-app selection, which deliberately ties ``NO_CHANGE`` with
``COMPATIBLE``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..model.change_catalog.registry import Verdict

__all__ = [
    "RELEASE_OPERATIONAL_SENTINELS",
    "RELEASE_VERDICT_ORDER",
    "ReleaseOutcome",
    "apply_removed_member_floor",
    "compatibility_rank",
    "consumer_verdict_rank",
    "fold_release_outcome",
    "release_global_verdicts",
    "release_verdict_rank",
    "worse_release_verdict",
]

#: The one release verdict ordering (see the module docstring).
RELEASE_VERDICT_ORDER: Mapping[str, int] = MappingProxyType(
    {
        "NO_CHANGE": 0,
        "COMPATIBLE": 1,
        "COMPATIBLE_WITH_RISK": 2,
        "API_BREAK": 3,
        "BREAKING": 4,
        "ERROR": 5,
        "not_comparable": 6,
    }
)

#: The release verdicts that are operational states, not real ``Verdict``
#: values: a member carrying one completed no comparison. They dominate the
#: reported ``verdict`` but must never mask a different, completed
#: compatibility result on ``run_outcome.compatibility``.
RELEASE_OPERATIONAL_SENTINELS = frozenset(
    {"ERROR", "not_comparable", "unsupported", "failed"}
)

#: A proven-removed member (ADR-065 D2) floors the reported verdict here.
_REMOVED_MEMBER_FLOOR = "COMPATIBLE_WITH_RISK"


def release_verdict_rank(verdict: str) -> int:
    """*verdict*'s rank in :data:`RELEASE_VERDICT_ORDER` (unknown -> 0)."""
    return RELEASE_VERDICT_ORDER.get(verdict, 0)


def worse_release_verdict(current: str, candidate: str) -> str:
    """One rollup step: *candidate* only when it strictly outranks
    *current* (a tie keeps *current*)."""
    if release_verdict_rank(candidate) > release_verdict_rank(current):
        return candidate
    return current


def compatibility_rank(verdict: Verdict | str) -> int:
    """Strict rank of a real compatibility ``Verdict``.

    Raises ``ValueError`` for an operational sentinel or any other string:
    a caller comparing compatibility strength must never silently treat an
    operational state as a verdict."""
    value = verdict.value if isinstance(verdict, Verdict) else verdict
    if value not in RELEASE_VERDICT_ORDER or value in RELEASE_OPERATIONAL_SENTINELS:
        raise ValueError(f"not a compatibility verdict: {verdict!r}")
    return RELEASE_VERDICT_ORDER[value]


def consumer_verdict_rank(verdict: object) -> int:
    """Severity rank for choosing the worst ``--used-by`` consumer.

    The release order restricted to real verdicts, with ``NO_CHANGE`` and
    ``COMPATIBLE`` tied at ``0`` (neither affects a consumer); anything that
    is not a real verdict ranks ``0``. Independent of any exit-code scheme:
    under a severity scheme a BREAKING app can exit 0, so ranking by exit
    code would let a later COMPATIBLE app overwrite the reported verdict."""
    value = getattr(verdict, "value", verdict)
    if not isinstance(value, str) or value in RELEASE_OPERATIONAL_SENTINELS:
        return 0
    return max(RELEASE_VERDICT_ORDER.get(value, 0) - 1, 0)


def release_global_verdicts(
    bundle_result: object, matrix_result: object
) -> tuple[str | None, str | None]:
    """The bundle and probe-matrix verdict strings (``None`` when that
    comparison did not run), read off the duck-typed result objects so this
    policy module never imports the workflow types that produce them."""
    bundle = getattr(getattr(bundle_result, "bundle_verdict", None), "value", None)
    matrix = getattr(getattr(matrix_result, "verdict", None), "value", None)
    return (
        bundle if bundle_result is not None else None,
        matrix if matrix_result is not None else None,
    )


@dataclass(frozen=True)
class ReleaseOutcome:
    """The folded release verdicts.

    ``reported_verdict`` -- worst of every member and release-global
    verdict, sentinels included, floored at ``COMPATIBLE_WITH_RISK`` when a
    proven-removed member exists; ``NO_CHANGE`` for an empty release.
    ``global_verdict`` -- the release-global verdicts alone, never masked by
    an unrelated member's ``ERROR``/``not_comparable`` (the input the exit
    resolver's evidence axes read). ``compatibility`` -- the worst real
    verdict, or ``None`` when no real comparison was observed. ``ran`` --
    whether a release-global comparison genuinely ran."""

    reported_verdict: str
    global_verdict: str
    compatibility: str | None
    ran: bool


def fold_release_outcome(
    member_verdicts: Iterable[str],
    global_verdicts: Iterable[str | None] = (),
    *,
    global_ran: bool | None = None,
    removed_members: Iterable[str] = (),
) -> ReleaseOutcome:
    """Fold a release's member and release-global verdicts.

    *global_ran* says whether a release-global comparison genuinely ran;
    when omitted, it is "any global verdict is not ``None``". It must be
    explicit whenever a vacuous global result exists (a bundle walk over
    zero compared pairs reports ``NO_CHANGE`` without being evidence), since
    the ``NO_CHANGE`` floor of ``global_verdict`` is otherwise
    indistinguishable from a real completed no-change result."""
    members = [str(v) for v in member_verdicts]
    present = [v for v in global_verdicts if v is not None]
    ran = bool(present) if global_ran is None else global_ran

    global_verdict = "NO_CHANGE"
    for v in present:
        global_verdict = worse_release_verdict(global_verdict, v)

    reported = "NO_CHANGE"
    for v in (*members, *present):
        reported = worse_release_verdict(reported, v)
    if any(True for _ in removed_members):
        reported = apply_removed_member_floor(reported, removed=True)

    compatibility: str | None = None
    real = [v for v in members if v not in RELEASE_OPERATIONAL_SENTINELS]
    if ran and global_verdict not in RELEASE_OPERATIONAL_SENTINELS:
        real.append(global_verdict)
    for v in real:
        compatibility = (
            v if compatibility is None else worse_release_verdict(compatibility, v)
        )
    return ReleaseOutcome(
        reported_verdict=reported,
        global_verdict=global_verdict,
        compatibility=compatibility,
        ran=ran,
    )


def apply_removed_member_floor(verdict: str, *, removed: bool) -> str:
    """Raise *verdict* to ``COMPATIBLE_WITH_RISK`` when a proven-removed
    member exists (ADR-065 D2: only an inventory-proven removal reaches
    here, never a bare unmatched member). Never lowers a verdict."""
    if removed:
        return worse_release_verdict(verdict, _REMOVED_MEMBER_FLOOR)
    return verdict
