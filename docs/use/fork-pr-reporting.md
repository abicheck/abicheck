---
doc_type: how-to
audience:
  - ci-owner
  - library-maintainer
level: intermediate
canonical_for:
  - fork-pr-reporting
depends_on:
  - .github/workflows/report-pr.yml
  - action/pr-report-delivery.sh
  - actions/report
  - actions/verify-source-run
  - abicheck/frontends/action/report_publication.py
  - abicheck/frontends/action/run_selection.py
lifecycle: active
generated: false
---

# Reporting on fork pull requests

A pull request from a fork gets a read-only token and no secrets. The job
that builds and compares the library therefore **cannot** post a comment —
and it must not be given a token that could, because that job runs the
contributor's own build logic.

The supported shape is two workflows:

1. an **unprivileged** `pull_request` workflow that analyses and uploads a
   canonical JSON report as an artifact, and
2. a **trusted** `workflow_run` workflow on the default branch that
   verifies that artifact and publishes it — with `actions: read`,
   `pull-requests: write` and `contents: read`, and nothing else.

Both halves are packaged: one input on the main Action produces the
artifact, and one reusable workflow publishes it. You write no glue.

## Quick start: two files

**1. Analysis workflow** — your existing abicheck job, plus one input:

```yaml
# .github/workflows/abi-analysis.yml
name: ABI analysis
on:
  pull_request:

permissions:
  contents: read

jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      # ... build the library ...
      - uses: abicheck/abicheck@v1
        with:
          old-library: baseline/libexample.so
          new-library: build/libexample.so
          header: include/
          pr-comment-via: auto     # fork PRs: upload for the publisher below
```

**2. Publisher workflow** — new, about eight lines:

```yaml
# .github/workflows/abi-report.yml
name: ABI report
on:
  workflow_run:
    workflows: ["ABI analysis"]        # the name: of the workflow above
    types: [completed]
permissions:
  actions: read
  contents: read
  pull-requests: write
jobs:
  report:
    uses: abicheck/abicheck/.github/workflows/report-pr.yml@v1
```

That is the whole setup. A same-repository pull request is commented on
directly by the analysis job, exactly as before; a fork's pull request is
commented on by the publisher.

!!! note "Why the publisher is a separate file"

    GitHub only fires `workflow_run` for a workflow file that lives on the
    default branch of **your** repository, so the trigger cannot come from
    a reusable workflow. `report-pr.yml` is the job; your eight-line file
    is the trigger and the permission grant. Commit it to the default
    branch — a `workflow_run` workflow added only on a pull request branch
    does not run.

### What `pr-comment-via` does

| Value | Same-repository PR | Fork (or Dependabot) PR |
|---|---|---|
| `direct` (default) | posts with `github-token` | cannot post: the token is read-only |
| `auto` | posts with `github-token` | uploads the report artifact |
| `artifact` | uploads the report artifact | uploads the report artifact |

On the artifact path the Action makes **no** GitHub API call and needs no
permission beyond the default: it uploads `abicheck-pr-report` (or
`abicheck-pr-report-<pr-comment-key>`) holding the canonical report wrapped
in a one-target `aggregate.json` whose `analysis_context` records the commit
this job analysed (`github.sha`, the ephemeral merge commit). The upload
also runs when a gated break fails the step, so the run a reviewer most
needs a comment for still gets one. Never give the analysis job a write
token to make forks work — that is exactly what this replaces.

`auto` also routes a Dependabot pull request (`github.actor` is
`dependabot[bot]`), whose `pull_request` token is read-only even though the
branch lives in your repository. This routing has not been exercised
against a live Dependabot run; if your publisher cannot post there, use
`pr-comment-via: direct` with your repository's Dependabot token settings
instead.

### No double comments

