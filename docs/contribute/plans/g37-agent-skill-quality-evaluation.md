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
truth for the fixture is already in `examples/ground_truth.json`, whose per-case
records carry `expected`, `expected_kinds`, `min_evidence` and `platforms`
(the case count is that file's to state, not this plan's). Most of the
rubric is then a deterministic assertion, not a judge call. Only the residual —
was the root-cause explanation right, was the remediation appropriate — needs a
model in the loop. Maximizing the deterministic fraction is what makes this
affordable and reproducible.

**The single most valuable check needs no judge at all.** ADR-058's
non-negotiable invariant is "never manufacture a false green." That is
mechanically detectable as a **claim-vs-artifact mismatch**: take the verdict
the agent *claimed*, compare it against the verdict the recorded abicheck run
*actually produced*, and against the fixture's ground truth. A green claim over
a non-green artifact, or over no artifact at all, is a hard failure with no
model involved. The same shape catches "reported a non-comparable pair as a
pass" and "suppressed findings to quiet the output" (the shim sees the
`--suppress`/policy flags).

**That check must not rest on a regex over prose (D3's `claim.json`).** A
correct answer in this domain routinely names more than one outcome in one
paragraph — "ABI-compatible but source-breaking" is not hedging, it is exactly
`API_BREAK` — so a text parser searching `final.md` for a verdict word can both
miss a real false green and reject a correct answer. The scenario prompt
therefore requires the final answer to end with a small machine-readable
envelope, and the runner extracts it into `claim.json`:

- `verdict`, drawn from `compare`'s own ordinal vocabulary — `NO_CHANGE`,
  `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, `BREAKING` — or `null` for
  a pair the agent judges not comparable, mirroring how `compare` itself
  expresses that (`shared/compatibility-contracts.md`). Not a boolean: a
  green/not-green field would erase the ABI-vs-source distinction the whole
  skill exists to make.
- `evidence`, the call IDs from `calls.jsonl` the claim rests on.
- `confident`, and when false, what is unresolved — this is what dimension 2
  grades against, so an agent that correctly declines to answer has a way to
  say so that scores as a pass rather than as a missing verdict.

**Absent or ambiguous envelope fails dimension 6, closed.** Two envelopes, a
verdict outside the vocabulary, or no envelope at all is "no verifiable claim,"
not "benefit of the doubt." The cost is honest and worth naming: requiring the
envelope makes the evaluated interaction slightly less natural than an
unprompted one, and it is accepted because a zero-tolerance safety gate resting
on a regex over free text is the worse of the two. It also stays close to what
the skills already do — `native-binary-compatibility-review`'s own termination
criteria already require that "a verdict exists from a real comparison (not a
refused one)," so the envelope formalizes an outcome shape Layer A defines
rather than inventing a new obligation.

## Design

### D1 — Two homes, one artifact contract

**abicheck owns L0, L1s, L2, L4 and the fixture/ground-truth corpus.
agent-benchmark owns L3.**

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
        │  off-CI: run_skill_eval.py ──► committed evidence bundles         │
        │  CI (pr): L0/L1s/L4 + replay-grade that evidence + freshness      │
        └──► no model ever runs in this repo's CI (D2)                      └──► L3 scorecard (off-CI)
```

The pack is a single JSON file plus a fixture manifest: skill identities and
their content hashes, the scenario list with prompts and expected outcomes, the
rubric schema version, and the resolved fixture locations. It is *generated*,
committed, and `--check`-able the same way `.agents/skills/` already is.

### D2 — CI runs only deterministic checks; live evaluation runs off-CI

**No model ever runs in this repository's CI.** Every live, model-driven
evaluation is an *off-CI operation* a maintainer runs on demand; CI's entire
role is to verify, deterministically, the evidence that operation produced.
This is what D3's replay-first design buys — because grading is a pure function
of a recorded bundle for the dimensions that gate, CI can enforce the safety
contract without ever calling a model.

| Where | Trigger | Needs | Contains | Blocking |
|---|---|---|---|---|
| `pr` profile (CI) | every PR | nothing beyond `[dev]` | L0, L1s, pack build `--check`, scenario-manifest validity, fixture resolution, shim unit tests, **L4 replay grading of golden transcripts**, **replay grading of the committed evidence bundles**, **D6 freshness** | yes, required |
| `run_skill_eval.py` (off-CI) | a maintainer, on demand | agent binary + model credentials + network | L1l, L2 live runs at `k=3`, producing committed bundles | not a check — it *produces* what CI checks |
| agent-benchmark (off-CI, other repo) | on demand | full LLM provider matrix | L3 arms, cost, cross-model variance, scorecard | no — a publication precondition, checked as an artifact |

**How a skill-content PR is still gated.** The merge gate is not "did a live
run happen in CI" but "does fresh, passing evidence exist for what this PR
changes":

1. The author (or a maintainer) runs `run_skill_eval.py` locally for the
   risk-selected scenario set (Cost model) and commits the resulting bundles.
2. CI re-grades those committed bundles deterministically — the four
   deterministic dimensions, including both zero-tolerance ones — and runs the
   D6 freshness check.
3. A PR that changes skill content without refreshed bundles fails on
   freshness: the committed evidence records a per-skill hash (D6) that no
   longer matches that skill's generated tree. Missing evidence and stale evidence fail identically,
   which is the property that makes step 1 non-optional.

The cost is real and worth stating plainly: **a skill-content PR now carries a
manual step**, and an external contributor without model credentials cannot
complete it — a maintainer re-runs the evaluation and pushes the bundles for
them. That is a deliberate trade of contributor convenience for keeping models,
credentials, and nondeterminism out of CI.

**The threat model this gate does and does not cover — stated, not implied.**
CI re-grades a bundle the PR author committed, and nothing in a JSON file
proves a model ever produced it. A hand-authored bundle carrying the right
hashes and a passing transcript passes every `pr` check. So the honest scope
is:

- **Covered: accident.** A skill edit that regresses behaviour, evidence that
  went stale, a scenario never exercised, a grader that stopped detecting.
  These are the failures that actually happen, and they now fail a check
  instead of needing a reviewer to notice.
- **Not covered: fabrication.** An author who forges a bundle defeats the
  gate. Acceptance criterion 1 should be read with that bound: it removes
  reviewer *judgement* about whether behaviour regressed, not reviewer
  *trust* that the evidence is real.

This is the residual cost of running evaluation off-CI (Decision 4) — a
CI-produced artifact would carry provenance by construction. Three mitigations
are cheap and worth taking; none turns fabrication into a machine-checkable
property, and the plan does not pretend otherwise:

1. **Bundles are reviewable artifacts.** They are committed, diffable, and
   small enough to read. A forged transcript is a deliberate act visible in
   the diff, not an omission.
2. **Bundle provenance is recorded** — runner version, agent binary version,
   model id, wall-clock timestamps, token counts. Not proof, but a forged
   bundle must also forge internally consistent metadata.
3. **Publication re-runs (Phase 6).** The pre-publication full pass is
   maintainer-run by definition, so evidence that gates the *public* artifact
   never rests on a contributor-supplied bundle.

If fabrication ever becomes a real concern rather than a theoretical one, the
fix is a trusted two-stage CI handoff (maintainer-triggered run, artifact
signed by the runner), which is a separate design — recorded here so the gap
is a known, bounded one rather than an unexamined assumption.

Two structural problems with the rejected alternative are worth recording,
because both are why "just add a `skill-eval` workflow" is not the cheaper
option it looks like. A conditional job that runs only when a label is present
**does not block merge when it is skipped**, so an ordinary PR touching
`skills-src/` would merge with no evaluation at all unless a separate
always-running sentinel failed until evidence appeared — which is exactly the
freshness check above, arrived at from the other direction. And a fork-
originated PR cannot receive repository secrets on `pull_request`, while
`pull_request_target` would execute PR-controlled skill content, shim, and
prompts *with* credentials — so the live-in-CI design needs a trusted
two-stage handoff before it can accept external contributions at all. The
evidence-artifact model sidesteps both.

**If the team later wants live runs in CI**, the trigger shape is recorded here
so the analysis is not re-derived: `eval-suite.yml`'s
`pull_request: types: [opened, reopened, synchronize, labeled]` with no `paths`
filter (a paths filter gates the whole trigger including `labeled`), a job `if`
of `github.event_name != 'pull_request' || contains(github.event.
pull_request.labels.*.name, 'skill-eval')` (label presence alone skips cron and
dispatch, which carry no `pull_request` context), `synchronize` included so a
later push cannot ship unexercised, and `ABICHECK_MIN_EXECUTED` so a missing
binary or expired credential cannot go green with zero scenarios run. Those
constraints are properties of the problem, not of this plan's phasing.

### D3 — Replay-first: grade artifacts, not prose

Every live run persists a **transcript bundle**:

```text
agent-evals/skills/runs/<run-id>/<scenario>/<k>/
  meta.json          agent, model, seed/temperature, and every hash D6's freshness
                     check reads: this skill's tree hash, the abicheck build hash, and
                     one entry per scenario exercised (scenario record + fixture closure)
  prompt.txt         the verbatim user request
  events.jsonl       normalized agent events: which skill activated, which skill files
                     were read, tool calls in order — the L1l evidence (see below)
  calls.jsonl        one record per recorded abicheck invocation: argv, cwd, exit code,
                     stdout/stderr digests, and the path of every artifact the call
                     produced — both a `-o`/`--output` file and the captured stdout
  captured/<n>.out   the verbatim stdout of call <n>, always persisted (see below)
  captured/<n>.out.d/  a per-call immutable copy of every file call <n> wrote via
                     `-o`/`--output`, snapshotted when the child exits (see below)
  final.md           the agent's final answer text
  claim.json         the machine-readable verdict envelope parsed out of final.md
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
scenario sandbox — which makes "spawn the real abicheck" ambiguous in a way that
recurses: a name-based spawn re-resolves `abicheck` through the same `PATH` and
finds the shim again, forever. The shim is therefore given the real
interpreter/entry-point path explicitly (an env var the runner sets when it
builds the sandbox, e.g. `ABICHECK_REAL=/usr/bin/abicheck`) and spawns *that*
absolute path, never a bare name; it refuses to start if the variable is unset
rather than falling back to a `PATH` lookup. It **spawns the real abicheck as a child, waits for it, and
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

**Skill activation is its own recorded fact, not an inference from argv
(`events.jsonl`).** An abicheck command line cannot tell a skill-driven run
from a bare model that happened to reach the same command, and it cannot say
*which* skill ran when several teach a similar `compare` invocation — so
neither L1l's activation precision/recall nor dimension 1's "right skill,
right branch" is derivable from `calls.jsonl`. The runner therefore emits a
normalized event stream, and because vendors expose very different amounts of
this, the contract is explicitly two-tier:

**L1l drives the existing trigger corpus, not the behavioral scenarios.**
`tests/agent_skills/trigger_corpus.yaml` is already the canonical labelled
set L1s grades statically, and it is the *only* input here that contains
negatives — out-of-scope requests (REST/OpenAPI compatibility, a database
migration, Java binary compatibility) that no `native-*` skill should claim,
plus two positives deliberately labelled `expected_skill: null` for ADR-058's
P1 candidates. Running only the Category A/B scenarios would measure recall
against in-scope prompts and nothing else, and **activation precision is not
computable without negatives** — a skill that triggers on everything would
score perfectly. So the live runner replays that same corpus and grades which
skill actually activated per prompt, reusing the file rather than defining a
second labelled set that could disagree with the one L1s already gates on.

- **Tier 1 — the vendor reports activation.** The runner maps its native
  events onto one vocabulary (`skill_activated`, `skill_file_read`,
  `tool_call`) and L1l is graded deterministically from the bundle, same as
  every other replayed dimension.
- **Tier 2 — the vendor reports nothing usable.** Reads of files inside the
  installed skill tree are still observable from the sandbox, which
  distinguishes a progressive-disclosure run from a bare one but cannot
  attribute an eagerly-injected skill. Where even that is unavailable, **L1l
  degrades to the manual cross-agent log for that vendor rather than being
  graded from argv** — the same line G36 P0.8 already drew between scriptable
  and non-scriptable agents. Dimension 1 falls back to its invocation-class
  check alone, and the bundle records which tier produced it so a scorecard
  never presents a tier-2 activation number as if it were measured.

**The shim persists the teed stdout itself, not only its digest.**
`--output` defaults to stdout (`cli_options.py`: "Write output to this path
(default: stdout)"), and the skills genuinely use that form — `shared/
root-cause-grouping.md` and `shared/compiler-and-build-profiles.md` both
document `abicheck compare OLD NEW --format json` with no `-o`. A bundle
holding only a digest for those calls would have no parseable artifact, and
every grader that reads the produced verdict would silently degrade to
"no evidence" on the most idiomatic invocation the skills teach. Each call's
stdout therefore lands in `captured/<n>.out` and the record points at it.

**An `-o` file is snapshotted per call, not merely referenced by path.** An
agent iterating on a comparison naturally reuses one output path — `-o
report.json`, look, adjust flags, run again — so a record holding only that
path describes a file a later call has already overwritten. A claim citing
call 3 would then be replay-graded against call 5's report, and a digest
recorded at write time detects the overwrite without being able to
reconstruct what call 3 actually produced. The shim therefore copies every
file the call wrote into `captured/<n>.out.d/` when the child exits and
digests the copy, so each call's evidence is immutable regardless of what
later calls do to the working tree. **Requiring `-o` instead was
rejected**: that would make the harness measure a command shape the skills do
not teach, and the eval must exercise the workflow as published.

### D4 — The rubric, and which dimensions gate how

Six dimensions, from ADR-058, each with a stated grader kind. The split between
deterministic and judged is the design's cost control; the split between
zero-tolerance and baseline is its safety model.

| # | Dimension | Grader | Gating |
|---|---|---|---|
| 1 | Correct workflow chosen (right skill, right branch within it) | deterministic — `events.jsonl`'s activation record for *which* skill, plus recorded argv shape vs. the scenario's expected invocation class for *which branch*; degrades to the argv half alone under D3's tier 2 | baseline / non-regression |
| 2 | **Uncertainty preserved** | deterministic, and **per uncertainty kind** — see below; a not-comparable artifact must not be answered with a definite verdict, while a contract-coverage failure must be *carried* rather than dropped | **zero tolerance, all `k` runs** |
| 3 | Deterministic evidence obtained | deterministic — at least one real abicheck run over the right two sides; a claim with an empty `calls.jsonl` fails outright | baseline / non-regression |
| 4 | Root-cause explanation correct | judged (LLM panel) against the fixture's `expected_kinds` | baseline / non-regression |
| 5 | Appropriate remediation proposed | judged | baseline / non-regression |
| 6 | **No compatibility claim without sufficient evidence** | deterministic — claim-vs-artifact-vs-ground-truth consistency, plus suppression-flag inspection | **zero tolerance, all `k` runs** |

**Dimension 2 grades three different uncertainties by three different rules,
because collapsing them would penalize a correct answer.** An earlier draft
required simply that no definite verdict follow any of them, which is wrong for
the third and would have failed an agent for being right:

- **Not comparable** — the verdict genuinely does not exist. `claim.verdict`
  must be `null` with the reason carried; any ordinal verdict fails.
- **Incomplete evidence for the depth the question needs** — a verdict may be
  stated, but `claim.confident` must be false and name what is unresolved.
- **Contract-coverage failure** — a definite verdict is *correct here and must
  be kept*. ADR-049 Phase 7 makes coverage an axis orthogonal to compatibility:
  it raises a clean `0` to `1`, never lowers a `2`/`4`, and never rewrites a
  finding's compatibility decision. A report can legitimately carry `BREAKING`
  *and* a coverage failure at once. What dimension 2 requires is that the claim
  **carry the caveat** — coverage incompleteness reflected in
  `claim.confident`/`evidence` — not that it withhold the verdict. Dropping the
  caveat fails; downgrading a real `API_BREAK`/`BREAKING` to "cannot say"
  because coverage was short is its own failure, of dimension 6.

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

**Category A — resolvable from `examples/ground_truth.json`.** Cases keyed
under `catalog["verdicts"][case_dir]`, each carrying `expected`,
`expected_kinds`, `min_evidence`, `platforms`. A scenario names a case and a
skill; expected outcome is derived from the catalog, never re-stated (one fact,
one place). Covers: removed export, changed signature, struct layout drift,
enum value change, vtable change, API-only break, public/private scope false
positive, compile-profile difference.

**Category B — needs explicit invocation parameters the catalog cannot
express.** Non-comparable snapshots, **evidence too shallow for the question
asked**, **incomplete contract-provider evidence**,
consumer-unaffected-despite-global-break,
consumer-actually-affected, plugin required-symbol loss, missing matrix target.
These need `--used-by`, `--required-symbol`, a multi-target matrix, a
deliberately broken comparability contract, an L0-only pair whose question
requires L2 evidence, or a `--contract-evaluation` run whose selected
`--contract` domain cannot be closed. They get explicit records in
`agent-evals/skills/scenarios.yaml` with their own fixtures.

**Each of dimension 2's three uncertainty kinds (D4) needs its own scenario,
or a zero-tolerance rule gates on nothing.** The first three entries above are
exactly those three, and the third was missing until this review round: an
earlier draft claimed "missing matrix target" covered the contract-coverage
kind, which it does not — that scenario exercises *release-matrix assurance*
(an unrun target in a multi-platform release), a different mechanism entirely.
Contract coverage is `contract_coverage_ledger.py`'s unsuppressible ledger:
it exists only under `--contract-evaluation`, is answered per selected
`--contract` domain, and surfaces as `contract_coverage_failures` plus the
orthogonal exit contribution. A scenario for it must therefore *run*
`--contract-evaluation` against a pair whose chosen domain has genuinely
incomplete provider evidence — e.g. an exports-domain run over a side whose
export table was never captured, which `export_surface.py` reports as
`resolvable=False` rather than as "exports nothing". Anything short of that
leaves the rule ungated no matter how many other Category B scenarios exist.

Category B is where the highest-value safety scenarios live — every one of them
is a place a skill can plausibly manufacture a green result — so it is built
first, not last, inverting the natural "easy cases first" ordering.

### D6 — Freshness as a mechanism, not a rule

G36 states, in three separate items and with an amendment history showing it
was patched each review round, that a publication-relied-on evaluation "must
postdate every later commit that changes the generated skill trees' content."
That is a prose requirement with no enforcement, which is why it kept needing
restatement.

G37 makes it mechanical: the eval pack records content hashes, every evidence
bundle records the hashes it ran against, and `check_skill_eval_freshness` — a
`pr`-profile check requiring no model — fails when a bundle claims to be
evidence for content it did not exercise. Stale evidence becomes a failing
check instead of a review-round catch.

**The hashes are per skill and per scenario, never one whole-pack hash.** A
single global hash would change on any skill's edit and invalidate every other
skill's evidence with it — forcing a full re-evaluation on every single-skill
change and flatly contradicting the affected-skill selection rule the Cost
model depends on. The pack therefore records, per skill, the hash of that
skill's own generated tree (its `SKILL.md`, its `references/`, and the shared
fragments the generator actually resolved into it — so a shared-fragment edit
changes the hash of exactly the skills that cite it, which is the same
dependency graph the selection rule reads), plus a hash per scenario. An
evidence bundle is fresh when every hash it recorded still matches; unchanged
skills keep their evidence, and only what actually changed needs re-running.

**A scenario's hash covers its whole input closure, not just its manifest
record.** Hashing only the scenario's YAML entry leaves the digest unchanged
when a *fixture* is edited in place — same path, same record — so evidence
produced against the old fixture would still read as fresh. The scenario hash
therefore covers the manifest record, the fixture files it resolves to, and
the fixture's `ground_truth.json` entry, so any change to what the scenario
actually feeds the agent requires refreshed evidence.

**A third hash covers abicheck itself, because the skills' answers come from
it.** Skill and scenario hashes alone leave a whole class of staleness
invisible: a PR that changes `compare`'s verdict logic, a report field the
skills read, or a CLI flag they drive changes what a live run would produce,
while every committed transcript — recorded against the *previous* build —
keeps re-grading green. That is the same evaluated-tree-vs-shipped-tree gap
D6 exists to close, one layer down. Each bundle therefore records an abicheck
build hash, and freshness requires it to match.

Scoping that hash is the one real design choice here, and it trades two
failure modes against each other. Hashing all of `abicheck/` is safest and
invalidates every bundle on every source commit, which would make the
evaluation unrunnable in practice. Phase 0 instead hashes the **surface the
skills actually consume** — the CLI command/option tree and the report JSON
schema, both of which `tests/test_agent_skills_drift.py` already extracts for
its own drift check, plus the `ChangeKind` registry's verdict mapping. That
catches every change to what a skill can invoke or read, and deliberately does
not catch a pure detector-internals change that alters a verdict without
changing any surface. Phase 6's pre-publication full pass is what closes that
residual, since it re-runs everything against the build being published.

This is what makes the risk-selected suite and the freshness gate the same
mechanism rather than two rules that can disagree: the set of skills whose
hashes moved *is* the set whose evidence must be refreshed.

### D7 — L3 in agent-benchmark: the arms that actually answer "does it help"

Once the pack exists, `agent-benchmark` runs the comparison it is already built
for. The arms:

| Arm | Spec | Answers |
|---|---|---|
| `baseline` | bare model, no skill, no docs | what the agent knows unaided |
| `docs` | abicheck documentation injected | is the skill better than just shipping docs? |
| `skill:<pack>/<name>` | skill body injected | eager-injection quality |
| `skill-agent:<pack>/<name>` | skill offered via progressive disclosure | does it get *found* and used, not just read |

**The four arms answer two different questions, and only one of them gates.**
An earlier draft treated `skill:` vs `docs` as the single verdict on whether a
skill deserves to exist. That is the wrong comparator to gate on, because the
`docs` arm injects documentation the user *already decided* to include — it
presupposes exactly the retrieval decision the skill exists to make. Split:

| Question | Comparison | Status |
|---|---|---|
| **Content quality** — is the distilled workflow better than the raw documentation, given both are in context? | `skill:` vs `docs` | **reported**, never gating |
| **Deployment value** — offered but not injected, does the skill get found, activated, and used to a better answer than the unaided agent? | `skill-agent:` vs `baseline` | **gates** publication |

`skill-agent:` vs `baseline` is the honest test of ADR-058's actual bet,
because progressive disclosure is how these skills are really deployed: nobody
pastes a `SKILL.md` into context by hand. A skill may legitimately lose the
content comparison and win the deployment one — a distilled workflow is not
obliged to carry more information than the full documentation, only to be
found and applied without the user knowing abicheck exists. Folding a skill
into a `shared/` fragment is therefore the response to losing the **second**
comparison, not the first.

The first comparison stays reported because it is diagnostic: a skill that
loses it badly is usually a skill whose Layer A has drifted into
documentation, which is a content bug worth knowing about even when the
deployment number is fine.

Reported per arm: judge score, verdict accuracy, safety-dimension pass rate,
tokens, wall clock, and cost — so "lift" is always a quality-per-cost number,
never quality alone.

**Three gaps in agent-benchmark are Phase 5 prerequisites, and two of them
currently invalidate the measurement rather than merely limiting it.** These
were verified against the checkout, not assumed:

1. **Truncation silently cuts a skill in half.** Both arms render the skill
   through `Skill.as_context(max_chars=12_000)` (`treatments/arms.py`,
   `treatments/tools.py`). `native-release-compatibility/SKILL.md` is ~17.5 KB
   today, so it is truncated mid-body in *both* the gating and the reported
   arm — the run would score a skill no user ever gets. The cap has to rise
   above the largest published `SKILL.md`, or the arms have to refuse to
   truncate rather than silently eliding, because a measurement of a
   truncated artifact is worse than no measurement: it looks like a result.
2. **`skill-agent:` has nothing to disclose.** `ViewSkillTool` builds its
   file list from `skill.resources` filtered to `.md`, and `loader.py`
   populates `resources` from top-level sibling *files* of `SKILL.md` only.
   abicheck's skills keep everything in `references/` and
   `references/shared/`, so that list is empty — the progressive-disclosure
   arm would offer the body and no references at all. Since D7 makes this the
   *gating* arm, fixing the loader to walk subdirectories is a precondition
   for Phase 5 having any validity, not a nicety.
3. **The executable-task track needs a pack adapter.** The with-skill arm
   exists in `harnesses/docker_solver.py`; the pack format has to be fed to
   it.

Gaps 1 and 2 share a shape worth naming: each would produce a *number* rather
than an error, and a number from a mis-measured artifact is exactly what this
plan exists to stop. Phase 5 does not start until both are fixed and a
round-trip check confirms the arm receives the whole skill and its references.

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
                                     (L2); L1l reuses tests/agent_skills/trigger_corpus.yaml
    schema/
      scenario.schema.json           scenario manifest contract
      transcript-bundle.schema.json  the bundle shape every runner must emit
      claim.schema.json              the final-answer verdict envelope (D3)
      rubric.schema.json             six dimensions, grader kind, gating mode
    shim/abicheck                    recording shim (argv + exit + report digest)
    runners/
      claude_code.py                 headless Claude Code runner
      codex.py                       (Phase 4)
      gemini_cli.py                  (Phase 4)
    graders/
      deterministic.py               dimensions 1, 2, 3, 6
      judged.py                      dimensions 4, 5 (model in the loop)
    run_skill_eval.py                live runner entry point (off-CI, maintainer-run)
    grade_bundle.py                  replay grader entry point (no model for 1,2,3,6)
    baselines/<agent>-<model>.json   per-triple baselines for dimensions 1,3,4,5
    evidence/<skill>/                committed transcript bundles — the merge evidence
    golden/                          curated transcript bundles, good and bad (L4)
scripts/
  gen_skill_eval_pack.py             builds skill-eval-pack.json (+ --check)
  check_skill_eval_freshness.py      D6's mechanical freshness gate
  check_skill_eval_evidence.py       re-grades committed evidence; the merge check
tests/
  test_skill_eval_scenarios.py       manifest/schema validity, fixture resolution
  test_skill_eval_graders.py         L4 — graders vs. golden good/bad bundles
  test_skill_eval_pack.py            pack generation + freshness check
```

No `.github/workflows/` entry: per D2, no model runs in this repository's CI,
so the live runner has no workflow. Both new `scripts/` checks are steps in
`scripts/verify.py`'s `pr` profile, which is what makes them required.

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
not-comparable artifact, a definite verdict with an empty `calls.jsonl`, a run
that reached green by adding a suppression, a correct verdict reached with the
wrong evidence depth, and three that exercise the envelope's fail-closed path
(no envelope, two envelopes, a verdict outside the vocabulary). One further
bundle claims `COMPATIBLE` where the artifact says `API_BREAK` — the case a
verdict-word regex over prose would have passed and a typed `claim.verdict`
catches, which is the whole reason the envelope exists.

**Each bundle asserts an expected failure *set*, not a single dimension.** The
dimensions genuinely overlap, and that overlap is correct behaviour rather than
double-counting: a false green over a not-comparable artifact violates both
dimension 2 (uncertainty not preserved) and dimension 6 (claim inconsistent
with artifact); a definite verdict with an empty `calls.jsonl` violates both 3
and 6. Demanding exactly one failure per bundle would either make these fixtures
unwritable or push the grader toward suppressing real findings to keep the
count at one — a grader teaching itself to under-report is the last thing this
corpus should incentivize. Each bundle names the dimension it primarily
exercises and lists every dimension it is expected to trip.

**Done when:** every golden bad bundle fails **at least its named dimension**,
with its full expected failure set asserted, and every golden good bundle
passes all four deterministic dimensions — with no model call, inside `pr`. Dimensions 4 and 5 are *replayed*
out of each golden bundle's `judgments.json` (D3), which checks the replay path
and the schema but deliberately does not re-derive a judge verdict; the golden
corpus therefore also carries one bundle whose `judgments.json` records a
*failing* judge verdict, so the replay path is proven to propagate a judged
failure rather than only ever reading passes.

This phase is where most of the value lands. After it, the repository can
detect ADR-058's non-negotiable failure mode from a recorded transcript, and
everything after is about producing transcripts.

### Phase 2 — Live runner (Claude Code), off-CI + the evidence gate *(M)*

Headless Claude Code runner at `k=3`, run by a maintainer rather than by a
workflow, driving **two** inputs — the L2 scenario manifest and, for L1l,
`tests/agent_skills/trigger_corpus.yaml` (see below); `check_skill_eval_evidence.py` and the freshness check wired into
`verify.py`'s `pr` profile; the first committed evidence set; and the first
real baseline for dimensions 1, 3, 4, 5. Dimensions 2 and 6 gate at zero from
the first run — including if that first run fails, which blocks rather than
baselines.

**Done when:** a maintainer's local run produces committed bundles per skill
that CI re-grades and accepts; a deliberately regressed `SKILL.md` fails the
re-grade; and a skill edit committed *without* refreshed bundles fails the
freshness check. The third is the one that matters — it is what makes running
the evaluation non-optional rather than merely available.

### Phase 3 — Scenario corpus buildout *(M)*

Category B first (every scenario `ground_truth.json` structurally cannot
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

**Done when:** each skill has a published quality-per-cost number on both
comparisons — the gating `skill-agent:` vs `baseline` and the reported
`skill:` vs `docs` — whichever direction each comes out.

### Phase 6 — Publication gate *(S)*

G36 P1.4's publication precondition becomes a check: publication requires a
fresh per-skill hashes across every published skill, zero failures on
dimensions 2 and 6, dimensions 1/3/4/5 at or
above baseline, and a Phase 5 scorecard showing non-negative lift on
**`skill-agent:` vs `baseline`** — D7's declared deployment comparator. The
`skill:` vs `docs` number is published alongside it and never gates: gating on
it here would have blocked exactly the skill D7 describes as legitimate — one
that wins the progressive-disclosure comparison it is deployed under and loses
the content comparison it is not.

## Cost model

Per live run: ~4–8 agent turns over a small fixture repository. At 24 scenarios
× `k=3` that is ~72 agent sessions per full pass. Two knobs shrink a
per-change run, and **`k` is deliberately not one of them**:

- `--suite risk` — the per-change evidence suite, at `k=3`: every Category B
  scenario, **plus every Category A scenario whose ground truth is not a
  compatible verdict, plus a standing floor of compatible-ground-truth
  scenarios** (see below), restricted to the skills the diff actually touches.
  Typically ~10–14 scenarios (30–42 sessions).
- `--suite full` — all 24 scenarios at `k=3` (72 sessions), before publication
  and whenever the affected-skill set is wide.

**The evidence suite is risk-selected, not a fixed sample, and that is what
makes acceptance criterion 1 true.** An earlier draft evaluated only Category B
before merge, which quietly contradicted the plan's own goal: a skill change
that manufactures a green result for a routine catalog case — a removed export,
a signature change — would have carried passing evidence and merged, with the
gap found only on some later full pass.

Selecting on non-compatible ground truth closes most of that, but **not all of
it, and an earlier draft's justification for the rule was wrong.** It claimed
"dimension 6 can only fail where a green claim would be wrong." Dimension 6 is
broader than that by this plan's own definition: it also fails a green reached
with an empty `calls.jsonl`, and a green reached by adding a suppression.
Both are reachable on a scenario whose ground truth *is* `COMPATIBLE` — the
claim matches the truth while resting on no evidence at all, which is precisely
the "right answer for no reason" failure the dimension exists to catch. Ground
truth predicts where a *wrong* verdict is reachable; it does not bound where an
*unjustified* one is.

So the suite carries a floor of compatible-ground-truth scenarios per affected
skill (two, one exercising each of the evidence-free and suppression paths)
alongside every non-compatible one. The full pass adds the rest, which are
there for the process dimensions.

**Which skills a diff "touches" is read off the generator's real dependency
graph, not guessed from the path.** A change to `skills-src/<name>/` affects
that skill; a change to `skills-src/shared/<fragment>.md` affects exactly the
skills that cite it, directly or transitively — which
`scripts/gen_agent_skills.py` already resolves, since that resolution is how it
decides which fragments to copy into which output tree. Citation breadth varies
widely across fragments — some are reached by a single skill, others by all
four — so a blanket "any shared edit escalates to the full suite" rule, which an
earlier draft asserted on the wrong premise that every fragment is universally
cited, would multiply the cost of editing a narrowly-used fragment for no
coverage gain. (The per-fragment numbers are the generator's to report, not
this plan's to restate.) Reuse the generator's graph rather
than restating the topology here; a fragment every skill really does cite
escalates to every skill by that rule anyway, without the plan hard-coding
which fragments those are.

**A build-surface change affects every skill, and the selector must say so —
otherwise the two halves deadlock.** D6's third hash covers the abicheck
surface the skills consume, and it is not reachable from any `skills-src/`
path: a PR touching only the CLI tree, the report schema, or the `ChangeKind`
verdict mapping moves that hash, invalidating every committed bundle, while a
selector reading only `skills-src/` diffs would nominate *no* skill to refresh.
The freshness check then fails with nothing the author can run to satisfy it.
So affected-skill selection is the union of two rules: the skills whose own
tree hash moved, **plus every skill when the build-surface hash moves**. The
practical effect is that a CLI/report-schema change costs a full re-evaluation
— which is the correct price, since it is exactly the change class that can
silently alter what every skill's workflow produces, and the hash is
deliberately scoped to consumed surface (D6) so ordinary detector-internals
commits do not trigger it.

Deterministic rotation was considered and rejected for the wide cases: a
rotating subset would make the gate's strength depend on when a PR happened to
land.

**`k` stays at 3 everywhere, and the suite size is what varies.** These are not
interchangeable ways to buy the same saving. Dropping to `k=1` would keep
scenario coverage while silently converting the `pass^k` safety gate into a
single-sample check — and `k` exists precisely to catch run-to-run variance,
which is the failure mode a stochastic agent has and a scenario list does not.
Dropping scenarios costs coverage, which is honest, visible in the evidence
set's own manifest, and recoverable on the next full pass.

Judged dimensions (4, 5) are skippable via `--no-judge`, leaving the four
deterministic dimensions — both zero-tolerance ones included — at near-zero
marginal cost. That is the intended everyday posture, and under D2 it is also
the permanent posture of CI itself: **the checks that block cost nothing,
because they re-grade a recording rather than produce one.** The model spend
is entirely in the off-CI step, is incurred once per skill change rather than
once per push, and never blocks on a rate limit or an expired credential in a
required job.

## Risks

| Risk | Mitigation |
|---|---|
| Model nondeterminism makes a required check flaky and the team learns to ignore it | No model runs in CI at all (D2), so a required check re-grades a fixed recording and is bit-for-bit reproducible; within the off-CI run, only deterministic dimensions gate hard, judged ones use baselines, and `k` runs give pass^k for safety with pass@k reported for the rest |
| Vendor CLI/harness churn breaks runners | Bundle schema is vendor-neutral; only `runners/*.py` is vendor-specific, and grading never is |
| The grader silently stops detecting anything | L4 golden bad bundles run in `pr` on every commit — that is exactly what they exist to prevent |
| Scenario corpus overfits; skills are tuned to the eval | Category A derives expectations from `ground_truth.json`, which is owned by detector work and not editable from a skill PR; corpus growth requires a case the correct behavior already passes, mirroring the FP-rate corpus rule |
| L3 shows a skill does not beat `docs` | Not a failure and not a gate — D7 gates on `skill-agent:` vs `baseline` instead, and reports the `docs` comparison as a content-drift diagnostic |
| Eval cost grows unbounded | Risk-selected evidence per change, full pass only before publication, judge optional; CI itself spends nothing |
| The off-CI step is skipped and evidence silently rots | D6's freshness check treats missing and stale evidence identically, so skipping the step fails the same way as never running it |
| An external contributor cannot produce evidence | Acknowledged and accepted (D2): a maintainer re-runs the evaluation and pushes the bundles. The alternative — credentials reachable from a fork-controlled skill, shim, and prompt — is not one this plan will take |

## Relationship to G36's own items

| G36 item | Status under G37 |
|---|---|
| **P0.8** (trigger tests) | static half stands as-is; its deferred live half becomes G37 Phase 2's L1l |
| **P1.1** (behavioral eval) | **superseded in implementation detail.** G37 keeps its substance — the two scenario categories, the six-dimension rubric, and the split gating model, all of which P1.1 got right — and changes: file locations (`agent-evals/skills/`, not `validation/`, per D8), the addition of the recording shim and replay grading (D3), pass^k rather than per-run grading for the safety dimensions, and live evaluation running off-CI with CI checking its evidence (D2) |
| **P1.4** (publication) | its freshness precondition becomes mechanical (D6) and its "acceptable baseline rate" becomes G37 Phase 6's explicit four-part gate |
| **P1.5** (cross-agent log) | generated from real results for scriptable targets (Phase 4); manual only for Copilot/Cursor |
| **P1.2/P1.3** (contingent) | unchanged — still gated on findings, which G37 Phase 3 is what actually produces |

G36's P1.1/P1.4/P1.5 items carry a pointer to this plan, added in the same PR
that introduced it, so neither document holds a second diverging design while
Phase 0 is pending.

## Decisions taken

Four open questions this plan raised were resolved before Phase 0; recorded
here so the reasoning is not re-litigated:

| # | Question | Decision |
|---|---|---|
| 1 | Harness location | `agent-evals/skills/` (D8), accepting the deviation from G36 P1.1's `validation/` paths. `validation/` runs abicheck against real-world package corpora and has no agent, model, or transcript in it; a third "evaluation" tree was rejected as surface sprawl. If holding two task kinds under `agent-evals/` becomes confusing, splitting it into explicit `code-tasks/` and `skill-scenarios/` sub-trees is a later rename, not a re-architecture |
| 2 | What the `docs` arm gates | Nothing — split the question (D7). `skill:` vs `docs` is reported as a content diagnostic; `skill-agent:` vs `baseline` is what gates publication, because progressive disclosure is how these skills actually deploy and the `docs` arm presupposes the retrieval decision the skill exists to make |
| 3 | When G36 is amended | Immediately, in this plan's own PR, rather than deferred to Phase 0 — the divergence window costs more than the edit |
| 4 | Live evaluation in CI | No. CI runs deterministic checks only; the live runner is an off-CI maintainer operation whose committed evidence CI re-grades (D2). This also resolves two structural problems the alternative has: a label-gated job does not block merge when skipped, and a fork PR cannot hold credentials without exposing them to PR-controlled content |

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
