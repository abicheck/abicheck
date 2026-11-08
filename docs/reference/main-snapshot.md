---
doc_type: reference
audience:
  - ci-owner
  - library-maintainer
level: intermediate
depends_on:
  - actions/main-snapshot/action.yml
  - actions/fetch-main-snapshot/action.yml
  - abicheck/frontends/action/main_snapshot.py
lifecycle: active
generated: false
---

# `main-snapshot` / `fetch-main-snapshot` Actions Reference

Two composite Actions that give a pull request an exact default-branch
snapshot to measure **its own** changes against, instead of everything that
changed since the last release. Design and remaining work:
[PR delta against a main snapshot](../contribute/plans/pr-delta-vs-main-snapshot.md).
The comparison itself is still an ordinary compare — pass `snapshot-path` as
`old-library`. Wiring this into the main Action as one input
(`delta-baseline`) is planned, not shipped.

## `actions/main-snapshot` — on every push to the default branch

Dumps one snapshot (or adopts one you already produced) and uploads it as the
artifact `abicheck-main-<full sha>[-<profile>]`. It refuses to run on
`pull_request`/`pull_request_target`: a pull request must never write what
other pull requests are measured against.

| Input | Default | Meaning |
|---|---|---|
| `library` | — | Library to `abicheck dump`. |
| `header` | — | Header file/directory; one per line. |
| `dump-args` | — | Extra `dump` arguments, one per line; a line such as `-I include` is split on whitespace. Use the same evidence inputs as the PR side. |
| `snapshot` | — | An already-produced snapshot to upload instead of dumping. |
| `profile` | — | Name suffix for multi-configuration projects. |
| `retention-days` | `14` | Artifact lifetime. |

Outputs: `artifact-name`, `snapshot-path`.

## `actions/fetch-main-snapshot` — in the pull-request job

Chooses the base commit by an exact rule, then finds the snapshot uploaded
**for that commit** by the configured workflow on the default branch.

| Checkout | Base | Required `fetch-depth` |
|---|---|---|
| PR merge commit (the `pull_request` default) — mode `merge-commit` | first parent of HEAD | `2` |
| PR head — mode `head` | unique merge-base with the default branch | `0` |
| `merge_group` — mode `merge-group` (chosen by `auto`) | `merge_group.base_sha` | any |

| Input | Default | Meaning |
|---|---|---|
| `workflow` | *(required)* | The snapshot workflow's file, e.g. `abi-main.yml`. |
| `mode` | `auto` | `auto`, `merge-commit`, `head`. |
| `branch` | repository default | Branch the snapshot must come from. |
| `profile` | — | As in `main-snapshot`. |
| `wait-timeout` | `600` | Seconds to wait while the base's snapshot run is still in progress. |
| `destination` | runner temp | Parent directory; the snapshot is extracted into a new subdirectory of it. |
| `github-token` | `github.token` | Needs `actions: read`. |

Outputs: `outcome`, `base-sha`, `snapshot-path` (when resolved), `message`
(one line for a comment or summary).

A snapshot is accepted only if the run that uploaded it is a `push`,
`workflow_dispatch` or `schedule` run of `workflow` in this repository, on
`branch`, concluded `success`, and built exactly the base commit. When a
re-run uploaded it again, the newest upload is used and the older one is
logged as superseded. `main-snapshot` itself refuses to upload when the
working directory is a checkout of a different commit than the one it is
naming; a snapshot adopted from outside a checkout is the producing
workflow's responsibility.

### Outcomes

The step succeeds for every outcome; only `resolved` yields a snapshot. No
other outcome may be read as "no changes".

| `outcome` | Meaning | What to do |
|---|---|---|
| `resolved` | Snapshot found for the exact base. | Compare against `snapshot-path`. |
| `base_unknown` | The base is not in the local history, or HEAD is not a merge commit. | Fix `fetch-depth`, or the `mode`. |
| `base_ambiguous` | Head mode found several merge-bases. | Rebase onto or merge the default branch. |
| `base_pending` | The base's snapshot run was still running after `wait-timeout`. | Re-run when it finishes. |
| `base_failed` | The base's snapshot run did not succeed. | Fix the default-branch run. |
| `base_not_snapshotted` | No snapshot run for the base (path filter, `[skip ci]`). | Snapshot every push, or accept no delta. |
| `base_expired` | The snapshot passed `retention-days`. | Push or *Update branch*; a re-run reuses the old merge commit. |
| `snapshot_missing` | The base's snapshot run succeeded, but no artifact is listed: expired, deleted or never uploaded (the API cannot tell which). | Push or *Update branch*; check the snapshot workflow's upload step. |
| `untrusted_source` | An artifact with the right name came from another workflow, event, branch or commit. | Investigate; it is never used. |
| `lookup_failed` | A GitHub API call or the download failed, or the artifact held no `snapshot.json`. | Retry; this is not "absent". |

## Example

```yaml
# .github/workflows/abi-main.yml
on: { push: { branches: [main] } }
permissions: { contents: read }
jobs:
  snapshot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: ./build.sh
      - uses: abicheck/abicheck/actions/main-snapshot@v1
        with:
          library: build/libfoo.so
          header: include/
```

```yaml
# in the pull-request workflow (permissions: actions: read)
      - uses: actions/checkout@v5
        with: { fetch-depth: 2 }
      - run: ./build.sh
      - id: base
        uses: abicheck/abicheck/actions/fetch-main-snapshot@v1
        with:
          workflow: abi-main.yml
      - if: steps.base.outputs.outcome == 'resolved'
        uses: abicheck/abicheck@v1
        with:
          old-library: ${{ steps.base.outputs.snapshot-path }}
          new-library: build/libfoo.so
          new-header: include/
      - if: steps.base.outputs.outcome != 'resolved'
        env:
          MESSAGE: ${{ steps.base.outputs.message }}   # data, never shell text
        run: printf '%s\n' "$MESSAGE" >> "$GITHUB_STEP_SUMMARY"
```
