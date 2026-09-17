# Consumer integration consolidation — delivered surfaces and handoff

**Status:** four slices landed on `claude/relaxed-gates-b7h32y`; three
capabilities explicitly still missing (listed in
[Still missing](#still-missing)). Working document, deliberately out of the
published nav.

A downstream consumer (PVXS) had reimplemented a baseline publisher, a
tested-commit provenance channel, and a component-spec binder beside its own
CI, because the upstream interfaces stopped one step short of what an
automatic `workflow_run` integration needs. This records what was closed,
what the contract now is, and what a consumer still has to supply itself.

## Tested revisions

| What | Revision |
|---|---|
| abicheck, before this work | `e3e5197e4` (`origin/main`, 2026-09-17) |
| abicheck, after this work | `ceb2f663b` on `claude/relaxed-gates-b7h32y` (12 commits) |
| PVXS integration as reviewed | `napetrov/pvxs` PR #2 head `40c8eb89a` (its `f5ade4a3d` is the commit the previous review measured) |
| PVXS migration demonstrated here | `adf909b31` — a **disposable local branch**, never pushed |
| PVXS default branch | `3b8f5d109` (`origin/master`) |

**Pull-request status.** `abicheck/abicheck` had **no open pull requests** at
the time of writing; these five commits are on the designated branch and a
pull request for them is the next step. `napetrov/pvxs#2` and
`epics-base/pvxs#216` are the consumer's own; **nothing was pushed, merged,
commented on or published to either.** No future merge SHA is named anywhere
in this document.

Gates run locally: `ruff check` / `ruff format --check` clean over
`abicheck/` and `tests/`; `mypy abicheck/` **0 errors** over 869 files;
`python scripts/check_architecture.py` **0 errors** (both new modules and the
`cli_provenance.py` split stay under the per-file ceiling);
`scripts/check_docs_contract.py` **0 errors**; `scripts/verify.py --profile
pr --only docs-build,docs-contract,action-cli-surface` passed; and the full
fast unit lane (`-m "not integration and not libabigail and not abicc and
not slow and not golden"`), **51,348 passed** with one class of failure
accounted for below.

`scripts/check_ai_readiness.py` reports **15 errors, all pre-existing**: ADR
`**Verified:**` receipts naming commits unreachable from `origin/main`. This
branch touches no file under `docs/contribute/adr/`, and the same check was
run in a worktree of `origin/main` and fails there identically — checked,
not assumed, since "pre-existing" is the easiest thing in a handoff to
assert and not verify. `test_ai_readiness.py::test_adr_status_sync_holds`
is that same check and fails for the same reason.

The only other failures in the full run were an environment artefact, named
here so the next reader does not chase them: this sandbox periodically
deletes `/tmp`, which removes the git commit-signing helper the environment
configures, and every test that builds a throwaway repository then fails at
`git commit` with exit 128 (`test_protect_committed_baseline_workflow.py`,
`test_agent_evals.py`, `test_l2_real_profiles.py` — 22 of them). All pass
once the helper is restored, and none of them touch this changeset.

Not run here: the `integration`/`libabigail`/`abicc` marker lanes (no
external toolchain for them in this environment) and the coverage floor,
which is a canonical-lane CI measurement. Nothing in this changeset touches
analysis, so those lanes are unaffected by construction — but that is a
claim, not a measurement, and is recorded as such.

## What landed

### 1. The publication tag is no longer compared against a capture's commit

`abicheck/frontends/action/tag_resolution.py` (new) is the one owner of
"which commit does this tag name". `publish-baseline.yml`'s pre-captured path
used to compare a baseline-set's own `project_ref` against the *release tag*,
which is correct only for a set that workflow captured itself — its own
capture step stamps the tag into the manifest. A producer's capture records
the commit it built, so the comparison rejected every genuine cross-run
capture.

The new `expected-project-ref` input states the expectation as a **closed
set**: `''`/`commit` (default) resolves the tag through `refs/tags/<tag>`,
peeling an annotated tag; `tag` expects the literal tag string, which is the
documented legacy behaviour this workflow's own capture path produces; a full
SHA expects exactly that. Anything else is a usage error — never a fallback
from a failed comparison, which would accept a set matching neither.

Three refusals rather than workarounds: a name that only prefix-matches a
longer tag (the tags endpoint answers with an *array* in that case, so asking
for `1.5` can come back describing `1.5.2`), a ref that is not exactly
`refs/tags/<tag>`, and a tag object that does not peel to a commit.

`baseline_source.resolve_tag` — which already existed and was the *looser* of
the two, accepting an object of any non-`tag` type as the commit, so a ref
pointing at a **tree** resolved as a release baseline — now delegates to the
same owner and keeps only its own `tag`/`not_a_tag`/`lookup_failed`
vocabulary. The more specific reason travels on the new
`TagResolution.refusal_code`.

### 2. The analysed revision travels in the canonical document

`abicheck/model/analysis_context.py` (new) owns the record;
`abicheck/frontends/action/cli_provenance.py` (new) owns both ends.

On a `pull_request` run the producer builds an **ephemeral merge commit** that
no GitHub API endpoint names. That single missing fact is why the consumer
wrote a `tested-sha.txt` sidecar, parsed it in privileged shell, and ran the
whole verifier a *second* time to check it — acquiring the same bytes twice,
with no guarantee the second copy is the first.

* Producer: `actions/aggregate`'s `record-analysis-context: 'true'` writes an
  `analysis_context` block (`abicheck.analysis-context/1`) into the aggregate
  document it already publishes. **Emitted for a zero-comparison run too**, so
  an incomplete analysis stays publishable *as* incomplete with its identity
  intact.
* Consumer: `actions/verify-source-run`'s `provenance-from` reads it out of the
  artifact that step has already extracted, through the same bounded reader,
  shape-checks every field before it can reach a step output, and verifies the
  claim's association with the pull request against the API.

Five identities stay **separate** fields — PR head, PR base, the commit
actually built, the producer's run/attempt, and the orchestration revision —
because every reporting error here is "one displayed as another". A field the
producer cannot state stays empty rather than borrowing a plausible sibling.

New outputs: `tested-sha-source` (`analysis-context` | `run-head`),
`provenance` (`recorded` | `absent` | `not-requested`), and
`report-path`/`report-available`, which return the report location *with* the
identity it was checked under. With `require-provenance: true` (the default) an
absent record **refuses** rather than substituting the PR head.

### 3. Cross-run baseline acquisition

`abicheck/frontends/action/precaptured_source.py` (new).
`baseline-set-source-run-id` (plus `-repository`, `-run-attempt`,
`-expect-workflow`, `-expect-event`, `-allowed-conclusions`) lets
`publish-baseline.yml` publish a set captured by a **different, already
completed** run.

* A **pull-request-triggered producer is refused unconditionally**. ADR-047
  §12 forbids a baseline-publishing workflow from triggering on a pull
  request; a capture taken from one reaches the same immutable channel by a
  longer route. A configurable version of that rule is not a rule — a caller
  that declares `pull_request` as its expected event is still refused.
* Artifacts are selected by their own **ids**, carried through the matrix, and
  fetched by id. An artifact can be added to a run between discovery and
  publication, so a name-resolved fetch could publish bytes no eligibility
  check ever looked at.
* A declared attempt reads **that attempt's own document**
  (`/actions/runs/{id}/attempts/{n}`), so a re-run started after the trigger
  cannot be published under the first attempt's decision, and cannot be
  reported as a mismatch that is really a race. An unreadable attempt fails
  explicitly.
* Archives are unpacked under the same size/entry/ratio caps
  `verify-source-run` applies. A producer-local directory is never the
  interface; every crossing is an artifact through the API.
* The read-only producer query lives in the `discover` job (`actions: read`,
  **no write scope at all**); the write-capable publication is the `publish`
  job, which only fetches an id it was handed.

Both acquisition modes converge on the **same** validator, staging and
immutability chain. Nothing downstream of the validator branches on how the
bytes arrived — asserted as an invariant over the workflow, not by
inspection.

### 4. Declared binding in the component resolver

`resolve_library_set(..., bindings=...)` /
`resolve-libraries --bind NAME=VALUE` / `actions/baseline`'s
`library-spec-bindings`. A checked-in declaration cannot spell an install
prefix or a host-arch directory component, so every such project wrote its own
substitution pass — and a textual one mangles any value containing `&` (which
means "the whole match" in a replacement), a quote, a backslash, or the
delimiter, none of which is rare in a filesystem path.

Substitution is structural (the document is walked as JSON), the caller's map
is the **whole allowlist** (an unbound `${...}` is refused, never left as text
and never read from the environment), and values are inserted literally and
never re-scanned — one pass, no fixed point, no recursion to bound. Only the
braced form is syntax: no `$NAME`, no defaulting, no nesting.

## The candidate / tag / run identity contract

Five identifiers, deliberately never folded together:

| Identifier | Who states it | Verified how |
|---|---|---|
| **PR head** | GitHub API (`/commits/{sha}/pulls`) | Authoritative; never taken from the artifact |
| **Tested commit** (the merge commit actually built) | Producer, in `analysis_context.tested_sha` | Must be the PR head *or* a merge of it — a single-parent child of the head is refused |
| **Producer run / attempt** | The publisher's own trigger payload | Re-read from the API; a declared attempt reads that attempt's document |
| **Orchestration revision** | The producer, in `analysis_context.orchestration_ref` | Not verified — it is a label, and it is *separate* so a historical bootstrap (old captured revision, current orchestration) does not read as a tag push of the old one |
| **Release tag → commit** | GitHub API (`git/ref/tags/<tag>`, peeled) | Exact `refs/tags/<tag>` only; a prefix match and a `refs/heads/` ref are both refused |

Everything a producer states is a **claim**. What changed is where claims
travel and who parses them, not whether they are verified. API association
establishes consistency and a safe destination; it is not independent
attestation of contributor-produced analysis.

## Permissions and default-branch prerequisites

* `publish-baseline.yml` in cross-run mode needs **`actions: read` on
  `baseline-set-source-repository`** in addition to `contents: write` on the
  publishing repository. The two live in different jobs.
* `verify-source-run` needs `actions: read`; `actions/report` needs
  `pull-requests: write`.
* `workflow_run` only ever runs the copy of a workflow on the **default
  branch**. Until a maintainer merges the publisher there, nothing in a pull
  request — including the pull request that adds it — publishes anything. That
  is by design and is unchanged.
* A calling workflow must still be push/dispatch/release-triggered, never
  `pull_request`/`pull_request_target` (ADR-047 §12). The producer-side half of
  that rule is now enforced in code rather than by convention.

## Consumer deletion map

Demonstrated on a **disposable** PVXS checkout (branch `migration`,
`adf909b31`, never pushed). Integration lines added against `origin/master`,
counting `.github/` and `.ci-local/` only:

| File | Before | After | What went |
|---|---:|---:|---|
| `.github/actions/abicheck-publish-baseline/action.yml` | 149 | **0** | Deleted. Profile check, tag→commit resolution, manifest `project_ref` comparison, release lookup and the `--clobber`/no-op collision branch are all `publish-baseline.yml`'s. |
| `.github/workflows/abicheck-report.yml` | 201 | 164 | The `tested-sha.txt` read, the SHA shape-check in privileged shell, the **second** `verify-source-run` invocation and its second download, and the `steps.tested-verified.outputs… \|\| steps.source.outputs…` fallback expressions. |
| `.github/workflows/ci-scripts-build.yml` | 473 | 438 | The hand-written `analysis-context.json` block and the `tested-sha.txt` writer. |
| `.github/actions/abicheck-capture/action.yml` | 216 | 170 | The generic JSON placeholder-binding pass and its resolved-spec temp file. |
| `.github/workflows/abicheck-baseline.yml` | 247 | 259 | Both publish jobs become one `uses:` each; a small `eligible` gating job was **added** (see below). |
| `.ci-local/*` | 79 | 79 | Unchanged — the component declaration and extraction config are the consumer's own. |
| **Total** | **1365** | **1110** | **−255 (−19%)**, one whole local Action deleted |

`abicheck-baseline.yml` grew by 12 lines, which is worth stating plainly
rather than netting away: the original used `verify-baseline-source mode: tag`
as an in-job **skip** for a push that is not a release, and a reusable-workflow
call is a job, so it cannot be gated on a step output in the same one. The
migration adds a small `eligible` job that runs the same existing Action and
gates `publish` on its output. That preserves the behaviour — a non-tag push
must skip, not fail — and is not an invented workaround, but it *is* structure
the consumer still carries. See [Still missing](#still-missing).

Deliberately **preserved**, not deleted: the historical bootstrap's build
commands, its `contents: read` / `contents: write` job split, and its own
tag→commit lookup for choosing a checkout ref. That lookup is not redundant
scanner logic — it only picks what to build, and the publisher independently
re-resolves the tag and validates the capture's recorded `project_ref` against
it, so a wrong answer there can build the wrong tree but cannot publish one.

The full diffs are reproducible from the branch:
`git diff origin/master...migration -- .github .ci-local` (1177 lines) and
`git diff pr2..migration -- .github .ci-local` (752 lines).

## Tests

Reported separately, as added lines against `origin/main`:

| | Added lines |
|---|---:|
| Production code and workflows | 2,179 |
| Tests | 3,230 |
| Documentation and changelog fragments | 798 |

Consumer reduction is the separate number in the deletion map below: **−255
integration lines (−19%)** with one whole local Action deleted.

The table below is the tests added, by boundary.

| Boundary | File | Cases |
|---|---|---|
| Tag → commit (primitive) | `tests/test_action_tag_resolution.py` | 82 |
| Tag resolution (real step shell, stubbed `gh`) | `tests/test_publish_baseline_tag_resolution_step.py` | 17 |
| Provenance, producer→consumer round trip | `tests/test_action_analysis_context.py` | 87 |
| Cross-run eligibility & selection (primitive) | `tests/test_action_precaptured_source.py` | 37 |
| Cross-run acquisition (real step shell, stubbed `gh`) | `tests/test_publish_baseline_cross_run.py` | 36 |
| Declared binding (primitive) | `tests/test_action_library_spec_binding.py` | 82 |
| Documented Action examples resolve | `tests/test_docs_action_examples.py` | 122 |

Written to the repository's own bug-class discipline rather than as
fixed-input reproducers:

* **Generated siblings, not one example.** Object types are swept
  exhaustively over the small domain GitHub can produce; every event name is
  swept rather than naming the two that are refused; hostile binding values
  are a family chosen because each breaks a *different* textual substitution.
* **Independent oracles.** Expected results are constructed directly (the
  already-substituted document, the ref document's own meaning), never by
  calling the implementation a second time or re-deriving with the same regex.
* **Vacuity guards** on every scan-shaped assertion, because a
  "token not in script" check passes trivially against an empty script.
* **The steps are executed**, not only read: the tag-resolution and cross-run
  steps run their real `run:` blocks against a `gh` stub that answers only the
  endpoints they are supposed to call and refuses everything else — so a
  shortcut through a different endpoint (`repos/{repo}/commits/{tag}`, which
  resolves a *branch* of the same name) fails there rather than passing
  quietly. Seven cross-run refusal paths are exercised end to end and each
  asserted by its own code.

Each fix was mutation-checked by hand: reverting the exact-ref selection, the
annotated-tag peel, the commit-mode fallback, the validator's
`EXPECTED_PROJECT_REF` wiring, and two `verify-source-run` output declarations
each turns the relevant tests red.

A fourth documentation defect class turned up on the way and is fixed here
rather than filed: three documented Action examples passed inputs the Action
does not declare (`severity-addition` on two pages — `use/severity.md`'s own
next paragraph says per-category overrides are not Action inputs, so the
example contradicted the prose two lines below it — and `old`/`new`/
`headers`/`output` on the fork-PR page). GitHub accepts an unknown `with:`
key silently, so a reader copying any of them got no error, just an ignored
value. `tests/test_docs_action_examples.py` now sweeps every documented
first-party step's inputs and every `steps.<id>.outputs.<name>` expression
against the real `action.yml`, with `contribute/adr|archive|plans` exempt as
historical records — the same line `check_docs_contract.py` already draws.

Five pre-existing tests were **sharpened rather than relaxed** to accommodate
new surface, and each now states a stronger invariant than before:

* `test_publish_baseline_workflows.py`'s permission tests pinned an exact
  permissions dict; they now assert that **no job but `publish` grants any
  writable scope**, which the dict form did not say (it would have accepted a
  job quietly gaining `packages: write` alongside `contents: write`).
* `test_action_report_contract.py`'s declared-surface test pinned an exact
  output set; it now asserts that no published output **disappears** and that
  every declared output **resolves to a real step id** — an output declared
  with a typo was silently empty for every consumer and nothing said so.
* `test_action_aggregate.py`'s input-wiring test matched only the
  `INPUT_<NAME>` spelling; it now accepts any composite step that *reads* the
  input, with a vacuity guard proving an unreferenced name still fails.
* `test_action_baseline_source.py`'s tag fixtures used symbolic object names
  (`"C1"`) and omitted `ref`, which no real API response does. They are now
  shaped like the API's, which is what makes three newly-reachable refusals
  testable at all.
* `use/fork-pr-reporting.md` — the canonical owner of the fork-PR topic —
  documented the sidecar-plus-second-verification sequence as *the* way to
  report an analysed merge commit. Left alone it would have kept teaching the
  workaround the fix removes, so both halves are rewritten and the old
  pattern stays only as a note saying why not to do it.

### The migration itself

The full diffs live on the disposable branch
(`git diff origin/master...migration -- .github .ci-local`, 1177 lines) but
that checkout does not survive; the three substantive replacements are
recorded here so the next reader needs nothing else. `<abicheck-ref>` is the
SHA this work is pinned to once merged.

**Publishing a tag build's capture** — the whole job becomes one call:

```yaml
  publish:
    needs: eligible                      # see the note on the gating job above
    if: ${{ needs.eligible.outputs.is-tag == 'true' }}
    permissions:
      actions: read                      # the cross-run artifact fetch
      contents: write                    # the release upload
    uses: abicheck/abicheck/.github/workflows/publish-baseline.yml@<abicheck-ref>
    with:
      baseline-set-artifact-prefix: abicheck-candidate-
      baseline-set-source-run-id: ${{ github.event.workflow_run.id }}
      baseline-set-source-run-attempt: ${{ github.event.workflow_run.run_attempt }}
      baseline-set-expect-workflow: .github/workflows/ci-scripts-build.yml
      baseline-set-expect-event: push
      baseline-set-allowed-conclusions: success
      release-tag: ${{ github.event.workflow_run.head_branch }}
      baseline-generation: '1'
```

The bootstrap path is the same call with `baseline-set-artifact-prefix:
abicheck-bootstrap-` and no `baseline-set-source-*`: its capture is uploaded
into the same run, so no producer verification applies.

**Recording and reading the analysed commit** — producer side, on the
existing `actions/aggregate` step:

```yaml
        record-analysis-context: 'true'   # tested-sha defaults to github.sha
        profile: ${{ env.ABICHECK_PROFILE }}
        orchestration-ref: <abicheck-ref>
```

Publisher side, on the single `verify-source-run` step:

```yaml
        provenance-from: aggregate.json
        report-from: aggregate.json
```

and then `report: ${{ steps.source.outputs.report-path }}`,
`sha: ${{ steps.source.outputs.tested-sha }}`. The whole second
`verify-source-run` step, the `tested-sha.txt` reader, and every
`steps.tested-verified.outputs.… || steps.source.outputs.…` fallback go.

**Binding build-decided values** — on the existing `actions/baseline` step,
replacing the consumer's own substitution pass:

```yaml
        library-spec-bindings: |
          EPICS_BASE=${{ steps.epics.outputs.epics-base }}
          EPICS_HOST_ARCH=${{ steps.epics.outputs.host-arch }}
```

Every reference above was checked against the real upstream `action.yml` /
`workflow_call` input sets and output declarations, mechanically: 8 + 3
reusable-workflow inputs, 11 `verify-source-run` inputs, 13 `report` inputs,
6 `aggregate` inputs, 11 `baseline` inputs, and the 10 `verify-source-run`
outputs the reporter reads — all declared.

### Bug classes to register on merge

Three genuinely new classes came out of this pass. They are **not** in
`tests/regressions/manifest.py` yet, deliberately: that registry requires a
non-empty `fixed_by`, and its own rule is that "a class with no `fixed_by` is
a hypothesis, not a registered escape-history entry". These trace to commits
on an unmerged branch, and inventing a pull-request number for them would
make the registry's traceability claim false. Add them to
`tests/regressions/manifest_tool_surface.py` with the real number once this
merges:

| Id | Invariant, in one line | Seed tests |
|---|---|---|
| `identity.two_distinct_identifiers_compared_as_one` | A name and the object it names are never folded into one equality; the peel has exactly one owner and refuses every near-miss rather than guessing. | `test_action_tag_resolution.py`, `test_publish_baseline_tag_resolution_step.py` |
| `identity.absent_evidence_defaulted_to_a_plausible_sibling` | "Not recorded" and "recorded as X" never collapse; related identities stay separate fields; which state was reached is itself published. | `test_action_analysis_context.py` |
| `config.textual_substitution_into_a_structured_document` | A value bound into a structured document is inserted structurally, once, from a closed allowlist — never by rewriting serialized text. | `test_action_library_spec_binding.py` |

## Acceptance coverage

The requested acceptance table, mapped to what actually asserts each row.
Rows this pass did **not** reach are marked as such rather than argued
around.

| Required acceptance | Where |
|---|---|
| Same-run and cross-run captures reach the same validator | `test_publish_baseline_cross_run.py::TestBothAcquisitionsConvergeOnOneValidator` — the validator's `if:` names no acquisition, and nothing after it does either |
| Wrong run / attempt / profile / tag / commit is rejected | `test_action_precaptured_source.py` (each with its own refusal code) and `TestTheAcquisitionStepActuallyRuns::test_each_refusal_fails_the_step_with_its_own_code` — seven paths executed against a stubbed `gh` |
| Numeric and annotated tags work | `test_publish_baseline_tag_resolution_step.py::TestTheStepResolvesRealTagShapes`, including that the annotated tag's own object SHA is never the answer |
| Equal canonical content is idempotent; conflicting same-name content is not accepted | Pre-existing (`test_publish_baseline_upload_step*.py`); unchanged by this work, which routes both acquisition modes into that same chain |
| Publication invokes no build, capture or comparison | `TestPublicationStillInvokesNoAnalysis`, `TestCrossRunModeStillNeverBuilds`, plus the pre-existing `test_publish_baseline_existing_set.py::TestExistingSetModeNeverBuilds` |
| Head checkout, merge checkout, stale/wrong/missing context, partial analysis, absent report retain accurate identities | `test_action_analysis_context.py` — the five-identity separation, the absent-vs-recorded distinction, the zero-comparison case, and the `report-available: 'false'` state |
| The normal path acquires the artifact once | `TestTheNormalPathAcquiresTheArtifactOnce` — exactly one download, one extraction, and one `verify-run`, counted over the real shell |
| Applicability changes by event without fabricated failures | **Consumer-side, not upstream.** The declaration is the consumer's (`aggregate`'s `checks:` input already supports it); the gating job the migration adds is the part that still has no upstream home — see item 1 below |
| Four requested checks retain component/channel identity and missing-state coverage | Pre-existing (`test_action_aggregate.py`, `aggregate`'s `channels` output); this pass added no orchestration adapter — see item 3 below |
| Compatible addition, break, existing-warning resolution under `changes`, persistent-only background, incomplete analysis render correctly | Pre-existing (`report_publication.py`'s own suites); unchanged here beyond the report now arriving on `report-path` |
| A disposable external consumer consumes official interfaces | Demonstrated and mechanically checked (see the deletion map); **not** a committed upstream test, since it needs an external checkout CI does not have. The nearest durable guard added instead is `test_docs_action_examples.py`, which holds every *documented* integration example to the same standard |
| Hostile-artifact refusal and side-effect absence | Pre-existing (`test_action_artifact_extraction.py`); the cross-run path is asserted to route through the same extractor rather than `unzip`, and a refused acquisition is asserted to leave no staged bytes |

Two measurement rows from the request are deliberately **not** claimed:
build/capture/comparison/rendering timings were not measured (nothing here
changes any of them, and reporting a number this pass did not take would be
worse than saying so), and no PVXS matrix was run.

## Still missing

Named explicitly rather than implied.

1. **A reusable workflow cannot be skipped from inside a job.** The consumer
   still carries an `eligible` gating job so a non-tag push skips instead of
   failing. Closing this properly means `publish-baseline.yml` growing an
   explicit "this ref is not a release, so publish nothing and succeed"
   outcome, distinguishable from a lookup failure — the same
   `not_a_tag`/`lookup_failed` distinction `baseline_source` already draws.
   Not done: it changes the workflow's success semantics, which deserves its
   own review rather than riding along here.
2. **The canonical summary renderer is not delivered.** The consumer still
   writes its own "comparison not performed / incomplete / completed"
   step-summary block, and it still *infers* that a zero-analysis outcome
   means a missing baseline. `actions/aggregate` already publishes
   `status`/`coverage`/`channels`/`analyzed`/`expected`, so the inputs exist;
   what is missing is a renderer that reports the **structured reason** per
   unavailable target rather than a count. This is the one §3 deliverable
   this pass did not reach, and the consumer's own summary is the workaround
   it leaves in place.
3. **Check declaration is still stated per check.** `actions/aggregate` takes
   the declared set and `check-target` produces each report, but the consumer
   still spells four `check-target` steps and then four matching ids. An
   adapter that executes a *declared set* over already-captured inputs and
   carries the same identities into aggregation was scoped but not built; the
   binding work above (item 4) is the piece of it that landed. Any such
   adapter must preserve two components, two independently labelled baseline
   channels, event-specific applicability, selected depth and advisory
   policy — and must not turn two independent component comparisons into a
   bundle analysis with different scope to shorten the YAML.

## Evidence-layer leads — not investigated here

Recorded so they stay visible and correctly labelled. None was pursued; none
should be read as diagnosed.

* **Dependency include-root ownership and model-memory changes.** Landed on
  `main` after the reviewed PVXS pin (`80cf72abb`) — the relevant commits are
  `9bc42feb` (per-instance `__dict__` removal on the hot L2 types) and
  `e10c2846` (snapshot-encoding memory). Validating the extraction-provenance
  behaviour needs **freshly captured** inputs: reusing an old snapshot
  exercises the stored representation, not the new extraction path, so it
  cannot show whether new provenance works. Shared-tree sibling ownership is a
  separate case and should not be folded into the same test.
* **CastXML succeeding at C++11 where the full dump pipeline fails.** The
  consumer's `.ci-local/abicheck.yml` records a re-measurement on the CI
  toolchain (CastXML 0.6.20260105 with bundled Clang 21.1.8, GCC 13.3.0,
  libstdc++ 13) showing `-std=c++11` and `-std=c++14` failing for *two
  different* reasons and `-std=c++17` succeeding. That is a record of the
  symptom, not a reduction. What is needed before anyone blames the compiler
  or removes the `-std=c++17` workaround: the exact commands, the generated
  translation unit, include paths, macros, environment, and which stage
  failed — reduced to a reproducible upstream case.
* **`nm -D` showing a weak internal-lambda RTTI symbol disappearing.** That is
  binary evidence that a symbol went away. It is *not* evidence that the
  symbol belongs to the supported public contract, and the two must stay
  recorded separately. No broad suppression should be added on the strength
  of the former.

None of these is a blocker for the four slices above, and none was allowed to
expand this work into an AST/storage/detector redesign.
