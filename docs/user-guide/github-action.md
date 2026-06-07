# GitHub Action

abicheck ships as a reusable GitHub Action that you can add to any CI pipeline
with a few lines of YAML. It installs Python, system dependencies, and abicheck
automatically, then runs ABI comparison and reports results.

> **Picking a mode or failure policy?** See
> [Choose Your Workflow](choose-your-workflow.md) for the decision matrix —
> which artifacts map to which `mode`, and which severity inputs gate the build.

## Quick start

```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
```

## Inputs

### Library inputs

| Input | Required | Description |
|-------|----------|-------------|
| `mode` | no | `compare` (default), `compare-release`, `dump`, `appcompat`, `deps`, or `stack-check` |
| `old-library` | yes (compare, compare-release) | Path to old library, JSON snapshot, ABICC dump, directory, or package |
| `new-library` | yes | Path to new library, binary, directory, or package |

### Header inputs

| Input | Required | Description |
|-------|----------|-------------|
| `header` | no | Public header file(s) or directory(ies) for both sides (space-separated) |
| `old-header` | no | Header file(s) or directory(ies) for old side only |
| `new-header` | no | Header file(s) or directory(ies) for new side only |
| `include` | no | Extra include dirs for castxml (both sides) |
| `old-include` | no | Include dirs for old side only |
| `new-include` | no | Include dirs for new side only |

### Application compatibility inputs (appcompat mode)

| Input | Required | Description |
|-------|----------|-------------|
| `app-binary` | yes (appcompat) | Path to application binary (ELF, PE, or Mach-O) |
| `check-against` | no | Library for weak-mode symbol availability check (no old library needed) |
| `show-irrelevant` | no | Include library changes not affecting the application (default `false`) |
| `list-required-symbols` | no | List symbols the app requires and exit (default `false`) |

### Version labels

| Input | Default | Description |
|-------|---------|-------------|
| `old-version` | `old` | Version label for old library |
| `new-version` | `new` | Version label for new library |

### Language and compiler

| Input | Default | Description |
|-------|---------|-------------|
| `lang` | `c++` | Language mode: `c++` or `c` |
| `gcc-path` | — | Path to cross-compiler binary (dump mode only) |
| `gcc-prefix` | — | Cross-toolchain prefix, e.g. `aarch64-linux-gnu-` (dump mode only) |
| `gcc-options` | — | Extra flags for castxml (dump mode only) |
| `sysroot` | — | Alternative system root (dump and deps modes) |
| `nostdinc` | `false` | Skip standard include paths (dump mode only) |

### Full-stack dependency validation (Linux ELF)

| Input | Default | Description |
|-------|---------|-------------|
| `follow-deps` | `false` | Include transitive dependency graph and symbol bindings in dump/compare output |
| `baseline` | — | Sysroot for baseline environment (required for `stack-check` mode) |
| `candidate` | — | Sysroot for candidate environment (required for `stack-check` mode) |
| `search-path` | — | Additional library search directories (space-separated) |
| `ld-library-path` | — | Simulated `LD_LIBRARY_PATH` (colon-separated) |

### Output and policy

| Input | Default | Description |
|-------|---------|-------------|
| `format` | `markdown` | Output format: `markdown`, `json`, `sarif`, `html`. `sarif`/`html` only supported in compare mode; other modes fall back to `markdown` |
| `output-file` | — | Path to write report (auto-set for SARIF) |
| `policy` | `strict_abi` | Built-in policy: `strict_abi`, `sdk_vendor`, `plugin_abi` |
| `policy-file` | — | Custom YAML policy file |
| `suppress` | — | YAML suppression file (supports `label`, `source_location`, `expires`) |
| `verbose` | `false` | Enable debug output |

To enable suppression lifecycle enforcement, pass the flags via `extra-args`:

```yaml
extra-args: '--strict-suppressions --require-justification'
```

### Action behavior

