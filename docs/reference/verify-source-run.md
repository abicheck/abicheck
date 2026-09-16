---
doc_type: reference
audience:
  - ci-owner
level: advanced
summarizes:
  - fork-pr-reporting
lifecycle: active
generated: false
---

# `verify-source-run` Action Reference

`actions/verify-source-run` is the selection step of a trusted
`workflow_run` publisher: it decides **which producer run this job is allowed
to report on**, resolves the pull request that run belongs to, and unpacks
its artifact as hostile input. It analyses nothing.

Task owner for the whole pattern:
[Reporting on fork pull requests](../use/fork-pr-reporting.md). Design
record:
[ADR-073](../contribute/adr/073-report-only-publication-and-the-trusted-reporter-boundary.md).

It exists as an Action because every project attempting the two-workflow
split needs the same checks, and they are the ones that are easy to get
subtly wrong and impossible to notice when wrong.

## Inputs

| Input | Default | Description |
|---|---|---|
| `source-run-id` | *(required)* | The producer run — `github.event.workflow_run.id`. |
| `expect-repository` | current repo | `owner/repo` the run must belong to. |
| `expect-workflow` | *(empty)* | Workflow path (`.github/workflows/abi.yml`) or name the run must be. **Strongly recommended.** |
| `expect-event` | `pull_request` | Event the run must have been triggered by. |
| `expect-run-attempt` | *(empty)* | Attempt the run must be. Empty means any. |
| `allowed-conclusions` | `success` | Comma-separated. Empty allows any — a real choice for a publisher that reports analysis failures. |
| `artifact-name` | *(required)* | Artifact to download from that run. |
| `tested-sha` | *(empty)* | The commit the producer actually analysed, if it recorded one. Verified against the PR head. Empty means the run's own head SHA. |
| `claimed-pr-number` | *(empty)* | A PR number the artifact states. Cross-checked only; a disagreement fails the step. |
| `destination` | `abicheck-source-artifact` | Directory to extract into. |
| `max-total-bytes` | `67108864` | Total uncompressed bytes the artifact may expand to. |
| `max-entry-bytes` | `33554432` | Uncompressed bytes any single entry may expand to. |
| `max-entries` | `2000` | Maximum entries. |
| `max-ratio` | `200` | Maximum uncompressed-to-compressed expansion ratio. |
| `github-token` | *(required)* | Token with `actions: read`. |
| `python-version` | `3.13` | For `actions/setup-python`. |
| `abicheck-version` | *(empty)* | PyPI version to install instead of this Action's own checkout. |

## Outputs

| Output | Description |
|---|---|
| `verified` | `true` when every check passed. |
| `pr-number` | The pull request the **API** associates with this run. |
| `pr-head-sha` | The pull request's own head commit. |
| `tested-sha` | The commit that was actually analysed, verified as the PR head or a merge of it. |
| `from-fork` | `true` when the PR head is in a different repository. |
| `artifact-path` | Directory the artifact was extracted into. |
| `refusal-code` | Machine-readable refusal reason; empty on success. |

## The three questions it answers

### Is this the run we think it is?

The run's repository, workflow identity, triggering event, id, attempt and
conclusion are each checked against what the publisher declared. Repository
and workflow are the two that make this a security boundary rather than a
sanity check: without them the publisher can be handed any run id on GitHub
and will fetch, unpack and publish whatever that run produced.

### Which pull request, and was the analysed commit really its?

The pull request is resolved through
`GET /repos/{repo}/commits/{sha}/pulls` — **not** from the run document's own
`pull_requests` array, which is empty for a fork's pull request (exactly the
case this design exists for), and **never** from the artifact. A PR number
the artifact supplies is only ever cross-checked; a disagreement is a
refusal, because an artifact that could choose the PR number could make a
fork's analysis post onto an unrelated pull request.

Ambiguity is a failure, not a choice: no associated PR, more than one open
one, or a PR whose base is a different repository each refuse.

The **PR head SHA** and the **commit that was actually built** are kept
distinct. A `pull_request` workflow checks out an ephemeral merge commit, so
these routinely differ, and conflating them is a real reporting error in both
directions: reporting the merge SHA as the head makes the comment unmatchable
against the commit list, and reporting the head when a merge was analysed
claims coverage of a tree nothing looked at. The association is verified —
the analysed commit must be the PR head, or have it as a parent.

### Is the archive safe to unpack?

The whole central directory is validated before a byte is written, so a bomb
is refused without any of it reaching the filesystem, and the caps are
re-enforced against the bytes actually read so a lying header buys nothing.

Refused: absolute paths, `..` traversal, symlinks and every other
non-regular entry — checked by the entry's recorded Unix type, so the rule
covers kinds the zip format could carry rather than a list of the ones worth
naming; and archives over the total, per-entry, entry-count or
compression-ratio caps. Extracted files are written without an executable
bit. Nothing in an artifact is executed, imported, `pickle`-d or
`yaml.load`-ed — `json.loads` is the only consumer.

## Refusal codes

| Code | Meaning |
|---|---|
| `run-unreadable` | The run document is not a JSON object. |
| `wrong-repository` | The run belongs to another repository. |
| `wrong-workflow` | The run is a different workflow. |
| `wrong-event` | The run was triggered by a different event. |
| `wrong-run` / `wrong-attempt` | The fetched run or attempt is not the declared one. |
| `wrong-conclusion` | The run's conclusion is not in the allowed set. |
| `no-pull-request` | No pull request in this repository is associated with the run's head commit. |
| `ambiguous-pull-request` | Several are; the publisher will not guess. |
| `pull-request-mismatch` | The artifact claims a different pull request than the API reports. |
| `no-tested-sha` | No analysed commit was recorded. |
| `head-sha-mismatch` | The run's head is not the pull request's head. |
| `unverified-tested-sha` | A non-head commit was analysed and no commit document was supplied to establish the relationship. |
| `unassociated-tested-sha` | The analysed commit is neither the PR head nor a merge of it. |
| `artifact-not-found` / `ambiguous-artifact` / `artifact-expired` | The named artifact is missing, duplicated, or gone. |
| `artifact-unreadable` | The download is not a readable zip, or a document is not readable JSON. |
| `artifact-bad-path` / `artifact-absolute-path` / `artifact-traversal` | An entry names no file, an absolute path, or one outside the destination. |
| `artifact-symlink` / `artifact-non-regular` | An entry is a symlink or another non-regular type. |
| `artifact-too-many-entries` / `artifact-entry-too-large` / `artifact-too-large` | A cap was exceeded. |
| `artifact-compression-bomb` | An entry, or the archive, expands beyond the ratio limit. |

## What this boundary does *not* establish

It does not make the report's contents trusted evidence. See the warning at
the top of [Reporting on fork pull requests](../use/fork-pr-reporting.md):
these checks are about recipient and execution safety, and the report's own
recorded evidence facts remain the only statement of assurance and
provenance.
