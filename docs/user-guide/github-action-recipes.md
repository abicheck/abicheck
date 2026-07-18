# GitHub Action: More Recipes

A grab-bag of `abicheck/abicheck` workflow recipes beyond the basics in
[GitHub Action](github-action.md): caching, SARIF, cross-compilation,
multi-library/multi-platform matrices, dependency/appcompat checks, PR
comments, and package-comparison modes.

> Split out of [GitHub Action](github-action.md), which covers quick
> start, inputs/outputs, and the three core usage examples.

## Use GitHub Actions cache for baseline

```yaml
      - name: Restore cached baseline
        uses: actions/cache@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-

      - name: Check ABI
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## SARIF with GitHub Code Scanning

Upload results to the Security tab so ABI breaks appear as code scanning alerts.

!!! note
    Requires `security-events: write` permission. On PRs, GitHub only shows
    **new** alerts introduced by the PR — existing alerts stay on the default
    branch and don't clutter the review.

```yaml
jobs:
  abi-check:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - run: mkdir build && cd build && cmake .. && make

      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          format: sarif
          upload-sarif: true
```

## Cross-compilation check (dump mode)

Cross-compilation flags (`gcc-prefix`, `sysroot`, `gcc-options`) are only supported
in `dump` mode. Use `mode: dump` to generate a baseline from a cross-compiled binary,
then compare with a separate step.

```yaml
      # Step 1: dump ABI snapshot from cross-compiled binary
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build-arm64/libfoo.so
          header: include/foo.h
          gcc-prefix: aarch64-linux-gnu-
          sysroot: /usr/aarch64-linux-gnu
          lang: c
          output-file: baseline-arm64.json
```

## Matrix: multiple libraries

```yaml
    strategy:
      matrix:
        lib:
          - { name: libfoo, so: build/libfoo.so, header: include/foo.h }
          - { name: libbar, so: build/libbar.so, header: include/bar.h }
    steps:
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baselines/${{ matrix.lib.name }}.json
          new-library: ${{ matrix.lib.so }}
          new-header: ${{ matrix.lib.header }}
```

If the release also carries build-emitted source facts from one shared
`abicheck_inputs/` pack, see [Source Scans → Recommended flow: a
multi-library release with one shared facts
pack](github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for the full walkthrough — it chains this recipe with inline `build-info`
embedding and the [post-matrix ABI gate](#post-matrix-abi-gate-unified-verdict)
below.

## Matrix: multiple platforms (native scan per OS)

Use native runners to get the best platform-specific signal (Linux/ELF, macOS/Mach-O, Windows/PE):

```yaml
jobs:
  abi-scan:
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
            ext: so
          - os: macos-latest
            ext: dylib
          - os: windows-latest
            ext: dll
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      # Build your platform artifact here (example command only)
      - name: Build
        run: |
          echo "build on ${{ matrix.os }}"

      - name: ABI compare (native)
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baselines/${{ runner.os }}/abi-old.json
          new-library: build/${{ runner.os }}/libfoo.${{ matrix.ext }}
          new-header: include/foo.h
          format: json
          output-file: abi-report-${{ runner.os }}.json

      - name: Upload platform ABI report
        uses: actions/upload-artifact@v4
        with:
          name: abi-report-${{ runner.os }}
          path: abi-report-${{ runner.os }}.json
```

## Post-matrix ABI gate (fan-out builds, fan-in verdict)

Each platform builds and compares on its own matrix leg (fan-out) and uploads
a JSON report; a gate job downloads them all and folds them into one verdict
(fan-in) with **`abicheck aggregate`**:

```yaml
jobs:
  abi-scan:
    strategy:
      fail-fast: false            # don't cancel other legs when one fails
      matrix:
        include:
          - target: linux-x86_64
            os: ubuntu-latest
            ext: so
          - target: macos-arm64
            os: macos-latest
            ext: dylib
          - target: windows-x86_64
            os: windows-latest
            ext: dll
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - name: Build
        run: cmake -B build && cmake --build build

      - name: ABI compare (native)
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baselines/${{ matrix.target }}/abi-old.json
          new-library: build/libfoo.${{ matrix.ext }}
          new-header: include/foo.h
          format: json
          output-file: abi-report-${{ matrix.target }}.json
          fail-on-breaking: false   # let the gate job decide

      - name: Upload platform ABI report
        if: ${{ always() }}         # upload even if the build/compare failed
        uses: actions/upload-artifact@v4
        with:
          name: abi-report-${{ matrix.target }}
          path: abi-report-${{ matrix.target }}.json
          if-no-files-found: ignore

  abi-gate:
    needs: abi-scan
    if: ${{ always() }}             # run the gate even if a matrix leg failed
    runs-on: ubuntu-latest
    steps:
      - name: Download all ABI reports
        uses: actions/download-artifact@v4
        with:
          pattern: abi-report-*
          merge-multiple: true
          path: abi-reports/

      - name: Aggregate verdicts and gate
        run: |
          pip install abicheck --quiet
          abicheck aggregate abi-reports/ \
            --expect linux-x86_64,macos-arm64,windows-x86_64
