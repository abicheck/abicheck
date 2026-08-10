# CLAUDE.md — `agent-evals/skills/`

Quality measurement for the published Agent Skills (ADR-058), per
[`docs/contribute/plans/g37-agent-skill-quality-evaluation.md`](../../docs/contribute/plans/g37-agent-skill-quality-evaluation.md).
The plan is the narrative owner — read it for *why* each contract has the
shape it does; this file is the orientation for working in this directory.

Sibling of `agent-evals/`'s existing task harness, not a replacement: that one
scores an agent **modifying abicheck**, this one scores an agent **equipped
with a skill**. Same mechanism, different subject.

## What exists today (Phase 0, plus the first slice of Phases 1–2)

Phase 0's contracts and deterministic checks are complete. On top of them sit a
recording shim, a headless two-arm runner, and the four deterministic graders
with the bad-run corpus that exercises them — enough to produce a transcript
*and* score it. **Still missing: the judged dimensions (4 and 5), the trigger
corpus runner, and any committed evidence.** Nothing here has been run at scale
yet, so there are no numbers to cite.

| Path | Role |
|------|------|
| `scenarios.yaml` | The behavioral (L2) corpus. Category A names an `examples/` case and derives its expected outcome from `ground_truth.json`; Category B carries invocation parameters the catalog structurally cannot express and states its own outcome. |
| `rubric.yaml` | The six dimensions with their grader kind and gating mode. Graders and the publication gate both read this, so they cannot disagree about which dimension is zero-tolerance. |
| `schema/scenario.schema.json` | `scenarios.yaml`'s contract. |
| `schema/rubric.schema.json` | `rubric.yaml`'s contract. |
| `schema/claim.schema.json` | The final-answer envelope: a typed ordinal verdict (or `null`), the calls it rests on, and a closed-vocabulary uncertainty reason. |
| `schema/transcript-bundle.schema.json` | The bundle shape every runner must emit — `behavioral` or `trigger`, both carrying the hashes they ran against and the inputs the run was observed reading. |
| `skill-eval-pack.json` | **Generated** (`scripts/gen_skill_eval_pack.py`). The hashes freshness is computed from, and the interface `agent-benchmark` consumes. |
| `shim/abicheck` | Recording shim: every `abicheck` call the agent makes, with argv, exit status, persisted stdout and a per-call snapshot of the files it wrote (fixed against later calls — see the tamper note below). |
| `runners/claude_code.py` | Headless runner. Two arms — `skill` installs the published skill into the workspace, `baseline` installs none — identical in every other respect, so a difference is attributable to the skill. Refuses an in-repo `--out`, and records what the CLI said it could see. |
| `graders/` | The four deterministic dimensions (1, 2, 3, 6) as pure functions of one recorded run: `claim.py` extracts and validates the envelope, `evidence.py` reads what the calls actually did, `dimensions.py` applies the rules. No grader here calls a model. |
| `grade_bundle.py` | Grade one run directory against its scenario. Exit 2 when a zero-tolerance dimension failed. |
| `run_skill_eval.py` | Grade a whole batch and print the two arms side by side. Reports; does not gate. |

## Running an A/B

```bash
# 1. produce transcripts — the output root MUST be outside this checkout
python agent-evals/skills/runners/claude_code.py --out /tmp/skill-eval --repetitions 3
# 2. grade them
python agent-evals/skills/run_skill_eval.py --runs /tmp/skill-eval --json /tmp/grades.json
```

The `--out` restriction is not tidiness. Claude Code discovers skills from the
project the working directory belongs to, and this repo's root carries all four
published trees — so a workspace under it hands the *baseline* arm everything it
is defined by not having, and the comparison comes back "the skill changes
nothing" for a reason that has nothing to do with the skill. Verified against
the real CLI, which is also why each run records the skill list the CLI reported
and the runner aborts when the arms are not what they claim.

## The two things that are easy to get wrong here