| Input | Default | Description |
|-------|---------|-------------|
| `python-version` | `3.13` | Python version for setup-python |
| `install-deps` | `true` | Install castxml + gcc automatically |
| `upload-sarif` | `false` | Upload SARIF to GitHub Code Scanning |
| `fail-on-breaking` | `true` | Fail step on binary ABI break |
| `fail-on-api-break` | `false` | Fail step on source-level API break |
| `severity-preset` | — | Severity preset: `default`, `strict`, or `info-only` (compare mode only) |
| `severity-addition` | — | Severity for additions: `error`, `warning`, or `info` (compare mode only) |
| `extra-args` | `''` | Additional CLI arguments passed to abicheck |
| `add-job-summary` | `true` | Write summary to Job Summary panel (ignored for dump mode) |

### Package comparison inputs (compare-release mode)

| Input | Default | Description |
|-------|---------|-------------|
| `debug-info1` | — | Debug info package for old side (RPM/Deb/tar) |
| `debug-info2` | — | Debug info package for new side (RPM/Deb/tar) |
| `devel-pkg1` | — | Development package with headers for old side |
| `devel-pkg2` | — | Development package with headers for new side |
| `dso-only` | `false` | Only compare shared objects, skip executables |
| `include-private-dso` | `false` | Include private (non-public) shared objects |
| `keep-extracted` | `false` | Keep extracted temp files for debugging |
| `fail-on-removed-library` | `false` | Exit 8 when a library present in old is absent in new |

## Outputs

| Output | Description |
|--------|-------------|
| `verdict` | **compare:** `COMPATIBLE`, `SEVERITY_ERROR`, `API_BREAK`, `BREAKING`, or `ERROR`. **compare-release:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, `REMOVED_LIBRARY`, or `ERROR`. **appcompat:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, or `ERROR`. **dump:** `COMPATIBLE` or `ERROR`. **stack-check:** `PASS`, `WARN`, `FAIL`, or `ERROR`. **deps:** `PASS`, `FAIL`, or `ERROR`. |
| `exit-code` | **compare:** `0` (compatible), `1` (severity error), `2` (API break), `4` (ABI break). **compare-release:** `0` (compatible), `2` (API break), `4` (ABI break), `8` (library removed). **appcompat:** `0` (compatible), `2` (API break), `4` (ABI break). **stack-check:** `0` (pass), `1` (warn), `4` (fail). **deps:** `0` (ok), `1` (missing). |
| `report-path` | Path to the generated report file (empty when no output file was produced) |

## Usage examples

### Compare two libraries on a PR

```yaml
name: ABI Check
on: [pull_request]

jobs:
  abi-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build library
        run: mkdir build && cd build && cmake .. && make

      - name: Check ABI compatibility
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json  # committed to repo
          new-library: build/libfoo.so
          new-header: include/foo.h
          new-version: pr-${{ github.event.pull_request.number }}
```

### Save a baseline on release

The baseline is a JSON snapshot of the library's ABI surface. Generate it when
you release a version, then compare against it on every PR.

```yaml
name: ABI Baseline
on:
  release:
    types: [published]

jobs:
  save-baseline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build library
        run: mkdir build && cd build && cmake .. && make

      - name: Dump ABI baseline
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build/libfoo.so
          header: include/foo.h
          new-version: ${{ github.ref_name }}
          output-file: abi-baseline.json

      - name: Upload baseline as release asset
        uses: softprops/action-gh-release@v2
        with:
          files: abi-baseline.json
```

### Download baseline and compare on PR

```yaml
      - name: Download baseline from latest release
        run: gh release download --pattern 'abi-baseline.json' --dir .
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Check ABI
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

### Use GitHub Actions cache for baseline

```yaml
      - name: Restore cached baseline
        uses: actions/cache@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-

      - name: Check ABI
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

### SARIF with GitHub Code Scanning

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

      - uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          format: sarif
          upload-sarif: true
