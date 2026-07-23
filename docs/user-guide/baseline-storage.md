---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: beginner
summarizes:
  - baseline-lifecycle
lifecycle: active
generated: false
---

# Storing Baselines

abicheck does not mandate where baselines are stored — it has no opinion on
*where* you keep the JSON file [Creating and Comparing a
Baseline](create-baseline.md) produces. Choose the pattern below that fits
your team. For what a baseline is and why you may need two of them, see
[Baseline Management](baseline-management.md).

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
      - uses: actions/cache@v4
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