Each run takes **one** path: it either posts or uploads, never both. The
publisher treats a verified run with no artifact as "nothing to publish"
(that run posted directly, was configured not to comment, or failed before
producing a report — its own red check says which) and skips with a
notice. So wiring both files never produces two comments for one run, and
with `auto` every PR gets exactly one sticky comment. If you switch a PR's
path mid-flight (say from `direct` to `artifact`), the old direct comment
is not updated by the publisher; it keeps its last content.

### Matrix jobs

Give each leg its own `pr-comment-key` on the producer, and call the
publisher once per key:

```yaml
jobs:
  report:
    strategy:
      matrix:
        key: [linux-gcc, linux-clang]
    uses: abicheck/abicheck/.github/workflows/report-pr.yml@v1
    with:
      comment-key: ${{ matrix.key }}
```

Each key has its own artifact and its own sticky comment.

### Publisher inputs

| Input | Default | Meaning |
|---|---|---|
| `comment-key` | `''` | The producer's `pr-comment-key`: selects `abicheck-pr-report-<key>` and the sticky identity `abicheck:<key>` |
| `artifact-name` | derived | Override the artifact name |
| `report-from` | `aggregate.json` | Report inside the artifact to publish; any canonical abicheck JSON report `actions/report` accepts |
| `provenance-from` | `report-from` | Document the analysed commit is read from |
| `require-provenance` | `true` | Refuse a report that records no analysed commit |
| `expect-workflow` | the triggering run's | Producer workflow path or name; set it when `workflows:` lists several |
| `allowed-conclusions` | `success,failure` | Producer conclusions to publish (a gated break fails the producer) |
| `require-artifact` | `false` | Fail, instead of skip, when the producer uploaded nothing |
| `detail` | `standard` | `summary`, `standard` or `full` |
| `post-on` | `changes` | `changes`, `always` or `never` — the producer's `pr-comment-on` does not travel |
| `gate-api-break` / `gate-breaking` | `false` / `true` | Mirror the producer's `fail-on-api-break` / `fail-on-breaking` in the comment wording |

