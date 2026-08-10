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
recording shim and a headless runner — enough to *produce* a transcript, not
yet to grade one. **Still missing: the graders, the golden bad-bundle corpus,
and any committed evidence.** Until the graders exist, a run yields transcripts
a human reads, not a score.

| Path | Role |
|------|------|
| `scenarios.yaml` | The behavioral (L2) corpus. Category A names an `examples/` case and derives its expected outcome from `ground_truth.json`; Category B carries invocation parameters the catalog structurally cannot express and states its own outcome. |
| `rubric.yaml` | The six dimensions with their grader kind and gating mode. Graders and the publication gate both read this, so they cannot disagree about which dimension is zero-tolerance. |
| `schema/scenario.schema.json` | `scenarios.yaml`'s contract. |
| `schema/rubric.schema.json` | `rubric.yaml`'s contract. |
| `schema/claim.schema.json` | The final-answer envelope: a typed ordinal verdict (or `null`), the calls it rests on, and a closed-vocabulary uncertainty reason. |
| `schema/transcript-bundle.schema.json` | The bundle shape every runner must emit — `behavioral` or `trigger`, both carrying the hashes they ran against and the inputs the run was observed reading. |
| `skill-eval-pack.json` | **Generated** (`scripts/gen_skill_eval_pack.py`). The hashes freshness is computed from, and the interface `agent-benchmark` consumes. |
| `shim/abicheck` | Recording shim: every `abicheck` call the agent makes, with argv, exit status, persisted stdout and an immutable snapshot of the files it wrote. |
| `runners/claude_code.py` | Headless runner. Two arms — `skill` installs the published skill into the workspace, `baseline` installs none — identical in every other respect, so a difference is attributable to the skill. |

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
- Don't let a runner declare its own inputs. The bundle records what an
  accessor *observed* being read; a self-declaration reproduces one level up
  the exact gap the completeness check exists to close.
