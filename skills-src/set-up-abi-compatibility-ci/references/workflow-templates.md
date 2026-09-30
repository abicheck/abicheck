# Workflow templates

Starting points, not drop-ins. Every `<placeholder>` must be replaced with a
fact read from the repository (step 1 of the skill), and the build steps
should be the repository's own, copied from its existing CI. Each template
states the properties it depends on; keep them when adapting.

Placeholders used below:

| Placeholder | Example |
|---|---|
| `<lib>` | `libfoo` (library name, used in snapshot/artifact names) |
| `<lib-path>` | `build/libfoo.so` (exact path after the build step) |
| `<public-headers>` | `include/` (public header directory only) |
| `<lang>` | `c` or `c++` |
| `<build-steps>` | the repository's own dependency install + configure + build |

The build must produce a **shared** library **with debug info**, the same way
on both sides of the comparison. For CMake that usually means
`-DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_SHARED_LIBS=ON` (the latter only
if the project's shared build is option-controlled).

---

## A. Release baseline

Two files. The release workflow publishes one snapshot per library as a
release asset; the PR workflow fetches it with `abi-baseline:
latest-release`, which searches the latest GitHub Release for a single
`*.abicheck.json` asset. **The asset name must end in `.abicheck.json`.**
With more than one library, `latest-release` would find several assets, so
use template A's multi-library variant (explicit download) instead.

### `.github/workflows/abi-baseline.yml`

```yaml
name: ABI baseline

on:
  release:
    types: [published]
  workflow_dispatch:            # bootstrap: backfill an existing release
    inputs:
      tag:
        description: Existing release tag to attach a snapshot to
        required: true

permissions:
  contents: read

jobs:
  dump:
    runs-on: ubuntu-24.04
    permissions:
      contents: write           # upload the release asset; nothing else needs it
    env:
      TAG: ${{ github.event.release.tag_name || inputs.tag }}
    steps:
      # contents: write is an elevated permission -> pin every action by SHA
      # (resolve with: git ls-remote https://github.com/<owner>/<action> 'refs/tags/<tag>*'
      #  and prefer the `<tag>^{}` line when present: that is the commit, the
      #  plain line of an annotated tag is the tag object and will not resolve)
      - uses: actions/checkout@<sha>  # v6
        with:
          ref: ${{ env.TAG }}

      <build-steps>

      - name: Dump ABI snapshot
        uses: abicheck/abicheck@<sha>  # v0.6.0
        with:
          mode: dump
          new-library: <lib-path>
          header: <public-headers>
          lang: <lang>
          new-version: ${{ env.TAG }}
          output-file: <lib>.abicheck.json

      - name: Attach snapshot to the release
        run: gh release upload "$TAG" <lib>.abicheck.json --clobber
        env:
          GH_TOKEN: ${{ github.token }}
```

**Bootstrap:** after merging, run this workflow once by hand
(`gh workflow run abi-baseline.yml -f tag=<latest-tag>`) so the latest
existing release gets a snapshot. Until then, the PR check has nothing to
fetch and fails — state that in the setup report as a required step.

### `.github/workflows/abi-check.yml`

```yaml
name: ABI check

on:
  pull_request:
  push:
    branches: [<default-branch>]

permissions:
  contents: read

concurrency:
  group: abi-check-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  abi-check:
    runs-on: ubuntu-24.04
    permissions:
      contents: read            # checkout + fetch the release asset
      pull-requests: write      # sticky PR comment (no-op for fork PRs)
    steps:
      - uses: actions/checkout@v6

      <build-steps>

      - name: Compare against the last release
        uses: abicheck/abicheck@v0.6.0
        with:
          abi-baseline: latest-release
          new-library: <lib-path>
          new-header: <public-headers>
          lang: <lang>
          new-version: ${{ github.event.pull_request.head.sha || github.sha }}
          # Advisory during rollout; flip to the label expression when blocking:
          #   ${{ !contains(github.event.pull_request.labels.*.name, 'abi-break-approved') }}
          fail-on-breaking: 'false'
          fail-on-api-break: 'false'
```

Why each property is there: [pitfalls](pitfalls.md).

### Multi-library variant

One asset per library (`libfoo.abicheck.json`, `libbar.abicheck.json`) and
a matrix in both workflows:

```yaml
    strategy:
      fail-fast: false          # one library's break must not hide another's
      matrix:
        include:
          - lib: libfoo
            path: build/libfoo.so
            headers: include/foo/
          - lib: libbar
            path: build/libbar.so
            headers: include/bar/
```

In the PR workflow, replace `abi-baseline` with an explicit download plus
`old-library`:

```yaml
      - name: Fetch ${{ matrix.lib }} baseline from the latest release
        run: gh release download --pattern '${{ matrix.lib }}.abicheck.json' --dir baseline
        env:
          GH_TOKEN: ${{ github.token }}
      - uses: abicheck/abicheck@v0.6.0
        with:
          old-library: baseline/${{ matrix.lib }}.abicheck.json
          new-library: ${{ matrix.path }}
          new-header: ${{ matrix.headers }}
```

If the matrix build is expensive, build once in a separate job and pass the
build tree with `actions/upload-artifact`/`download-artifact`, rather than
rebuilding per library.

---

## B. Merge-base build

One file, no infrastructure: the PR job builds the PR's base commit and the
PR head side by side and compares the two native libraries, each parsed with
its **own** headers (`old-header`/`new-header`). Costs a second build per
run; always comparable because both sides use the same runner and flags.

```yaml
name: ABI check

on:
  pull_request:

permissions:
  contents: read

concurrency:
  group: abi-check-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  abi-check:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      pull-requests: write      # sticky PR comment (no-op for fork PRs)
    steps:
      - name: Check out PR head
        uses: actions/checkout@v6
        with:
          path: new
      - name: Check out PR base
        uses: actions/checkout@v6
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          path: old

      # <build-steps>, run twice with identical flags:
      - name: Build base
        working-directory: old
        run: |
          cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
          cmake --build build -j
      - name: Build head
        working-directory: new
        run: |
          cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
          cmake --build build -j

      - name: Compare base vs head
        uses: abicheck/abicheck@v0.6.0
        with:
          old-library: old/<lib-path>
          new-library: new/<lib-path>
          old-header: old/<public-headers>
          new-header: new/<public-headers>
          lang: <lang>
          old-version: ${{ github.event.pull_request.base.sha }}
          new-version: ${{ github.event.pull_request.head.sha }}
          fail-on-breaking: 'true'
```

Limit to state in the report: this answers "does *this PR* break what is on
the base branch", not "is the branch still compatible with the last
release" — a break merged earlier is invisible to every later PR. Offer
template A once the project cuts releases.

If the base commit predates the library (the PR that introduces it), the
base build has no `<lib-path>`: guard the compare step with a file-existence
check and say so in the summary rather than failing.

---

## C. Committed snapshot

```yaml
      - uses: abicheck/abicheck@v0.6.0
        with:
          old-library: abi/<lib>.abicheck.json   # committed, reviewed
          new-library: <lib-path>
          new-header: <public-headers>
          lang: <lang>
```

Produce the file once with the release-side `dump` (template A's dump step,
run locally or via `workflow_dispatch`), commit it, and document the refresh
rule: the snapshot is regenerated **only** when a release is cut, in the
release PR, never in the PR that the check is failing on
([safety invariants](../../shared/safety-invariants.md) item 7).

---

## Optional add-ons

- **SARIF / Code Scanning:** `format: sarif` + `upload-sarif: 'true'`, which
  requires `security-events: write` — so pin every action in that job by SHA.
  Single-pair compare only.
- **Full report as an artifact:** `extra-args: -o json=abicheck-report.json`,
  then `actions/upload-artifact`, and pass its `artifact-url` output to
  `pr-comment-report-artifact-url`.
- **Fork PR comments:** the trusted `workflow_run` split —
  [fork-PR reporting](../../../docs/use/fork-pr-reporting.md).
- **Many targets/profiles/baseline channels:** the reusable
  `check-project.yml` workflow —
  [which scenario am I](../../../docs/integration/index.md).
