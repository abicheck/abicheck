# Token efficiency of `set-up-abi-compatibility-ci` — 2026-09-30

Question: what does the skill cost per run, where does that cost come from,
and can it be cut without losing quality? Same harness, same isolation, same
model (`claude-sonnet-5-5`), same 10 scenarios as `2026-09-30.md`. Breakdowns
from `token_report.py`.

## Where the money goes

A run's cost is roughly one third each: **cache reads** (the whole context —
Claude Code's ~35k-token system prompt, the skill text, and every tool result
so far — re-sent on every turn), **cache writes** (new content entering that
context once), and **output**. So two things drive cost: how much text sits in
the context, and how many turns re-send it. The skill's own text matters
twice — once written, then re-read on every later turn.

What agents actually read (35 runs of the pre-change skill): `SKILL.md`
(16.5 KB) always, via the Skill tool; `workflow-templates.md` and
`pitfalls.md` **every run**; `safety-invariants.md` in 27/35 although only
three of its eight items concern setup; `depth-toolchain-and-floors.md` in
21/35. About 35–40 KB, ~9–10k tokens, carried for the rest of the run.

## Change

- `SKILL.md` 16.5 → 8.6 KB with no rule removed: the step-4 checklist and
  `pitfalls.md` had restated each other; the inventory table became a list;
  the three relevant safety invariants are summarised inline (the link stays
  for the rest).
- `workflow-templates.md` (9 KB) split into one file per baseline strategy
  (1–5 KB), with "read only the template you chose".
- `pitfalls.md` is now read on demand (repairs, add-ons), not as part of the
  mandatory path.

## A generator bug found on the way — and why the first measurement was wrong

The first measurement of the trimmed skill (run 5) looked like a regression:
$0.272 vs $0.251, 14.2 vs 10.3 turns, 28/30. One agent reported the
release-baseline template as "corrupted". It was:
`scripts/gen_agent_skills.py` restored nested code-region masks in the wrong
order, so the published templates had whole workflow steps replaced by a
NUL-delimited placeholder — including the version already merged in #1429,
which runs 2–4 had used. Agents in those runs reconstructed the missing steps
themselves (which is part of why they spent turns and still scored well). The
generator is fixed and the round trip is now tested; every comparison below
uses trees rendered by the fixed generator.

## Result (30 runs per version, both fixed)

| | cost/run | turns | wall time | context | output | success |
|---|---|---|---|---|---|---|
| previous skill (as merged, fixed render) | $0.214 | 8.7 | 50 s | 351k | 4.4k | 30/30 |
| **trimmed skill** | **$0.167** | 8.0 | **37 s** | 283k | 3.6k | **30/30** |

−22% cost and −26% wall time at identical quality; cheaper in **every one**
of the ten scenarios (−11% to −34%). Reads in the trimmed version:
release-baseline template 27/30, merge-base 9/30, committed-snapshot 3/30,
`pitfalls.md` 15/30, `safety-invariants.md` 1/30.

For scale: the broken templates alone had cost ~15% ($0.251 in run 4 vs
$0.214 for the same skill rendered correctly), and the no-skill baseline
cost $0.14–0.25 per run while producing a usable workflow in 1 of 30 runs —
so the trimmed skill costs about what an unequipped agent does, and
succeeds.

## Lessons

- **Turns are the multiplier.** Every turn re-sends the whole context, so an
  instruction that saves one tool call is worth more than trimming a few
  hundred bytes. The negative run-5 result was a reminder that a change which
  makes agents open more files can cost more even when every file is smaller.
- **Measure against a correct artifact.** Two of three "measurements" in this
  file's history compared broken renders; the fix was found only by reading
  what the agents said about what they read.

Per-run grades: `2026-09-30-run5.json` (trimmed skill, broken render),
`2026-09-30-run6-v4.json`, `2026-09-30-run6-v5.json`.