```

`--expect` is the target set the matrix was supposed to produce — the same
list the matrix `include:` block defines, so the two never drift. `aggregate`
then guarantees the property the old hand-written gate loop silently violated:

* **A required target with no report is _unavailable_ (unknown), never counted
  as compatible.** If the Windows leg fails before uploading
  `abi-report-windows-x86_64.json`, the gate reports Windows as unavailable and
  exits non-zero — it does **not** pass green as "all platforms compatible"
  when a required platform was never analyzed.
* **Findings and coverage are separate.** The exit code follows `compare`'s
  scheme over the *analyzed* targets (`0` compatible / `2` source break / `4`
  ABI break), and — under the default `--on-missing-required fail` — incomplete
  required coverage also fails the gate at `4`. A build-infrastructure failure
  on one leg is reported as a coverage gap, never as a fake ABI regression.

!!! tip
    Set `fail-on-breaking: false` in each matrix job and let the gate decide.
    Use `fail-fast: false` on the matrix and `if: ${{ always() }}` on the
    upload step and the gate job so one failed leg neither cancels the others
    nor skips the fan-in. Pass `--on-missing-required warn` to `aggregate` if
    you want a missing required target to be reported but not fail the gate
    (findings alone then decide the exit code); mark a target `--optional`
    if its absence should never fail coverage.

Sample output when the Windows leg failed to produce a report:

```text
ABI aggregate: Partial
Analyzed 2 of 3 required targets

  linux-x86_64: COMPATIBLE
  macos-arm64: COMPATIBLE
  windows-x86_64: ⚠ unavailable — no report was produced for this expected target

Findings:
  No ABI regressions in the analyzed targets.
Coverage:
  Incomplete — unknown on: windows-x86_64.
```

Add `--format json` for a machine-readable result (per-target `analyzed`/
`verdict`, `findings_verdict`, `coverage`) to post elsewhere.

## Skip system dependency installation

If `castxml` + compiler are already available (custom image, pre-provisioned VM,
or conda-forge environment), set `install-deps: false`:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: old.json
          new-library: new.json
          install-deps: false
```

Example (conda-forge pre-step):

```yaml
      - name: Install abicheck from conda-forge
        run: |
          conda install -y -c conda-forge abicheck

      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: old.json
          new-library: new.json
          install-deps: false
```

When comparing two JSON snapshots, no header-analysis toolchain is needed.

## Full-stack dependency check on container image update

Validate that updating a base image doesn't break your application's dependency
stack. This runs `deps-compare` to compare the binary's full transitive
dependency tree across old and new container root filesystems:

```yaml
jobs:
  deps-compare:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Extract old rootfs
        run: |
          mkdir -p /tmp/old-root
          docker export $(docker create old-image:latest) | tar -xf - -C /tmp/old-root

      - name: Extract new rootfs
        run: |
          mkdir -p /tmp/new-root
          docker export $(docker create new-image:latest) | tar -xf - -C /tmp/new-root

      - name: Full-stack ABI check
        uses: abicheck/abicheck@v0.3.0
        with:
          mode: deps-compare
          new-library: usr/bin/myapp
          old-root: /tmp/old-root
          new-root: /tmp/new-root
          format: json
          output-file: stack-report.json
```

Exit codes for `deps-compare`: `0` = PASS, `1` = WARN (ABI risk), `4` = FAIL (load failure or ABI break).

## Dependency tree audit

Show the resolved dependency tree and symbol binding status for a binary.
Useful for auditing which libraries a binary actually loads and detecting
missing dependencies before deployment:

```yaml
      - name: Audit dependencies
        uses: abicheck/abicheck@v0.3.0
        with:
          mode: deps-tree
          new-library: build/myapp
          sysroot: /path/to/target/rootfs
```

## Include dependency info in compare

Add `follow-deps: true` to include the transitive dependency graph and symbol
binding information alongside the regular ABI diff:

```yaml
      - name: Compare with dependency context
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          follow-deps: true
```

## Inline PR annotations

Add `--annotate` to get ABI breaking changes as inline comments on the PR diff.
See [GitHub PR Annotations](annotations.md) for full details.

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          extra-args: --annotate
```

## Sticky PR comment

On `pull_request` runs the action posts a single, self-updating comment that
groups every finding into **Breaking**, **Needs review**, and **Safe** sections
and shows the scanned head SHA. It is a *content* channel only — it never
changes the check's red/green state, which is still driven by `fail-on-breaking`
/ `fail-on-api-break` / `severity-*`. This means review-needed items (source
breaks, risk, additions) surface as a green check with a `⚠️ Review recommended`
comment, while real ABI breaks turn the check red **and** post a `❌` comment.

```yaml
permissions:
  contents: read
  pull-requests: write   # required for the comment
