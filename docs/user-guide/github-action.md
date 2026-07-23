# GitHub Action

abicheck ships as a reusable GitHub Action that you can add to any CI pipeline
with a few lines of YAML. It installs Python, system dependencies, and abicheck
automatically, then runs ABI comparison and reports results.

> **Picking a mode or failure policy?** See
> [Choose Your Workflow](choose-your-workflow.md) for the decision matrix —
> which artifacts map to which `mode`, and which severity inputs gate the build.

## Quick start

```yaml
- uses: abicheck/abicheck@v0.5.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
```

## Mode/input compatibility

Not every input is meaningful in every `mode`. The Action's first step
(`Validate mode/input combination`) checks the combinations below **before**
Python setup, system-dependency installation, or `pip install abicheck` —
an unsupported combination fails immediately with a clear error instead of
after a multi-minute toolchain install, and instead of silently falling
back to a different, unrequested behavior.

| Capability | `compare` | `dump` | `scan` | `deps-tree` / `deps-compare` |
|---|:--:|:--:|:--:|:--:|
| Single binary/snapshot | yes | yes | yes | yes |
| Directory/package (`new-library`/`old-library`) | yes (fans out per-library) | **error** | **error** | — |
| Source-only (no `new-library`, via `sources`/`build-info`/`compile-db`) | — | yes | — | — |
| `format: sarif` | yes (single pair only) | n/a (always JSON) | **error** | **error** |
| `format: html` | yes (single pair only) | n/a (always JSON) | **error** | yes (dependency-stack report) |
| `format: json` | yes | n/a (always JSON) | yes | yes |
| `format: markdown` / `text` | yes | n/a (always JSON) | `text` only | `markdown` only |
| `upload-sarif: true` | yes (needs `format: sarif`) | **error** | **error** | **error** |
| `pr-comment` | yes | no-op | no-op | no-op |

