# Template A — release snapshot baseline

Replace every `<placeholder>` with a fact from the repository: `<lib>`
(`libfoo`), `<lib-path>` (exact built file, e.g. `build/libfoo.so`),
`<public-headers>` (`include/`), `<lang>` (`c`/`c++`), `<build-steps>` (the
repository's own install + configure + build, with debug info and a shared
build on both sides).

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
      # Resolve each pin to its COMMIT (annotated tags' plain line is the tag
      # object and will not run):
      #   git ls-remote https://github.com/<owner>/<action> 'refs/tags/<tag>' 'refs/tags/<tag>^{}' | sort -k2 | tail -1 | cut -f1
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
