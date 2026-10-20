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

      - run: <build-steps>      # one run step per build command, or a `run: |` block

      - name: Dump ABI snapshot
        uses: abicheck/abicheck@<sha>  # v0.7.0
        with:
          mode: dump
          new-library: <lib-path>
          header: <public-headers>
          lang: <lang>
          new-version: ${{ env.TAG }}
          output-file: <lib>.abicheck.json

      - name: Attach snapshot to the release (write-once)
        run: |
          # Never --clobber: a re-run must not replace the snapshot consumers
          # already resolved. This check makes the refusal explicit; a plain
          # upload also refuses a duplicate name on its own.
          if gh release view "$TAG" --json assets -q '.assets[].name' \
               | grep -Fxq '<lib>.abicheck.json'; then
            echo "::error::<lib>.abicheck.json already exists on $TAG. Baselines are write-once. To re-baseline, delete that asset deliberately (gh release delete-asset $TAG <lib>.abicheck.json) and re-run." >&2
            exit 1
          fi
          gh release upload "$TAG" <lib>.abicheck.json
        env:
          GH_TOKEN: ${{ github.token }}
```

**Bootstrap:** after merging, run this workflow once by hand
(`gh workflow run abi-baseline.yml -f tag=<latest-tag>`) so the latest
existing release gets a snapshot. The run fails if that release already has
the asset; that is the write-once guard, not a fault to route around. Until a
snapshot is attached, the PR check has nothing to fetch and fails — state that
in the setup report as a required step. The check-then-upload step can race
with a second run for the same tag. The losing run's upload then fails loudly
and the winner's asset stands, so nothing is replaced silently.

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

      - run: <build-steps>      # one run step per build command, or a `run: |` block

      - name: Compare against the last release
        uses: abicheck/abicheck@v0.7.0
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
      - uses: abicheck/abicheck@v0.7.0
        with:
          old-library: baseline/${{ matrix.lib }}.abicheck.json
          new-library: ${{ matrix.path }}
          new-header: ${{ matrix.headers }}
```

If the matrix build is expensive, build once in a separate job and pass the
build tree with `actions/upload-artifact`/`download-artifact`, rather than
rebuilding per library.

## Baseline-set variant (one archive per contract profile)

Use this only when the repository already builds each contract profile and
writes a `build-output.json` for it. The publisher works from that build
output, not from a bare `.so` and headers, so a repository without contract
profiles stays on the single-snapshot template above.

**Release side.** Call the reusable publisher instead of writing an upload
step. It dumps every library of each profile, packs one archive per profile,
and applies the write-once rule itself: differing content fails the run, and
nothing is clobbered. Its reference is
[`publish-baseline.yml`](../../../docs/reference/publish-baseline.md).

```yaml
  publish-baseline:
    needs: <build-job>          # each profile's build uploads an abicheck-build-<profile-id> artifact
    permissions:
      contents: write           # this job alone writes the release asset
    uses: abicheck/abicheck/.github/workflows/publish-baseline.yml@<sha>  # v0.7.0
    with:
      build-output-artifact-prefix: abicheck-build-
      build-config: .abicheck.yml   # needed even when the file sits at the repository root
```

**PR side.** When `baseline-profile` and `baseline-target` are both set, the
root Action reads the target's snapshot out of the release archive:

```yaml
      - name: Compare against the latest release baseline set
        uses: abicheck/abicheck@v0.7.0
        with:
          abi-baseline: latest-release
          baseline-profile: <profile-id>
          baseline-target: <lib>        # the library name in the archive's manifest.json
          new-library: <lib-path>
          new-header: <public-headers>
          lang: <lang>
          new-version: ${{ github.event.pull_request.head.sha || github.sha }}
          fail-on-breaking: 'false'
          fail-on-api-break: 'false'
```

`abi-baseline` selects the archive by its asset name. It does not check the
manifest's generation. Add `baseline-generation: <generation>` only when the
publisher's `asset-name-template` contains `{generation}`.

**Explicit resolution.** When a stale generation, a wrong profile, or a
missing baseline must fail with its own typed outcome, stage the archive and
resolve it with [`actions/resolve-baseline`](../../../docs/reference/resolve-baseline.md)
(every outcome other than resolved fails the step, so none reaches the compare
as an empty or stale baseline), then compare against the snapshot it returns:

```yaml
      - name: Fetch the latest baseline-set archive
        run: gh release download --pattern 'abicheck-baseline-<profile-id>.tar.zst' --dir baseline
        env:
          GH_TOKEN: ${{ github.token }}

      - name: Resolve <lib>'s baseline
        id: baseline
        uses: abicheck/abicheck/actions/resolve-baseline@v0.7.0
        with:
          baseline-path: baseline/abicheck-baseline-<profile-id>.tar.zst
          channel: release-contract
          target: <lib>
          profile: <profile-id>
          expected-baseline-generation: <generation>   # omit to skip the generation check

      - name: ABI compatibility check
        uses: abicheck/abicheck@v0.7.0
        with:
          old-library: ${{ steps.baseline.outputs.snapshot-path }}
          new-library: <lib-path>
          new-header: <public-headers>
          lang: <lang>
          new-version: ${{ github.event.pull_request.head.sha || github.sha }}
          fail-on-breaking: 'false'
          fail-on-api-break: 'false'
```

**Refreshing after a scanner upgrade.** Do not re-run a release to change
the baseline: the asset is write-once. Set `baseline-generation` on the
publisher, and put `{generation}` in its `asset-name-template` (for example
`abicheck-baseline-g{generation}-{profile}.tar.zst`), so the new generation is
a separate asset beside the old one. Then move the PR job's
`baseline-generation` (or `expected-baseline-generation`) to the same value.
See [scanner upgrades and baseline generations](../../../docs/use/baseline-management.md#scanner-upgrades-and-baseline-generations).
