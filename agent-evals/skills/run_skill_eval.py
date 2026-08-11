#!/usr/bin/env python3
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

"""Grade a whole recorded batch and compare the two arms (G37 L2).

    python agent-evals/skills/run_skill_eval.py --runs <out-root>

The runner produces the transcripts; this reads them all and answers the one
question the A/B exists for — does equipping the agent with the skill change
the outcome — as a per-arm table plus the per-scenario detail behind it.

**It reports, it does not gate.** A first batch establishes what the numbers
are; making a number a floor before knowing whether the floor is a false green
is exactly the failure ADR-058 calls non-negotiable, so the publication gate
reads committed evidence rather than this command's exit status.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graders.dimensions import grade_run  # noqa: E402

PACK = Path(__file__).resolve().parent / "skill-eval-pack.json"

#: Mirrors runners/claude_code.py's FLAGSHIP_SKILL. The runner already keeps
#: a fresh --out root from *producing* new prototype-skill rows by default,
#: but that filter cannot retroactively clean an --out root created before
#: the 2026-08-11 scope freeze (docs/contribute/plans/
#: g37-agent-skill-quality-evaluation.md), or one built with
#: --include-prototype-skills. index.json still indexes every recorded row
#: regardless of which skill it belongs to, so this grading step is the
#: second, independent place the freeze must be enforced — without it, an
#: old or explicitly-opted-in prototype-skill row would silently fold into a
#: nominally flagship-only aggregate.
FLAGSHIP_SKILL = "native-binary-compatibility-review"


def _model_identity(row: dict) -> str | None:
    """Mirrors runners/claude_code.py's own `_model_identity`.

    Prefers the requested `--model` argument (may be an alias like
    `sonnet`) over the resolved model name, so a timed-out row pinned to
    the same `--model` as the rest of the batch compares equal to them
    instead of having no identity at all (`model` is unset for a timeout —
    see that module's `TimeoutExpired` handler).
    """
    requested = row.get("requested_model")
    if isinstance(requested, str):
        return requested
    model = row.get("model")
    return model if isinstance(model, str) else None


def _pct(part: int, whole: int) -> str:
    return "—" if not whole else f"{100 * part / whole:.0f}%"


def summarize(runs: list[dict]) -> dict:
    total = len(runs)
    return {
        "runs": total,
        "correct": sum(1 for r in runs if r["correct"]),
        "ran_a_comparison": sum(1 for r in runs if r["comparisons"] > 0),
        "claim_present": sum(1 for r in runs if r["claim_status"] == "ok"),
        "zero_tolerance_failures": sum(1 for r in runs if r["zero_tolerance_failed"]),
        "dimension_pass": {
            str(d): sum(
                1
                for r in runs
                for dim in r["dimensions"]
                if dim["dimension"] == d and dim["status"] == "pass"
            )
            for d in (1, 2, 3, 6)
        },
        "dimension_applicable": {
            str(d): sum(
                1
                for r in runs
                for dim in r["dimensions"]
                if dim["dimension"] == d and dim["status"] != "not_applicable"
            )
            for d in (1, 2, 3, 6)
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", required=True, help="The runner's --out root")
    parser.add_argument("--json", help="Write the full grading to this path")
    parser.add_argument(
        "--include-prototype-skills",
        action="store_true",
        help=(
            "Also grade recorded rows for prototype-status skills (all "
            f"skills other than {FLAGSHIP_SKILL}). Off by default per G37's "
            "2026-08-11 scope note."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.runs)
    index_path = root / "index.json"
    if not index_path.is_file():
        print(f"no index.json under {root}", file=sys.stderr)
        return 1

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))

    graded: list[dict] = []
    orphaned: set[str] = set()
    excluded_prototype: set[str] = set()
    graded_models: set[str] = set()
    unknown_model: set[str] = set()
    for row in index:
        sid, arm, rep = row["scenario_id"], row["arm"], row["repetition"]
        run_dir = root / sid / arm / str(rep)
        if not run_dir.is_dir():
            continue
        if sid not in pack["scenarios"]:
            # Renaming a scenario or flipping it out of the corpus is ordinary
            # work, so recorded runs and the current pack legitimately diverge.
            # Indexing the pack directly turned that into a KeyError that
            # printed no summary at all, discarding every other gradeable run.
            orphaned.add(sid)
            continue
        if not args.include_prototype_skills and pack["scenarios"][sid].get(
            "skill"
        ) != FLAGSHIP_SKILL:
            # A prototype-skill row can reach index.json even though the
            # runner no longer schedules new ones by default — an --out root
            # created before the freeze, or built with the runner's own
            # --include-prototype-skills. Excluding it here, not just at
            # scheduling time, is what keeps a stale or opted-in row from
            # silently folding into a nominally flagship-only aggregate.
            excluded_prototype.add(sid)
            continue
        grade = grade_run(run_dir, pack["scenarios"][sid], arm)
        grade.update(scenario_id=sid, arm=arm, repetition=rep)
        graded.append(grade)
        identity = _model_identity(row)
        if identity is not None:
            graded_models.add(identity)
        else:
            # A timed-out run with no --model pin has a genuinely unknown
            # model (see runners/claude_code.py's TimeoutExpired handler) —
            # not a value that happens to be missing. It is graded (a
            # timeout is a real, meaningful result for dimensions 1/3), but
            # letting it sail through the mixed-model check below purely
            # because it contributes no identity to compare against would
            # accept exactly the batch that check exists to refuse: known
            # model X on some rows, silently-unproven model on this one.
            unknown_model.add(f"{sid}/{row.get('arm')}/{row.get('repetition')}")

    if unknown_model and graded_models:
        print(
            "runs graded here include rows with no recorded model alongside "
            f"rows recorded under {', '.join(sorted(graded_models))} — not "
            "provably the same model, so not provably attributable to the "
            "skill: " + ", ".join(sorted(unknown_model)),
            file=sys.stderr,
        )
        return 1

    if len(graded_models) > 1:
        # The runner's own check_one_model refuses a mixed-model batch at
        # record time, but it is scoped per invocation (in-scope skill set,
        # not the whole on-disk index) — see the 2026-08-11 scope-freeze
        # commits. Two separately-consistent invocations (e.g. a flagship
        # run under one model, a prototype run under another, later combined
        # here via --include-prototype-skills) can still reach this grading
        # step with two models across what it is about to aggregate solely
        # by arm. Refusing here, not just warning, matches CLAUDE.md's
        # documented invariant: "a batch that mixes two [models] is
        # refused" — an arm-to-arm difference would not be attributable to
        # the skill if it might instead be a model difference.
        print(
            f"runs graded here used more than one model ({', '.join(sorted(graded_models))}); "
            f"an apparent skill/baseline difference would not be attributable to the skill. "
            f"Split --runs into separate per-model output roots.",
            file=sys.stderr,
        )
        return 1

    if orphaned:
        print(
            "skipped runs for scenario(s) the pack no longer lists: "
            + ", ".join(sorted(orphaned)),
            file=sys.stderr,
        )
    if excluded_prototype:
        print(
            "skipped runs for prototype-status skill scenario(s) (pass "
            "--include-prototype-skills to grade them): "
            + ", ".join(sorted(excluded_prototype)),
            file=sys.stderr,
        )

    if not graded:
        print("no run directories found to grade", file=sys.stderr)
        return 1

    by_arm: dict[str, list[dict]] = defaultdict(list)
    for grade in graded:
        by_arm[grade["arm"]].append(grade)

    report = {
        "arms": {arm: summarize(runs) for arm, runs in sorted(by_arm.items())},
        "runs": graded,
    }

    print(f"{'':<26}{'skill':>12}{'baseline':>12}")
    skill, base = by_arm.get("skill", []), by_arm.get("baseline", [])
    rows = [
        ("runs graded", len(skill), len(base), None),
        (
            "correct verdict",
            sum(1 for r in skill if r["correct"]),
            sum(1 for r in base if r["correct"]),
            True,
        ),
        (
            "ran a comparison",
            sum(1 for r in skill if r["comparisons"] > 0),
            sum(1 for r in base if r["comparisons"] > 0),
            True,
        ),
        (
            "claim well-formed",
            sum(1 for r in skill if r["claim_status"] == "ok"),
            sum(1 for r in base if r["claim_status"] == "ok"),
            True,
        ),
        (
            "zero-tolerance failures",
            sum(1 for r in skill if r["zero_tolerance_failed"]),
            sum(1 for r in base if r["zero_tolerance_failed"]),
            True,
        ),
    ]
    for label, s, b, ratio in rows:
        s_text = f"{s} ({_pct(s, len(skill))})" if ratio else str(s)
        b_text = f"{b} ({_pct(b, len(base))})" if ratio else str(b)
        print(f"{label:<26}{s_text:>12}{b_text:>12}")

    print("\nper scenario (correct verdict, skill vs baseline):")
    for sid in sorted({g["scenario_id"] for g in graded}):
        s = [g for g in graded if g["scenario_id"] == sid and g["arm"] == "skill"]
        b = [g for g in graded if g["scenario_id"] == sid and g["arm"] == "baseline"]
        expected = next(iter(s + b))["expected_verdict"]
        print(
            f"  {sid:<24} {sum(1 for g in s if g['correct'])}/{len(s)}"
            f"   {sum(1 for g in b if g['correct'])}/{len(b)}   (expected {expected})"
        )

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nfull grading written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