For a multi-library release directory (several `.so`/`.dll`/`.dylib` files),
use `mode: compare` with a directory/package operand — it fans out to a
per-library comparison automatically (see [Package comparison
inputs](#package-comparison-inputs-compare-mode-directorypackage-operands-only)
below). `dump` and `scan` have no such fan-out: dump each library
individually (one step per binary, or a matrix), and scan one representative
artifact at a time, or run `compare` for the binary side and `scan --sources`
separately for the source/API side (see [Choose Your
Workflow](choose-your-workflow.md) for weighing that split against a single
combined step). If the release also carries build-emitted source facts (a
shared `abicheck_inputs/` pack from one build), see [Source Scans →
Recommended flow: a multi-library release with one shared facts
pack](github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for the full matrix-dump-then-compare walkthrough.

## Inputs

The tables below group inputs by task, with just enough detail to pick the
right ones for your workflow. For the exhaustive, generated field-by-field
list (every input/output, its exact default, and full description straight
from `action.yml`), see the
[GitHub Action Inputs/Outputs Reference](../reference/github-action-inputs.md).

### Library inputs

| Input | Required | Description |
|-------|----------|-------------|
| `mode` | no | `compare` (default), `dump`, `scan`, `deps-tree`, or `deps-compare` |
| `old-library` | yes (compare) | Path to old library, JSON snapshot, ABICC dump, directory, or package (a directory/package fans out to a per-library comparison automatically — no separate mode) |
| `new-library` | yes (compare, dump\*, scan, deps-tree, deps-compare) | Path to new library, binary, or JSON snapshot. **Directory/package is `compare`-only** — `dump` and `scan` each analyse exactly one artifact and reject a directory/package with a fail-fast error, before any dependency install. \*`dump` may omit `new-library` entirely for a source-only dump (`sources`/`build-info`/`compile-db` given instead). See [Mode/input compatibility](#modeinput-compatibility) below. |

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
    embedded, or `debug-info1`/`debug-info2` packages when `old-library`/
    `new-library` are directories or packages), and **L2** (`header`/`include`).

    The deeper layers — **L3** build context, **L4** source-ABI replay, and
    **L5** source graphs — are now first-class Action inputs. Use the
    `sources`/`build-info`/`compile-db` inputs in `scan` or `dump` mode and
    abicheck collects them inline; no separate CLI steps are required. See
    [Source scans](#source-scans-build-source-evidence) below and the
    [Build Info & Sources](../concepts/build-source-data.md) concept guide.

### Application-scoped comparison (ADR-043: appcompat folded into `compare --used-by`)

There is no separate `appcompat` mode. Scope a normal `compare` to what an
application actually uses via `extra-args`:

```yaml
- uses: abicheck/abicheck@v0.5.0
  with:
    old-library: libfoo-old.so
    new-library: libfoo-new.so
    extra-args: '--used-by myapp'
```

`--used-by <app-binary>` (repeatable) runs the full library comparison once,
then scopes the primary verdict/exit code to the worst app-affecting result;
the full verdict and unrelated changes stay as informational context. The
`OLD`/`NEW` operands may be real library binaries or JSON snapshots that
carry binary evidence (a `dump` of a real library, not headers-only).

### Version labels

| Input | Default | Description |
|-------|---------|-------------|
| `old-version` | `old` | Version label for old library |
| `new-version` | `new` | Version label for new library |

### Language and compiler

| Input | Default | Description |
|-------|---------|-------------|
| `lang` | `c++` | Language mode for the header backend: `c++` or `c` |
| `ast-frontend` | `auto` (resolves to castxml when present) | L2 header-AST frontend (compare/dump modes): `auto`, `castxml`, `clang` (`clang -ast-dump=json`, for clang-only hosts), or `hybrid` (runs both and merges them — needs both tools on the runner, never auto-selected). `auto` falls back to clang on a castxml toolchain error. Same as `ABICHECK_AST_FRONTEND`. |
| `gcc-path` | — | Path to cross-compiler binary (dump mode only) |
| `gcc-prefix` | — | Cross-toolchain prefix, e.g. `aarch64-linux-gnu-` (dump mode only) |
| `gcc-options` | — | Extra flags for castxml (dump mode only) |
| `sysroot` | — | Alternative system root (dump and deps-tree modes) |
| `nostdinc` | `false` | Skip standard include paths (dump mode only) |

### Full-stack dependency validation (Linux ELF)

| Input | Default | Description |
|-------|---------|-------------|
| `follow-deps` | `false` | Include transitive dependency graph and symbol bindings in dump/compare output |
| `old-root` | — | Sysroot for the old (baseline) environment (required for `deps-compare` mode) |
| `new-root` | — | Sysroot for the new (candidate) environment (required for `deps-compare` mode) |
| `search-path` | — | Additional library search directories (space-separated) |
| `ld-library-path` | — | Simulated `LD_LIBRARY_PATH` (colon-separated) |

### Source-scan and build-source evidence (scan / dump modes)

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
| `depth` | scan, dump | Evidence-depth dial: `binary`, `headers`, `build`, or `source`. Maps to `--depth`. Omit in scan mode for `auto` (risk-driven). |
| `against` | scan | Previous build's dump/library to compare against (or use `abi-baseline` to auto-fetch one). Maps to `--against`. Omit it (and `abi-baseline`) on a step to run a single-build hygiene lint instead — `scan` already runs audit-only whenever no baseline is given. |
| `since` | scan | Focus the scan on files changed vs a git ref (e.g. `origin/main`). |
| `changed-path` | scan | Changed path(s) to focus on (space-separated; alternative to `since`). |
| `budget` | scan | Time guard (e.g. `15m`). The step **fails** on overflow (`verdict: BUDGET_OVERFLOW`) — a budget never silently shrinks scope. |
| `crosscheck` | scan | Per-check severity overrides `KEY=LEVEL` (`off`/`info`/`warning`/`error`), space-separated. Promoting a check to `=error` makes a finding for it exit `2` (the API_BREAK tier); pair with `fail-on-api-break: true` to gate the step. |
| `risk-rules` | scan | Path to a YAML file overriding the `risk_rules` profile. |

!!! note "format in scan mode"
    `scan` supports `format: text` (default) or `json`; any other value is a
    hard error raised before any dependency install (see [Mode/input
    compatibility](#modeinput-compatibility)).

!!! tip "Consuming build-emitted source facts (wrapper / Clang plugin)"
    If your **product build** emits its own `abicheck_inputs/` pack — via the
    `abicheck-cc` compiler wrapper or the optional
    [Clang plugin](../concepts/build-source-data.md) (both write the identical
    schema) — there is no separate ingestion step. Pass the pack directory
    directly in `sources` or `build-info` (scan/dump mode); abicheck
    auto-detects it and folds it in with no re-parse. The Action does not run
    the wrapper/plugin itself (that happens in your build). The standalone
    `merge` CLI command that used to expose this is gone — see
    [Companion Commands](companion-commands.md).

### Output and policy

| Input | Default | Description |
|-------|---------|-------------|
| `format` | `markdown` (`text` for scan) | Output format: `markdown`, `json`, `sarif`, `html`. `sarif` is only available in `compare` mode when `old-library`/`new-library` are a single pair — a directory/package comparison rejects it with a clear error (choose `markdown` or `json` instead). `html` is available in `compare` (same single-pair restriction) and in `deps-tree`/`deps-compare` (a dependency-stack report); `scan` supports only `text`/`json`. Requesting an unsupported format for the mode is a **hard error**, raised before any dependency install — it used to silently fall back to a supported format with only a warning, which is unsafe for CI (see [Mode/input compatibility](#modeinput-compatibility)). |
| `output-file` | — | Path to write report (auto-set for SARIF) |
| `dry-run` | `false` | Resolve inputs/config and print what the run would do, without analyzing anything or writing output (always exits 0). Maps to `--dry-run`; supported by every mode. In scan mode this also prints the projected per-layer cost. |
| `estimate` | `false` | **Deprecated.** scan mode only. Functional alias for `dry-run: 'true'` — prefer `dry-run` directly, which applies to every mode. |
| `audit` | `false` | **Deprecated.** scan mode only. Forces a single-build hygiene lint by skipping `--against` even when `against`/`abi-baseline` is configured elsewhere in the workflow. Prefer omitting `against`/`abi-baseline` on the step instead — `scan` already runs audit-only whenever no baseline is given. |
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
| `upload-sarif` | `false` | Upload SARIF to GitHub Code Scanning. Requires `format: sarif` and `mode: compare`; any other combination is a hard error raised before any dependency install. |
| `fail-on-breaking` | `true` | Fail step on binary ABI break |
| `fail-on-api-break` | `false` | Fail step on source-level API break |
| `severity-preset` | — | Severity preset: `default`, `strict`, or `info-only` (compare mode only) |
| `severity-addition` | — | Severity for additions: `error`, `warning`, or `info` (compare mode only) |
| `extra-args` | `''` | Additional CLI arguments passed to abicheck |
| `add-job-summary` | `true` | Write summary to Job Summary panel (ignored for dump mode) |
| `pr-comment` | `true` | Post a sticky ABI report comment on the PR (compare mode, including directory/package comparisons). No-op outside `pull_request` events. |
| `pr-comment-mode` | `update` | `update` keeps one comment and edits it in place; `new` posts a fresh comment each run |
| `pr-comment-on` | `changes` | When to comment: `changes`, `always`, or `never` |
| `pr-comment-detail` | `standard` | Comment detail: `summary`, `standard`, or `full` |
| `github-token` | `${{ github.token }}` | Token for the PR comment and baseline auto-fetch (needs `pull-requests: write`) |

### Package comparison inputs (compare mode, directory/package operands only)

These only apply when `old-library`/`new-library` are directories or
packages, rather than a single library each — abicheck detects this from the
operands themselves, so there is no separate mode to select.

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
| `verdict` | **compare** (single pair or directory/package operands, including `--used-by`/`--required-symbol`-scoped runs): `COMPATIBLE`, `SEVERITY_ERROR`, `API_BREAK`, `BREAKING`, `REMOVED_LIBRARY` (directory/package operands with `fail-on-removed-library` set), or `ERROR`. **dump:** `COMPATIBLE` or `ERROR`. **scan:** `COMPATIBLE`, `API_BREAK`, `BREAKING`, `BUDGET_OVERFLOW`, or `ERROR`. **deps-compare:** `PASS`, `WARN`, `FAIL`, or `ERROR`. **deps-tree:** `PASS`, `FAIL`, or `ERROR`. |
| `exit-code` | **compare:** `0` (compatible), `1` (severity error), `2` (API break), `4` (ABI break), `8` (library removed). **scan:** `0` (compatible/advisory), `2` (API break), `4` (ABI break), `5` (budget overflow). **deps-compare:** `0` (pass), `1` (warn), `4` (fail). **deps-tree:** `0` (ok), `1` (missing). |
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
        uses: abicheck/abicheck@v0.5.0
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
        uses: abicheck/abicheck@v0.5.0
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
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## Source scans (build & source evidence)

`mode: scan` is the **one-step entry point** for source intelligence: it
classifies the PR's changed paths, runs the always-on pattern and cross-source
checks plus the pinned evidence depth (L3 build context / L4 source-ABI replay
/ L5 source graph), and — with an `against` baseline — compares against it. The full
CI recipes — pinning `depth`, single-release audit, cost estimation,
cross-check gating, and the ways to feed build/source evidence into a
baseline (`dump --sources`, `build-info`, build-emitted packs) — live on
their own page:

➡️ **[GitHub Action: Source Scans & Build Evidence](github-action-source-scans.md)**

## More usage recipes

Caching a baseline, SARIF upload, cross-compilation, multi-library/multi-platform
matrices, dependency/app-scoped checks, PR-comment tuning, and the
directory/package comparison recipes (RPM/Deb/tar/conda) are on their
own page:

➡️ **[GitHub Action: More Recipes](github-action-recipes.md)**

## Versioning

The action follows [semantic versioning](https://semver.org/). While abicheck
is pre-1.0, pin an exact release tag (the examples in this guide use the latest,
`v0.3.0`); a floating major tag is not published yet:

```yaml
uses: abicheck/abicheck@v0.5.0     # exact release tag (recommended, reproducible)
uses: abicheck/abicheck@abc123def  # exact commit SHA (most secure)
```

Released tags are listed on the
[Releases page](https://github.com/abicheck/abicheck/releases). Once abicheck
reaches a stable `1.0`, a floating `v1` major tag updated on each patch/minor
release will become the recommended pin.
