# GitHub Action

abicheck ships as a reusable GitHub Action that you can add to any CI pipeline
with a few lines of YAML. It installs Python, system dependencies, and abicheck
automatically, then runs ABI comparison and reports results.

> **Picking a mode or failure policy?** See
> [Choose Your Workflow](choose-your-workflow.md) for the decision matrix —
> which artifacts map to which `mode`, and which severity inputs gate the build.

## Quick start

```yaml
- uses: abicheck/abicheck@v0.3.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
```

## Inputs

### Library inputs

| Input | Required | Description |
|-------|----------|-------------|
| `mode` | no | `compare` (default), `compare-release`, `dump`, `scan`, `merge`, `appcompat`, `deps`, or `stack-check` |
| `old-library` | yes (compare, compare-release) | Path to old library, JSON snapshot, ABICC dump, directory, or package |
| `new-library` | yes (compare, dump, scan, …) | Path to new library, binary, directory, or package. In `scan` mode this is the scanned binary or `.abi.json` snapshot. |

### Header inputs

| Input | Required | Description |
|-------|----------|-------------|
| `header` | no | Public header file(s) or directory(ies) for both sides (space-separated) |
| `old-header` | no | Header file(s) or directory(ies) for old side only |
| `new-header` | no | Header file(s) or directory(ies) for new side only |
| `include` | no | Extra include dirs for castxml (both sides) |
| `old-include` | no | Include dirs for old side only |
| `new-include` | no | Include dirs for new side only |

