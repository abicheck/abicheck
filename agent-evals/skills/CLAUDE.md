# CLAUDE.md — `agent-evals/skills/`

Quality measurement for the published Agent Skills (ADR-058), per
[`docs/contribute/plans/g37-agent-skill-quality-evaluation.md`](../../docs/contribute/plans/g37-agent-skill-quality-evaluation.md).
The plan is the narrative owner — read it for *why* each contract has the
shape it does; this file is the orientation for working in this directory.

Sibling of `agent-evals/`'s existing task harness, not a replacement: that one
scores an agent **modifying abicheck**, this one scores an agent **equipped
with a skill**. Same mechanism, different subject.

## What exists today (Phase 0 through Phase 3)

Phase 0's contracts and deterministic checks are complete. On top of them sit a
recording shim, a headless two-arm runner, and the four deterministic graders
with the bad-run corpus that exercises them — enough to produce a transcript
*and* score it. Phase 3 landed a full 12-scenario corpus and a real 48-run
pilot against it — see `pilot-results/README.md` for the numbers and, just as
important, the harness confound (`--max-turns`) that limits how much weight
they can bear today. **Still missing: the judged dimensions (4 and 5), the
trigger corpus runner, and any committed evidence.**

**`harbor/` is now the canonical evaluation surface** (ADR-058's
Harbor-migration amendment, decided 2026-08-21) — a real, schema-validated
[Harbor](https://www.harborframework.com) task per scenario, generated
from the same `scenarios.yaml`/`skill-eval-pack.json` and sharing the same
deterministic graders unmodified. The two-arm runner above
(`runners/claude_code.py`) is now historical/frozen: kept because it is
what produced the one pilot that exists, not maintained for new work. See
`harbor/CLAUDE.md` for what has and has not actually been verified (no
Docker in this environment, so no real Harbor trial has run yet — being
canonical is a decision about where new work goes, not a claim that
Harbor execution itself is already proven end to end).

| Path | Role |
|------|------|
| `scenarios.yaml` | The behavioral (L2) corpus. Category A names an `examples/` case and derives its expected outcome from `ground_truth.json`; Category B carries invocation parameters the catalog structurally cannot express and states its own outcome. |
| `rubric.yaml` | The six dimensions with their grader kind and gating mode. Graders and the publication gate both read this, so they cannot disagree about which dimension is zero-tolerance. |
| `schema/scenario.schema.json` | `scenarios.yaml`'s contract. |
| `schema/rubric.schema.json` | `rubric.yaml`'s contract. |
| `schema/claim.schema.json` | The final-answer envelope: a typed ordinal verdict (or `null`), the calls it rests on, and a closed-vocabulary uncertainty reason. Also carries an **optional** `decision` field — the customer-facing outcome in `check-abi-compatibility`'s own SKILL.md vocabulary (`VERIFIED_COMPATIBLE`/`COMPATIBLE_WITH_DEPLOYMENT_RISK`/`SOURCE_BREAK`/`BINARY_BREAK`/`NOT_VERIFIED`), distinct from the raw tool `verdict`. Additive groundwork, not yet the primary metric: no scenario's `expected` block states one yet, and `dimension_6` only checks it for internal consistency against `verdict`/`confident` when a claim happens to state it — see `graders/claim.py::decision_inconsistency`. |
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

## Environment prerequisites for a real run

Two environment gaps block every skill-arm run before it reaches the
scenario at all, found and closed while producing this directory's first
real evidence (G37 Phase 3 pilot). Neither is a defect in the skill or the
harness — both are "the sandbox lacks what a real deployment already has,"
and a run against an unfixed sandbox produces a *contaminated* comparison,
not a low score: the skill-arm dead-ends at its own preflight while the
baseline arm proceeds normally, which reads as "the skill hurts," when the
actual cause is that neither arm had a workable toolchain.

1. **`abicheck --version` must report a version inside every ready skill's
   declared `abicheck-version-range`.** A fresh editable install
   (`pip install -e .`) stamps `importlib.metadata` from `pyproject.toml`'s
   own `version` field — which, per this repo's own convention
   (`skills-src/CLAUDE.md` rule 7), stays at the *last cut* release number
   between releases even though the working tree's actual CLI surface is
   already ahead of it. `check-abi-compatibility` declares
   `>=0.6.0,<0.7.0`; a checkout still reporting an earlier version (e.g.
   `0.5.0`) makes the skill's own preflight step — correctly, per its own
   stated contract — refuse to proceed on every single skill-arm run. This
   surfaced as a real run's own `not_comparable` misfire: see "A `null`
   verdict is a claim too" below, which was originally written from exactly
   this failure mode without yet naming the cause. **Do not "fix" this by
   loosening the skill's declared range** — the range is a real fact about
   which release contains which CLI surface, not a knob to tune per
   environment. Instead, for an evaluation environment only, make
   `abicheck --version` report truthfully what the checkout can already do.
   **Two independent metadata sources can both answer this, and
   `importlib.metadata` (what the CLI's own `--version` reads) picks
   whichever it finds first — patching only one is not enough on a plain
   editable install (`pip install -e .`), confirmed by patching each in
   turn and re-checking `abicheck --version` after each:**
   ```bash
   # 1. the editable install's own egg-info (this repo's checkout root) —
   #    found to take precedence over (2) on a plain `pip install -e .`
   sed -i 's/^Version: 0\.5\.0/Version: 0.6.0/' abicheck.egg-info/PKG-INFO
   # 2. the site-packages dist-info the editable install also registers —
   #    patch this too so a resolution order that prefers it still works
   D=$(python3 -c "import importlib.metadata as m; print(next(str(d._path) for d in m.distributions() if d.metadata['Name']=='abicheck'))")
   sed -i 's/^Version: 0\.5\.0/Version: 0.6.0/' "$D/METADATA"
   ```
   Verify with `python3 -c "from importlib.metadata import version;
   print(version('abicheck'))"` and `abicheck --version` — both must report
   `0.6.0` before a skill-arm run's preflight will pass. Both edits are
   local, ephemeral (a fresh container/checkout reverts them), outside
   version control, and must never be treated as evidence the version was
   actually released; never edit `pyproject.toml`'s own `version` field to
   do this, since that is a real release decision, not an evaluation
   convenience.
2. **`abicheck compare --depth headers`/`dump` need a header-AST frontend
   this host can satisfy.** `abicheck`'s default is CastXML, policy-gated to
   `>=0.6.11,<0.8.0` (`castxml_policy.py`); a plain `apt install castxml` on
   a recent Ubuntu base commonly resolves an older build (observed:
   `0.6.3-1build2`) that abicheck correctly refuses as unsupported rather
   than silently trusting. Either install a real conda-forge CastXML in the
   supported range, or set `ABICHECK_ALLOW_AST_FALLBACK=1` in the runner's
   environment (inherited by every nested `abicheck` invocation both arms
   make) so a rejected CastXML degrades to the direct-Clang backend instead
   of hard-erroring — `clang`/`clang++` are commonly already present.
   `--ast-frontend clang` forced explicitly is the alternative if the
   fallback's own warning noise is unwanted; either is a legitimate,
   disclosed environment choice, not a change to what is being measured.

Record both facts (and the resolution actually used) alongside any
evaluation results this directory produces — a scorecard is not
interpretable without knowing whether these were fixed for that run.

## The workspace must not contain the answer

Same failure as the `--out` rule below, one directory further in. A catalog case
is *documentation* — its `README.md` states the verdict in its second line and
gives the exact `abicheck compare` command, its `CMakeLists.txt` calls
`abicheck_add_case`, its demo consumer prints `"NO_CHANGE at binary ABI level"`,
and three of the eight ready fixtures annotate their own sources
(`/* helper() removed — BREAKING change */`; one header names the tool and the
change kinds it reports). Copying a case wholesale handed both arms the tool the
prompt deliberately never names and the answer they were about to be graded on.

So the workspace gets the library sources and headers with their comments
stripped, and neither the explanatory files nor the case's demo consumer. The
corpus is not edited to suit the evaluation — those annotations are useful where
they live. Stripping was verified by compiling every ready fixture both ways:
16/16 translation units built, with identical exported symbol sets.

`workspace_leaks()` is the backstop and runs before any model call, because a
filename denylist cannot see inside a file it correctly copied. A leak aborts
the run rather than degrading it: the result would be evidence about the
fixture, not about the skill.

Both checks that can reject a run — this one and `check_treatment` — fire
*after* `_run_once` has written `final.md`, so a rejected run is
indistinguishable on disk from a crashed one. That is why `_recovered_record`
re-runs them rather than trusting the file: recovering on the strength of
`final.md` alone would launder a contaminated run into accepted evidence on the
next resume.

Tests for all of this live in `tests/test_skill_eval_harness.py` — the
harness half — while `tests/test_skill_eval_graders.py` pins the grading rules.
A grader cannot detect a run contaminated before it started, which is why the
two are separate files rather than one.

## Reading an argv is not obvious, and two readers must agree

The shim and the graders both parse the same command line, and Click accepts
more spellings than either originally handled — each gap verified against the
real CLI before it was closed. Options sit *between* positionals
(`compare x --format json x`); boolean flags consume nothing
(`compare x -vv x`); short options pack into clusters that can carry a value
(`-voreport.json` writes `report.json`); and `compat check` speaks ABICC's
single-dash *long* options (`-old`, `-d1`), which are not clusters at all.

Arity therefore comes from Click's own command tree rather than a list here —
`compare` alone declares 50 boolean flags. When the table cannot be built the
graders assume every option takes a value, which can miss a self-comparison;
the opposite assumption invents an operand and fails a *correct* run, and that
is the direction that gets a gate switched off.

One more confound the arms carry by construction: they run **sequentially**,
and nothing pinned a model. A configured default that moves between them — or
between a batch and the resume that finishes it — is aggregated by arm alone,
so what reads as skill lift could be a model difference. `--model` pins one;
either way the resolved model is recorded per run and a batch that mixes two is
refused. Same answer as the in-repo workspace and the answer-bearing fixture:
observe it, record it next to the outcome, refuse when the arms are not
comparable.

A test that pins a platform-dependent default must say so. `_bypassed_the_
recorder` defaults `interposed` to `os.name != "nt"` — on Windows no
interposer is installed, so every module entry really is a bypass — and three
tests that left it to the host asserted something different depending on where
they ran. The Windows lane caught it; pass `interposed=` explicitly.

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

1. Category A if `catalog/ground_truth.json` can answer it — name the case,
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
