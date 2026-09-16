---
doc_type: reference
audience:
  - ci-owner
  - library-maintainer
level: intermediate
summarizes:
  - fork-pr-reporting
lifecycle: active
generated: false
---

# `report` Action Reference

`actions/report` publishes an **already-produced** abicheck JSON report to a
pull request, as a sticky comment and/or a job summary. It is the
publication half of the two-workflow split described in
[Reporting on fork pull requests](../use/fork-pr-reporting.md), and its
design record is
[ADR-073](../contribute/adr/073-report-only-publication-and-the-trusted-reporter-boundary.md).

> **It analyses nothing.** No comparison, no dump, no build query, no
> compiler, no project-dependency install. It installs abicheck and renders.
> That is what makes it safe to run in a trusted `workflow_run` job holding a
> token that can write to the pull request, and it is checked mechanically —
> `tests/test_action_report_contract.py` fails if either of this Action's
> scripts reaches for a toolchain, and runs the Action end to end against a
> `PATH` whose compilers and `gh` all record their own invocation and fail.

## What it does

1. Reads the report at `report`. Four shapes are recognised: a `compare`
   report, a directory/package release report, a `compare --no-baseline`
   audit report, and an `aggregate` fan-in document.
2. Renders the sticky comment body — the same projection the root Action
   posts ([ADR-072](../contribute/adr/072-pr-comment-reporting-fidelity.md)),
   unchanged.
3. Bounds the body and the job summary independently against GitHub's real
   limits, disclosing any truncation in the body itself.
4. Decides what to do: create, update in place, clear a now-resolved result,
   or skip.
5. Performs that one API call.

## Inputs

| Input | Default | Description |
|---|---|---|
| `report` | *(required)* | Path to a canonical abicheck JSON report. An `aggregate` document's per-target member reports are read from that document's own directory. |
| `detail` | `standard` | `summary`, `standard` or `full`. |
| `on` | `changes` | `always`, `changes` or `never`. **Must be quoted in the caller's YAML** (`"on": changes`) — an unquoted `on` key is a YAML 1.1 boolean. Use `post-on` to avoid the quoting. |
| `post-on` | *(empty)* | Alias for `on` that needs no quoting. Wins when both are set. |
| `sha` | *(empty)* | The analysed head/build SHA to display. For a `pull_request` run this is the ephemeral merge commit, not the PR head — pass what was actually analysed. |
| `run-label` | *(empty)* | Footer label, e.g. `run #128`. |
| `report-url` | *(empty)* | Linked as "View workflow run"; also used when the body is shortened. |
| `report-artifact-url` | *(empty)* | Linked as "Download full report". Pass it only for an upload that succeeded. |
| `path-prefix` | *(empty)* | Checkout root to strip from rendered source locations. |
| `gate-api-break` | `false` | Render API/source breaks under "Breaking", mirroring a producer that gated on them. Presentation only. |
| `gate-breaking` | `true` | Whether the producer gated on ABI breaks. Affects only the analysis-incomplete section's wording. |
| `profile` | `default` | Profile this report covers; the default comment identity is derived from it. |
| `comment-identity` | *(empty)* | Explicit sticky identity. Defaults to `abicheck:<profile>`. |
| `repository` | current repo | `owner/repo` to publish into. |
| `pr-number` | *(empty)* | The pull request. Required unless `dry-run`. Resolve it through the API — see [`verify-source-run`](verify-source-run.md) — never from a contributor-produced artifact. |
| `github-token` | *(empty)* | Token with `pull-requests: write`. Required unless `dry-run`. |
| `job-summary` | `true` | Also write the body to this job's summary. |
| `max-comment-bytes` | `60000` | Byte budget for the comment body. |
| `max-summary-bytes` | `900000` | Byte budget for the job summary. |
| `dry-run` | `false` | Render and write `body-path` without contacting the API. |
| `python-version` | `3.13` | For `actions/setup-python`. |
| `abicheck-version` | *(empty)* | PyPI version to install instead of this Action's own checkout. |

