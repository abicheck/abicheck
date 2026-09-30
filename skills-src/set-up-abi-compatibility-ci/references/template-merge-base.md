# Template B — merge-base build

Replace every `<placeholder>` with a fact from the repository: `<lib>`
(`libfoo`), `<lib-path>` (exact built file, e.g. `build/libfoo.so`),
`<public-headers>` (`include/`), `<lang>` (`c`/`c++`), `<build-steps>` (the
repository's own install + configure + build, with debug info and a shared
build on both sides).

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
[template A](template-release-baseline.md) once the project cuts releases.

If the base commit predates the library (the PR that introduces it), the
base build has no `<lib-path>`: guard the compare step with a file-existence
check and say so in the summary rather than failing.
