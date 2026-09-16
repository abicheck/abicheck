# ADR-073: Report-Only Publication and the Trusted-Reporter Boundary

**Date:** 2026-09-16
**Status:** Accepted — implemented. Adds `actions/report` and
`actions/verify-source-run`, backed by
`abicheck/frontends/action/report_publication.py`,
`abicheck/frontends/action/run_selection.py` and
`abicheck/frontends/action/cli.py`. Extends
[047](047-github-actions-integration-model.md)'s Actions integration model
with a *publication-only* primitive and constrains it with
[070](070-action-layer-does-not-encode-cli-semantics.md)'s rule that the
Action layer may not re-implement engine semantics. Builds on
[072](072-pr-comment-reporting-fidelity.md): the body this Action publishes
is that ADR's projection, unchanged. It changes no compatibility verdict, no
gate, and no exit code.

## Context

abicheck's root Action posts a PR comment as a *side effect of analysing*.
That works for a pull request from a branch in the same repository, and
cannot work for one from a fork:

- A `pull_request` run for a fork receives a read-only token and no secrets.
  It can build and compare; it cannot comment.
- Making that job privileged is the thing every hardening guide names as the
  mistake. A fork PR's job runs contributor-controlled build logic — a
  `configure` script, a `CMakeLists.txt`, a compiler plugin — so a token in
  that job is a token the contributor controls.
- `pull_request_target` moves the trust but not the risk: it runs in a
  privileged context against the base ref while still being *about* the
  fork's code, and every recipe that then checks out the fork's head has
  re-created the original problem with extra steps.

The supported shape is a two-workflow split: an unprivileged
`pull_request` job that analyses and uploads a canonical JSON report as an
artifact, and a separate `workflow_run` job on the default branch — trusted,
`actions: read` + `pull-requests: write` — that downloads that artifact and
publishes it.

abicheck had no Action for the second job. `python -m abicheck.cli_pr_comment`
already renders a body from a stored report without analysing anything, so
the *renderer* existed; what did not was a supported, hardened Action around
it. Every project attempting the split therefore hand-rolled the trusted
half, and the checks it needs are exactly the ones that are easy to get
subtly wrong and impossible to notice when wrong:

- verifying the source run is the run you think it is (repository, workflow,
  event, id, attempt, conclusion);
- resolving the pull request through the API rather than from a number the
  artifact supplied — an artifact that can choose the PR number can make a
  fork's analysis post onto an unrelated pull request;
- telling the PR head SHA apart from the merge commit that was actually
  built, and verifying they are related;
- treating the artifact as hostile input rather than as a tarball from a
  colleague.

A second, independent defect sat underneath all of this. `pr_comment.build_model`
dispatched on payload keys and had no branch for the `aggregate` fan-in
document, so an aggregate report fell through to the `compare` adapter, was
read for a `changes` array it does not carry, and rendered "No ABI changes"
for a run that may have been failing on every target in it. A publisher is
the consumer that makes that a user-visible lie, so it is fixed here and
recorded under [072](072-pr-comment-reporting-fidelity.md)'s own D1.

## Decision

**A report-only publication Action exists, it analyses nothing, and
verifying a report's origin does not make its contents trusted evidence.**

### D1 — Publication is a separate capability from analysis

`actions/report` takes an already-produced canonical JSON report — `compare`,
a directory/package release report, a `compare --no-baseline` audit, or an
`aggregate` document — and publishes it. It installs abicheck and nothing
else. It runs no comparison, no dump, no build query, no compiler, and no
project dependency install.

This is not a convention: it is the property that makes the Action safe to
run in a privileged job, so it is enforced mechanically
(`tests/test_action_report_contract.py`) both statically, over the Action's
executable surface with comments stripped, and behaviourally, by running the
Action against a `PATH` whose compilers, build tools and `gh` all record
their own invocation and fail.

### D2 — Rendering is separable from publication

Everything the Action *decides* lives in importable Python
(`frontends/action/report_publication.py`): render, bound, and choose between
create / update / clear / skip. The Action's shell marshals arguments and
makes the API call. `dry-run: true` renders and writes `body-path` without
contacting the API at all.