!!! note "Evidence layers in the Action"
    The Action drives the same [five-layer evidence
    model](../concepts/evidence-and-detectability.md) as the CLI. The inputs
    above cover **L0** (`old-library`/`new-library`), **L1** (debug info —
    embedded, or `debug-info1`/`debug-info2` packages in `compare-release`
    mode), and **L2** (`header`/`include`).

    The deeper layers — **L3** build context, **L4** source-ABI replay, and
    **L5** source graphs — are now first-class Action inputs. Use the
    `sources`/`build-info`/`compile-db` inputs in `scan` or `dump` mode and
    abicheck collects them inline; no separate CLI steps are required. See
    [Source scans](#source-scans-build-source-evidence) below and the
    [Build Info & Sources](../concepts/build-source-data.md) concept guide.

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
| `lang` | `c++` | Language mode for the header backend: `c++` or `c` |
| `ast-frontend` | `auto` (resolves to castxml when present) | L2 header-AST frontend (compare/dump modes): `auto`, `castxml`, or `clang` (`clang -ast-dump=json`, for clang-only hosts). `auto` falls back to clang on a castxml toolchain error. Same as `ABICHECK_AST_FRONTEND`. |
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

### Source-scan and build-source evidence (scan / dump / merge modes)

These inputs drive [source intelligence](../concepts/build-source-data.md) —
L3 build context, L4 source-ABI replay, and L5 source graphs — through the
`scan` orchestrator, or fold the same evidence into a `dump` snapshot. L4/L5
need `clang` (installed automatically by `install-deps: true`); without it the
scan degrades gracefully and L0–L2 stay authoritative.

| Input | Modes | Description |
|-------|-------|-------------|
| `sources` | scan, dump | Source checkout/tree; drives L4 replay and graph collection. With a source-level depth and no compile DB, `abicheck` auto-detects the build system (CMake/Bazel) and runs the query itself to emit one — no flag, no manual build. |
| `build-info` | scan, dump | Out-of-tree L3 context: a build dir, a `compile_commands.json`, or a collected evidence pack. |
| `compile-db` | scan (dump folds into `build-info`) | Explicit `compile_commands.json` path. |
| `build-config` | scan, dump | Trusted `.abicheck.yml`; its `build.query` runs automatically (operator-supplied = trusted). |
| `allow-build-query` | scan, dump | **Deprecated, ignored.** Build queries now run automatically when `sources` is given; kept as a no-op for backward compatibility. |
| `depth` | scan, dump | Evidence-depth dial: `binary`, `headers`, `build`, `source`, or `full`. Maps to `--depth`. Omit in scan mode for `auto` (risk-driven). |
| `baseline` | scan | Previous build's dump/library to compare against (or use `abi-baseline` to auto-fetch one). |
| `since` | scan | Focus the scan on files changed vs a git ref (e.g. `origin/main`). |
| `changed-path` | scan | Changed path(s) to focus on (space-separated; alternative to `since`). |
| `budget` | scan | Time guard (e.g. `15m`). The step **fails** on overflow (`verdict: BUDGET_OVERFLOW`) — a budget never silently shrinks scope. |
| `audit` | scan | Single-build hygiene lint, no baseline (intra-version cross-source checks). |
| `estimate` | scan | Dry-run: print projected per-layer cost and scan nothing (always exits 0). |
| `crosscheck` | scan | Per-check severity overrides `KEY=LEVEL` (`off`/`info`/`warning`/`error`), space-separated. Promoting a check to `=error` makes a finding for it exit `2` (the API_BREAK tier); pair with `fail-on-api-break: true` to gate the step. |
| `risk-rules` | scan | Path to a YAML file overriding the `risk_rules` profile. |
| `merge-inputs` | merge | Space-separated `.abi.json` dumps and/or a Wrapper-injection `abicheck_inputs/` pack directory to combine. |
| `on-conflict` | merge | `warn` (default, first-wins + diagnostic) or `error` (exit non-zero) when two inputs supply the same layer with differing facts. |

!!! note "format in scan mode"
    `scan` supports `format: text` (default) or `json`; other values fall back
    to `text` with a warning. `merge` writes a `.abi.json` baseline to
    `output-file` (default `merged-baseline.json`).

!!! tip "Consuming build-emitted source facts (wrapper / Clang plugin)"
    If your **product build** emits its own `abicheck_inputs/` pack — via the
    `abicheck-cc` compiler wrapper or the optional
    [Clang plugin](../concepts/build-source-data.md) (both write the identical
    schema) — the Action ingests it with **`mode: merge`**: pass the binary dump
    plus the pack directory in `merge-inputs`. The Action does not run the
    wrapper/plugin itself (that happens in your build); it folds the resulting
    pack into a baseline with no re-parse, exactly like the local
    `abicheck merge` flow.

### Output and policy

| Input | Default | Description |
|-------|---------|-------------|
| `format` | `markdown` (`text` for scan) | Output format: `markdown`, `json`, `sarif`, `html`. `sarif`/`html` are compare-only; `compare-release`/`appcompat`/`deps`/`stack-check` fall back to `markdown`; `scan` supports only `text`/`json` and falls back to `text`. |
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
| `pr-comment` | `true` | Post a sticky ABI report comment on the PR (compare/compare-release/appcompat). No-op outside `pull_request` events. |
| `pr-comment-mode` | `update` | `update` keeps one comment and edits it in place; `new` posts a fresh comment each run |
| `pr-comment-on` | `changes` | When to comment: `changes`, `always`, or `never` |
| `pr-comment-detail` | `standard` | Comment detail: `summary`, `standard`, or `full` |
| `github-token` | `${{ github.token }}` | Token for the PR comment and baseline auto-fetch (needs `pull-requests: write`) |

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
| `verdict` | **compare:** `COMPATIBLE`, `SEVERITY_ERROR`, `API_BREAK`, `BREAKING`, or `ERROR`. **compare-release:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, `REMOVED_LIBRARY`, or `ERROR`. **appcompat:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, or `ERROR`. **dump:** `COMPATIBLE` or `ERROR`. **scan:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, `BUDGET_OVERFLOW`, or `ERROR`. **merge:** `COMPATIBLE` or `ERROR`. **stack-check:** `PASS`, `WARN`, `FAIL`, or `ERROR`. **deps:** `PASS`, `FAIL`, or `ERROR`. |
| `exit-code` | **compare:** `0` (compatible), `1` (severity error), `2` (API break), `4` (ABI break). **compare-release:** `0` (compatible), `2` (API break), `4` (ABI break), `8` (library removed). **appcompat:** `0` (compatible), `2` (API break), `4` (ABI break). **scan:** `0` (compatible/advisory), `2` (API break), `4` (ABI break), `5` (budget overflow). **merge:** `0` (ok). **stack-check:** `0` (pass), `1` (warn), `4` (fail). **deps:** `0` (ok), `1` (missing). |
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
        uses: abicheck/abicheck@v0.3.0
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
        uses: abicheck/abicheck@v0.3.0
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
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## Source scans (build & source evidence)

`mode: scan` is the **one-step entry point** for source intelligence: it
classifies the PR's changed paths, runs the always-on pattern and cross-source
checks plus the pinned evidence depth (L3 build context / L4 source-ABI replay
/ L5 source graph), and — with a `baseline` — compares against it. The full
CI recipes — pinning `depth`, single-release audit, cost estimation,
cross-check gating, and the three ways to feed build/source evidence into a
baseline (`dump --sources`, `mode: merge`, build-emitted packs) — live on
their own page:

➡️ **[GitHub Action: Source Scans & Build Evidence](github-action-source-scans.md)**

## More usage recipes

Caching a baseline, SARIF upload, cross-compilation, multi-library/multi-platform
matrices, dependency/appcompat checks, PR-comment tuning, and the
`compare-release` package-comparison recipes (RPM/Deb/tar/conda) are on their
own page:

➡️ **[GitHub Action: More Recipes](github-action-recipes.md)**

## Versioning

The action follows [semantic versioning](https://semver.org/). While abicheck
is pre-1.0, pin an exact release tag (the examples in this guide use the latest,
`v0.3.0`); a floating major tag is not published yet:

```yaml
uses: abicheck/abicheck@v0.3.0     # exact release tag (recommended, reproducible)
uses: abicheck/abicheck@abc123def  # exact commit SHA (most secure)
```

Released tags are listed on the
[Releases page](https://github.com/abicheck/abicheck/releases). Once abicheck
reaches a stable `1.0`, a floating `v1` major tag updated on each patch/minor
release will become the recommended pin.
