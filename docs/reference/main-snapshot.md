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
  - abicheck/frontends/action/delta_base_dump.py
  - action/build-delta-base.sh
  - abicheck/frontends/action/main_snapshot_cache.py
  - action.yml
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

The artifact holds `snapshot.json` and `operand.json`, which records the
`library` path as given (and `profile`) so a pull request whose library moved
can still be paired with it (§ Operand identity below). The file name and the
SONAME / Mach-O install name are read from the snapshot itself, so an
artifact without `operand.json` still pairs by name.

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
| Contents | ≤ 1 GiB, safe zip extraction, `snapshot.json` present | `snapshot.json` + `provenance.json` (both JSON objects) and optionally `operand.json`, nothing else, regular files only, ≤ 1 GiB each and in total; `operand.json` is copied beside the snapshot and read by the operand pairing, as from an artifact — checked **after** `actions/cache` has extracted the entry. That extraction (`tar -P`, relative to the workspace) is not limited by abicheck: an entry can place files outside the staging directory before any check runs, as with any `actions/cache` use |
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
The main Action's `delta-baseline` reads either backend: see
[`delta-storage: cache`](#delta-storage-cache-in-the-main-action).

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
failing step. Outputs: `delta-outcome` (the lookup's outcome),
`delta-base-sha`, `delta-operand-match`, and `delta-comment-outcome` (what
the comment shows: the lookup outcome, or one of the outcomes below).

### `delta-storage: cache` in the main Action

`delta-storage` names the backend `main-snapshot` saved to, and must match
its `storage`. With `cache` the main Action runs `fetch-main-snapshot`'s own
cache path: the same base selection, the same scope refusal, an
`actions/cache/restore` of the **exact** key (never `restore-keys`), and the
same entry check — everything in the table above, including its limits.

```yaml
# producer (push to the default branch)
      - uses: abicheck/abicheck/actions/main-snapshot@v1
        with: { library: build/libfoo.so, header: include/, storage: cache }
# pull-request workflow (permissions: contents: read, pull-requests: write -- no actions: read)
      - uses: abicheck/abicheck@v1
        with:
          abi-baseline: latest-release
          new-library: build/libfoo.so
          new-header: include/
          delta-baseline: main
          delta-storage: cache                  # no delta-snapshot-workflow
```

- `delta-snapshot-workflow` is not needed (a cache entry names no producing
  run to verify; setting it only warns), nor is `actions: read`.
  `delta-wait-timeout` does not apply.
- A pull request into another branch and a `merge_group` run are refused
  before anything is restored (`untrusted_source`), as above;
  `pull_request_target` runs no delta lookup at all.
- Every unavailable entry is a named outcome — a miss is
  `base_not_snapshotted`, an entry for the wrong commit, repository or ref is
  `untrusted_source`, a malformed entry or a restore that did not complete is
  `lookup_failed` — and each selected a base commit, so
  `delta-on-missing-base: build` builds it and `fail` fails the separate
  step. `operand.json` travels with the entry, so the
  [operand pairing](#operand-identity-is-the-snapshot-this-prs-library) is
  the same as from an artifact.
- The gate is unaffected by every outcome: a failed restore or check leaves
  `lookup_failed` and the comparison runs. One limit is the cache's own
  (see *Contents* above): `actions/cache` extracts the entry into the
  workspace **before** the comparison and before any check, and that
  extraction is not confined to the staging directory, so an entry forged
  by someone able to write a readable scope could overwrite the gate's own
  inputs. Those writers — workflows on this pull request's ref, and anyone
  able to push to `branch` — can already change this pull request's build
  or workflow, so this grants them nothing new; with `artifact` the
  snapshot is extracted under `runner.temp` and the limit does not apply. `delta-outcome` and the reports'
  `delta_baseline` block carry the cache lookup's outcome; the backend is
  not recorded in the report.

### Operand identity: is the snapshot this PR's library?

Before the delta comparison runs, the Action checks that the snapshot
describes the same library as `new-library`. A snapshot of a different
library is **never compared**: every one of its symbols would read as
removed. The rule (`abicheck/workflows/delta_operand.py`):

| Snapshot vs. PR | Result | `delta-operand-match` |
|---|---|---|
| Same SONAME / install name (any path or file name: a different build directory, a renamed output) | delta runs | `loader_name` |
| SONAME / install name differs only by version (`libfoo.so.1` → `libfoo.so.2`, `libfoo.1.dylib` → `libfoo.2.dylib`) | delta runs; it reports the bump as a finding, and the comment says the two were paired by version | `loader_name_version` |
| No loader name on a side (PE, or a snapshot without one): same path or file name | delta runs | `file_name` |
| …file names differ only by version | delta runs, with a note | `file_name_version` |
| Unrelated SONAMEs, same file name or path | `operand_ambiguous` — not compared | — |
| Only one side has a SONAME, and it names a different library than the other side's file | `operand_ambiguous` — not compared | — |
| Nothing links them | `operand_unmatched` — not compared | — |
| Either side could not be read | `operand_unreadable` — not compared | — |
| `delta-operand-map` is malformed | `operand_map_invalid` — not compared; the job log names the line | — |

When the automatic rule cannot see the pairing (a PE DLL renamed, a SONAME
renamed outright), declare it with `delta-operand-map`, one
`SNAPSHOT=PR` pair per line, each side a path, file name or SONAME:

```yaml
          delta-operand-map: |
            foo.dll=foo-core.dll
```

A map, once given, is decisive: if no entry names the snapshot's library, or
the entry names a different PR library, the outcome is `operand_unmatched`
rather than an automatic match; entries declaring different targets for the
same snapshot library are `operand_ambiguous` (line order never decides).
Every refusal's reason is also written to the job log as a warning. Every refusal keeps the gate's report in the
comment under a "Delta unavailable" note; with `delta-on-missing-base: fail`
an `operand_*` refusal fails the separate "Require the delta base snapshot"
step like a missing snapshot does. A base built by
`delta-on-missing-base: build` goes through the same check.

It runs on `pull_request` and `merge_group` only (a `pull_request_target`
checkout is the base branch, not the PR), and for a single-library
comparison; a directory/package comparison keeps the gate's report with a
note. A failed lookup reads as `lookup_failed` and never stops the gate.

With `pr-comment-via: artifact` (fork pull requests) the delta report is the
one staged for the publisher, and the same two notes appear on its comment.
They travel as structured fields of the report's `analysis_context`
(`delta_outcome`, `delta_base_sha`, `gate_verdict`, `delta_operand_match`),
never as text: the
trusted publisher validates their shape and writes the notes itself, with the
wording the direct path uses. A field that fails validation drops the notes
rather than showing fork-written text.

### `delta-on-missing-base: build`

When the lookup selected an exact base commit but no stored snapshot is
usable for it (`base_pending`, `base_failed`, `base_not_snapshotted`,
`base_expired`, `snapshot_missing`, `untrusted_source`, `lookup_failed`), the
Action can build that commit in the PR job instead of explaining the gap.
With `storage: cache` the same applies: a miss (`base_not_snapshotted`), a
refused entry or scope (`untrusted_source`) and a malformed entry
(`lookup_failed`) concern the cache entry, not which commit is the base, and
the build never reads the refused entry:

```yaml
      - uses: actions/checkout@v5
        with: { fetch-depth: 2, persist-credentials: false }
      - run: ./build.sh
      - uses: abicheck/abicheck@v1
        with:
          abi-baseline: latest-release
          new-library: build/libfoo.so
          new-header: include/
          delta-baseline: main
          delta-snapshot-workflow: abi-main.yml
          delta-on-missing-base: build
          delta-base-build-command: ./build.sh
```

1. **After** the normal comparison (the gate) has run, and only when a
   comment is rendered, the Action checks the base commit out into a git
   worktree under `runner.temp` (the PR checkout and its build outputs are
   untouched; a commit missing from a shallow clone is fetched by SHA; the
   repository is found from `new-library`'s directory, so a checkout under
   `path:` works) and runs `delta-base-build-command` with
   `bash -e -o pipefail` from that worktree's root, with `ABICHECK_BASE_SHA`
   set.
2. The comparison step dumps the result with this run's **new-side**
   evidence inputs (`new-library`, `header`/`new-header`,
   `include`/`new-include`, `new-debug-info`, `sources`, `build-info`,
   `depth`, `build-config`, the compile context and `compile.*`/`build.*`
   `set:` lines), each path moved to the same place relative to the base
   checkout's root, so both sides of the delta are captured at the same
   evidence depth. Old-side inputs describe the release baseline and are not
   used. A path outside the PR checkout (other than an include directory,
   kept as a toolchain directory) cannot be provided by the base build and
   is refused rather than reused, as is a `--dump-manifest`.
3. That snapshot is the delta's old side, exactly as a resolved stored
   snapshot is. The worktree is removed afterwards.

`delta-base-build-command` must therefore leave the library and those
inputs at the same paths, relative to the checkout root, as the PR build
does, and must not install over the PR build: when it changes
`new-library`, the delta is not measured (`base_build_failed`), since it would
compare the base with itself. Submodules, caches and dependencies are the
command's job.

The command is isolated from the rest of the job. The gate has already
compared, so nothing it writes can change the gate's result. Its
`GITHUB_ENV`, `GITHUB_PATH`, `GITHUB_OUTPUT` and `GITHUB_STEP_SUMMARY` point at
throwaway files, so it cannot change a later step's environment or forge
the build's result. No token and none of the Action's inputs are in its
environment, and the git commands it runs see no `actions/checkout`
credential. The persisted credential is still in `.git/config`, readable
by any code the build runs, so check out with `persist-credentials: false`
(as above). The code it runs is the base commit's, already on the default
branch.

`base_unknown` and `base_ambiguous` selected no commit, so nothing is built:
the comment explains them as with `report`. The comment and
`delta-comment-outcome` name what happened, and the step's status is always
the gate's:

| `delta-comment-outcome` | Comment |
|---|---|
| `base_built` | The delta, headed "Changes in this PR (vs `main@<sha7>`, built in this job …)". |
| `base_build_failed` | The checkout or `delta-base-build-command` failed; the release comparison is shown and the job log has the build output. |
| `base_dump_failed` | The build succeeded but its dump failed, or an evidence path cannot be moved into the base checkout; the release comparison is shown. |
| `rerun_failed` | The comparison against the built base produced no report. |

None of them reads as "no changes", and none of them changes the exit code.

### The record in the JSON report

Both reports say which comparison they are, in a top-level `delta_baseline`
block (compare report schema 7.3, release report schema 3.1; field reference
in [output formats](../use/output-formats.md#gate-or-delta-comparison-delta_baseline)):

| Report | `comparison_role` | `outcome` | `gate_verdict` | `operand_match` |
|---|---|---|---|---|
| The gate's (the `output-file`, the summary, the SARIF/JUnit sources) | `gate` (`decides_exit_code: true`) | the lookup's outcome, or `unsupported_operand` for a directory/package comparison | `null` -- its own `verdict` is the gate's | `null` -- the gate runs before the operands are paired |
| The delta's (the one the comment renders, and the one staged for a fork publisher) | `delta` (`decides_exit_code: false`) | `resolved`, or `base_built` when `delta-on-missing-base: build` built the base in this job | the gate's verdict | how the snapshot's library was paired with `new-library` (the `delta-operand-match` output) |

`base_sha` and `branch` are the base commit and default branch on both. The
gate's block records the lookup as it stood when the gate ran: if the delta
comparison then produces no report, the comment falls back to the gate's
report under `rerun_failed`, which the gate's block does not show; the same
holds for the operand-identity refusals (`operand_*`) and for a failed base
build (`base_build_failed`, `base_dump_failed`), which are decided after the
gate and run no delta comparison. The block
never changes a verdict or exit code. A record the CLI would refuse is not
passed at all (the run warns), so the block can never turn the gate's run
into a usage error.

A stored report renders its own notes: the fork publisher reads the
analysis context's `delta_*` fields first and, without them, the report's
`delta_baseline` block (for the one-target aggregate the fork path stages, the
block of the one member report it names, read through the same path-safe
loader as the comment's member rendering) -- a `delta` report is labelled as the delta with the
recorded gate verdict, an unresolved `gate` report with its outcome. A
resolved `gate` report rendered on its own carries no note, since it does not
record whether a delta comparison followed.