The consequence that matters is testability: every publication decision in
this ADR is covered with no credentials, no network and no runner. A
decision that can only be exercised by posting to a real pull request is a
decision nobody tests.

### D3 — Publication failure and compatibility outcome are different channels

A failed post fails the step with its own message and sets `posted=false`. It
is never downgraded to a warning and never reported as a clean compatibility
result. Symmetrically, a non-clean compatibility verdict never fails this
Action: it is a reporter, not a gate, and the gate already ran in the
producer job.

This is ADR-042's compatibility-versus-gate separation applied one layer out,
and it is the same failure mode
`report.unestablished_result_reads_as_success` names: a consumer that could
not establish a result must say so, never publish a passing one.

### D4 — Sticky identity carries a monotonic ordering guard

The comment carries a hidden `<!-- abicheck-report-identity: {...} -->`
marker holding the identity, the producer run id, the run attempt and the
head SHA. The identity is the sticky key — one comment per identity per pull
request, defaulting to the profile name so one PR can hold one comment per
profile.

The run id and attempt exist because producer runs finish out of order: a
re-run of an older commit, a slow matrix leg, a retried publisher. Without an
ordering record the last writer wins, which means the *oldest* result can be
the one a reviewer is left looking at. When an existing comment records a
strictly newer run, this one skips with `skipped-reason=stale` — including
when it has content and the newer one is clean.

"Cannot tell" is not staleness. An unorderable marker on either side falls
through and publishes: a publisher frozen permanently by one malformed
marker is a worse outcome than a redundant update.

### D5 — A result that no longer holds is cleared, not left standing

When the report now shows nothing and a prior comment for this identity
exists, that comment is updated to say the previously reported findings are
resolved. This applies under `on: changes` too — "the report no longer shows
this" is itself the news. When there is no prior comment and nothing to say,
the Action stays quiet.

The comment is rewritten rather than deleted so it keeps its ordering marker,
and with it the ability of a later, slower producer run to know it has been
superseded.

### D6 — Both destinations are bounded independently, and truncation is disclosed

A comment body is capped by GitHub at 65,536 characters; a job summary at
1 MiB per step, over which the summary is dropped entirely. The two are
bounded separately, in *bytes* with headroom — bounding bytes bounds
characters too, since a UTF-8 string never has more characters than bytes.

Cuts land on line boundaries (never mid-codepoint, never mid-row), any open
`<details>` is closed, and the truncation is stated in the body itself. A
shortened comment may never read as a complete one with fewer findings, and
the complete machine-readable report is left intact and linked.

### D7 — Run selection is a shared, tested boundary, not a per-project recipe

`actions/verify-source-run` owns the trusted publisher's selection step, and
its checks live in `frontends/action/run_selection.py`:

- the source run's repository, workflow identity, event, id, attempt and
  conclusion are each verified against what the publisher declared;
- the pull request is resolved through `GET /repos/{repo}/commits/{sha}/pulls`
  — not from the run document's own `pull_requests` array, which is empty for
  a fork's PR, and never from the artifact. An artifact-supplied number is
  only ever *cross-checked*; a disagreement is a refusal. Zero associations,
  several open ones, or one based on another repository are all visible
  failures rather than guesses;
- the PR head SHA and the commit actually analysed are kept distinct, and
  their association is verified (equality, or the head being a parent of the
  analysed merge commit). Reporting one as the other either makes the comment
  unmatchable against the commit list or claims the analysis covered a tree it
  never saw;
- artifacts are downloaded only from that exact run, re-established against
  each entry's own `workflow_run.id` rather than trusting the URL the listing
  came from.

Every refusal carries a stable code, so a workflow can branch on the reason
and so the negative-control tests assert *which* check fired. A test that
accepts any rejection passes equally against an implementation that rejects
everything, and each group therefore carries a positive control too.

### D8 — The artifact is hostile input

Total size, per-entry size, entry count and decompression ratio are capped;
absolute paths, `..` traversal, symlinks and every other non-regular entry
are refused — by the entry's recorded Unix *type*, not by an enumeration of
the kinds that happen to be nameable (a GitHub artifact is a zip, which
cannot express a hardlink at all); the whole central directory is validated
before a byte is written, and the caps are re-enforced against the bytes actually read so a
lying header buys nothing. Extracted files are written without an executable
bit. Nothing from an artifact is executed, imported, `pickle`-d or
`yaml.load`-ed — `json.loads` is the only consumer.

