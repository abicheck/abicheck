---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# PR delta against a main snapshot (accepted-main consumption)

**Owner ADRs:** [ADR-047](../adr/047-github-actions-integration-model.md) (the
`accepted-main` channel), [ADR-065](../adr/065-comparison-scope-selection-and-completeness.md)
(absent is not removed) · **Status:** pieces 1–2 landed (`actions/main-snapshot`,
`actions/fetch-main-snapshot`, `abicheck/frontends/action/main_snapshot.py`);
piece 3 landed (main Action `delta-baseline`, `delta-on-missing-base:
report|fail|build` with `delta-base-build-command`, the comment's two notes
via `pr_comment --note`, and on the artifact (fork) path via
`analysis_context`); operand identity landed (§ Operand identity:
`operand.json`, `delta-operand-map`, `operand_*` outcomes, applied to a
built base too); open: piece 4's report-schema field, the path-equivalent
*ancestor* rule (`base-equivalent-paths`), cache storage · **Effort:** L

## Problem

A pull request compared only against the **release** baseline reports
everything that changed since the release. A symbol added to `main` the day
after the release shows up as "added" in every later PR's comment, so the
comment stops telling a reviewer what *this* PR does.

The release comparison is still the right **gate** ("is this still compatible
with what we shipped?"). What is missing is a second, cheap comparison that
answers "what did this PR change?" — the `accepted-main` question of
[baseline-management.md](../../use/baseline-management.md#two-kinds-of-baseline-release-contract-vs-accepted-main).

A producer half already exists: `update-main-baseline.yml` (a reusable
`workflow_call` workflow) writes an `accepted-main` snapshot to the Actions
cache when a calling workflow runs it on a default-branch push.
Nothing consumes it (the "Known gap" sections of
[publish-baseline.md](../../reference/publish-baseline.md) and
[resolve-baseline.md](../../reference/resolve-baseline.md)), and restoring by
key prefix picks the *newest* entry, not the one for the PR's base commit.

## Goal & acceptance criteria

**Goal.** A project adds two short workflows and gets, on every PR: the
release-baseline gate, plus a PR comment listing only this PR's ABI/API
changes — with no rebuild of `main` in the PR job and no new state committed
to the repository.

1. Default integration is ≤ ~15 lines of user YAML per workflow; everything
   else lives in Actions / reusable workflows owned by this repository.
2. The comparison base is chosen by an exact, documented rule (§ Base
   selection). abicheck never substitutes a different commit's snapshot —
   the one exception is the opt-in `base-equivalent-paths` rule, which is
   recorded in the report — and never substitutes the release baseline for
   the delta.
3. Both sides of the delta are captured at the same evidence depth (§ Evidence
   parity); a mismatch is reported, never a silently narrower delta.
4. Every way the base snapshot can be unavailable is a **named outcome**,
   rendered in the comment/summary with the action the user should take.
   No outcome renders as "no changes".
5. The gate (release comparison) is unaffected by any delta outcome; the
   delta comparison changes the step's exit code only under the explicit
   `on-missing-base: fail` opt-in (§ Outcomes).
6. Works for fork PRs through the turnkey `workflow_run` publisher
   (`report-pr.yml`, delivered separately — see § Dependencies).
7. Storage: workflow artifacts by default, Actions cache as an option; both
   addressed by exact commit SHA.

## Design

### What is stored: snapshots, not deltas

Each eligible default-branch commit gets one immutable `dump` snapshot (or a
baseline-set for multi-library projects), named by its full commit SHA. The
delta is never stored: a PR computes it by comparing two snapshots (base
snapshot vs. the PR build) — a snapshot-to-snapshot `compare`, no rebuild.
The delta report is then simply this run's report artifact.

### Base selection

The delta is only meaningful if the base snapshot is **exactly** the code the
PR build was produced on top of. An older snapshot leaks other people's
commits into the delta; a newer one hides or inverts changes. So the rule is
"exact commit or a named failure", never "nearest available".

What the PR job builds determines the base commit:

| What the job checked out | Base commit | Why |
|---|---|---|
| **The PR merge commit** (`pull_request` default: `$GITHUB_SHA` = `refs/pull/N/merge`) — **default** | its **first parent** (`git rev-parse HEAD^1`) | The merge commit is *base-branch tip at event time* + the PR's changes. Its first parent is that tip, whatever the branch history looks like. |
| **The PR head** (`ref: head.sha`) | `git merge-base <default-branch> HEAD` | The head contains exactly the PR's commits on top of its merge-base. |
| `merge_group` (merge queue) | `github.event.merge_group.base_sha` | The queue entry is built on that base. Its delta can span several PRs, so it is headed "Changes in this merge-queue entry", not "this PR". |
| `push` to the default branch | `HEAD^1` (optional; "what did this merge change") | Same rule as the merge commit. |

The checkout must contain that base: `fetch-depth: 2` for the merge commit
(its first parent), `fetch-depth: 0` in head mode (the merge-base). With a
default depth-1 checkout the parent is not local, and the outcome is
`base_unknown` with that instruction — never a guess.

The first parent of the merge commit is preferred over
`github.event.pull_request.base.sha`: it is a property of the commit actually
built rather than of the event payload, so the two cannot disagree.

#### Scenarios

| Scenario | Merge-commit mode (default) | Head mode |
|---|---|---|
| Fresh PR off current main | base = main tip → snapshot normally present | merge-base = branch point → present while within retention |
| **Old PR, branched months ago**, new push | base = *current* main tip (GitHub re-merges on every `synchronize`) → present. Age of the branch is irrelevant. | merge-base = old branch point → artifact past retention → `snapshot_missing` (an expired artifact is no longer listed; `base_expired` only if still listed as expired): "rebase or merge main" |
| **Rebased** onto new main | base = current main tip → present | merge-base = the rebase target → present if that commit was snapshotted |
| **Merged main into the branch** | base = current main tip → present | merge-base = the merged main commit → present if it was a snapshotted push tip |
| Branched off a **non-tip** main commit (pushed together with others, or a commit skipped by CI) | unaffected | merge-base has no snapshot → `base_not_snapshotted` (see path-equivalence below) |
| **Criss-cross** history (several merge-bases) | unaffected | `base_ambiguous` — refuse; ask for a rebase or merge of main |
| Main moved after the PR's last push, **re-run** of the old job | re-run reuses the old merge commit → its base is an older tip; usually still present, else `snapshot_missing`/`base_expired`: "push or *Update branch* — a re-run reuses the old merge commit" | same as above |
| PR opened seconds after a merge to main; main's snapshot job still running | `base_pending` → wait up to `wait-timeout`, then still `base_pending` | same |
| Main's snapshot job failed / cancelled for the base commit | `base_failed` | same |
| Base commit skipped by the main workflow (`paths` filter, `[skip ci]`) | `base_not_snapshotted` | same |
| Merge conflict | GitHub creates no merge commit and does not run `pull_request` workflows; nothing to do | head mode still runs; rules above apply |

Merge-commit mode removes most of the "old base" problem: GitHub rebuilds the
merge commit on every push to the PR, and its first parent is always a recent
default-branch tip. That is why it is the default.

#### Optional: path-equivalent ancestor

Projects whose main workflow has a `paths:` filter will often hit
`base_not_snapshotted`. An **opt-in** input (`base-equivalent-paths`) lets the
fetcher walk first-parent ancestors of the base (bounded, e.g. 50) to the
nearest snapshotted commit **only if** `git diff --quiet <ancestor> <base> --
<paths>` holds — i.e. nothing that feeds the ABI changed in between. The
report records the substitution (`base_commit`, `snapshot_commit`,
`equivalence: paths`) and the comment shows it. The path list is a project
claim, not something abicheck verifies; with the input unset there is no
substitution at all. The provenance check (§ Storage) then applies to the
ancestor's SHA.

### Operand identity (landed)

Base selection picks the right *commit*; the snapshot must also be the right
*library*. The delta rerun substitutes the snapshot for the gate's old
operand, so a snapshot of a different library would report its whole surface
as removed — and a library whose path or name changed between the snapshot
and the PR (renamed output, another build directory, a SONAME bump) must
still get its delta. The decision lives in `abicheck/workflows/delta_operand.py`
(pure over two identities, unit-tested over an exhaustive small domain);
`action/run.sh` asks it (`delta-operand`) before the rerun.

- **What the snapshot records.** `main-snapshot` uploads `operand.json`
  (`schema`, `path` as given, `profile`) beside `snapshot.json`. File name
  and loader name (ELF `DT_SONAME`, Mach-O install-name base name) come
  from the snapshot itself, so artifacts written before the record existed
  still pair by name; PE has no loader name beyond the file name.
- **How the PR side matches.** Equal loader names; else loader names equal
  up to version (`binary_utils._canonical_library_key`, the release
  member-pairing key); else, with a loader name missing on a side, equal
  path or file name, then file names equal up to version — unless the one
  loader name present names another library than the other side's file.
  Unrelated loader names never match automatically.
- **Explicit mapping.** `delta-operand-map` (`SNAPSHOT=PR` lines, either side
  a path, file name or loader name) is the project's claim and is decisive:
  once given, an uncovered or contradicting pair is refused rather than
  matched automatically, and differing targets declared for one snapshot
  library are ambiguous rather than resolved by line order.
- **Outcomes.** `operand_unmatched` (nothing links them), `operand_ambiguous`
  (same file name/path but disagreeing loader evidence, or several map
  targets), `operand_unreadable`, `operand_map_invalid` (the log names the
  line). Each is
  "Delta unavailable" with the gate's report shown — never a removal — and
  counts as unavailable for `on-missing-base: fail`. A non-trivial match
  (`declared`, `*_version`) adds a comment note; the rule travels to the fork
  publisher as the structured `delta_operand_match` context field.
- **Scope.** One library per snapshot, as the rest of piece 3. A
  multi-library baseline-set will pair members by the same rule, with the
  map covering renames; that waits on piece 1's `baseline` input.

### Evidence parity

A snapshot-to-snapshot delta is only as deep as its shallower side: a header
snapshot compared against a binary-only PR side reports evidence differences
as changes, and the reverse misses type-level changes. `main-snapshot`
therefore takes the same evidence inputs as the PR side (its `dump-args`
document this), and piece 3 compares the snapshot's recorded evidence depth
with the PR side's. A mismatch is reported in the comment header as a
partial delta naming both depths; it never silently narrows the delta.

### Outcomes

`fetch-main-snapshot` returns one typed outcome (same style as
`resolve-baseline`). Each one maps to a fixed comment line:

| Outcome | Delta comparison runs? | Comment / summary says |
|---|---|---|
| `resolved` | yes | the delta, headed "Changes in this PR (vs `main@<sha7>`)" ("this merge-queue entry" for `merge_group`) |
| `base_unknown` | no | "the base commit could not be determined — check out with `fetch-depth: 2` (`0` in head mode)" |
| `resolved_equivalent` | yes | the delta + "base `<sha7>` not snapshotted; used `<sha7'>`, identical under declared paths" |
| `base_pending` (after `wait-timeout`) | no | "main snapshot for `<sha7>` still being built — re-run when it finishes" |
| `base_failed` | no | "main snapshot job for `<sha7>` failed (link)" |
| `base_not_snapshotted` | no | "main workflow did not snapshot `<sha7>` (skipped / filtered)" |
| `base_expired` | no | "snapshot for `<sha7>` expired — rebase onto or merge `main`" (head mode) / "push or *Update branch*" (re-run) |
| `base_ambiguous` | no | "several merge-bases — rebase onto or merge `main`" |
| `untrusted_source` | no | "a snapshot exists but did not come from the configured main workflow" |
| `snapshot_missing` | no | "the snapshot run succeeded but no artifact is listed (expired, deleted, or never uploaded)" — the API no longer lists expired artifacts, so expiry cannot be told apart |
| `lookup_failed` | no | "the snapshot lookup failed (API error)" — an operational error, never "absent" |
| `cache_miss` (cache storage) | no | "no cache entry for `<sha7>` — retry, or use `on-missing-base: build`" (the cache cannot tell pending from evicted) |

On any non-`resolved*` outcome the user picks the fallback with
`on-missing-base`:

- `report` (default) — the line above; the release gate result still shows.
- `build` — the PR job builds the base commit itself (checkout `<base>` →
  user's build command → `dump`). Exact, but costs a second build; needs a
  `base-build-command` input, so it is opt-in. Applies only to outcomes that
  selected a base commit — never to `base_unknown` or `base_ambiguous`, which
  have no commit to build.
- `fail` — the one explicit exception to gate isolation: the step fails with
  the delta outcome as its reason, and the `delta-outcome` output says so,
  so a required check can tell "gate failed" from "delta required but
  unavailable". Without this opt-in no delta outcome changes the exit code.

The release baseline is **never** used as a delta fallback.

### Storage backends

| | Workflow artifact (default) | Actions cache (option) |
|---|---|---|
| Name / key | `abicheck-main-<full-sha>` (+ `-<profile>` for multi-profile) | `<prefix>-<profile>-<full-sha>` (existing `accepted_main_cache_key`) |
| Lookup | `GET /repos/{o}/{r}/actions/artifacts?name=…` → exact name | `actions/cache/restore` with the **exact** key — never `restore-keys` |
| Provenance check | the uploading run must be a run of the configured workflow in this repository, event `push`/`workflow_dispatch`/`schedule`, on the default branch, conclusion success, and its `head_sha` must equal the requested snapshot SHA (the ancestor's under path equivalence) | none available beyond "written from the default branch's scope"; documented as weaker |
| Duplicates | a re-run uploads the same name again; every eligible candidate was built from the base by the trusted workflow, so the newest is used and the others are logged as superseded. (Comparing archive `digest`s does not work: an artifact zip records upload timestamps.) | keys are immutable; no duplicates |
| Lifetime | `retention-days` (default 14, max 90 per repo setting) | evicted after 7 days unused and by the 10 GiB LRU, which can evict unrelated caches |
| Pending detection | list runs of the main workflow with `head_sha == base` and `status != completed` | not possible → only `not found` |
| Fork PRs | needs `actions: read` on the read-only fork token (to verify on a public and a private repo during implementation) | default-branch caches are readable by PRs, forks included |

Artifacts win on exact lookup, provenance, pending detection and explicit
lifetime, so they are the default. Concurrency is a non-issue in both: names
are per-SHA and immutable, so parallel main pushes write different entries
and each PR reads its own.

Nightly-only snapshots are compatible with the design but almost always
produce `base_not_snapshotted` for merge-commit mode; the docs recommend
per-push snapshots and mention nightly only together with
`base-equivalent-paths` or `on-missing-base: build`.

### Integration surface (the target user YAML)

```yaml
# .github/workflows/abi-main.yml — snapshot every push to main
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
# .github/workflows/abi-pr.yml
on: pull_request
permissions: { contents: read, actions: read, pull-requests: write }
jobs:
  abi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with: { fetch-depth: 2 }          # the merge commit's first parent is the base
      - run: ./build.sh
      - uses: abicheck/abicheck@v1
        with:
          abi-baseline: latest-release      # gate
          new-library: build/libfoo.so
          new-header: include/
          delta-baseline: main              # comment = this PR's changes only
          delta-snapshot-workflow: abi-main.yml
```

Fork PRs add the ~8-line `workflow_run` publisher from the fork-reporting
work, which publishes the delta report; nothing else changes.

### Pieces

1. **`actions/main-snapshot`** (producer). *Landed:* `dump` of one library
   (or adoption of a prepared snapshot) → upload `abicheck-main-<sha>` with
   `retention-days`; refuses `pull_request`/`pull_request_target` (ADR-047
   §12) and a checkout that is not the commit being named. *Planned:*
   `baseline` for a multi-library set, and `storage: cache` writing the
   existing `accepted-main` cache key, after which `update-main-baseline.yml`
   becomes a thin caller of this Action rather than a second implementation.
2. **`actions/fetch-main-snapshot`** (consumer). *Landed:* inputs `mode`
   (`merge-commit`/`head`/`auto`), `workflow`, `branch`, `profile`,
   `wait-timeout`, `destination`; outputs `outcome`, `base-sha`,
   `snapshot-path`, `message`. Read-only API calls only. The decision module
   is `abicheck/frontends/action/main_snapshot.py`, beside
   `baseline_source.py`, because it reuses that package's
   `SourceRun`/`verify_source_run` provenance checks; base selection and
   outcome typing are pure and unit-tested without credentials. *Planned:*
   `storage`, `base-equivalent-paths`, and a `snapshot-sha` output (it differs
   from `base-sha` only under path equivalence).
3. **Main Action inputs** (*landed* as `delta-baseline: main`,
   `delta-snapshot-workflow`, `delta-mode`, `delta-profile`,
   `delta-wait-timeout`, `delta-on-missing-base: report|fail|build`,
   `delta-base-build-command`; a `<path>` value is planned). `build` runs
   after the gate comparison: it checks the base out into a worktree, runs
   the command sandboxed (no token or inputs, throwaway runner files), and
   dumps it with the gate's new-side evidence moved into that worktree
   (`abicheck/frontends/action/delta_base_dump.py`), so evidence parity holds
   by construction; its results are the comment-only outcomes `base_built`,
   `base_build_failed` and `base_dump_failed`. When set, the Action runs the existing compare twice:
   the gate as today, and the delta compare with `pr-comment` routed to the
   delta report and the gate's verdict as one header line ("Release gate:
   ✅ compatible / ❌ breaking — see summary"). The step's exit code comes
   from the gate only.
4. **Report rendering**: a `delta` block in the comment header (base/snapshot
   SHAs, outcome, equivalence note). Report schema gains an optional
   `comparison_role: gate|delta` + `delta_base` object (minor schema bump,
   ADR surface traceability).

Explicit `old-library` / `old-header` pointing at a snapshot the user staged
themselves stays supported — the two-compare recipe from the previous
discussion is the manual form of the same thing.

## Files & surfaces

- New: `actions/main-snapshot/action.yml`, `actions/fetch-main-snapshot/action.yml`,
  `abicheck/workflows/<delta_base>.py` (base selection, outcome model),
  `abicheck/frontends/action/<fetch_main_snapshot>.py` (API + artifact I/O).
- Changed: `action.yml` (+ `delta-baseline`, `delta-snapshot-workflow`,
  `on-missing-base`, outputs `delta-outcome`, `delta-base-sha`),
  `action/cli-surface.txt`, `docs/reference/github-action-inputs.md`,
  report renderer + compare report schema, `update-main-baseline.yml`.
- Docs: `docs/use/baseline-management.md` (lead with this as the
  accepted-main recipe), `docs/use/baseline-storage.md` (artifact recipe),
  `docs/use/github-action-recipes.md`, `docs/use/fork-pr-reporting.md`,
  new reference pages for the two Actions, resolve the two "Known gap"
  notes.
- ADR: amendment to ADR-047 recording base-selection rule, outcomes, and
  artifact storage as the accepted-main default.

## Tests

- **Base selection (property-style):** generate small commit DAGs (linear,
  rebase, merge-of-main, criss-cross, non-tip branch point) with a real
  `git` repo in a temp dir; oracle = the definition (first parent of the
  synthetic merge commit; `git merge-base --all` cardinality), not the
  implementation's helper.
- **Outcome table:** every row of § Outcomes driven through a fake API
  (artifact list, run list with pending/failed/success, wrong workflow, wrong
  event, wrong branch) → exact outcome + exact rendered line; no outcome
  renders "no changes".
- **Operand identity (landed, `tests/test_delta_operand_identity.py`):**
  every rule row; symmetry and "unrelated loader names never match" over an
  exhaustive small identity domain against a hand-written version-stem
  oracle; the map's decisiveness; `run.sh` end to end — an unmatched snapshot
  is never passed to `compare`.
- **Path-equivalence:** substitution only when the diff is empty under the
  declared paths; refused otherwise; recorded in the report.
- **Gate isolation:** with `on-missing-base: report`/`build`, for every delta
  outcome the step exit code equals the gate-only exit code; with a failing
  gate and a resolved snapshot the delta still runs and its findings appear
  in the comment. `on-missing-base: fail` is tested as its own case.
- **E2E** (`test-action.yml`-style): main push writes the artifact; a PR job
  in the same repo resolves it; a re-run after main moves reports
  `resolved` against the old tip.

## Effort & risk

L. Main risks: (a) the fork token's `actions: read` on private repos — if
absent, the docs route fork PRs of private repos to `storage: cache`;
(b) artifact listing API rate limits on very busy repos — lookup is one
exact-name query plus one run query, bounded; (c) users with `paths`-filtered
main workflows — covered by the opt-in equivalence rule and documented.

## Dependencies

- Turnkey fork publishing (`report-pr.yml` reusable workflow + producer-side
  artifact upload) — developed separately; this plan only requires that it
  can publish a named report file (the delta report) from the artifact.

## Out of scope

- Storing or diffing **reports** between runs (option "B" of the original
  discussion: set-difference of findings vs. a stored main report) — rejected
  as fragile across schema and finding-identity changes.
- Snapshots for non-default long-lived branches (release branches). The same
  pieces work with `branches: [release/*]`, but outcome rules for them are
  not designed here.
- Any change to what the release gate decides.