jobs:
  abi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          # all optional — these are the defaults:
          pr-comment: true
          pr-comment-mode: update      # one sticky comment, edited each run
          pr-comment-on: changes       # skip the comment when nothing changed
          pr-comment-detail: standard  # per-symbol tables for breaking/review
```

Behavior knobs:

- `pr-comment-mode: new` posts a fresh comment per run instead of editing the
  previous one (use when you want a per-commit history in the thread).
- `pr-comment-on: always` comments every run, including a clean *No ABI changes*
  result; `never` disables it.
- `pr-comment-detail: full` lists every change with source locations and expands
  all sections; `summary` reduces the comment to the verdict and counts.

On large diffs the `standard` view stays readable by rolling related changes up
to their enclosing API — overloads, template instantiations and members of the
same type/namespace collapse into one row showing the family and a member count
(distinct symbols keep their own row; `full` keeps every change separate). The
body is always kept under GitHub's 65,536-character comment limit: if it would
overflow, the detail level is automatically reduced (and, as a last resort, the
body is truncated), with a link back to the **full report** uploaded as the
workflow-run artifact so nothing is lost.

"Safe" mirrors whatever the checker already classified as compatible — so
public-header surface scoping (`--scope-public-headers`) and policy profiles
(e.g. `sdk_vendor` demoting a removal) flow through automatically; the comment
never re-classifies anything.

The comment also tracks the gate: with `fail-on-api-break: true` (which turns
the check red on source/API breaks), those findings are filed under **Breaking**
in the comment to match, rather than **Needs review**.

## Conditional failure

Allow API breaks but block binary ABI breaks:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          fail-on-breaking: true
          fail-on-api-break: false
```

## Detect unintentional API expansion

Block PRs that accidentally add new public symbols or types:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          fail-on-breaking: true
          severity-addition: error   # exit code 1 if any new public API appears
```

When `severity-addition: error`:
- Exit code `1` → new public symbol/type added (`verdict: SEVERITY_ERROR`)
- Exit code `0` → no additions, no breaks (`verdict: COMPATIBLE`)
- Exit code `4` → binary ABI break (`verdict: BREAKING`)

This is useful when your library has a stable frozen API and any expansion
must be a deliberate, reviewed decision rather than an accidental side effect.

## Compare RPM packages

`old-library`/`new-library` may be directories or packages instead of a
single library each — `compare` (the default mode) detects this and fans
out to a per-library comparison automatically, no separate mode needed.
Supported formats: RPM, Deb, tar (`.tar.gz`, `.tar.xz`, `.tar.bz2`, `.tgz`),
conda (`.conda`, `.tar.bz2`), wheel (`.whl`), and plain directories.

```yaml
      - name: Compare RPM packages
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: libfoo-1.0-1.el9.x86_64.rpm
          new-library: libfoo-1.1-1.el9.x86_64.rpm
```

## Compare packages with debug info

Provide separate debug info packages for full type-level analysis via
build-id resolution:

```yaml
      - name: Compare with debug info
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: libfoo-1.0.rpm
          new-library: libfoo-1.1.rpm
          debug-info1: libfoo-debuginfo-1.0.rpm
          debug-info2: libfoo-debuginfo-1.1.rpm
```

## Compare Deb packages with development headers

```yaml
      - name: Compare Deb packages
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: libfoo1_1.0-1_amd64.deb
          new-library: libfoo1_1.1-1_amd64.deb
          devel-pkg1: libfoo-dev_1.0-1_amd64.deb
          devel-pkg2: libfoo-dev_1.1-1_amd64.deb
```

## Compare tar archives (DSOs only)

```yaml
      - name: Compare SDK tarballs
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: sdk-2.0.tar.gz
          new-library: sdk-2.1.tar.gz
          dso-only: true
```

## Compare conda packages

```yaml
      - name: Compare conda packages
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: pkg-v1.conda
          new-library: pkg-v2.conda
```

## Application compatibility check

There is no separate `appcompat` mode (ADR-043 folded it into `compare
--used-by`). Check whether your application binary is affected by a library
update by scoping a normal `compare` to it via `extra-args`:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: libfoo.so.1
          new-library: build/libfoo.so.2
          header: include/foo.h
          extra-args: '--used-by build/myapp'
```

## Quick symbol availability check (weak mode)

Verify a library provides all symbols an application needs by comparing it
against itself (no real ABI change) — the app-scoped verdict reports
COMPATIBLE only if every symbol it uses resolves:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          old-library: build/libfoo.so
          new-library: build/libfoo.so
          install-deps: false
          extra-args: '--used-by build/myapp'
```
