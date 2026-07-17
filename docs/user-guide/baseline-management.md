# Baseline Management

ABI baselines are pre-computed snapshots of a library's ABI surface at a known-good
point (typically a release). Comparing future builds against a baseline detects
breaking changes before they ship.

> The baseline is the input to the CI gating pipeline (classify → suppress →
> severity → exit code) — see [CI Gating](ci-gating.md) for how it combines
> with policies, suppressions, and severity.

> **The built-in baseline registry command is gone.** The pre-1.0 CLI reset
> (ADR-043) removed the whole `abicheck baseline` subcommand group
> (`push`/`pull`/`list`/`delete`) with no replacement command — abicheck's
> CLI has no opinion on *where* you store a snapshot. This page's storage
> recipes below (GitHub Releases, git-committed files, Actions cache,
> external artifact stores) all just move a plain JSON file around and
> continue to work unchanged; only the registry's own addressing/integrity
> layer (`library:version:platform` keys, checksum-on-pull) has no direct
> equivalent. For a one-off "compare against a previous build" without
> managing a baseline file yourself, see
> [`scan --against`](scan-levels.md) below.

## Creating a Baseline

```bash
# Basic: write to stdout
abicheck dump libfoo.so -H include/foo.h --version 2.0.0

# Write to a specific file
abicheck dump libfoo.so -H include/foo.h --version 2.0.0 -o baseline.json

# Conventionally named (see naming convention below)
abicheck dump libfoo.so -H include/foo.h --version 2.0.0 -o libfoo-2.0.0.abicheck.json
```

### Provenance Metadata

Snapshots include provenance metadata that tracks where and when they were created:

```bash
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 \
  --git-tag v2.0.0 \
  --build-id "$CI_RUN_ID" \
  -o libfoo-2.0.0.abicheck.json
```

This embeds in the snapshot JSON:

| Field | Source | Example |
|-------|--------|---------|
| `git_commit` | Auto-detected from `git rev-parse HEAD` | `abc1234def5678` |
| `git_tag` | `--git-tag` flag | `v2.0.0` |
| `created_at` | Auto-set (ISO 8601 UTC) | `2026-03-24T12:00:00+00:00` |
| `build_id` | `--build-id` flag | `gh-actions-1234` |

Use `--no-git` to skip automatic git commit detection (e.g., in non-git environments).

### The `.abicheck.json` Naming Convention

Name baselines `<library>-<version>.abicheck.json` (via `-o`):

| Library | Version | Output File |
|---------|---------|-------------|
| `libfoo.so.1` | `2.0.0` | `libfoo-2.0.0.abicheck.json` |
| `bar.dll` | `3.1` | `bar-3.1.abicheck.json` |
| `libqux.dylib` | `1.0` | `libqux-1.0.abicheck.json` |

This convention makes CI scripts predictable: upload with `*.abicheck.json`, download
with `--pattern '*.abicheck.json'`. The GitHub Action's `abi-baseline` input looks for
`*.abicheck.json` assets on releases.

## Storage Patterns

abicheck does not mandate where baselines are stored. Choose the pattern that fits
your team:

### Recipe A: GitHub Releases (Recommended)

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
        uses: abicheck/abicheck@v0.3.0
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
        uses: abicheck/abicheck@v0.3.0
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
        uses: abicheck/abicheck@v0.3.0
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

### Recipe B: Git-Committed Baselines

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
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: abi/libfoo.abicheck.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

No download step needed — the baseline file is in the repo.

### Recipe C: GitHub Actions Cache

Best for: ephemeral, branch-scoped comparisons (e.g., comparing HEAD~1 vs HEAD).

```yaml
      - uses: actions/cache@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-
```

### Recipe D: External Artifact Store (S3, Artifactory, GCS)

Best for: large binaries, private repos, retention policies.

```yaml
      # Release workflow
      - name: Upload baseline to S3
        run: aws s3 cp libfoo-2.0.0.abicheck.json s3://my-bucket/abi-baselines/

      # PR workflow
      - name: Download baseline from S3
        run: aws s3 cp s3://my-bucket/abi-baselines/libfoo-2.0.0.abicheck.json baseline.json

      - name: ABI check
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## Comparing Against a Baseline

Once you have a baseline, comparison is the same regardless of storage:

```bash
# JSON snapshot vs new binary
abicheck compare baseline.json build/libfoo.so --header new=include/foo.h

# Two snapshots (no headers or tools needed)
abicheck compare old-baseline.json new-baseline.json
```

Snapshots are self-contained — they include all type, function, variable, and enum
information. Comparing two snapshots requires no headers, compilers, or debug info.

You can also pass **packages** (RPM, Deb, tar, conda, or wheel) straight to
`compare` — it compares all shared libraries inside them without manual
extraction:

```bash
abicheck compare libfoo-1.0.rpm libfoo-1.1.rpm
```

See [Multi-Binary Releases](multi-binary.md) for the bundle/package flags and the
[GitHub Action](github-action.md) guide for CI examples with packages.

### `scan --against` for a one-off comparison

`abicheck scan ARTIFACT` doesn't require a stored baseline at all — pass
`--against` with any previous dump, library, directory, or package to compare
against, and `scan` runs its always-on audit checks plus that comparison in
one pass:

```bash
abicheck scan new/libfoo.so --header new/include --against old/libfoo.so
```

`-H`/`--header` and `-I`/`--include` are side-aware: a bare value applies to
both `ARTIFACT` and the `--against` side, and an `old=`/`new=` prefix scopes
to one side. When `--against` is a **native library** (not a snapshot) and
its public headers differ from the new version, parse the old side with
**its own** headers using the `old=` prefix:

```bash
abicheck scan new/libfoo.so --against old/libfoo.so \
  --header new=new/include --header old=old/include