Outputs: `posted`, `comment-url`, `skipped-reason` (`no-artifact`, or
`actions/report`'s `never`/`no-changes`/`stale`). The job only runs for
producer runs triggered by `pull_request` and skips cancelled ones, so a
producer workflow that also runs on `push` needs no extra filter.

## Why not `pull_request_target`?

It moves the trust without removing the risk. The job runs privileged
against the base ref while still being *about* the fork's code, so every
recipe that then checks out the fork's head has rebuilt the original problem
with extra steps. The split below never gives a privileged job the
contributor's code at all.

## Advanced: the building blocks

The two files above are assembled from public pieces you can wire yourself
when the packaged path does not fit — a custom aggregate, several reports
in one artifact, or a publisher that does more than comment. Everything
below is what `pr-comment-via` and `report-pr.yml` do for you.

### 1. The analysis workflow (unprivileged)

Runs on `pull_request`, with default (read-only) permissions. It does the
real work and uploads the result. It never needs a token.

```yaml
name: ABI analysis
on:
  pull_request:

permissions:
  contents: read

jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      # Build and compare however your project does it. The only requirement
      # this pattern places on you is that the job produce a canonical
      # abicheck JSON report.
      - uses: abicheck/abicheck@v1
        continue-on-error: true          # the gate is reported, not enforced here
        with:
          old-library: baseline/libexample.so
          new-library: build/libexample.so
          header: include/
          format: json
          output-file: reports/compare.json

      - uses: actions/upload-artifact@v4
        with:
          name: abi-reports
          path: reports/
```

A multi-target matrix works the same way: each leg writes its own report,
a trailing job runs `abicheck aggregate reports/ -o json=reports/aggregate.json`,
and the publisher is pointed at the aggregate document. Its per-target member
reports must sit **in the same directory**, since that is where the renderer
reads them from.

#### Recording the commit that was actually analysed

The publisher resolves the pull request through the API, so it needs nothing
from here to do that. There is exactly one fact it **cannot** get from the
API: on a `pull_request` run the analysis job checks out the *ephemeral merge
commit* (`$GITHUB_SHA`), not the pull request's head, and no endpoint names
that commit. Only this job knows it.

Record it in the aggregate document, where it travels beside the report it
describes:

```yaml
      - uses: abicheck/abicheck/actions/aggregate@v1
        with:
          reports-dir: reports
          manifest-path: expected-checks.json
          checks: ${{ steps.declare.outputs.checks }}
          record-analysis-context: 'true'   # `tested-sha` defaults to github.sha
```

Everything it records is a **claim** by this unprivileged, possibly-fork job.
Each field is shape-checked when written, here; the publisher verifies the
recorded commit against the API before displaying it. Do not write a sidecar
file for this — the publisher would then have to parse contributor-produced
content in the job holding the write token, which is the boundary this whole
pattern exists to keep.

### 2. The publisher workflow (trusted)

Runs on `workflow_run`, from the default branch, with exactly the two
permissions it needs. It never checks out the contributor's code and never
builds anything.

```yaml
name: ABI report
on:
  workflow_run:
    workflows: ["ABI analysis"]
    types: [completed]

permissions:
  contents: read           # to read commit documents when verifying the analysed SHA
  actions: read            # to read the producer run and its artifacts
  pull-requests: write     # to post the comment

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - id: verify
        uses: abicheck/abicheck/actions/verify-source-run@v1
        with:
          source-run-id: ${{ github.event.workflow_run.id }}
          expect-repository: ${{ github.repository }}
          expect-workflow: .github/workflows/abi-analysis.yml
          expect-event: pull_request
          allowed-conclusions: success,failure
          artifact-name: abi-reports
          destination: incoming
          github-token: ${{ secrets.GITHUB_TOKEN }}

      - uses: abicheck/abicheck/actions/report@v1
        with:
          report: incoming/compare.json
          repository: ${{ github.repository }}
          pr-number: ${{ steps.verify.outputs.pr-number }}
          sha: ${{ steps.verify.outputs.tested-sha }}
          # The run that PRODUCED the report, not this publisher, so the
          # sticky comment's ordering guard compares the right thing.
          source-run-id: ${{ github.event.workflow_run.id }}
          source-run-attempt: ${{ github.event.workflow_run.run_attempt }}
          profile: linux-gcc
          post-on: changes
          # Quoted: an unquoted `#` starts a YAML comment, which would
          # silently truncate this label to "run".
          run-label: "run #${{ github.event.workflow_run.run_number }}"
          report-url: ${{ github.event.workflow_run.html_url }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

There is no `actions/checkout` step: the Actions are referenced by their
published path, so the publisher never needs a copy of this repository — and
never checks out the contributor's code, which is the whole point.

#### Reporting a merge commit the producer analysed

With `record-analysis-context` set on the producer's `aggregate` step (above),
the publisher reads that commit out of the artifact it has **already**
extracted and verifies it in the same pass:

```yaml
      - id: verify
        uses: abicheck/abicheck/actions/verify-source-run@v1
        with:
          source-run-id: ${{ github.event.workflow_run.id }}
          expect-repository: ${{ github.repository }}
          expect-workflow: .github/workflows/abi-analysis.yml
          artifact-name: abi-reports
          destination: incoming
          provenance-from: aggregate.json   # read the analysed commit from here
          report-from: aggregate.json       # and return its path with the identity
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

Then pass `${{ steps.verify.outputs.report-path }}` as the reporter's
`report` and `${{ steps.verify.outputs.tested-sha }}` as its `sha`. The two
come back together, so the document rendered is the one whose context was
checked.

`tested-sha-source` says which answer you got: `analysis-context` when the
producer recorded the commit and the API confirmed it belongs to this pull
request, `run-head` when it did not. With `require-provenance` left at its
default (`true`) the second case **refuses** rather than quietly displaying
the pull request head, which would claim analysis of a tree nothing looked
at.

Without `provenance-from`, `verify-source-run`'s `tested-sha` is the producer
run's head commit — the pull request's head, which that step has verified.
That is the right answer when your analysis job builds the head itself.

> **Do not** read the commit out of the artifact in this job and pass it back
> as `tested-sha`. That parses contributor-produced content in the privileged
> job, and verifying the result needs a *second* run of this Action, which
> downloads the same artifact again with no guarantee the second copy is the
> first. `provenance-from` exists so neither is necessary.

## What the publisher checks

`verify-source-run` refuses, with a distinct `refusal-code` for each:

| Check | Refusal code | Why it matters |
|---|---|---|
| The run belongs to this repository | `wrong-repository` | Otherwise the publisher can be pointed at any run on GitHub. |
| The run is the expected workflow | `wrong-workflow` | A different workflow in the same repository may have run with different trust. |
| The run's event, id, attempt | `wrong-event`, `wrong-run`, `wrong-attempt` | The publisher reports on the run it was triggered for, not another. |
| The run's conclusion is allowed | `wrong-conclusion` | Configurable: allow `failure` if you want analysis failures reported. |
| Exactly one pull request is associated | `no-pull-request`, `ambiguous-pull-request` | Posting somewhere is worse than posting nowhere. |
| An artifact-supplied PR number agrees with the API | `pull-request-mismatch` | An artifact that could choose the PR number could post a fork's analysis onto an unrelated PR. |
| The analysed commit is the PR head or a merge of it | `unverified-tested-sha`, `unassociated-tested-sha` | A displayed SHA that does not belong to this PR makes the comment unmatchable — or claims coverage of a tree nothing analysed. |
| The artifact belongs to that exact run | `artifact-not-found`, `ambiguous-artifact`, `artifact-expired` | The listing endpoint is chosen by the shell; the ownership is re-established per entry. |
| The archive is not hostile | `artifact-traversal`, `artifact-symlink`, `artifact-non-regular`, `artifact-too-large`, `artifact-entry-too-large`, `artifact-too-many-entries`, `artifact-compression-bomb` | Zip-slip, symlink redirection, decompression bombs. |

Nothing from the artifact is executed, imported, `pickle`-d or
`yaml.load`-ed; `json.loads` is its only consumer, and extracted files are
written without an executable bit.

## One comment per profile, and no duplicates

`actions/report` keeps a sticky comment keyed by `comment-identity`, which
defaults to `abicheck:<profile>`. A matrix publishing several profiles to one
pull request gets one comment each — set `profile` per leg — and a re-run
updates the existing comment rather than posting beside it.

The marker also records the producer run id and attempt, so a
**late-finishing older run never overwrites a newer result**: it publishes
nothing and reports `skipped-reason=stale`. This happens routinely when
someone re-runs an old commit's workflow.

When a previously reported break is fixed, the comment is **updated to say
so** rather than left standing or silently deleted — including under
`post-on: changes`.

## Failure semantics

- A failed post **fails the publisher step**, with a message naming what
  failed, and sets `posted=false`. It is never downgraded to a warning and
  never reported as a clean compatibility result.
- A non-clean compatibility verdict **never** fails the publisher. It is a
  reporter, not a gate — the gate belongs in the analysis workflow, which is
  where the exit code was produced.

If you want the pull request to be *blocked* on the ABI result, gate it in
the analysis workflow (drop the `continue-on-error: true` above) and use a
required status check. Do not gate on the publisher: a reporting failure and
an ABI break are different problems and must not share a signal.

## Trying it without posting

`actions/report` accepts `dry-run: true`, which renders the body and writes
`body-path` without contacting the API and without needing a token or a pull
request. It is the fastest way to see exactly what a given report will
produce.

```yaml
- uses: ./actions/report
  with:
    report: reports/compare.json
    dry-run: true
    post-on: always
```
