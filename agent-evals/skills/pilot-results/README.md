# G37 Phase 3 pilot — `review-native-library-change`, 2026-08-20

The first real run of `agent-evals/skills/`'s A/B harness against the
full 12-scenario corpus. **This is a pilot, not a validation.** 24 runs
per arm (12 scenarios × 2 repetitions), one model
(`claude-sonnet-5`, confirmed single-model across every run), one
headless environment. It exists to produce real numbers where none
existed before and to surface what the harness itself gets wrong before
a larger run is worth spending on — not to certify the skill as
behaviorally validated. ADR-058 continues to designate the skill an
**internal candidate**; nothing here changes that.

## Headline numbers

|                        | skill      | baseline   |
|------------------------|------------|------------|
| runs graded            | 24         | 24         |
| correct verdict        | 9 (38%)    | 9 (38%)    |
| ran a comparison (dim 1)| 23 (96%)  | 6 (25%)    |
| claim well-formed       | 9 (38%)   | 11 (46%)   |
| zero-tolerance failures (dim 2/6)| 16 (67%) | 21 (88%) |

Read the "correct verdict" row skeptically before anything else — see
**"The confound that dominates this run"** immediately below. It is not
a fair skill-vs-baseline comparison as it stands.

**Corrected 2026-08-20 (same day), skill-arm zero-tolerance/dimension-6
counts only.** A grading-logic fix (`consumer_scope_targets()` normalizing
a conventional `lib`-prefixed shared-library name, e.g. `librenderer.so`,
to the scenario's declared bare consumer name `renderer`) changed one
run's own dimension-6 status, from a false `fail` to a correct `pass` —
`consumer-unaffected-despite-break/skill/1` had cited a genuinely
correctly-scoped `--used-by` call the grader simply couldn't recognize as
matching the declared target. No other run's grading changed (every other
one of the 48 recorded runs was re-graded, pre-fix vs. post-fix, and
produced byte-identical output). The "correct verdict" and per-scenario
tables are unaffected, since that run's verdict was already graded
correct — only its dimension-6/zero-tolerance status moved.

## The confound that dominates this run

`runners/claude_code.py` invokes `claude -p ... --max-turns 12`. Of the
48 runs, **15 (31%) hit that ceiling and were cut off with no final
answer at all** (`error_max_turns` in the transcript's own `result`
event) — the ABI-review workflow (preflight, compile old/new sides,
`abicheck compare`, interpret, report) routinely needs more than 12
turns, and the two arms hit the ceiling at very different rates:

|                              | skill | baseline |
|------------------------------|-------|----------|
| runs cut off at 12 turns     | 11/24 (46%) | 4/24 (17%) |
| correct verdict, all runs    | 9/24 (38%)  | 9/24 (38%) |
| correct verdict, **completed runs only** | 9/13 (69%) | 9/20 (45%) |

The skill arm's own ten-step decision procedure is more turn-hungry
than the baseline's ad-hoc approach, so it is disproportionately
truncated by a ceiling set without that workflow in mind. Once
truncated runs are excluded, the skill arm's accuracy on what it
actually finished looks meaningfully better than the baseline's (69%
vs. 45%) — but that comparison rests on only 13 and 20 completed runs
respectively, from a single pilot, and is reported here as an
observation to investigate, not a validated result. **The single
highest-value next step before any further pilot is raising
`--max-turns`** (the harness's own constant, not a fixture or scenario
property) and re-running — every other number in this document should
be read through this confound until that happens.

## Dimension-level detail (the four deterministic dimensions; 4 and 5 are not graded — no judge model wired yet)

|                                          | skill pass | baseline pass |
|------------------------------------------|-----------:|---------------:|
| 1 — Correct workflow chosen              | 23/24 (96%) | 6/24 (25%) |
| 2 — Uncertainty preserved                 | 1/24 (4%, 8 not-applicable) | 1/24 (4%, 8 not-applicable) |
| 3 — Deterministic evidence obtained       | 23/24 (96%) | 6/24 (25%) |
| 6 — No compatibility claim without sufficient evidence (zero-tolerance) | 8/24 (33%) | 4/24 (17%) |

Dimension 1's own split is the cleanest signal this pilot produced,
confound aside: the skill arm reached for a real comparison 23/24 times
(96%) against the baseline's 6/24 (25%) — the baseline arm, given only
`Bash`/`Read`/`Glob`/`Grep` and no knowledge that `abicheck` exists,
mostly reasoned from `nm`/`readelf`/manual header diffing instead, which
is exactly the without-the-skill behavior this comparison exists to
characterize. Dimension 6's low pass rate on both arms is dominated by
`claim_status: absent` (15 skill runs, 13 baseline runs never produced a
claim envelope at all) — itself mostly the turn-budget confound above,
not a demonstrated evidence-discipline failure.

