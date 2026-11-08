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
  - abicheck/frontends/action/main_snapshot_cache.py
lifecycle: active
generated: false
---

# `main-snapshot` / `fetch-main-snapshot` Actions Reference

Two composite Actions that give a pull request an exact default-branch
snapshot to measure **its own** changes against, instead of everything that
changed since the last release. Design and remaining work:
[PR delta against a main snapshot](../contribute/plans/pr-delta-vs-main-snapshot.md).
The main Action wires both together through `delta-baseline` (below); the
standalone Actions remain for custom workflows — pass `snapshot-path` as
`old-library` to an ordinary compare.

## `actions/main-snapshot` — on every push to the default branch

Dumps one snapshot (or adopts one you already produced) and uploads it as the
artifact `abicheck-main-<full sha>[-<profile>]` — or, with `storage: cache`,
saves it to the Actions cache (see [Cache storage](#cache-storage)). It refuses to run on
`pull_request`/`pull_request_target`: a pull request must never write what
other pull requests are measured against.

| Input | Default | Meaning |
|---|---|---|
| `library` | — | Library to `abicheck dump`. |
| `header` | — | Header file/directory; one per line. |
| `dump-args` | — | Extra `dump` arguments, one per line; a line such as `-I include` is split on whitespace. Use the same evidence inputs as the PR side. |
| `snapshot` | — | An already-produced snapshot to upload instead of dumping. |
| `profile` | — | Name suffix for multi-configuration projects. |
| `retention-days` | `14` | Artifact lifetime (`storage: artifact`). |
| `storage` | `artifact` | `artifact` or `cache`. |
| `branch` | repository default | `storage: cache`: the branch the entry may be saved from; set it on events whose payload names no default branch. |

Outputs: `artifact-name`, `snapshot-path`, `cache-key` (`storage: cache`).

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
| `workflow` | *(required with `artifact`)* | The snapshot workflow's file, e.g. `abi-main.yml`. Unused with `cache`. |
| `storage` | `artifact` | Must match the producer's: `artifact` or `cache`. |
| `mode` | `auto` | `auto`, `merge-commit`, `head`. |
| `branch` | repository default | Branch the snapshot must come from. |
| `profile` | — | As in `main-snapshot`. |
| `wait-timeout` | `600` | Seconds to wait while the base's snapshot run is still in progress. Ignored with `cache`. |
| `destination` | runner temp | Parent directory; the snapshot is extracted into a new subdirectory of it. |
| `github-token` | `github.token` | Needs `actions: read` (`artifact` only). |

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
| `base_not_snapshotted` | No snapshot run for the base (path filter, `[skip ci]`); with `cache`, no entry under the exact key (not saved yet, evicted, never saved, or a cache-service error — the cache cannot tell which). | Snapshot every push, or accept no delta; with `cache`, re-run once the snapshot job finished. |
| `base_expired` | The snapshot passed `retention-days`. | Push or *Update branch*; a re-run reuses the old merge commit. |
| `snapshot_missing` | The base's snapshot run succeeded, but no artifact is listed: expired, deleted or never uploaded (the API cannot tell which). | Push or *Update branch*; check the snapshot workflow's upload step. |
| `untrusted_source` | An artifact with the right name came from another workflow, event, branch or commit; with `cache`, the entry's `provenance.json` names another repository, ref, commit or key. | Investigate; it is never used. |
| `lookup_failed` | A GitHub API call or the download failed, or the artifact held no `snapshot.json`; with `cache`, the entry is malformed (unexpected or non-regular files, over the size limit, unreadable JSON). | Retry; this is not "absent". |

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

## Cache storage

`storage: cache` on **both** Actions moves the snapshot from a workflow
artifact to an Actions-cache entry under the key
`abicheck-main-snapshot-<full sha>[-<profile>]`. Use it when the
pull-request job cannot be given `actions: read` — typically fork pull
requests of a private repository. Default-branch caches are readable from
every pull request, forks included, with no token at all.

```yaml
# producer
      - uses: abicheck/abicheck/actions/main-snapshot@v1
        with: { library: build/libfoo.so, header: include/, storage: cache }
# pull-request job (no actions: read needed)
      - id: base
        uses: abicheck/abicheck/actions/fetch-main-snapshot@v1
        with: { storage: cache }
```

**What is verified.** A cache entry has no producing run the consumer could
inspect, so `resolved` means less than on the artifact path:

| Check | Artifact | Cache |
|---|---|---|
| Bound to the exact base commit | name `abicheck-main-<sha>` + the run's `head_sha` | the exact key only — `restore-keys` is never used, so no other commit's entry can stand in |
| Who wrote it | the uploading run is a `push`/`workflow_dispatch`/`schedule` run of `workflow`, in this repository, on `branch`, concluded `success` | **not verified.** GitHub's cache scoping limits it: a job reads its own ref's entries, its base branch's and the default branch's. `main-snapshot` saves only from `refs/heads/<branch>`, and `fetch-main-snapshot` refuses (`untrusted_source`, before restoring anything) unless it runs on a pull request **into `branch`** or on `refs/heads/<branch>` itself — a PR into another branch or a `merge_group` run would also read a scope other workflows write. Any workflow on `branch` can still write `branch`'s scope |
| Origin record | — (the run is the record) | `provenance.json`, written by `main-snapshot`, must name this repository, `refs/heads/<branch>`, the base SHA and the key. It is **self-reported**: anything able to write the scope can write it |
| Contents | ≤ 1 GiB, safe zip extraction, `snapshot.json` present | exactly `snapshot.json` + `provenance.json`, regular files only, ≤ 1 GiB each and in total, both JSON objects — checked **after** `actions/cache` has extracted the entry. That extraction (`tar -P`, relative to the workspace) is not limited by abicheck: an entry can place files outside the staging directory before any check runs, as with any `actions/cache` use |
| In progress / failed run | `base_pending` / `base_failed` | not observable — a miss is `base_not_snapshotted`; `wait-timeout` does not apply |
| Lifetime | `retention-days` | 7 days unused, and the repository's cache size limit (LRU) |
| Replacing a bad entry | a re-run uploads a newer one, which wins | impossible: a key is written once. Delete it (`gh cache delete <key>`) and re-run the snapshot job |
| Runner | any | the same OS family on both sides: the cache version covers the compression method, so an entry saved on Linux is a miss on Windows. `provenance.json` records `runner_os` |

What remains is two ways an entry can be forged. A workflow that runs **on
the pull request's own ref** can save an entry under the same key, and that
job's own lookup reads its own scope first, so a pull request can mislead
its own delta comment. And any workflow running on `branch` — not only the
snapshot workflow — can write `branch`'s scope, with a `provenance.json`
that says whatever it likes; that is everyone with push access to `branch`.
Neither reaches another pull request's comment through a third party's
scope, and neither reaches the release gate, which this lookup does not
feed. Prefer `artifact` whenever the pull-request token can have
`actions: read`.

A cache-service error and a version mismatch (another OS family, another
compression) both read as `base_not_snapshotted`: `actions/cache/restore`
reports them as a miss.

The restored entry is checked in a workspace directory
(`.abicheck-main-snapshot-cache`, which must not already exist) and removed
afterwards; the snapshot is copied into a new subdirectory of `destination`.
The main Action's `delta-baseline` uses artifact storage.

## `delta-baseline` in the main Action

```yaml
# pull-request workflow (permissions: contents: read, actions: read, pull-requests: write)
      - uses: actions/checkout@v5
        with: { fetch-depth: 2 }
      - run: ./build.sh
      - uses: abicheck/abicheck@v1
        with:
          abi-baseline: latest-release          # the gate: decides red/green
          new-library: build/libfoo.so
          new-header: include/
          delta-baseline: main                  # the comment: this PR's changes only
          delta-snapshot-workflow: abi-main.yml
```

The Action looks up the base snapshot as `fetch-main-snapshot` does, runs the
normal comparison (the gate) unchanged, and, when the snapshot resolved,
reruns the same comparison with the snapshot as the old side (old-side
`old-header`/`old-include`/`old-label`/`old-debug-info` are dropped: the
snapshot carries its own evidence). The comment renders that second report
under two notes: what it was measured against, and the gate's verdict. When
the snapshot is unavailable the comment shows the gate's report under a note
naming the outcome. The step's exit code, `verdict` and every other output
come from the gate in all cases; `delta-on-missing-base: fail` adds a separate
failing step. Outputs: `delta-outcome`, `delta-base-sha`.

It runs on `pull_request` and `merge_group` only (a `pull_request_target`
checkout is the base branch, not the PR), and for a single-library
comparison; a directory/package comparison keeps the gate's report with a
note. A failed lookup reads as `lookup_failed` and never stops the gate.

With `pr-comment-via: artifact` (fork pull requests) the delta report is the
one staged for the publisher, and the same two notes appear on its comment.
They travel as structured fields of the report's `analysis_context`
(`delta_outcome`, `delta_base_sha`, `gate_verdict`), never as text: the
trusted publisher validates their shape and writes the notes itself, with the
wording the direct path uses. A field that fails validation drops the notes
rather than showing fork-written text.