```

### Cross-compilation check (dump mode)

Cross-compilation flags (`gcc-prefix`, `sysroot`, `gcc-options`) are only supported
in `dump` mode. Use `mode: dump` to generate a baseline from a cross-compiled binary,
then compare with a separate step.

```yaml
      # Step 1: dump ABI snapshot from cross-compiled binary
      - uses: napetrov/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build-arm64/libfoo.so
          header: include/foo.h
          gcc-prefix: aarch64-linux-gnu-
          sysroot: /usr/aarch64-linux-gnu
          lang: c
          output-file: baseline-arm64.json
```

### Matrix: multiple libraries

```yaml
    strategy:
      matrix:
        lib:
          - { name: libfoo, so: build/libfoo.so, header: include/foo.h }
          - { name: libbar, so: build/libbar.so, header: include/bar.h }
    steps:
      - uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baselines/${{ matrix.lib.name }}.json
          new-library: ${{ matrix.lib.so }}
          new-header: ${{ matrix.lib.header }}
```

### Matrix: multiple platforms (native scan per OS)

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
        uses: napetrov/abicheck@v0.3.0
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

### Post-matrix ABI gate (unified verdict)

After per-platform matrix runs, a gate job downloads all JSON reports and
produces one aggregated exit code for the entire workflow:

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

      - name: Build
        run: cmake -B build && cmake --build build

      - name: ABI compare (native)
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baselines/${{ runner.os }}/abi-old.json
          new-library: build/libfoo.${{ matrix.ext }}
          new-header: include/foo.h
          format: json
          output-file: abi-report-${{ runner.os }}.json
          fail-on-breaking: false   # let gate job decide

      - name: Upload platform ABI report
        uses: actions/upload-artifact@v4
        with:
          name: abi-report-${{ runner.os }}
          path: abi-report-${{ runner.os }}.json

  abi-gate:
    needs: abi-scan
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
          python3 - <<'PYEOF'
          import json, sys, os, glob

          SEVERITY = {"NO_CHANGE": 0, "COMPATIBLE": 0, "COMPATIBLE_WITH_RISK": 0,
                      "API_BREAK": 2, "BREAKING": 4, "ERROR": 4}

          worst = 0
          rows = []
          for path in sorted(glob.glob("abi-reports/*.json")):
              with open(path) as f:
                  data = json.load(f)
              verdict = data.get("verdict", "ERROR")
              platform = os.path.basename(path).replace("abi-report-", "").replace(".json", "")
              rows.append(f"| {platform} | {verdict} |")
              worst = max(worst, SEVERITY.get(verdict, 4))

          table = "\n".join(rows)
          print(f"## ABI Gate\n\n| Platform | Verdict |\n|---|---|\n{table}")

          if worst >= 4:
              print("BREAKING ABI change detected on at least one platform.", file=sys.stderr)
              sys.exit(4)
          elif worst >= 2:
              print("API break detected on at least one platform.", file=sys.stderr)
              sys.exit(2)
          print("All platforms: compatible.")
          PYEOF
```

!!! tip
    Set `fail-on-breaking: false` in each matrix job so runners don't fail
    early. The gate job reads all JSON reports and exits `4` (breaking),
    `2` (API break), or `0` (compatible).

### Skip system dependency installation

If `castxml` + compiler are already available (custom image, pre-provisioned VM,
or conda-forge environment), set `install-deps: false`:

```yaml
      - uses: napetrov/abicheck@v0.3.0
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

      - uses: napetrov/abicheck@v0.3.0
        with:
          old-library: old.json
          new-library: new.json
          install-deps: false
```

When comparing two JSON snapshots, no header-analysis toolchain is needed.

### Full-stack dependency check on container image update

Validate that updating a base image doesn't break your application's dependency
stack. This runs `stack-check` to compare the binary's full transitive
dependency tree across old and new container root filesystems:

```yaml
jobs:
  stack-check:
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
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: stack-check
          new-library: usr/bin/myapp
          baseline: /tmp/old-root
          candidate: /tmp/new-root
          format: json
          output-file: stack-report.json
```