```

- Without an `old=`-scoped header, a native `--against` library is parsed
  with the same headers as `ARTIFACT` (correct only when the headers didn't
  change).
- A **JSON-snapshot** `--against` target already has its headers baked in, so
  side-scoped headers are unnecessary there. Prefer a pre-dumped snapshot
  baseline when you can — it's unambiguous and needs no toolchain at compare
  time.

See [Source-Scan Depth](scan-levels.md) for the full `scan` flag reference.

## Two kinds of baseline: release contract vs. accepted-main

A single fixed baseline answers only one question well. Most projects
actually need *two* baselines, because they answer different questions and
should behave differently when a PR is labeled as an intentional break:

| Baseline | Question it answers | Where it comes from | What advances it |
|---|---|---|---|
| **Release / contract baseline** | Is the current code still compatible with what we already shipped? | A dump of the last **released** version (a release tag/asset — [Recipe A](#recipe-a-github-releases-recommended) above) | Only a new project release |
| **Accepted-main baseline** | Did *this PR* introduce a new break (as opposed to one already merged)? | A dump of the last build that passed CI on the default branch | Every PR merged to the default branch |

Conflating them causes a specific, recurring failure: if CI only keeps a
*fixed* release baseline and skips the whole check whenever a PR carries an
`intentional-breaking-change` label, the break lands on the default branch
still relative to the old release. Every subsequent, unrelated PR then
diffs against that same stale release baseline, sees the same break again,
and fails too — even though the break was already reviewed and accepted.
The label suppressed the *check*, not just the *gate*, so nothing ever
re-baselines.

**The fix is to keep both baselines running, and let the label only relax
their gates — never whether either comparison runs:**

- Always run and publish **both** comparisons — the release-contract report
  stays visible even when its gate is relaxed, so "compatible with the last
  release" doesn't silently go unreported.
- On the PR that introduces the break, the label relaxes **both** jobs'
  `fail-on-breaking` — that PR is, by construction, the one case where the
  accepted-main comparison is *expected* to report a break (that's what it's
  for), and the label plus its review is what makes the break "accepted."
  Neither job's *comparison* is skipped, only its gate, for that one PR.
- The accepted-main baseline is what ordinarily gates every other PR:
  refresh it from the default branch after every merge (a lightweight `dump`
  step on a `push` trigger, [Recipe C](#recipe-c-github-actions-cache) or a
  git-committed file work well for this since it churns on every merge).
  Once refreshed, the gate is strict again for the *next* PR — the label
  only ever excuses the PR that carries it, not the ones that follow.
- The release-contract baseline advances deliberately, only when you cut a
  new release — treat that refresh as part of the release process, not
  something a regular PR should touch.

```yaml
# PR workflow — both baselines compared, both share the same label-relaxed gate
jobs:
  release-contract:
    steps:
      - uses: abicheck/abicheck@v0.3.0
        with:
          abi-baseline: latest-release       # fixed until the next release
          new-library: build/libfoo.so
          new-header: include/foo.h
          fail-on-breaking: ${{ !contains(github.event.pull_request.labels.*.name, 'intentional-breaking-change') }}

  accepted-main:
    steps:
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: main-baseline.json     # refreshed on every merge to main
          new-library: build/libfoo.so
          new-header: include/foo.h
          # Same label relaxes this gate too — this comparison is *expected*
          # to report a break for the one PR that introduces it. Once merged
          # and main-baseline.json is refreshed, every subsequent PR is
          # gated strictly again (the label doesn't carry over).
          fail-on-breaking: ${{ !contains(github.event.pull_request.labels.*.name, 'intentional-breaking-change') }}
```

### Baseline identity is more than a version number

A baseline file name like `2.0.0.abicheck.json` is not self-describing
enough on its own to guarantee two dumps are comparable — a meaningful
identity also includes the platform/architecture, build profile (compiler,
ISA, debug/release), the public-header/source configuration used to dump it,
and (for build-source evidence) the producer and toolchain that collected it
(replay vs. `abicheck-cc` vs. the Clang plugin — see [Producing Source
Facts](producing-source-facts.md) for how each is versioned). If your project ships more
than one platform/architecture/build-profile combination, encode that in the
baseline's path or filename (e.g.
`linux-x86_64-icx-avx2-debug/2.0.0.abicheck.json`), not just the version —
otherwise a baseline dumped on one profile can silently get compared against
a candidate built on another.
