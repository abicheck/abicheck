---
doc_type: how-to
audience:
  - ci-owner
  - library-maintainer
level: intermediate
canonical_for:
  - fork-pr-reporting
depends_on:
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
   downloads that artifact and publishes it — with `actions: read` and
   `pull-requests: write`, and nothing else.

Two Actions exist for the second half:
[`actions/verify-source-run`](../reference/verify-source-run.md) selects and
unpacks the producer run, and
[`actions/report`](../reference/report-action.md) renders and posts. Neither
analyses anything.

!!! warning "A trusted reporter does not make the report trusted evidence"

    Everything below is about the **recipient and execution boundary**: that
    the document reaching the publisher is the one that run produced, that it
    is about the pull request the API says it is, and that unpacking it
    cannot write outside its own directory or exhaust the runner.

    It says nothing about whether the report's *contents* are true. A fork's
    analysis job chose what to put in that JSON, and running a trusted job
    around it does not upgrade a single claim inside it. Assurance and
    provenance keep coming from the report's own recorded evidence facts —
    `confidence`, `evidence_tier`, `coverage_warnings`, the contract-coverage
    ledger, the scope-completeness block — which the comment renders exactly
    as the producer recorded them.

    If you need the *findings* to be trustworthy (to gate a merge on them,
    say), re-run the analysis in a trusted context. This pattern deliberately
    does not offer to do that for you.

## Why not `pull_request_target`?

It moves the trust without removing the risk. The job runs privileged
against the base ref while still being *about* the fork's code, so every
recipe that then checks out the fork's head has rebuilt the original problem
with extra steps. The split below never gives a privileged job the
contributor's code at all.

## 1. The analysis workflow (unprivileged)

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
          old: baseline/libexample.so
          new: build/libexample.so
          headers: include/
          format: json
          output: reports/compare.json

      # The publisher resolves the pull request through the API, so it does
      # not need one from here. What it *does* need is the commit that was
      # actually analysed, which on a `pull_request` run is the ephemeral
      # merge commit -- not the PR head.
      - name: Record what was analysed
        run: |
          printf '%s\n' "$GITHUB_SHA" > reports/tested-sha.txt

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

## 2. The publisher workflow (trusted)

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
  actions: read            # to read the producer run and its artifacts
  pull-requests: write     # to post the comment

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5      # this repository's code, not the PR's

      - id: verify
        uses: ./actions/verify-source-run
        with:
          source-run-id: ${{ github.event.workflow_run.id }}
          expect-repository: ${{ github.repository }}
          expect-workflow: .github/workflows/abi-analysis.yml
          expect-event: pull_request
          allowed-conclusions: success,failure
          artifact-name: abi-reports
          destination: incoming
          github-token: ${{ secrets.GITHUB_TOKEN }}

      # The analysed commit, as the producer recorded it. It is passed
      # through verify-source-run, which checks it really is this pull
      # request's head or a merge of it -- an unverified value is refused.
      - id: tested
        run: echo "sha=$(cat incoming/tested-sha.txt)" >> "$GITHUB_OUTPUT"

      - uses: ./actions/report
        with:
          report: incoming/compare.json
          repository: ${{ github.repository }}
          pr-number: ${{ steps.verify.outputs.pr-number }}
          sha: ${{ steps.verify.outputs.tested-sha }}
          profile: linux-gcc
          post-on: changes
          run-label: run #${{ github.event.workflow_run.run_number }}
          report-url: ${{ github.event.workflow_run.html_url }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

!!! note "Ordering: read the tested SHA before verifying it"

    The snippet above reads `incoming/tested-sha.txt` *after*
    `verify-source-run` has extracted the artifact, and then passes
    `steps.verify.outputs.tested-sha` — not the raw file — to the reporter.
    To have the SHA verified, feed it back in with the `tested-sha` input on
    a second `verify-source-run` step, or write it into the artifact under a
    name your own workflow reads and pass it as `tested-sha` on the first.
    The reporter displays whatever `sha` it is given; only
    `verify-source-run` can establish that the value belongs to this pull
    request.

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
