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

SKILLS_SRC = Path(__file__).resolve().parents[2] / "skills-src"


def evaluated_skills() -> frozenset[str]:
    """The published portfolio; mirrors runners/claude_code.py's own.

    The runner keeps a fresh --out root from *producing* rows for skills that
    are no longer published, but cannot retroactively clean an older root or
    one built with --include-prototype-skills. index.json indexes every row
    regardless of skill, so grading is the second, independent place the
    filter applies: a retired skill's row must not fold into the published
    skills' aggregates.
    """
    return frozenset(p.name for p in SKILLS_SRC.iterdir() if (p / "SKILL.md").is_file())


def _model_label(row: dict) -> str | None:
    """Mirrors runners/claude_code.py's own `_model_label`.

    Resolved name first — real evidence of what the CLI actually ran, and
    the only thing that would catch an alias like `sonnet` silently
    resolving to a different version between two runs — falling back to
    the requested `--model` argument only when no resolved name was
    captured (a timeout, which never reaches the CLI's init event; see
    that module's `TimeoutExpired` handler).
    """
    model = row.get("model")
    if isinstance(model, str):
        return model
    requested = row.get("requested_model")
    return requested if isinstance(requested, str) else None


def _models_compatible(a: dict, b: dict) -> bool:
    """Mirrors runners/claude_code.py's own `_models_compatible` exactly.

    Kept as an incremental pairwise check here too, not folded into flat
    per-field sets: a row can carry *both* a resolved name and a requested
    identity (a completed, pinned run), and a set keyed by "resolved if
    present, else requested" would silently drop that row's resolved
    identity from consideration in a comparison that should have used it.
    See that module's docstring for the full reasoning, including why a
    requested alias is compared only when *neither* row has a resolved
    name — Claude Code documents an alias like `sonnet` as pointing at
    "the latest" model, a moving target, so it is trusted only when it is
    the single best evidence available on both sides.
    """
    a_model, b_model = a.get("model"), b.get("model")
    if isinstance(a_model, str) and isinstance(b_model, str):
        return a_model == b_model
    a_requested, b_requested = a.get("requested_model"), b.get("requested_model")
    if (
        not isinstance(a_model, str)
        and not isinstance(b_model, str)
        and isinstance(a_requested, str)
        and isinstance(b_requested, str)
    ):
        return a_requested == b_requested
    a_has_identity = isinstance(a_model, str) or isinstance(a_requested, str)
    b_has_identity = isinstance(b_model, str) or isinstance(b_requested, str)
    if a_has_identity and b_has_identity:
        return False
    return True


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