Exit codes for `stack-check`: `0` = PASS, `1` = WARN (ABI risk), `4` = FAIL (load failure or ABI break).

### Dependency tree audit

Show the resolved dependency tree and symbol binding status for a binary.
Useful for auditing which libraries a binary actually loads and detecting
missing dependencies before deployment:

```yaml
      - name: Audit dependencies
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: deps
          new-library: build/myapp
          sysroot: /path/to/target/rootfs
```

### Include dependency info in compare

Add `follow-deps: true` to include the transitive dependency graph and symbol
binding information alongside the regular ABI diff:

```yaml
      - name: Compare with dependency context
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          follow-deps: true
```

### Inline PR annotations

Add `--annotate` to get ABI breaking changes as inline comments on the PR diff.
See [GitHub PR Annotations](annotations.md) for full details.

```yaml
      - uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          extra-args: --annotate
```

### Conditional failure

Allow API breaks but block binary ABI breaks:

```yaml
      - uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          fail-on-breaking: true
          fail-on-api-break: false
```

### Detect unintentional API expansion

Block PRs that accidentally add new public symbols or types:

```yaml
      - uses: napetrov/abicheck@v0.3.0
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

### Compare RPM packages

Use `mode: compare-release` to compare all shared libraries inside two packages
without manual extraction. Supported formats: RPM, Deb, tar (`.tar.gz`,
`.tar.xz`, `.tar.bz2`, `.tgz`), conda (`.conda`, `.tar.bz2`), wheel (`.whl`),
and plain directories.

```yaml
      - name: Compare RPM packages
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: libfoo-1.0-1.el9.x86_64.rpm
          new-library: libfoo-1.1-1.el9.x86_64.rpm
```

### Compare packages with debug info

Provide separate debug info packages for full type-level analysis via
build-id resolution:

```yaml
      - name: Compare with debug info
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: libfoo-1.0.rpm
          new-library: libfoo-1.1.rpm
          debug-info1: libfoo-debuginfo-1.0.rpm
          debug-info2: libfoo-debuginfo-1.1.rpm
```

### Compare Deb packages with development headers

```yaml
      - name: Compare Deb packages
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: libfoo1_1.0-1_amd64.deb
          new-library: libfoo1_1.1-1_amd64.deb
          devel-pkg1: libfoo-dev_1.0-1_amd64.deb
          devel-pkg2: libfoo-dev_1.1-1_amd64.deb
```

### Compare tar archives (DSOs only)

```yaml
      - name: Compare SDK tarballs
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: sdk-2.0.tar.gz
          new-library: sdk-2.1.tar.gz
          dso-only: true
```

### Compare conda packages

```yaml
      - name: Compare conda packages
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: pkg-v1.conda
          new-library: pkg-v2.conda
```

### Application compatibility check

Check whether your application binary is affected by a library update:

```yaml
      - uses: napetrov/abicheck@v0.3.0
        with:
          mode: appcompat
          app-binary: build/myapp
          old-library: libfoo.so.1
          new-library: build/libfoo.so.2
          header: include/foo.h
```

### Quick symbol availability check (weak mode)

Verify a library provides all symbols an application needs — no old library required:

```yaml
      - uses: napetrov/abicheck@v0.3.0
        with:
          mode: appcompat
          app-binary: build/myapp
          check-against: build/libfoo.so
          install-deps: false
```

## Versioning

The action follows [semantic versioning](https://semver.org/). While abicheck
is pre-1.0, pin an exact release tag (the examples in this guide use the latest,
`v0.3.0`); a floating major tag is not published yet:

```yaml
uses: napetrov/abicheck@v0.3.0     # exact release tag (recommended, reproducible)
uses: napetrov/abicheck@abc123def  # exact commit SHA (most secure)
```

Released tags are listed on the
[Releases page](https://github.com/napetrov/abicheck/releases). Once abicheck
reaches a stable `1.0`, a floating `v1` major tag updated on each patch/minor
release will become the recommended pin.