**1. Never hand-edit `skill-eval-pack.json`.** It is derived; `scripts/verify.py
--profile pr` re-derives it and fails on drift. After changing a skill, a
scenario, a fixture, or a `ground_truth.json` entry, run:

```bash
python scripts/gen_skill_eval_pack.py          # rewrite the pack
python scripts/check_skill_eval_freshness.py   # what the PR gate runs
```

**2. `status: planned` is not a placeholder — it is an exclusion.** A planned
scenario contributes to no expected-evidence set, so a planned scenario whose
fixture already exists is a scenario silently not being run. Flip it to `ready`
in the same change that lands its fixture; `tests/test_skill_eval_scenarios.py`
fails if you don't.

## A `null` verdict is a claim too

Dimension 6 asks a claim to cite a call that could have produced it. That
applies to a stated verdict, and — since a review found the null branch skipping
the check entirely — to a `null` verdict given for `not_comparable`, which must
cite a call that *determined* non-comparability (`compare` 16, `scan --against`
6, `compat check` 9). Without it, a run that recorded nothing scored clean on
the `not-comparable-pair` scenario by naming the outcome it was about to be
graded against. The other three uncertainty kinds are exempt on purpose: a run
that stops on shallow evidence may honestly have produced neither a verdict nor
a determination, and demanding a citation it cannot have fails correct runs.

**One real consequence, worth knowing before reading a grade.** The four
uncertainty reasons describe things the *comparison* could not settle. None of
them says "the environment failed the skill's own precondition" — so the pilot's
skill-arm runs, stopped by the published skills' `abicheck-version-range`
preflight, reached for `not_comparable` as the nearest term and now fail
dimension 6 for citing only `abicheck --version`. The grader is right on its own
terms; the vocabulary is short a term. Resolve that in the rubric deliberately,
not by relaxing the rule — the run genuinely did not establish that the pair was
incomparable.

## Adding a scenario

1. Category A if `examples/ground_truth.json` can answer it — name the case,
   never restate the expected verdict. Category B only when the question needs
   `--used-by`, `--required-symbol`, a matrix, a broken comparability contract,
   a depth the question outruns, or an unclosable `--contract` domain.
2. Write the prompt the way a user would ask it. **It must not name abicheck**
   — the skill has to be *found*, which is the thing being measured.
3. Regenerate the pack and commit it alongside.
4. If the scenario exercises one of dimension 2's four uncertainty kinds, say
   so in `expected.uncertainty`. Each kind must keep exactly one scenario or
   its zero-tolerance rule gates on nothing — which is a test, not a habit.

## What NOT to do

- Don't add a model call to anything that runs in `pr`. The whole reason these
  checks can be required is that they re-grade recordings rather than produce
  them (G37 D2); a required check that calls a model is flaky, costs money per
  push, and blocks on an expired credential.
- Don't weaken a zero-tolerance dimension to make a bundle pass. Dimensions 2
  and 6 have no baseline by design — a first run containing a false green would
  enshrine it as the floor, which is exactly the failure ADR-058 calls
  non-negotiable.
- Don't hash more of `abicheck/` into the consumed-surface digest to "be safe".
  Hashing the package's bytes invalidates every committed bundle on every
  source commit, which makes the evaluation unrunnable rather than stricter;
  the residual is closed by Phase 6's full-build pass, not by widening this.
- Don't describe a transcript as tamper-proof. The shim fixes each call's
  artifacts against *later calls*, which is what stops one `-o` path from
  overwriting the evidence an earlier claim cites. It does not defend against
  the evaluated agent itself: that agent has shell access and the record file
  sits above its working directory, so a run could rewrite its own transcript.
  No file placement closes this — shell access reaches any path the process
  can — and the harness does not sandbox. Treat evidence as trustworthy to the
  degree the agent under test is, and reach for a sandbox or an out-of-process
  record channel before publishing a number that assumes otherwise.
- Don't let a runner declare its own inputs. The bundle records what an
  accessor *observed* being read; a self-declaration reproduces one level up
  the exact gap the completeness check exists to close.
