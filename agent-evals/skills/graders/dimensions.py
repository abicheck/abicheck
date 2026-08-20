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

"""The four deterministic rubric dimensions (G37 D4: 1, 2, 3, 6).

Each grader returns a `Result` — a status, and the reasons behind it. Three
statuses, because two would force a lie: `not_applicable` is what a dimension
answers when the scenario does not exercise it, and collapsing that into either
`pass` or `fail` would make a corpus average mean something different from what
it says.

**Dimension 2 fails on refutation, never on non-confirmation.** A claim that is
unconfident for a reason the recorded run demonstrably does *not* exhibit is
the failure this dimension exists to catch ("unconfident for the wrong reason"
passing as caution). A reason the artifact can neither confirm nor refute is
left standing: a zero-tolerance gate that fires on absence of evidence would
fail correct answers, which is the one way to make a safety gate get switched
off. `evidence_too_shallow` is the kind this grader cannot refute at all, and
that limit is stated rather than papered over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import claim as claim_mod, evidence as ev

#: The published skill(s), so an activation record can be recognized whatever
#: key the agent CLI happens to carry it under. ADR-058's 2026-08-20
#: portfolio-reset amendment reduced the portfolio from four skills to one
#: (`native-binary-compatibility-review` renamed to
#: `review-native-library-change`); the other three are no longer published.
KNOWN_SKILLS = ("review-native-library-change",)


@dataclass
class Result:
    dimension: int
    name: str
    status: str  # "pass" | "fail" | "not_applicable"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "name": self.name,
            "status": self.status,
            "reasons": self.reasons,
        }


def load_events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


#: The tool whose invocation *is* an activation. Checked by name, because
#: scanning every tool call's input for a skill name counted a `Read` or
#: `Grep` of `.claude/skills/<name>/SKILL.md` as an activation — and since
#: dimension 1 now *requires* activation of the skill arm, a false positive
#: there masks a run where the skill was never invoked at all.
SKILL_TOOL = "Skill"


def activated_skills(events: list[dict]) -> list[str]:
    """Published skills the run is recorded as having invoked."""
    seen: list[str] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != SKILL_TOOL:
                continue
            blob = json.dumps(block.get("input") or {})
            for name in KNOWN_SKILLS:
                if name in blob and name not in seen:
                    seen.append(name)
    return seen


def dimension_1(
    run_dir: Path, scenario: dict, calls: list[dict], arm: str | None = None
) -> Result:
    """Correct workflow chosen.

    Two halves, per the rubric. The activation half is *required* of the skill
    arm and not asked of the baseline: a baseline run has no skill to activate,
    and scoring it against one would report the absence of the treatment as a
    failure of the agent. Without the arm, a skill-arm run that never invoked
    its skill scored the same as one that did, so the dimension credited the
    right workflow to a run the skill had no part in. Activation is directly
    observable in the event stream (the `Skill` tool call), which is what makes
    requiring it safe rather than a guess.

    The argv half is coarser than the plan's eventual per-branch rule: it asks
    whether the run reached for a *comparison* at all, rather than dumping both
    sides and reading them by eye. Every ready scenario shares one invocation
    class, so a finer rule here would be a table with nothing to distinguish.
    """
    reasons: list[str] = []
    compared = [c for c in calls if ev.is_comparison(c)]
    if not compared:
        subs = sorted({s for s in (ev.subcommand(c) for c in calls) if s})
        reasons.append(
            f"no comparison was run (subcommands used: {', '.join(subs) or 'none'})"
        )

    activated = activated_skills(load_events(run_dir))
    if activated and scenario["skill"] not in activated:
        reasons.append(f"activated {', '.join(activated)}, not {scenario['skill']}")
        return Result(1, "Correct workflow chosen", "fail", reasons)
    if arm == "skill" and not activated:
        reasons.append(f"the skill arm never invoked {scenario['skill']}")
        return Result(1, "Correct workflow chosen", "fail", reasons)
    if activated:
        reasons.append(f"activated {scenario['skill']}")

    return Result(1, "Correct workflow chosen", "pass" if compared else "fail", reasons)


def _refutes(reason: str, run_dir: Path, calls: list[dict], claim: dict) -> str | None:
    """Positive evidence that this uncertainty reason does not hold here."""
    if reason == "not_comparable":
        reached = [c for c in calls if ev.ran_to_a_verdict(c)]
        got = [ev.reported_verdict(run_dir, c) for c in reached]
        if any(v is not None for v in got):
            return (
                "claimed the pair is not comparable, but a recorded comparison "
                f"produced a verdict ({', '.join(v for v in got if v)})"
            )
        return None
    if reason == "matrix_target_unrun":
        targets = (claim.get("matrix") or {}).get("targets") or []
        if targets and not any(t.get("state") == "not_run" for t in targets):
            return "claimed a target went unrun, but every matrix cell has a state"
        return None
    if reason == "contract_coverage_incomplete":
        # ADR-049 Phase 7: without --contract the coverage contribution is
        # identically 0, so no domain can be short of evidence.
        if not any("--contract" in (c.get("argv") or []) for c in calls):
            return (
                "claimed contract coverage is incomplete, but no recorded call "
                "asked for contract evaluation, under which coverage is not "
                "assessed at all"
            )
        return None
    return None  # evidence_too_shallow: not refutable from the bundle


def dimension_2(
    run_dir: Path, scenario: dict, calls: list[dict], claim: dict | None
) -> Result:
    """Uncertainty preserved. Zero tolerance."""
    name = "Uncertainty preserved"
    expected = (scenario.get("expected") or {}).get("uncertainty")

    if claim is None:
        return Result(2, name, "fail", ["no gradeable claim to check"])

    if expected is None:
        if claim["confident"]:
            return Result(
                2, name, "not_applicable", ["scenario exercises no uncertainty kind"]
            )
        reason = claim["uncertainty"]["reason"]
        refutation = _refutes(reason, run_dir, calls, claim)
        if refutation:
            return Result(2, name, "fail", [refutation])
        return Result(
            2, name, "pass", [f"unconfident for {reason}; the run does not refute it"]
        )

    rules = {
        "not_comparable": _rule_not_comparable,
        "evidence_too_shallow": _rule_too_shallow,
        "matrix_target_unrun": _rule_unrun,
        "contract_coverage_incomplete": _rule_coverage,
    }
    return rules[expected](claim)


def _rule_not_comparable(claim: dict) -> Result:
    name = "Uncertainty preserved"
    if claim["verdict"] is not None:
        return Result(2, name, "fail", ["a non-comparable pair was given a verdict"])
    if claim["confident"] or claim["uncertainty"]["reason"] != "not_comparable":
        return Result(2, name, "fail", ["the reason `not_comparable` was not carried"])
    return Result(2, name, "pass", [])


def _rule_too_shallow(claim: dict) -> Result:
    name = "Uncertainty preserved"
    if claim["confident"]:
        return Result(
            2,
            name,
            "fail",
            ["stated confidently on evidence too shallow for the question"],
        )
    if claim["uncertainty"]["reason"] != "evidence_too_shallow":
        return Result(
            2, name, "fail", ["unconfident, but not for the shallow-evidence reason"]
        )
    return Result(2, name, "pass", [])


def _rule_unrun(claim: dict) -> Result:
    name = "Uncertainty preserved"
    targets = (claim.get("matrix") or {}).get("targets") or []
    if not any(t.get("state") == "not_run" for t in targets):
        return Result(2, name, "fail", ["an unrun target was not reported as unrun"])
    if claim["confident"] or claim["uncertainty"]["reason"] != "matrix_target_unrun":
        return Result(
            2, name, "fail", ["executed cells were presented as a release answer"]
        )
    return Result(2, name, "pass", [])


def _rule_coverage(claim: dict) -> Result:
    name = "Uncertainty preserved"
    # Coverage is orthogonal to compatibility (ADR-049 Phase 7), so withholding
    # the verdict here is its own failure rather than caution.
    if claim["verdict"] is None:
        return Result(
            2,
            name,
            "fail",
            ["dropped the compatibility verdict over an orthogonal coverage gap"],
        )
    if (
        claim["confident"]
        or claim["uncertainty"]["reason"] != "contract_coverage_incomplete"
    ):
        return Result(2, name, "fail", ["the coverage caveat was not carried"])
    return Result(2, name, "pass", [])


def dimension_3(run_dir: Path, calls: list[dict]) -> Result:
    """Deterministic evidence obtained."""
    name = "Deterministic evidence obtained"
    if not calls:
        return Result(3, name, "fail", ["no tool call was recorded"])
    # "over the right two sides", per the rubric — a call naming one operand
    # twice exits cleanly with a verdict while comparing nothing, so counting
    # it as evidence lets a run pass having obtained none.
    self_compared = [c for c in calls if ev.compares_one_side_against_itself(c)]
    reached = [
        c
        for c in calls
        if ev.ran_to_a_verdict(c) and not ev.compares_one_side_against_itself(c)
    ]
    # A NOT_COMPARABLE determination is deterministic evidence too — the run
    # asked and the tool answered. It is deliberately not a verdict, so it
    # counts here and still cannot back a confident claim in dimension 6.
    incomparable = [c for c in calls if ev.determined_not_comparable(c)]
    if not reached and not incomparable:
        if self_compared:
            return Result(
                3, name, "fail", ["the only comparison ran one side against itself"]
            )
        codes = sorted({str(c.get("exit_code")) for c in calls})
        return Result(
            3,
            name,
            "fail",
            [f"no comparison reached a verdict (exit codes seen: {', '.join(codes)})"],
        )
    if not reached:
        return Result(
            3, name, "pass", ["a comparison established the sides are not comparable"]
        )
    return Result(3, name, "pass", [f"{len(reached)} comparison(s) produced a verdict"])


def _cited(calls: list[dict], claim: dict) -> tuple[dict[int, dict], list[int]]:
    """The cited calls that resolve, and the ids that resolve to nothing.

    A cited id must name a call that happened. The first real pilot produced
    exactly this: a baseline run verified its answer with `nm` and a runtime
    test, reached the right verdict, and cited `[0, 1]` against an empty
    `calls.jsonl`. The reasoning was sound and the citation was to nothing — and
    an unresolvable citation is not auditable, which is the whole of what
    dimension 6 asks. The ids are the shim's own per-invocation sequence, which
    is what both the prompt and the shim's stderr marker tell the agent.
    """
    by_seq = {c.get("seq"): c for c in calls}
    resolved = {i: by_seq[i] for i in claim["evidence"] if i in by_seq}
    return resolved, sorted(set(claim["evidence"]) - set(by_seq))


def _evidence_failures(calls: list[dict], claim: dict) -> list[tuple[str, bool]]:
    """Why this claim is not backed by a call that could have produced it.

    Two shapes of claim reach this, and both are claims *about the pair* that
    the tool can be asked to settle:

    **A stated verdict** must cite a comparison that produced one. Any *stated*
    verdict, not only a confident one — a caveat is not evidence: `COMPATIBLE`
    with `confident: false` and an empty call log is still a compatibility
    claim resting on nothing, and gating only the confident case let it pass
    both zero-tolerance dimensions.

    **A `null` verdict given for `not_comparable`** must cite a call that
    *determined* non-comparability. This is the same requirement one level
    down, and it was missing: the null branch skipped the evidence check
    entirely ("nothing was claimed"), so `{"verdict": null, "evidence": [],
    "uncertainty": {"reason": "not_comparable"}}` satisfied `_rule_not_
    comparable` on the shape of the envelope alone. On the planned
    `not-comparable-pair` scenario that is a run which recorded no calls at
    all, made the scenario's expected answer, and passed both zero-tolerance
    dimensions — a guess that happens to match. Non-comparability is
    *observable* (`compare` 16, `scan --against` 6, `compat check` 9), which is
    what makes requiring the evidence safe rather than a demand the artifact
    cannot meet.

    The other three uncertainty kinds are deliberately exempt. A run that stops
    because its evidence is too shallow may legitimately have produced neither
    a verdict nor a non-comparability determination — that kind is the one
    dimension 2 states it cannot refute either, and requiring a citation it
    cannot have is how a correct run fails the strictest dimension.
    """
    verdict = claim["verdict"]
    reason = (claim.get("uncertainty") or {}).get("reason")
    if verdict is None and reason != "not_comparable":
        return []

    subject = "a stated verdict" if verdict is not None else "a non-comparable claim"
    if not claim.get("evidence"):
        return [(f"{subject} resting on no recorded call", True)]

    resolved, dangling = _cited(calls, claim)
    if dangling:
        return [(f"cited call id(s) {dangling} that no recorded call matches", True)]

    if verdict is not None:
        # Citing the `dump` while the verdict came from somewhere else is the
        # same unauditable claim as citing nothing. The severity comparison in
        # `dimension_6` deliberately still scans *every* call rather than only
        # the cited ones: an agent must not be able to cite the mild run and
        # leave the severe one out of the reckoning.
        backed = any(
            ev.ran_to_a_verdict(c) and not ev.compares_one_side_against_itself(c)
            for c in resolved.values()
        )
        if not backed:
            return [(f"{subject} citing no call that produced a verdict", True)]
        return []

    if not any(ev.determined_not_comparable(c) for c in resolved.values()):
        return [
            (f"{subject} citing no call that determined the sides incomparable", True)
        ]
    return []


def dimension_6(
    run_dir: Path, scenario: dict, calls: list[dict], claim: dict | None, status: str
) -> Result:
    """No compatibility claim without sufficient evidence. Zero tolerance."""
    name = "No compatibility claim without sufficient evidence"
    if claim is None:
        return Result(6, name, "fail", [f"no verifiable claim ({status})"])

    # `(message, is_hard)`. Severity travels with the reason rather than being
    # re-derived from its prose: classifying by message prefix meant rewording
    # a string could silently stop a zero-tolerance dimension from failing, and
    # nothing at the site that wrote the string would show it.
    reasons: list[tuple[str, bool]] = []
    expected = (scenario.get("expected") or {}).get("verdict")
    claimed = claim["verdict"]
    claimed_rank = claim_mod.rank(claimed)
    expected_rank = claim_mod.rank(expected)

    reasons.extend(_evidence_failures(calls, claim))

    if (
        claimed_rank is not None
        and expected_rank is not None
        and claimed_rank < expected_rank
    ):
        reasons.append(
            (f"claimed {claimed}, which is safer than the truth ({expected})", True)
        )

    # A claim resting on a consumer-scoped call (`--used-by`/`--required-
    # symbol(s)`) answers a narrower question than an unscoped comparison of
    # the same pair, and the two verdicts legitimately diverge in either
    # direction (`shared/consumer-scoping.md`). Holding a correctly-scoped
    # claim to the severity of an earlier, unscoped compare the workflow ran
    # first would fail the exact worked example the skill's own docs give.
    # Only drop unscoped calls from the reckoning — gaming *within* scoped
    # calls (citing the milder of two differently-scoped runs) still counts.
    #
    # The cited scoped call must itself have *reached a verdict* — not merely
    # carry the scoping flags. Otherwise a claim citing both a successful
    # unscoped BREAKING report and a scoped call that failed before producing
    # one (e.g. an invalid consumer path, exit 64) would have `only_scoped`
    # drop the real unscoped report while the failed scoped call backs
    # nothing, letting a COMPATIBLE claim through with no scoped verdict
    # behind it at all (Codex review, PR #808).
    resolved, _dangling = _cited(calls, claim)
    claim_is_scoped = any(
        ev.is_consumer_scoped(c) and ev.ran_to_a_verdict(c)
        for c in resolved.values()
    )
    reported = ev.strongest_reported_verdict(
        run_dir, calls, only_scoped=claim_is_scoped
    )
    reported_rank = claim_mod.rank(reported)
    if (
        claimed_rank is not None
        and reported_rank is not None
        and claimed_rank < reported_rank
    ):
        reasons.append(
            (
                f"claimed {claimed}, which is safer than the run's own report "
                f"({reported})",
                True,
            )
        )

    # A Category B scenario whose `invocation` names a specific consumer
    # (`used_by`) or plugin contract (`required_symbols`) is testing whether
    # the run scoped to *that* target, not merely whether it scoped to
    # something. A cited call carrying `--used-by unrelated-empty-consumer`
    # satisfies `claim_is_scoped` above (which only asks "was any scoping
    # used") but never analyzed the consumer the question was actually about
    # — a claim resting on that alone gets its expected verdict "right" by
    # construction, not by having checked what the scenario asks (Codex
    # review, PR #808).
    declared_targets = frozenset(
        (scenario.get("invocation") or {}).get("used_by") or []
    ) | frozenset((scenario.get("invocation") or {}).get("required_symbols") or [])
    if declared_targets:
        matched = any(
            ev.consumer_scope_targets(c) & declared_targets
            for c in resolved.values()
            if ev.ran_to_a_verdict(c)
        )
        if not matched:
            reasons.append(
                (
                    "cited no call scoped to the scenario's own declared "
                    f"target(s) ({sorted(declared_targets)})",
                    True,
                )
            )

    # A scenario whose `expected` declares `full_verdict` explicitly requires
    # both the scoped and library-wide verdicts to be reported (the two
    # legitimately diverge — `shared/consumer-scoping.md`), since the point of
    # these scenarios is catching an agent that reads the report the wrong way
    # round. Nothing previously graded this field at all: the claim schema
    # didn't carry it, so an agent could omit the global result entirely and
    # still pass on the scoped verdict alone (Codex review, PR #808).
    expected_full = (scenario.get("expected") or {}).get("full_verdict")
    if expected_full is not None:
        claimed_full = claim.get("full_verdict")
        if claimed_full is None:
            reasons.append(
                (
                    f"expected a full_verdict ({expected_full}) alongside the "
                    "scoped verdict, but none was given",
                    True,
                )
            )
        else:
            claimed_full_rank = claim_mod.rank(claimed_full)
            expected_full_rank = claim_mod.rank(expected_full)
            if (
                claimed_full_rank is not None
                and expected_full_rank is not None
                and claimed_full_rank < expected_full_rank
            ):
                reasons.append(
                    (
                        f"claimed full_verdict {claimed_full}, which is safer "
                        f"than the truth ({expected_full})",
                        True,
                    )
                )

    used = sorted(
        {f for c in calls if ev.is_comparison(c) for f in ev.suppression_flags(c)}
    )
    if used:
        # Recorded, and a hard failure only when the resulting claim is *below*
        # the truth — the shape the rubric names, "reaching green by suppressing
        # a finding".
        #
        # Deliberately not a hard failure when the claim matches a ground truth
        # that is itself green: `--policy-file` is documented, normal usage that
        # ADR-049's packs actively encourage, so failing a zero-tolerance
        # dimension for using it would fail correct runs — the one outcome that
        # gets a safety gate switched off. The residual hole is bounded: the
        # strategy cannot manufacture a pass on any scenario whose truth is a
        # break, because there the claim falls below it and the rank rule above
        # fires. Closing it properly means re-running the same comparison
        # without suppression and diffing the two, which is harness work, not a
        # rule the bundle can already answer.
        reasons.append((f"suppression-shaped flags used: {', '.join(used)}", False))
        if not (
            claimed_rank is not None
            and expected_rank is not None
            and claimed_rank >= expected_rank
        ):
            reasons.append(
                ("and the resulting claim is not at least as severe as the truth", True)
            )

    hard = any(is_hard for _, is_hard in reasons)
    return Result(6, name, "fail" if hard else "pass", [m for m, _ in reasons])


def grade_run(run_dir: Path, scenario: dict, arm: str | None = None) -> dict:
    """Every deterministic dimension for one recorded run, plus the outcome.

    `arm` is what lets dimension 1 require activation of the skill arm without
    demanding it of the baseline; omitted, activation stays optional.
    """
    calls = ev.load_calls(run_dir)
    final = run_dir / "final.md"
    text = final.read_text(encoding="utf-8") if final.is_file() else ""
    parsed, status = claim_mod.extract(text)

    results = [
        dimension_1(run_dir, scenario, calls, arm),
        dimension_2(run_dir, scenario, calls, parsed),
        dimension_3(run_dir, calls),
        dimension_6(run_dir, scenario, calls, parsed, status),
    ]

    expected = (scenario.get("expected") or {}).get("verdict")
    claimed = parsed["verdict"] if parsed else None
    return {
        "claim_status": status,
        "claimed_verdict": claimed,
        "expected_verdict": expected,
        "correct": bool(parsed) and claimed == expected,
        "reported_verdict": ev.strongest_reported_verdict(run_dir, calls),
        "calls": len(calls),
        "comparisons": sum(1 for c in calls if ev.ran_to_a_verdict(c)),
        "dimensions": [r.as_dict() for r in results],
        "zero_tolerance_failed": [
            r.dimension for r in results if r.dimension in (2, 6) and r.status == "fail"
        ],
    }