The same rules govern an `aggregate` document's member reports, which are
read relative to that document's own directory: an absolute path, a path
escaping the directory, a symlinked component, a non-regular file, an
oversized file or a non-object JSON value is refused *and surfaced as a
limitation in the rendered comment*, never silently dropped. A dropped target
is how a fan-in comment learns to lie.

### D9 — A trusted reporter does not make contributor content trusted evidence

This is the point most easily lost, so it is stated as a decision rather than
left to a docstring.

What the boundary above establishes is narrow: the document reaching the
publisher is the one that specific run produced, it is about the pull request
the API says it is, and unpacking it cannot write outside its own directory
or exhaust the runner. That is a statement about *recipient and execution
safety*, nothing more.

It establishes nothing about the report's *contents*. A fork's analysis job
chose what to put in that JSON, and running a trusted job around it does not
upgrade a claim inside it. Assurance and provenance continue to come from the
report's own recorded evidence facts — `confidence`, `evidence_tier`,
`coverage_warnings`, the contract-coverage ledger, the scope-completeness
block — which are rendered exactly as the producer recorded them and are
never improved by the identity of the renderer. A project that needs the
*findings* to be trustworthy must re-run the analysis in a trusted context;
this Action deliberately does not offer to.

### D10 — The aggregate document renders as itself

`pr_comment.build_model` dispatches on `aggregate_schema_version` ahead of
every other shape and folds the document through
`report/pr_comment_aggregate.py`: per-target rows from each member report's
own model, findings tagged with the target that reported them, and the
document's own outcome vocabulary (`compatibility`, `gate`, `coverage`,
`contract_coverage`, `analysis_assurance`, `scope_completeness`,
`disposition_audit`) read rather than recomputed. There is no second verdict
classifier and no verdict derived from the folded change set.

Unavailable targets, `not_comparable`/`operational_error` legs, missing
required targets, refused member reports and every axis shortfall render as
explicit limitations and post under `on: changes`. A target whose member
report could not be itemized contributes a *lower bound* derived from its own
verdict — the same conservative substitution `_release_lib_row` already makes
for a library whose comparison errored — so a row of zeros can never sit
beside a `BREAKING` verdict.

## Consequences

- A fork PR can be reported on without any privileged job ever running
  contributor-controlled build logic. The worked two-workflow example is in
  [`docs/use/fork-pr-reporting.md`](../../use/fork-pr-reporting.md).
- Two new composite Actions join `actions/`. Neither is a root CLI command
  and neither changes the CLI surface ([043](043-cli-pre-1.0-surface-reset.md) /
  [054](054-cli-project-integration-surface-consolidation.md) are untouched).
- `Finding` gains a `component` field and the renderer keys its API rollup on
  `(component, api)`. For every single-report mode the field is empty and the
  rendering is byte-identical to before.
- `pr_comment_render.py` shed its headline to
  `report/pr_comment_headline.py`, which is a real responsibility boundary:
  the headline decides what the comment *claims*, the renderer decides how
  much of the body *fits*.

## Alternatives considered

**Extend the root Action with a `mode: report`.** Rejected: the root Action's
inputs, validation and installed toolchain are all about analysing, and the
one property this Action must guarantee — that it analyses nothing — would
have become a claim about a code path inside a large script rather than a
property of the whole thing. It would also be unverifiable by the
static half of D1's check.

**Let the publisher take the PR number from the artifact.** Rejected in D7.
It is the single input that turns a reporting bug into a cross-pull-request
write.

**Delete the sticky comment on resolution.** Rejected in D5: deleting loses
the ordering marker, so a slower older run would then post a fresh stale
comment with nothing to tell it it had been superseded.

**Reconstruct per-target detail from the aggregate document's
`finding_matrix` instead of reading member reports.** Rejected: that block is
populated only for multi-profile targets, so the common case would silently
render without detail — and "silently render less than the document supports"
is the defect class this ADR is closing, not a mitigation for it.
