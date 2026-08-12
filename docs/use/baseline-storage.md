---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: beginner
canonical_for:
  - baseline-storage-backends
summarizes:
  - baseline-lifecycle
  - snapshot-storage-compression
lifecycle: active
generated: false
---

# Storing Baselines

abicheck does not mandate where baselines are stored — it has no opinion on
*where* you keep the JSON file [Creating and Comparing a
Baseline](create-baseline.md) produces. Choose the pattern below that fits
your team. For what a baseline is and why you may need two of them, see
[Baseline Management](baseline-management.md).

## Compressing stored snapshots

Every recipe below writes/reads a baseline as an `.abicheck.json` file, but
that file may just as well be gzip- or zstd-compressed on disk
(`.abicheck.json.gz`/`.abicheck.json.zst`) — every abicheck entry point
(`dump`, `compare`, `scan --against`, the `abicheck/abicheck` Action, and
`actions/baseline`) detects the encoding transparently from magic bytes, not
the filename, so a compressed baseline is a drop-in replacement for a plain
one everywhere on this page. It's a pure storage/transport envelope around
identical JSON content — see [Snapshot Format's storage encoding
section](../reference/snapshot-format.md#storage-encoding-adr-059) for the
format details and the file suffix each encoding canonically uses.

Recipe C (Actions Cache) and Recipe D (an external artifact store) are where
compression pays for itself most directly — smaller cache entries and
smaller transferred objects — since neither one already compresses the
asset the way Recipe A's release upload or Recipe A2's `tar --zstd`
packaging step does. Request it with `dump`'s `--compression {gzip,zstd}`
(CLI) or the Action's `snapshot-compression` input, and give the output
file a matching canonical suffix so every later reader's own suffix checks
(where applicable) agree with what's actually on disk.

## Recipe A: GitHub Releases (Recommended)

Best for: open-source libraries, public API contracts.

**Release workflow** (runs when a release is published):

```yaml
name: ABI Baseline
on:
  release:
    types: [published]

jobs:
  baseline:
    runs-on: ubuntu-latest
    permissions:
      contents: write   # needed for release asset upload
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make

      - name: Dump ABI baseline
        uses: abicheck/abicheck@v0.5.0
        with:
          mode: dump
          new-library: build/libfoo.so
          new-header: include/foo.h
          new-version: ${{ github.ref_name }}
          output-file: libfoo-${{ github.ref_name }}.abicheck.json

      - name: Upload baseline to release
        run: gh release upload ${{ github.ref_name }} libfoo-*.abicheck.json --clobber
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**PR workflow** (compares against latest release baseline):

```yaml
name: ABI Check
on: pull_request

jobs:
  abi:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make

      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          abi-baseline: latest-release
          new-library: build/libfoo.so
          new-header: include/foo.h
```

The `abi-baseline: latest-release` input automatically downloads the `*.abicheck.json`
asset from the latest GitHub Release and uses it as the old library.

To pin to a specific release:

```yaml
      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          abi-baseline: v2.0.0
          new-library: build/libfoo.so
          new-header: include/foo.h
```

**CLI equivalent** (requires the `gh` CLI and `GH_TOKEN`):

```bash
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 --git-tag v2.0.0 \
  -o libfoo-2.0.0.abicheck.json
gh release upload v2.0.0 libfoo-2.0.0.abicheck.json --clobber
```

### `abi-baseline` also understands a release-contract baseline-set archive

`abi-baseline` always searches for a single `*.abicheck.json[.gz|.zst]`
asset first — the recipe above, unchanged. When a release instead publishes
a `publish-baseline.yml`-produced baseline-set archive (one
`abicheck-baseline-<profile>.tar.zst` per contract profile, holding several
libraries' snapshots plus a `manifest.json` — see the
[`publish-baseline`/`update-main-baseline` reference](../reference/publish-baseline.md))
and that search finds nothing, set `baseline-profile` and `baseline-target`
to fetch the target's snapshot out of the archive instead:

```yaml
      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          abi-baseline: latest-release
          baseline-profile: linux-x86_64-gcc13-release
          baseline-target: libfoo
          new-library: build/libfoo.so
          new-header: include/foo.h
```

This is the same [`resolve_target()`](../reference/resolve-baseline.md)
resolver `actions/resolve-baseline` uses, so a `wrong_profile`/`stale_schema`
mismatch is reported the same way. Unlike `resolve-baseline` (which resolves
against an already-staged baseline-path the caller downloaded itself), this
fallback does the GitHub Release download and archive extraction inline,
mirroring `abi-baseline`'s own existing single-snapshot fetch — no separate
staging step needed. `baseline-asset-name-template` (default
`abicheck-baseline-{profile}.tar.zst`) only needs setting if the publishing
workflow customized `publish-baseline.yml`'s own `asset-name-template` input
away from its default.

## Recipe A2: Multi-Library Releases — the `baseline` Action (a generator, not a registry)

Recipe A above dumps one library. A release with several libraries needs one
snapshot per library plus a manifest tying them together — the
[`abicheck/abicheck/actions/baseline`](https://github.com/abicheck/abicheck/tree/main/actions/baseline)
Action is a thin convenience wrapper around exactly that: it runs a plain
`abicheck dump` once per library named in its `libraries` JSON input, writes
each snapshot into `output-dir`, and records fact-set identity, build
profile, and per-library content digests into a `manifest.json` alongside
them. Nothing about it is the removed baseline registry (see [Baseline
Management](baseline-management.md)) — it does not commit, publish, address
baselines by `library:version:platform` key, or replace this page's storage
recipes; it only replaces hand-writing a per-library `dump` matrix and a
manifest generator. Publishing the `output-dir` it produces is still the
calling workflow's job, using whichever storage recipe on this page fits
(Recipe A's `gh release upload`, Recipe B's git commit, etc., applied to the
whole directory instead of one file).

See [Recommended flow: a multi-library release with one shared facts
pack](github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for how this composes with `--build-info`/`--sources` when the libraries
share one collected facts pack, and the Action's own `action.yml` for its
full input/output reference (`profile`, `depth`, `previous-manifest` for
refresh detection, etc.).

## Recipe B: Git-Committed Baselines

Best for: small libraries where you want baselines auditable in PR diffs.

```bash
# Developer or release CI creates/updates the baseline
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 -o abi/libfoo.abicheck.json
git add abi/libfoo.abicheck.json
git commit -m "Update ABI baseline for v2.0.0"
git push
```

**PR workflow:**

```yaml
      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi/libfoo.abicheck.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

No download step needed — the baseline file is in the repo.

### A committed baseline can silently "approve itself" in a PR — read it from the base commit

The PR workflow above resolves `old-library: abi/libfoo.abicheck.json`
from whatever is checked out, which on a `pull_request` run is the PR's
own head commit. If the *same* PR also updates
`abi/libfoo.abicheck.json` to match a new (possibly incompatible) binary,
the comparison silently passes — it never reads the baseline as it stood
at the PR's *base* commit, so it's comparing the new binary against a
baseline the PR itself just rewrote to match it.

Fix by reading the baseline from the base commit explicitly:

```yaml
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - name: Materialize the baseline from the PR base commit
        run: |
          git show "${{ github.event.pull_request.base.sha }}:abi/libfoo.abicheck.json" \
            > /tmp/baseline.abicheck.json
      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: /tmp/baseline.abicheck.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

This way the PR can freely update its own copy of `abi/libfoo.abicheck.json`
(e.g. to keep it in sync for the *next* PR), but the comparison always
uses the base-commit content, which the PR itself cannot have written.

As defense-in-depth (a project that hasn't applied the fix above to every
recipe, or a workflow it doesn't control the source of), the
[`protect-committed-baseline.yml`](../reference/protect-committed-baseline.md)
reusable workflow fails outright any ordinary PR that touches a configured
baseline path at all, unless it carries an explicit human-reviewed bypass
label.

## Recipe C: GitHub Actions Cache

Best for: ephemeral, branch-scoped comparisons (e.g., comparing HEAD~1 vs HEAD)
without a release or a committed file.

**Default-branch workflow** (restores the previous cache entry, then refreshes
it with the current build):

```yaml
      - uses: actions/cache@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-

      - name: Refresh baseline
        uses: abicheck/abicheck@v0.5.0
        with:
          mode: dump
          new-library: build/libfoo.so
          new-header: include/foo.h
          output-file: abi-baseline.json
```

The cache key is unique per commit SHA, so every default-branch push is a cache
miss — once the job finishes, `actions/cache` saves whatever is at
`abi-baseline.json` (the snapshot the `dump` step just wrote) back under that
key, ready for the next PR's `restore-keys` fallback to pick up.

**PR workflow** (restores the latest default-branch baseline and compares):

```yaml
      - uses: actions/cache/restore@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-

      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

The PR side uses `actions/cache/restore` (restore-only), not the combined
`actions/cache` action from the default-branch workflow above. A PR run never
produces a fresh baseline to save, and the primary key here is scoped to the
PR's own commit SHA — with the combined action, a successful job would still
save whatever `abi-baseline.json` holds back under that PR-specific key, so a
*rerun* of the same commit would then hit that stale self-cached entry
instead of falling through `restore-keys` to the latest default-branch
baseline. Restore-only avoids that gap entirely.

## Recipe D: External Artifact Store (S3, Artifactory, GCS)

Best for: large binaries, private repos, retention policies.

```yaml
      # Release workflow
      - name: Upload baseline to S3
        run: aws s3 cp libfoo-2.0.0.abicheck.json s3://my-bucket/abi-baselines/

      # PR workflow
      - name: Download baseline from S3
        run: aws s3 cp s3://my-bucket/abi-baselines/libfoo-2.0.0.abicheck.json baseline.json

      - name: ABI check
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## Large baseline-sets: size limits and Git LFS

A single library's `.abicheck.json` is rarely large enough to matter, but a
release-contract or accepted-main **baseline-set** (`actions/baseline` /
[`actions/stage-baseline`](../reference/publish-baseline.md#actionsstage-baseline)'s
packaged archive, one snapshot per library plus `manifest.json`) can grow
large for a project with many libraries, deep template instantiation, or
embedded L3-L5 build-source evidence (`--sources`/`--build-info`) — and each
storage backend above has its own real limit worth knowing before you hit it
in CI, not after.

| Backend (recipe) | Real limit | What happens past it |
|---|---|---|
| GitHub Release asset (Recipe A / A2) | 2 GiB per asset | `gh release upload` fails outright; the workflow errors, it does not silently truncate. |
| GitHub Actions Cache (Recipe C) | 10 GiB **total per repository**, individual entries evicted after 7 days unaccessed | A cache write past the repo-wide quota evicts the LEAST-recently-used entries to make room — including another workflow's unrelated cache, not just older `accepted-main` baseline entries. |
| Git-committed file (Recipe B) | No hard limit from Git itself, but GitHub warns at 50 MiB and hard-blocks pushes over 100 MiB per file (without Git LFS) | A push containing an over-100 MiB baseline file is rejected by GitHub's own pre-receive hook — not an abicheck error, a `git push` failure. |
| Workflow artifact (used internally by `check-project.yml`'s per-cell baseline staging) | 10 GiB per artifact (GitHub-hosted runners' default) | Upload fails; same "hard error, not silent truncation" shape as the release-asset limit. |

**Mitigations, cheapest first:**

1. **Compress the stored snapshots** (`--compression zstd`, see
   [above](#compressing-stored-snapshots)) — pure win, no behavior change,
   typically the single biggest size reduction for a snapshot-heavy
   baseline-set since JSON compresses well. Already the default encoding
   `stage_baseline`/`publish-baseline.yml` wrap the whole baseline-set
   directory in via `tar --zstd`; per-snapshot compression compounds with
   that (it shrinks what goes *into* the archive, the archive step then
   compresses the already-smaller result further).
2. **Don't embed L3-L5 build-source evidence in every baseline-set snapshot
   unless a check actually needs it.** `--sources`/`--build-info` can add
   substantially to a single snapshot's size (call/type-graph nodes and
   edges scale with the codebase, not just the public ABI surface) — keep
   deep-evidence dumps to the specific libraries/profiles whose checks are
   configured to use `requested-depth: source`, rather than uniformly across
   every profile in a matrix.
3. **Split a very large multi-library release into more than one
   baseline-set archive** (e.g. by subsystem) rather than one archive
   covering the whole project — `asset-name-template`'s `{profile}`
   substitution already gives each contract profile its own archive; the
   same idea extends to splitting a single profile's libraries across more
   than one `actions/baseline` invocation if one profile alone is the
   bottleneck.
4. **For a genuinely large *committed* baseline (Recipe B) that Git LFS is
   the right fit for** — a project choosing to commit baseline-set archives
   rather than individual JSON snapshots, at a size where GitHub's ordinary
   git-push limits bite — track it with [Git
   LFS](https://git-lfs.com/) (`git lfs track "abi/*.tar.zst"`) the same way
   you would any other large committed binary artifact. abicheck itself has
   no LFS-specific integration or opinion here: `dump`/`compare`/`scan
   --against`/the Action all read a baseline file (or, for a baseline-set
   archive, a directory `resolve-baseline` already extracted it into) from
   an ordinary filesystem path, so as long as your checkout step resolves
   the LFS pointer to real content before abicheck runs (`actions/checkout`
   does this automatically when `lfs: true` is set, or `git lfs pull`
   explicitly), nothing else in the pipeline needs to change. One thing to
   get right: pair LFS with [the committed-baseline
   self-approval protection](../reference/protect-committed-baseline.md)
   above just as you would a plain committed JSON file — an LFS pointer
   file changing is still a change `git diff --name-only` reports, so the
   protection workflow's glob match works identically whether the tracked
   path resolves through LFS or not.
5. **Move to an external artifact store (Recipe D)** once a project's
   baseline-sets consistently exceed what GitHub's own storage backends
   comfortably hold — the recipe above has no analogous size ceiling beyond
   whatever the chosen store (S3, Artifactory, GCS, ...) itself imposes, at
   the cost of managing that store's own access control and retention
   policy yourself.

None of the mitigations above are mutually exclusive — compression (1) is
essentially free and worth doing regardless of which storage backend you
land on.