## Per-scenario correct-verdict count (skill / baseline, out of 2 repetitions each)

| Scenario | skill | baseline | expected |
|---|---|---|---|
| changed-signature | 0/2 | 0/2 | BREAKING |
| compatible-addition | 2/2 | 2/2 | COMPATIBLE |
| consumer-actually-affected | 1/2 | 1/2 | BREAKING |
| consumer-unaffected-despite-break | 1/2 | 2/2 | COMPATIBLE |
| contract-coverage-incomplete | 0/2 | 0/2 | COMPATIBLE |
| enum-value-change | 1/2 | 0/2 | BREAKING |
| evidence-too-shallow | 0/2 | 0/2 | COMPATIBLE |
| not-comparable-pair | 1/2 | 1/2 | not comparable |
| plugin-required-symbol-loss | 0/2 | 1/2 | BREAKING |
| removed-export | 2/2 | 2/2 | BREAKING |
| struct-layout-drift | 1/2 | 0/2 | BREAKING |
| vtable-change | 0/2 | 0/2 | BREAKING |

At n=2 per scenario per arm, no individual row is statistically
meaningful — this table is here for anyone re-running the pilot to spot
which fixtures are worth investigating first (e.g. `vtable-change` and
`evidence-too-shallow` failed on both arms every time; worth checking
whether a max-turns cutoff, a real fixture issue, or a real difficulty
is behind that before drawing conclusions).

## Environment facts this run depended on

Both documented in `agent-evals/skills/CLAUDE.md`'s "Environment
prerequisites for a real run" section, applied for this pilot:

- `abicheck --version` patched to report `0.6.0` (both the editable
  install's `abicheck.egg-info/PKG-INFO` and the site-packages
  `dist-info/METADATA` — the checkout otherwise reports the stale
  `0.5.0`, which trips the skill's own preflight refusal on every
  skill-arm run). Local, ephemeral, never committed.
- `ABICHECK_ALLOW_AST_FALLBACK=1` set in the runner's environment, since
  this sandbox's `apt`-installed CastXML (`0.6.3-1build2`) is below
  abicheck's own policy floor (`>=0.6.11,<0.8.0`); this degrades the
  header-AST backend to direct-clang rather than hard-erroring.

## What this pilot does not claim

- Not a statistically powered comparison — n=2 per scenario per arm, one
  model, one environment.
- Not evidence the skill improves agent behavior over baseline in
  general — the one clean signal (dimension 1's activation/workflow-
  choice gap) is real and large, but "picks the right tool" is a
  narrower claim than "produces more correct answers," and the
  correct-verdict numbers are confounded by the turn-budget gap above.
- Not a completed G37 evaluation — dimensions 4 (root-cause quality) and
  5 (remediation quality) have no judge model wired yet and were not
  graded; the trigger-corpus (activation-precision) runner does not
  exist yet either.
- Does not change the skill's status. It remains, per ADR-058, an
  **internal candidate**: not for external publication or citation as
  validated in any user-facing claim.

## Reproducing this run

```bash
export ABICHECK_ALLOW_AST_FALLBACK=1
# patch abicheck --version to report 0.6.0 first — see agent-evals/skills/CLAUDE.md
python agent-evals/skills/runners/claude_code.py --out /tmp/skill-eval --repetitions 2 --arms skill,baseline
python agent-evals/skills/run_skill_eval.py --runs /tmp/skill-eval --json /tmp/grades.json
```

## Recommended next steps, in priority order

1. ~~**Raise `--max-turns`**~~ Done (2026-08-21): `runners/claude_code.py`'s
   `MAX_TURNS` raised from 12 to 40 — real headroom over this pilot's own
   observed maximum (17, on both arms), not merely a value close enough to
   keep truncating the slower arm. **Not yet re-run** — every number in
   this document still reflects the 12-turn pilot until a fresh run
   confirms the confound is actually gone; don't read the raise itself as
   having changed anything above.
2. Re-run the pilot against the new ceiling, then re-run at a larger
   repetition count (the G37 plan's
   own target range) before drawing any conclusion about skill lift.
3. Wire dimensions 4/5 to a judge model so the full six-dimension rubric
   is actually scored, not four of six.
4. Investigate the scenarios that failed on both arms at n=2
   (`vtable-change`, `evidence-too-shallow`, `contract-coverage-
   incomplete`, `changed-signature`) once truncated runs are excluded
   from the picture — determine whether these are genuine difficulty,
   fixture issues, or more turn-budget casualties.
