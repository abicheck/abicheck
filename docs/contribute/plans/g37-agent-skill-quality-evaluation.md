---
doc_type: contributor
level: advanced
lifecycle: active
---

# G37 — Agent Skill Quality Evaluation: measuring whether the skills actually work

**ADR:** [ADR-058](../adr/058-native-compatibility-agent-skills.md)
**Type:** Initiative plan (multi-phase); no `usecase-registry.yaml` entries —
this is evaluation infrastructure for a distribution surface, not a detector
capability, tracked here the same way [G36](g36-native-compatibility-agent-skills.md)
is.
**Relationship to G36:** G37 is the *measurement* half of G36. G36 P0.8 built
the deterministic trigger tests and G36 P1.1/P1.5 named a behavioral evaluation
and a cross-agent validation log but did not design either. G37 designs both,
adds the two layers neither item covers (comparative lift, and grading the
grader), and decides where each layer runs. **G36 P1.1, P1.4 and P1.5 are
superseded in their implementation detail by this plan** — see
[Relationship to G36's own items](#relationship-to-g36s-own-items) for exactly
what changes and why.

---

## Problem

Four public skills ship today (`native-binary-compatibility-review`,
`native-api-evolution`, `native-release-compatibility`,
`native-consumer-compatibility`). Everything currently gating them measures the
*artifact*, not the *behavior*:

| Existing gate | What it proves | What it cannot prove |
|---|---|---|
| `tests/test_gen_agent_skills.py` | the three published trees match `skills-src/` | nothing about content quality |
| `tests/test_agent_skills_structural.py` | frontmatter, layering, version range, self-containment | nothing about whether the workflow works |
| `tests/test_agent_skills_drift.py` | every CLI flag and report-JSON path a skill names still exists | that naming a real flag means using it correctly |
| `tests/test_agent_skills_triggers.py` | the descriptions lexically cover and discriminate ADR-058's seven intents | that a real agent activates the right skill |
| `agent-evals/` (M1-5) | an agent can make a small abicheck code change | nothing — different axis entirely (agent *as contributor to* abicheck, not agent *equipped with* these skills) |

So the repository can currently ship a skill that is perfectly well-formed,
references only live CLI surface, and gives systematically wrong compatibility
answers — including the one failure ADR-058 calls non-negotiable, a
manufactured green verdict. ADR-058's Safety invariants exist as prose in
`skills-src/shared/safety-invariants.md` with **no executable check anywhere**
that a skill's actual behavior honors them.

Three distinct questions are unanswered, and they are unanswered in different
ways:

1. **Does the skill trigger?** — G36 P0.8 answered the static half; the live
   half (does Claude Code / Codex / Gemini CLI actually activate it) was
   deliberately deferred to an opt-in lane that was never built.
2. **Does the skill reach the right answer?** — G36 P1.1, not started. This is
   a correctness *gate*: absolute, ground-truth-anchored, and for the two
   safety dimensions, zero-tolerance.
3. **Does the skill *help*?** — asked nowhere. A skill that reaches the right
   answer that the bare agent also reaches, at three times the token cost, is
   not worth publishing. This is a comparative *measurement*: relative,
   statistical, needs arms and variance, and is the only layer that answers
   "результативность" — whether shipping the artifact is justified at all.

Questions 2 and 3 are different kinds of measurement and must not be collapsed
into one harness or one number. That distinction drives every decision below.

## Goal & acceptance criteria

**Goal.** Every claim ADR-058 makes about these skills is backed by a
re-runnable measurement, and the claims that are safety-critical are backed by
a check that blocks a merge.

Accepted when all of:

1. A skill-content PR that introduces a false-green behavior fails a check in
   this repository, not a reviewer's judgement.
2. Every published skill carries a scorecard: activation precision/recall,
   verdict accuracy against ground truth, safety-dimension pass rate across `k`
   runs, cost per resolved question, and measured lift over the no-skill
   baseline.
3. That scorecard is provably fresh — a results artifact whose recorded content
   hash does not match the current generated skill trees is rejected as
   evidence, mechanically (this replaces G36's repeatedly-patched prose
   requirement that "the run must postdate every content-changing commit").
4. The grading harness itself is tested against curated *bad* transcripts and
   demonstrably catches each of them.
5. Nothing in (1)–(4) requires a model, a credential, or a network call to run
   the ordinary `pr` profile.

## Assessment: the five layers, and where each one stands

"Quality of a skill" is not one quantity. It decomposes into five layers that
differ in determinism, cost, and what a failure means.

| Layer | Question | Determinism | Today |
|---|---|---|---|
| **L0 — Structural** | Is the artifact well-formed and non-drifted? | fully deterministic | **done**, in `pr` |
| **L1s — Trigger, static** | Do the descriptions cover and discriminate the target intents lexically? | fully deterministic | **done**, in `pr` |
| **L1l — Trigger, live** | Does a real agent activate the right skill on real phrasing? | stochastic, model-dependent | **not started** (G36 P0.8's deferred half) |
| **L2 — Behavioral correctness** | Does a skill-equipped agent reach the right answer, safely, on known-truth fixtures? | mixed — mostly deterministic if graded from artifacts (see D3) | **not started** (G36 P1.1) |
| **L3 — Comparative lift** | Does the skill beat no-skill / docs-only, and at what cost? | statistical, needs `k` runs × `n` arms | **not started, no home** |

Plus one layer that measures the measurement:

| **L4 — Meta** | Does the grader catch a bad answer? | fully deterministic (replay) | **not started** |

Two observations shape the design.

**L2 is far more deterministic than it looks.** Every P0 skill's workflow
terminates in an abicheck invocation that produces a JSON report. If the run
happens in a sandbox where `abicheck` is a *recording shim*, the harness gets
the exact argv, exit code, and produced report of every call — and the ground
truth for the fixture is already in `examples/ground_truth.json` (195 cases
with `expected`, `expected_kinds`, `min_evidence`, `platforms`). Most of the
rubric is then a deterministic assertion, not a judge call. Only the residual —
was the root-cause explanation right, was the remediation appropriate — needs a
model in the loop. Maximizing the deterministic fraction is what makes this
affordable and reproducible.

**The single most valuable check needs no judge at all.** ADR-058's
non-negotiable invariant is "never manufacture a false green." That is
mechanically detectable as a **claim-vs-artifact mismatch**: parse the verdict
the agent *stated* in its final answer, compare it against the verdict the
recorded abicheck run *actually produced*, and against the fixture's ground
truth. A green claim over a non-green artifact, or over no artifact at all, is
a hard failure with no model involved. The same shape catches "reported a
`NOT_COMPARABLE` as a pass" and "suppressed findings to quiet the output" (the
shim sees the `--suppress`/policy flags).

## Design

### D1 — Two homes, one artifact contract

**abicheck owns L0, L1s, L2, L4 and the fixture/ground-truth corpus.
agent-benchmark owns L1l's cross-agent matrix and L3.**

Rationale, stated as the two rejected alternatives:

*Everything in abicheck* fails on L3. Comparative lift needs N-way treatment
arms, an LLM-as-judge panel, multi-provider credentials, token/cost accounting,
variance across repeated runs, and a cross-artifact dashboard. That is not a
feature to add here — it is what `agent-benchmark` already is
(`agent_benchmarks/treatments/` with `skill:`/`skill-agent:`/`docs`/`baseline`
arms, `agent_benchmarks/eval/arm_runner.py`, the judge panel, `subjects/` with
`SUBJECT_KINDS` already containing `"skill"` and a `scorecard.py`, and
`harnesses/docker_solver.py` whose `skill_path` is literally "the with-skill
treatment arm"). Rebuilding it would also drag LLM provider keys into
abicheck's CI for a job that is not a merge gate.

*Everything in agent-benchmark* fails on L2. The correctness gate must block a
skill-content PR **in the repository where the skill source lives**, and it
must be anchored to `examples/ground_truth.json` and the `examples/case*`
fixtures, which live here. A cross-repo required check whose ground truth is in
a third repo is exactly the drift ADR-058 and `docs/AGENTS.md` forbid.

**The contract between them is an artifact, not an import.** abicheck publishes
a versioned **skill eval pack**; agent-benchmark consumes it as a subject.
Neither repository imports the other's Python. agent-benchmark drives abicheck
only through its published CLI, the same way it drives any other product.

```text
abicheck                                          agent-benchmark
────────                                          ───────────────
skills-src/ ──gen──► .agents/skills/  ┐
examples/ground_truth.json            ├─► skill-eval-pack.json ──► subjects/ (kind: skill)
agent-evals/skills/scenarios.yaml     │   + fixtures manifest      arms run: baseline | docs | skill | skill-agent
transcript bundles + rubric schema    ┘   + content hash           tasks run: harness × model matrix
        │                                                                   │
        └──► L0/L1s/L2/L4 gates (this repo, pr + skill-eval lanes)          └──► L3 scorecard + dashboard
```

The pack is a single JSON file plus a fixture manifest: skill identities and
their content hashes, the scenario list with prompts and expected outcomes, the
rubric schema version, and the resolved fixture locations. It is *generated*,
committed, and `--check`-able the same way `.agents/skills/` already is.

### D2 — Three lanes, split by determinism and cost

| Lane | Trigger | Needs | Contains | Blocking |
|---|---|---|---|---|
| `pr` profile | every PR | nothing beyond `[dev]` | L0, L1s, pack build `--check`, scenario-manifest validity, fixture resolution, shim unit tests, **L4 replay grading of golden transcripts** | yes, required |
| `skill-eval` | `skill-eval` PR label, nightly `workflow_dispatch`, weekly cron | agent binary + model credentials + network | L1l, L2 live runs at `k=3` | safety dimensions: yes, hard. Process dimensions: baseline/non-regression |
| agent-benchmark `agent-quality` | weekly cron + dispatch | full LLM provider matrix | L3 arms, cost, cross-model variance, scorecard | no — informational, but a publication precondition |

The `skill-eval` lane follows `eval-suite.yml`'s trigger shape — **not
`mutation.yml`'s**, and the difference is load-bearing:
`pull_request: types: [opened, reopened, synchronize, labeled]` with no `paths`
filter, plus cron and dispatch. Two separate traps are being avoided here, and
conflating them is how an earlier draft of this section got the trigger list
wrong:

- **No `paths` filter.** A paths filter gates the whole `pull_request` trigger,
  including `labeled`, which would stop the label from opting in a PR that
  changes skill content but not the eval tree. `eval-suite.yml` documents this
  one in its own header comment.
- **`synchronize` is required, unlike `mutation.yml`.** `mutation.yml` gets away
  with `types: [labeled]` alone because it is an informational, non-blocking
  weekly measurement — running once per label is enough. A safety gate cannot
  be: with `labeled` alone, the lane runs against the head that existed when
  the label was applied, and every subsequent push to the same PR ships
  unexercised. That is precisely the "evaluated tree ≠ published tree" failure
  D6 exists to prevent, reintroduced at the CI-trigger level. The job's `if`
  condition therefore tests label *presence* (`contains(github.event.
  pull_request.labels.*.name, 'skill-eval')`), not the triggering event type,
  so it reruns on every push while the label remains.

The lane exports `ABICHECK_MIN_EXECUTED` so a missing agent binary or expired
credential cannot turn it green with zero scenarios run — the silent-skip hole
`tests/conftest.py` already closes for the external-tool lanes.

### D3 — Replay-first: grade artifacts, not prose

Every live run persists a **transcript bundle**:

```text
agent-evals/skills/runs/<run-id>/<scenario>/<k>/
  meta.json          agent, model, versions, skill-tree content hash, seed/temperature
  prompt.txt         the verbatim user request
  calls.jsonl        one record per recorded abicheck invocation: argv, cwd, exit code,
                     stdout digest, path to the produced report JSON
  reports/*.json     the actual abicheck reports the agent produced
  final.md           the agent's final answer text
  usage.json         turns, tool calls, tokens in/out, wall clock, retries
  judgments.json     persisted judge verdicts for dimensions 4 and 5: judge model
                     + version, rubric version, prompt hash, score, rationale
```

Grading over dimensions 1, 2, 3 and 6 is a **pure function of the bundle** —
those four graders read `calls.jsonl`, `reports/`, and `final.md` and call no
model. Dimensions 4 and 5 are judged, so their *first* evaluation is not
reproducible from the bundle alone; what the bundle carries is their **recorded
verdict**, stamped with the judge model and rubric version that produced it.

The distinction matters, so it is stated as two separate operations rather than
one word:

- **Replay** re-derives dimensions 1/2/3/6 from the bundle and reads 4/5 out of
  `judgments.json`. Deterministic, offline, no credentials — this is what `pr`
  runs and what an auditor re-runs.
- **Re-judge** re-invokes the panel for 4/5 and appends a new, separately
  stamped `judgments.json` entry. Explicitly not deterministic, never run in
  `pr`, and never silently substituted for a replay: a rubric change that
  affects 4/5 requires a re-judge pass and is visible as such, because the old
  and new entries carry different rubric versions.

Consequences, all of which matter:

- A rubric change to the deterministic dimensions is re-gradable against stored
  transcripts with no model calls at all; a change to 4/5 needs an explicit,
  budgeted re-judge pass, and the plan does not pretend otherwise.
- The deterministic graders — which include *both* zero-tolerance safety
  dimensions — are unit-testable in `pr` with zero model calls (that is L4).
- Publication (G36 P1.4) gets an auditable evidence trail rather than a claim,
  including which judge model signed off on 4/5.
- Cross-agent comparison is apples-to-apples: same bundle schema for Claude
  Code, Codex, and Gemini CLI, so only the *runner* is vendor-specific, not the
  grading.

The recording shim is a small executable placed first on `PATH` inside the
scenario sandbox. It **spawns the real abicheck as a child, waits for it, and
finalizes the record after the child exits** — it does not `exec`, which would
replace the shim process and make the exit code and output digest `calls.jsonl`
requires unobservable, leaving every deterministic grader with incomplete
evidence. Concretely: write a provisional record with argv/cwd, spawn, tee
stdout and stderr through to the caller's own streams while digesting them,
then rewrite the record with exit code and digests and propagate the child's
exit status verbatim. Teeing rather than capturing is the invariant — the agent
must see byte-identical output on both streams, and the shim must exit with the
child's own status, because a shim that can change a result invalidates the
measurement it exists to produce. A shim crash after spawn must leave the
provisional record in place rather than nothing: a call that happened and was
lost would read to a grader as a call that never happened, which is the false
direction to fail in for dimension 3.

### D4 — The rubric, and which dimensions gate how

Six dimensions, from ADR-058, each with a stated grader kind. The split between
deterministic and judged is the design's cost control; the split between
zero-tolerance and baseline is its safety model.

| # | Dimension | Grader | Gating |
|---|---|---|---|
| 1 | Correct workflow chosen (right skill, right branch within it) | deterministic — recorded argv shape vs. the scenario's expected invocation class | baseline / non-regression |
| 2 | **Uncertainty preserved** | deterministic — a `NOT_COMPARABLE`/incomplete-evidence/coverage-failure artifact must not be answered with a definite verdict | **zero tolerance, all `k` runs** |
| 3 | Deterministic evidence obtained | deterministic — at least one real abicheck run over the right two sides; a claim with an empty `calls.jsonl` fails outright | baseline / non-regression |
| 4 | Root-cause explanation correct | judged (LLM panel) against the fixture's `expected_kinds` | baseline / non-regression |
| 5 | Appropriate remediation proposed | judged | baseline / non-regression |
| 6 | **No compatibility claim without sufficient evidence** | deterministic — claim-vs-artifact-vs-ground-truth consistency, plus suppression-flag inspection | **zero tolerance, all `k` runs** |

Dimensions 2 and 6 are graded with **pass^k** (every run must pass), not
pass@k. A safety property that holds two times in three is not a safety
property. G36 P1.1 already reached this conclusion in prose — that these two
cannot use a `SURVIVOR_BASELINE`-style "establish from the first run" model,
because a first run containing a false green would enshrine it as the floor.
D4 makes it executable.

The other four use the established baseline/non-regression model (the same
shape as `SURVIVOR_BASELINE` and the FP-rate gate), with the baseline recorded
per (skill, agent, model) triple — a model change re-baselines dimensions 1,
3, 4, 5 and never relaxes 2 or 6.

### D5 — Scenario corpus: two categories, as G36 P1.1 correctly identified

**Category A — resolvable from `examples/ground_truth.json`.** 195 cases keyed
under `catalog["verdicts"][case_dir]`, each carrying `expected`,
`expected_kinds`, `min_evidence`, `platforms`. A scenario names a case and a
skill; expected outcome is derived from the catalog, never re-stated (one fact,
one place). Covers: removed export, changed signature, struct layout drift,
enum value change, vtable change, API-only break, public/private scope false
positive, compile-profile difference.

**Category B — needs explicit invocation parameters the catalog cannot
express.** Non-comparable snapshots, consumer-unaffected-despite-global-break,
consumer-actually-affected, plugin required-symbol loss, missing matrix target.
These need `--used-by`, `--required-symbol`, a multi-target matrix, or a
deliberately broken comparability contract. They get explicit records in
`agent-evals/skills/scenarios.yaml` with their own fixtures.

Category B is where the highest-value safety scenarios live — every one of them
is a place a skill can plausibly manufacture a green result — so it is built
first, not last, inverting the natural "easy cases first" ordering.

### D6 — Freshness as a mechanism, not a rule

G36 states, in three separate items and with an amendment history showing it
was patched each review round, that a publication-relied-on evaluation "must
postdate every later commit that changes the generated skill trees' content."
That is a prose requirement with no enforcement, which is why it kept needing
restatement.

G37 makes it mechanical: the eval pack records the content hash of the
generated skill trees plus the scenario corpus; every results artifact records
the pack hash it ran against; and `check_skill_eval_freshness` — a `pr`-profile
check requiring no model — fails when a results artifact claims to be evidence
for a tree it did not exercise. Stale evidence becomes a failing check instead
of a review-round catch.

### D7 — L3 in agent-benchmark: the arms that actually answer "does it help"

Once the pack exists, `agent-benchmark` runs the comparison it is already built
for. The arms:

| Arm | Spec | Answers |
|---|---|---|
| `baseline` | bare model, no skill, no docs | what the agent knows unaided |
| `docs` | abicheck documentation injected | is the skill better than just shipping docs? |
| `skill:<pack>/<name>` | skill body injected | eager-injection quality |
| `skill-agent:<pack>/<name>` | skill offered via progressive disclosure | does it get *found* and used, not just read |

`baseline` vs `docs` is the load-bearing comparison and the one most likely to
be uncomfortable: if the skill does not beat documentation-in-context, ADR-058's
central product bet is not supported for that skill, and the honest outcome is
to fold it into a `shared/` fragment rather than publish it. The plan commits to
reporting that result whichever way it comes out.

Reported per arm: judge score, verdict accuracy, safety-dimension pass rate,
tokens, wall clock, and cost — so "lift" is always a quality-per-cost number,
never quality alone.

Two small gaps in agent-benchmark this requires, both real and both small:
`skills/loader.py`'s `resources` collection walks only top-level sibling *files*
of `SKILL.md`, so abicheck's `references/` and `references/shared/`
subdirectories are not recorded (fine for `skill:` eager injection, wrong for
`skill-agent:` progressive disclosure, which is the arm that matters most
here); and the executable-task track's with-skill arm exists in
`harnesses/docker_solver.py` but the pack format needs an adapter to feed it.

### D8 — Where the harness lives in this repo

`agent-evals/`, extended with a second task kind — not a new tree, and not
`validation/`.

`agent-evals/` is already "score an agent's behavior against hidden expectations
with a manifest, a scope contract, a gate contract, and a JSON result," and is
already a `FIRST_PARTY_PY_ROOTS` member with its own `CLAUDE.md`. Skill
evaluation is the same mechanism pointed at a different subject (an agent
*equipped with* a skill, rather than an agent *modifying* abicheck).
`validation/` is a different thing entirely — running abicheck against
real-world package corpora — and putting an agent-behavioral harness there
would create a third overlapping home for "evaluation."

**This deviates from G36 P1.1's stated file paths** (`validation/scripts/
run_skill_evals.py`, `validation/data/skill_eval_scenarios.yaml`). The
deviation is deliberate and is recorded in [Relationship to G36's own
items](#relationship-to-g36s-own-items) below.

## Files & surfaces

```text
agent-evals/
  skills/
    CLAUDE.md                        scoped agent context for this sub-tree
    scenarios.yaml                   Category A refs + Category B explicit records
    schema/
      scenario.schema.json           scenario manifest contract
      transcript-bundle.schema.json  the bundle shape every runner must emit
      rubric.schema.json             six dimensions, grader kind, gating mode
    shim/abicheck                    recording shim (argv + exit + report digest)
    runners/
      claude_code.py                 headless Claude Code runner
      codex.py                       (Phase 4)
      gemini_cli.py                  (Phase 4)
    graders/
      deterministic.py               dimensions 1, 2, 3, 6
      judged.py                      dimensions 4, 5 (model in the loop)
    run_skill_eval.py                live runner entry point
    grade_bundle.py                  replay grader entry point (no model for 1,2,3,6)
    baselines/<agent>-<model>.json   per-triple baselines for dimensions 1,3,4,5
    golden/                          curated transcript bundles, good and bad (L4)
scripts/
  gen_skill_eval_pack.py             builds skill-eval-pack.json (+ --check)
  check_skill_eval_freshness.py      D6's mechanical freshness gate
tests/
  test_skill_eval_scenarios.py       manifest/schema validity, fixture resolution
  test_skill_eval_graders.py         L4 — graders vs. golden good/bad bundles
  test_skill_eval_pack.py            pack generation + freshness check
.github/workflows/
  skill-eval.yml                     the label/cron/dispatch lane
```

In `agent-benchmark` (separate repository, separate PR):
`data/skills/abicheck/` (the consumed pack), a `subjects/` entry of kind
`skill` per P0 skill, the `references/` sub-directory fix in
`agent_benchmarks/skills/loader.py`, and a pack→`docker_solver` adapter.

## Phases

Each phase is one PR unless noted. Phases 0–2 are the load-bearing ones; 3–6
are buildout.

### Phase 0 — Contracts, no model *(S)*

Scenario/bundle/rubric JSON Schemas, the pack generator with `--check`, the
freshness checker, and `scenarios.yaml` with Category B scenarios declared but
not yet runnable. Wired into `scripts/verify.py`'s step catalog so `pr`, pixi,
pre-commit, and CI all route through it (`tests/test_verify_profiles.py`
enforces this).

**Done when:** `pr` fails on a hand-edited pack, an unresolvable fixture
reference, and a stale results artifact.

### Phase 1 — Deterministic grading core + L4 *(M)*

The shim, the four deterministic graders, `grade_bundle.py`, and the golden
corpus — including hand-authored **bad** bundles: a false green over a
`NOT_COMPARABLE` artifact, a definite verdict with an empty `calls.jsonl`, a
run that reached green by adding a suppression, and a correct verdict reached
with the wrong evidence depth. Each must be caught by the named dimension.

**Done when:** every golden bad bundle fails exactly the dimension it was
authored to fail, and every golden good bundle passes all four deterministic
dimensions — with no model call, inside `pr`. Dimensions 4 and 5 are *replayed*
out of each golden bundle's `judgments.json` (D3), which checks the replay path
and the schema but deliberately does not re-derive a judge verdict; the golden
corpus therefore also carries one bundle whose `judgments.json` records a
*failing* judge verdict, so the replay path is proven to propagate a judged
failure rather than only ever reading passes.

This phase is where most of the value lands. After it, the repository can
detect ADR-058's non-negotiable failure mode from a recorded transcript, and
everything after is about producing transcripts.

### Phase 2 — Live runner (Claude Code) + the `skill-eval` lane *(M)*

Headless Claude Code runner, the workflow with label/cron/dispatch triggers and
`ABICHECK_MIN_EXECUTED`, `k=3`, and the first real baseline for dimensions 1,
3, 4, 5. Dimensions 2 and 6 gate at zero from the first run — including if that
first run fails, which blocks rather than baselines.

**Done when:** a PR carrying the `skill-eval` label produces a scored run per
skill, and a deliberately regressed `SKILL.md` (locally, as a validation of the
lane itself) fails it.

### Phase 3 — Scenario corpus buildout *(M)*

Category B first (the five scenarios `ground_truth.json` structurally cannot
index), then Category A across the eight named categories. Target ~6 scenarios
per skill, ~24 total.

### Phase 4 — Cross-agent *(M)*

Codex and Gemini CLI runners emitting the same bundle schema; Copilot and
Cursor stay manual. G36 P1.5's cross-agent table in `skills-src/CLAUDE.md` is
then populated from generated results for the scriptable targets rather than
hand-maintained.

### Phase 5 — agent-benchmark integration, L3 *(M, separate repo)*

Pack consumption, the four arms, the loader `references/` fix, the scorecard
and its dashboard row.

**Done when:** each skill has a published quality-per-cost number against both
`baseline` and `docs` arms — whichever direction it comes out.

### Phase 6 — Publication gate *(S)*

G36 P1.4's publication precondition becomes a check: publication requires a
fresh pack hash, zero failures on dimensions 2 and 6, dimensions 1/3/4/5 at or
above baseline, and a Phase 5 scorecard showing non-negative lift over `docs`.

## Cost model

Per live run: ~4–8 agent turns over a small fixture repository. At 24 scenarios
× `k=3` that is ~72 agent sessions per full pass. Two knobs shrink a PR-label
run, and **`k` is deliberately not one of them**:

- `--suite smoke` — 6 scenarios (safety-critical Category B only), still at
  `k=3` (18 sessions), for PR-label runs.
- `--suite full` — all 24 scenarios at `k=3` (72 sessions), on the weekly cron
  and before publication.
- Judged dimensions (4, 5) are skippable via `--no-judge`, leaving the four
  deterministic dimensions — which include both zero-tolerance ones — at
  near-zero marginal cost.

**`k` stays at 3 in every lane, and the suite size is what varies.** These are
not interchangeable ways to buy the same saving. Dropping to `k=1` would keep
scenario coverage while silently converting the `pass^k` safety gate into a
single-sample check — and `k` exists precisely to catch run-to-run variance,
which is the failure mode a stochastic agent has and a scenario list does not.
Dropping scenarios costs coverage, which is honest, visible in the run's own
manifest, and recoverable on the next cron. So the PR lane runs fewer scenarios
at full repetition rather than every scenario once.

The last bullet is the intended everyday posture: the checks that block are the
cheap ones.

## Risks

| Risk | Mitigation |
|---|---|
| Model nondeterminism makes the lane flaky and the team learns to ignore it | Only deterministic dimensions gate hard; judged dimensions use baselines; `k` runs with pass^k for safety and pass@k reporting for the rest |
| Vendor CLI/harness churn breaks runners | Bundle schema is vendor-neutral; only `runners/*.py` is vendor-specific, and grading never is |
| The grader silently stops detecting anything | L4 golden bad bundles run in `pr` on every commit — that is exactly what they exist to prevent |
| Scenario corpus overfits; skills are tuned to the eval | Category A derives expectations from `ground_truth.json`, which is owned by detector work and not editable from a skill PR; corpus growth requires a case the correct behavior already passes, mirroring the FP-rate corpus rule |
| L3 shows a skill does not beat `docs` | That is a finding, not a failure — D7 commits to reporting it, and the response is to fold the skill into a `shared/` fragment |
| Eval cost grows unbounded | Smoke suite for PRs, full pass weekly, judge optional |

## Relationship to G36's own items

| G36 item | Status under G37 |
|---|---|
| **P0.8** (trigger tests) | static half stands as-is; its deferred live half becomes G37 Phase 2's L1l |
| **P1.1** (behavioral eval) | **superseded in implementation detail.** G37 keeps its substance — the two scenario categories, the six-dimension rubric, and the split gating model, all of which P1.1 got right — and changes: file locations (`agent-evals/skills/`, not `validation/`, per D8), the addition of the recording shim and replay grading (D3), and pass^k rather than per-run grading for the safety dimensions |
| **P1.4** (publication) | its freshness precondition becomes mechanical (D6) and its "acceptable baseline rate" becomes G37 Phase 6's explicit four-part gate |
| **P1.5** (cross-agent log) | generated from real results for scriptable targets (Phase 4); manual only for Copilot/Cursor |
| **P1.2/P1.3** (contingent) | unchanged — still gated on findings, which G37 Phase 3 is what actually produces |

G36 should be amended to point its P1.1/P1.4/P1.5 items here rather than
carrying a second, diverging design. That amendment is part of Phase 0's PR.

## Out of scope

- Changing any skill's content. G37 measures; a measurement that finds a
  problem produces a G36 follow-up, not an in-plan edit.
- A public leaderboard or cross-project skill benchmark. agent-benchmark's
  dashboard is the aggregation surface; standing up a published ranking is a
  separate decision.
- Evaluating non-abicheck skills. The pack format is deliberately generic
  enough not to prevent it, but nothing here commits to it.
- Human preference studies. Real-user telemetry is the honest complement to all
  of the above and is a different project.
