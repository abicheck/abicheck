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

"""Why a generated run-plan holds no checks, and whether that is acceptable.

Plan slice 7r's own concern, split out of :mod:`abicheck.buildsource.run_plan`
rather than added to it -- that module carries an `architecture/debt.yaml`
``no_growth`` baseline, and AGENTS.md's rule for one of those is to move
responsibility to an owned module, never to trim the file to fit. This is a
self-contained question (classify an empty selection, explain it) distinct
from the cell-resolution logic that produces the checks, the same split
``run_plan_profile_fields.py`` already makes for the profile-field helpers.

``run_plan.py`` re-exports every public name here, so
``from abicheck.buildsource.run_plan import RunPlanSkip`` still resolves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .project_targets import ProjectTargetsConfig
from .run_plan_schema import RUN_PLAN_SCHEMA_SKIPPED


class DiagnosticSink(Protocol):
    """The half of ``run_plan.RunPlanGenerationReport`` this module writes to.

    A structural type, not an import of the concrete class: ``run_plan``
    imports *this* module, so importing it back -- even under
    ``TYPE_CHECKING``, which `check_ai_readiness.py`'s import-cycle gate
    still counts -- would close a cycle. The same decoupling
    ``scripts/retired_surfaces.py``'s own ``Findings`` Protocol makes for
    its caller.
    """

    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        """Whether no error has been recorded yet."""


def _opt_str(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, str) and value else default


#: ``skipped.reason``: CONFIG declares no ``checks[]`` at all, so there was
#: nothing to resolve. A real, supported state (a project bootstrapping its
#: ``.abicheck.yml`` before declaring targets), reported as a skip rather
#: than as a failure.

SKIP_NO_CHECKS_DECLARED = "no_checks_declared"

#: ``skipped.reason``: CONFIG declares ``checks[]`` that resolved to no cell
#: at all -- every implicit sweep matched nothing, usually because no
#: ``--build-output`` was supplied for the profiles they name. **Not** a
#: legitimate skip: this is the "a CI matrix silently gates nothing" case,
#: so it is a generation error and the run-plan is not usable.
SKIP_CHECKS_DECLARED_NONE_RESOLVED = "checks_declared_none_resolved"


#: What each reason requires of ``declared_checks``. A reason absent from
#: this mapping is not a reason this build knows, and is rejected rather
#: than carried through as an opaque label a consumer would have to guess
#: at.
_EXPECTED_DECLARED_CHECKS: dict[str, Any] = {
    SKIP_NO_CHECKS_DECLARED: lambda n: n == 0,
    SKIP_CHECKS_DECLARED_NONE_RESOLVED: lambda n: n > 0,
}

#: The same rule in words, for the error message.
_REASON_EVIDENCE = {
    SKIP_NO_CHECKS_DECLARED: "nothing was declared, so the count must be 0",
    SKIP_CHECKS_DECLARED_NONE_RESOLVED: (
        "checks were declared and none resolved, so the count must be positive"
    ),
}


@dataclass(frozen=True)
class RunPlanSkip:
    """Why a generated plan holds no checks (plan slice 7r).

    An empty plan used to be indistinguishable from a broken one, which is
    why accepting one needed a ``--allow-empty`` bypass switch on the CLI:
    the artifact said nothing about *why* it was empty, so the only
    available answers were "fail always" or "a human asserts it is fine".
    Recording the reason makes the two cases separable, which is what lets
    the legitimate one succeed on its own and the dangerous one fail with
    no bypass at all.
    """

    reason: str
    #: How many ``checks[]`` entries CONFIG declared, across targets and
    #: bundles -- the fact the classification rests on, carried so a
    #: consumer can see the evidence rather than trust the label.
    declared_checks: int
    #: One sentence a human can act on, including where to look next.
    explanation: str

    def __post_init__(self) -> None:
        """Reject a skip whose reason and evidence contradict each other.

        ``declared_checks`` is not decoration beside the label: it is the
        fact the classification rests on, and the whole purpose of the v3
        fields is that a consumer can tell a legitimate bootstrap skip from
        declared checks that failed to resolve. A block reading
        ``{"reason": "no_checks_declared", "declared_checks": 7}`` asserts
        both at once, so accepting it makes the distinction unsafe for
        every consumer downstream (Codex review, PR #1278).

        Enforced here rather than in :meth:`from_dict` so one rule governs
        construction, serialization and parsing alike -- the same reason
        :func:`schema_for_plan` and :func:`parse_skipped_block` share their
        own invariant instead of stating it twice.
        """
        from ..workflows.aggregate import AggregateError

        expected = _EXPECTED_DECLARED_CHECKS.get(self.reason)
        if expected is None:
            raise AggregateError(
                f"run-plan 'skipped.reason' {self.reason!r} is not a "
                f"recognized skip reason ({sorted(_EXPECTED_DECLARED_CHECKS)})"
            )
        if not expected(self.declared_checks):
            raise AggregateError(
                f"run-plan 'skipped' is self-contradictory: reason "
                f"{self.reason!r} with declared_checks="
                f"{self.declared_checks} -- {_REASON_EVIDENCE[self.reason]}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "declared_checks": self.declared_checks,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> RunPlanSkip:
        from ..workflows.aggregate import AggregateError

        reason = _opt_str(d.get("reason"))
        if not reason:
            raise AggregateError("run-plan 'skipped' requires a 'reason' string")
        declared = d.get("declared_checks", 0)
        if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
            raise AggregateError(
                "run-plan 'skipped.declared_checks' must be a non-negative integer"
            )
        return cls(
            reason=reason,
            declared_checks=declared,
            explanation=_opt_str(d.get("explanation")),
        )


def declared_check_count(config: ProjectTargetsConfig) -> int:
    """How many ``checks[]`` entries *config* declares, across targets and
    bundles. The evidence :func:`_classify_empty_plan` classifies on."""
    return sum(len(t.checks) for t in config.targets.values()) + sum(
        len(b.checks) for b in config.bundles.values()
    )


def classify_empty_plan(
    config: ProjectTargetsConfig, report: DiagnosticSink
) -> RunPlanSkip:
    """Say why a plan resolved to no checks, and whether that is acceptable.

    Plan slice 7r. Two empty plans mean opposite things and used to be one
    error with one ``--allow-empty`` bypass past both:

    * **Nothing was declared.** A project bootstrapping its
      ``.abicheck.yml`` before it has ``targets:``/``bundles:`` ``checks[]``
      has nothing to resolve, and a run-plan generator is the wrong place to
      object -- the config's own well-formedness is ``project validate``'s
      question, and the explanation points there. An explained *skipped
      plan*, exit 0, no bypass switch needed.
    * **Checks were declared and none resolved.** Every downstream matrix
      and aggregate step is then silently skipped while the workflow
      reports success -- the exact failure the fail-closed default existed
      for. That stays a generation error with **no** way to wave it
      through: ``--allow-empty`` used to accept it, which is precisely the
      capability this slice removes rather than renames.

    The classification is recorded on the plan either way (a reader of the
    artifact alone can tell the two apart), and the error is added only for
    the second, and only when nothing else already failed -- a plan that is
    empty *because* an explicit profile selector failed already says so.
    """
    declared = declared_check_count(config)
    if declared == 0:
        explanation = (
            "CONFIG declares no targets:/bundles: checks[], so this run-plan "
            "resolves no checks and every downstream matrix/aggregate step is "
            "skipped. This is a complete, valid plan for a project that has "
            "not declared any checks yet -- run `abicheck project validate "
            "CONFIG` to confirm the config itself is well formed, and add a "
            "checks[] entry when there is something to check."
        )
        report.warnings.append(f"run-plan is empty: {explanation}")
        return RunPlanSkip(SKIP_NO_CHECKS_DECLARED, declared, explanation)

    explanation = (
        f"CONFIG declares {declared} checks[] entr"
        f"{'y' if declared == 1 else 'ies'}, and none of them resolved to a "
        "(target, profile) cell -- usually no --build-output was supplied for "
        "the profiles they name, or a profile id does not match the one in "
        "its build-output.json. Every downstream matrix/aggregate step would "
        "be skipped, so this run-plan is not usable; supply the missing "
        "--build-output, or run `abicheck project validate CONFIG` to check "
        "the declarations themselves."
    )
    if report.ok:
        report.errors.append(f"run-plan resolved to zero checks: {explanation}")
    return RunPlanSkip(SKIP_CHECKS_DECLARED_NONE_RESOLVED, declared, explanation)


def schema_for_plan(
    declared: str,
    *,
    gate_schema: str,
    has_gate: bool,
    skipped: bool,
    check_count: int,
) -> str:
    """Which ``schema`` string a plan serializes under.

    Stamped in descending order of what an old reader must reject: ``skipped``
    is unreadable to a pre-v3 reader in a way that changes what the artifact
    means (see :data:`RUN_PLAN_SCHEMA_SKIPPED`), and a skipped plan has no
    checks for a ``gate`` policy to apply to. *gate_schema* is passed in
    rather than imported so this module stays a leaf of ``run_plan`` instead
    of importing it back. *declared* (whatever the plan
    was constructed with) only survives when neither applies -- a
    discriminator an old reader must see is never caller-overridable.

    A plan carrying checks *and* a skip is a contradiction, and is rejected
    here rather than written: the same invariant
    :func:`parse_skipped_block` enforces on read, so an artifact this tool
    refuses to produce is also one it refuses to accept.
    """
    if skipped and check_count:
        from ..workflows.aggregate import AggregateError

        raise AggregateError(
            "a RunPlan carrying checks cannot also declare `skipped` -- "
            f"{check_count} check(s) resolved"
        )
    if skipped:
        return RUN_PLAN_SCHEMA_SKIPPED
    if has_gate:
        return gate_schema
    return declared


def parse_skipped_block(
    d: Mapping[str, Any], *, schema: str, version: int | None
) -> RunPlanSkip | None:
    """Read a plan document's optional ``skipped`` block.

    Absent → ``None``. Present under a declared pre-v3 schema → rejected,
    for the reason :data:`RUN_PLAN_SCHEMA_SKIPPED` states. Present beside a
    non-empty ``checks`` list → rejected too: a hand-authored or corrupted
    artifact claiming both that the run was skipped and that it resolved
    checks is a contradiction, and accepting it would let
    ``to_aggregate_manifest()`` (which reads only ``checks``) project a
    perfectly ordinary expected-target set out of a document that says the
    run never happened.
    """
    if "skipped" not in d:
        return None
    from ..workflows.aggregate import AggregateError

    if version is None or version < 3:
        raise AggregateError(
            "run-plan 'skipped' requires 'schema' >= "
            f"'{RUN_PLAN_SCHEMA_SKIPPED}' (got {schema!r}); a pre-v3 reader "
            "would see only an empty checks[] and could not tell a "
            "deliberate, explained skip from a plan whose every check "
            "failed to resolve"
        )
    raw = d["skipped"]
    if not isinstance(raw, dict):
        raise AggregateError("run-plan 'skipped' must be an object")
    checks_raw = d.get("checks")
    if isinstance(checks_raw, list) and checks_raw:
        raise AggregateError(
            "run-plan declares both 'skipped' and a non-empty 'checks' list "
            f"({len(checks_raw)} check(s)) -- a plan cannot have been skipped "
            "and have resolved checks"
        )
    return RunPlanSkip.from_dict(raw)