def _print_table(graded: list[dict]) -> None:
    """One skill's skill-vs-baseline table plus its per-scenario detail.

    Printed per skill: the skills answer different questions, and one table
    pooling them would let a strong result on one hide a weak one on the
    other. "correct answer" is the verdict, plus the root cause for a
    scenario that names one (see graders.dimensions.grade_run).
    """
    skill = [g for g in graded if g["arm"] == "skill"]
    base = [g for g in graded if g["arm"] == "baseline"]
    print(f"{'':<26}{'skill':>12}{'baseline':>12}")
    rows = [
        ("runs graded", len(skill), len(base), None),
        (
            "correct answer",
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
    for label, s_count, b_count, ratio in rows:
        s_text = f"{s_count} ({_pct(s_count, len(skill))})" if ratio else str(s_count)
        b_text = f"{b_count} ({_pct(b_count, len(base))})" if ratio else str(b_count)
        print(f"{label:<26}{s_text:>12}{b_text:>12}")

    print("\nper scenario (correct answer, skill vs baseline):")
    for sid in sorted({g["scenario_id"] for g in graded}):
        s_runs = [g for g in skill if g["scenario_id"] == sid]
        b_runs = [g for g in base if g["scenario_id"] == sid]
        first = next(iter(s_runs + b_runs))
        expected = first["expected_verdict"]
        if first.get("expected_cause"):
            expected = f"{expected}, {first['expected_cause']}"
        print(
            f"  {sid:<24} {sum(1 for g in s_runs if g['correct'])}/{len(s_runs)}"
            f"   {sum(1 for g in b_runs if g['correct'])}/{len(b_runs)}   (expected {expected})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--runs",
        required=True,
        action="append",
        help=(
            "The runner's --out root. Repeat to grade several roots as one "
            "batch (e.g. scenario subsets run in parallel); the one-model "
            "check below then spans all of them."
        ),
    )
    parser.add_argument("--json", help="Write the full grading to this path")
    parser.add_argument(
        "--include-prototype-skills",
        action="store_true",
        help=(
            "Also grade recorded rows for skills that are no longer "
            "published (retired prototypes). Off by default."
        ),
    )
    args = parser.parse_args(argv)

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    index: list[tuple[Path, dict]] = []
    for raw in args.runs:
        root = Path(raw)
        index_path = root / "index.json"
        if not index_path.is_file():
            print(f"no index.json under {root}", file=sys.stderr)
            return 1
        index.extend(
            (root, row) for row in json.loads(index_path.read_text(encoding="utf-8"))
        )

    graded: list[dict] = []
    orphaned: set[str] = set()
    excluded_prototype: set[str] = set()
    model_rows: list[dict] = []
    unknown_model: set[str] = set()
    for root, row in index:
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
        if (
            not args.include_prototype_skills
            and pack["scenarios"][sid].get("skill") not in evaluated_skills()
        ):
            # A prototype-skill row can reach index.json even though the
            # runner no longer schedules new ones by default — an --out root
            # created before the freeze, or built with the runner's own
            # --include-prototype-skills. Excluding it here, not just at
            # scheduling time, is what keeps a stale or opted-in row from
            # silently folding into a nominally flagship-only aggregate.
            excluded_prototype.add(sid)
            continue
        grade = grade_run(run_dir, pack["scenarios"][sid], arm)
        grade.update(
            scenario_id=sid,
            arm=arm,
            repetition=rep,
            runs_root=str(root),
            skill=pack["scenarios"][sid].get("skill"),
        )
        graded.append(grade)
        if isinstance(row.get("model"), str) or isinstance(
            row.get("requested_model"), str
        ):
            model_rows.append(row)
        else:
            # A timed-out run with no --model pin has a genuinely unknown
            # model (see runners/claude_code.py's TimeoutExpired handler) —
            # not a value that happens to be missing. It is graded (a
            # timeout is a real, meaningful result for dimensions 1/3), but
            # letting it sail past the pairwise check below purely because
            # it contributes no identity to compare against would accept
            # exactly the batch that check exists to refuse: known model X
            # on some rows, silently-unproven model on this one.
            unknown_model.add(f"{root}:{sid}/{arm}/{rep}")

    if unknown_model and model_rows:
        known = sorted({label for r in model_rows if (label := _model_label(r))})
        print(
            "runs graded here include rows with no recorded model alongside "
            f"rows recorded under {', '.join(known)} — not "
            "provably the same model, so not provably attributable to the "
            "skill: " + ", ".join(sorted(unknown_model)),
            file=sys.stderr,
        )
        return 1

    # Pairwise against every row already accepted, not one flat identity set
    # per row: a row can carry *both* a resolved name and a requested
    # identity (a completed, pinned run), and collapsing that down to a
    # single "best" identity per row — the way an earlier revision of this
    # check did — silently drops the field a sibling timeout under the same
    # pin actually needs to compare against. See _models_compatible's own
    # docstring for the full reasoning.
    for i, row in enumerate(model_rows):
        for earlier in model_rows[:i]:
            if not _models_compatible(earlier, row):
                sid, arm, rep = row["scenario_id"], row["arm"], row["repetition"]
                print(
                    f"{sid}/{arm}/{rep} used {_model_label(row)} while an "
                    f"earlier graded run used {_model_label(earlier)}; an "
                    "apparent skill/baseline difference would not be "
                    "attributable to the skill. Split --runs into separate "
                    "per-model output roots.",
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
        "skills": {
            name: {
                arm: summarize([g for g in runs if g["skill"] == name])
                for arm, runs in sorted(by_arm.items())
                if any(g["skill"] == name for g in runs)
            }
            for name in sorted({g["skill"] for g in graded})
        },
        "runs": graded,
    }

    skills = sorted({g["skill"] for g in graded})
    for name in skills:
        if len(skills) > 1:
            print(f"== {name}")
        _print_table([g for g in graded if g["skill"] == name])
        print()

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nfull grading written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