## Outputs

| Output | Description |
|---|---|
| `posted` | `true` when a comment was created, updated or cleared. |
| `comment-url` | URL of the comment, when one was written. |
| `body-path` | Path to the rendered body on disk. Written even under `dry-run`. |
| `body-bytes` | Size of the rendered body in UTF-8 bytes. |
| `skipped-reason` | `never`, `no-changes`, `stale`, `dry-run`, or empty when something was published. |

## Sticky comments and the ordering guard

The body carries a hidden marker:

```html
<!-- abicheck-report-identity: {"head_sha": "…", "identity": "abicheck:linux-gcc", "run_attempt": 1, "run_id": "1234"} -->
```

`identity` is the sticky key: **one comment per identity per pull request.**
Because it defaults to `abicheck:<profile>`, a matrix publishing several
profiles to one PR gets one comment each rather than several fighting over
one.

`run_id` and `run_attempt` are the ordering guard. Producer runs finish out
of order — a re-run of an older commit, a slow matrix leg, a retried
publisher — and without an ordering record the last writer wins, which means
the *oldest* result can be the one a reviewer is left looking at. When the
existing comment records a strictly newer run, this Action publishes nothing
and sets `skipped-reason=stale`, even if it has findings and the newer
comment is clean.

An existing marker that cannot be ordered (a malformed payload, no run id)
is *not* treated as newer: the Action publishes. A publisher frozen forever
by one broken marker is worse than one redundant update.

## Resolution and clearing

When the report now shows nothing and a prior comment for this identity
exists, that comment is **updated** to state the previously reported findings
are resolved — under `on: changes` too, because "the report no longer shows
this" is itself the news. It is rewritten rather than deleted so it keeps its
ordering marker, which a delete would discard along with the ability of a
slower older run to know it has been superseded.

When there is no prior comment and nothing to say, the Action stays quiet.

## Size limits

| Destination | GitHub's limit | This Action's default budget |
|---|---|---|
| Pull-request comment | 65,536 characters (262,144 bytes in storage) | 60,000 bytes |
| Job summary | 1 MiB per step, over which the summary is dropped entirely | 900,000 bytes |

Both are measured in UTF-8 bytes, which also bounds characters (a UTF-8
string never has more characters than bytes), and the two are bounded
independently — folding them into one budget would either waste the
summary's room or overrun the comment's.

A body over budget is cut on a line boundary (never mid-row, never
mid-codepoint), any open `<details>` is closed, and the cut is stated in the
body. **Truncation is never presented as an empty or smaller finding set**;
the exact counts stay in the header and the complete machine-readable report
stays intact and linked.

## Failure semantics

Two rules, and they are the reason this Action exists rather than a
`continue-on-error` step:

- **A publication failure fails the step**, with a message naming what
  failed, and sets `posted=false`. It is never downgraded to a warning and
  never reported as a clean compatibility result.
- **A non-clean compatibility verdict never fails this Action.** It is a
  reporter, not a gate. The gate already ran in the producer job, and its
  exit code is that job's to publish.

## Untrusted input

Report contents, symbol names and PR text are treated as untrusted
throughout: nothing from the report becomes a shell word, an argument or part
of a URL. The body is written to a file by Python, the API request document
is JSON that Python serialized, and the shell passes file paths. Rendered
Markdown is escaped by the renderer's own cell escaping.

Validated before use: `repository` must match `owner/repo`, and `pr-number`
must be a positive integer — both are checked before either reaches an API
path.

## Example

```yaml
- uses: abicheck/abicheck/actions/report@v1
  with:
    report: abicheck-source-artifact/aggregate.json
    repository: ${{ github.repository }}
    pr-number: ${{ steps.verify.outputs.pr-number }}
    sha: ${{ steps.verify.outputs.tested-sha }}
    profile: linux-gcc
    detail: standard
    post-on: changes
    report-url: ${{ github.event.workflow_run.html_url }}
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

See [Reporting on fork pull requests](../use/fork-pr-reporting.md) for the
complete two-workflow example.
