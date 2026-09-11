---
doc_type: how-to
audience:
  - ci-owner
level: beginner
canonical_for:
  - github-actions-surface
summarizes:
  - ast-frontend-resolution
lifecycle: active
generated: false
---

# GitHub Action

abicheck ships as a reusable GitHub Action that you can add to any CI pipeline
with a few lines of YAML. It installs Python, system dependencies, and abicheck
automatically, then runs ABI comparison and reports results.

> **Picking a mode or failure policy?** See
> [Choose Your Workflow](../start/choose-your-workflow.md) for the decision matrix —
> which artifacts map to which `mode`, and which severity inputs gate the build.

> **Have more than one library, profile, or baseline channel?** This page
> covers the root Action as one step. For a project's whole CI integration
> lifecycle — multiple targets, build profiles, baseline channels, and the
> `check-project.yml` matrix — see
> [Which Scenario Am I?](../integration/index.md).

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

`mode: scan` itself is retired outright (ADR-068's Action-input-lifecycle
amendment, D8 hard removal) — setting it fails the step immediately, naming
the replacement for your shape. See [Migrating from `mode:
scan`](#migrating-from-mode-scan) below if you're updating an existing
workflow.

| Capability | `compare`, two-sided | `compare`, audit-only | `dump` | `deps-tree` / `deps-compare` |
|---|:--:|:--:|:--:|:--:|
| Single binary/snapshot | yes | yes (`new-library` only) | yes | yes |
| Directory/package (`new-library`/`old-library`) | yes (fans out per-library) | **error** | **error** | — |
| Source-only (no `new-library`, via `sources`/`build-info`/`compile-db`) | — | — | yes | — |
| `format: sarif` | yes (single pair only) | yes | n/a (always JSON) | **error** |
| `format: html` | yes (single pair only) | **error** | n/a (always JSON) | yes (dependency-stack report) |
| `format: review` | yes (single pair only) | **error** | n/a (always JSON) | **error** |
| `format: json` | yes | yes | n/a (always JSON) | yes |
| `format: markdown` | yes | yes | n/a (always JSON) | `markdown` only |
| `format: junit` / `oneline` | yes | yes | n/a (always JSON) | **error** |
| `upload-sarif: true` | yes (needs `format: sarif`) | yes (needs `format: sarif`) | **error** | **error** |
| `pr-comment` | yes | yes | no-op | no-op |

For a multi-library release directory (several `.so`/`.dll`/`.dylib` files),
use `mode: compare` with a directory/package operand — it fans out to a
per-library comparison automatically (see [Package comparison
inputs](#package-comparison-inputs-compare-mode-directorypackage-operands-only)
below). `dump` and `compare`'s own audit-only shape have no such fan-out:
dump/audit each library individually (one step per binary, or a matrix) —
see [Choose Your Workflow](../start/choose-your-workflow.md) for weighing
that split against a single combined step. If the release also carries
build-emitted source facts (a
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
| `mode` | no | `compare` (default), `dump`, `deps-tree`, or `deps-compare`. `scan` is retired outright (ADR-068) — see [Migrating from `mode: scan`](#migrating-from-mode-scan). |
| `old-library` | no | Path to old library, JSON snapshot, ABICC dump, directory, or package (a directory/package fans out to a per-library comparison automatically — no separate mode). Omit it (and `abi-baseline`) on a `compare` step to run the audit-only shape (`compare --no-baseline`) against `new-library` alone instead. |
| `new-library` | yes (compare, dump\*, deps-tree, deps-compare) | Path to new library, binary, or JSON snapshot. **Directory/package is `compare`-only, and only for the two-sided shape** — `dump` and `compare`'s own audit-only shape each analyse exactly one artifact and reject a directory/package with a fail-fast error, before any dependency install. \*`dump` may omit `new-library` entirely for a source-only dump (`sources`/`build-info`/`compile-db` given instead). See [Mode/input compatibility](#modeinput-compatibility) below. |

### Header inputs

| Input | Required | Description |
|-------|----------|-------------|
| `header` | no | Public header file(s) or directory(ies) for both sides (space-separated) — use `old-header`/`new-header` instead when old and new actually declare different headers |
| `old-header` | no | Header file(s) or directory(ies) for old side only |
| `new-header` | no | Header file(s) or directory(ies) for new side only |
| `include` | no | Extra include dirs for castxml (both sides) |
| `old-include` | no | Include dirs for old side only |
| `new-include` | no | Include dirs for new side only |

!!! note "Evidence layers in the Action"
    The Action drives the same [five-layer evidence
    model](../learn/evidence-and-detectability.md) as the CLI. The inputs
    above cover **L0** (`old-library`/`new-library`), **L1** (debug info —
    embedded, or `debug-info1`/`debug-info2` packages when `old-library`/
    `new-library` are directories or packages), and **L2** (`header`/`include`).

    The deeper layers — **L3** build context, **L4** source-ABI replay, and
    **L5** source graphs — are now first-class Action inputs. Use the
    `sources`/`build-info`/`compile-db` inputs in `compare` or `dump` mode and
    abicheck collects them inline; no separate CLI steps are required. See
    [Source scans](#source-scans-build-source-evidence) below and the
    [Build Info & Sources](../learn/build-source-data.md) concept guide.

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

> A dedicated `used-by` input (space-separated, mutually exclusive with
> `required-symbol`/`required-symbols`) was added after the `v0.5.0` release
> — on a commit-SHA pin newer than `v0.5.0`, prefer `used-by: myapp` over
> `extra-args` for the same effect.

### Version labels

| Input | Default | Description |
|-------|---------|-------------|
| `old-version` | `old` | Version label for old library |
| `new-version` | `new` | Version label for new library |

### Language and compiler

| Input | Default | Description |
|-------|---------|-------------|
| `lang` | `c++` | Language mode for the header backend: `c++` or `c` |
| `ast-frontend` | `auto` (resolves to castxml, fail-closed) | L2 header-AST frontend (dump mode, and compare mode with a single-pair operand — both the two-sided and audit-only shapes): `auto`, `castxml`, `clang`, or `hybrid`. Like `dump` and single-pair `compare`, this Action folds it into a synthesized `.abicheck.yml` `compile:` block forwarded via `--config` instead (along with gcc-path/gcc-prefix/gcc-options/sysroot/nostdinc/lang, when any of those are also set — combining this group with `build-config` is supported: the synthesized `compile:` block merges into a copy of the named build-config, and this Action's input wins on a key conflict). Pass `--allow-ast-frontend-fallback`/`--frontend-context` via `extra-args` for the opt-in castxml→clang fallback or SYCL/DPC++ device context on any mode (set `.abicheck.yml`'s `compile.ast_frontend_fallback`/`compile.frontend_context` directly instead if `extra-args` isn't reaching the mode you need — those two flags have no CLI of their own on any mode). Same as `ABICHECK_AST_FRONTEND`. See [Header-Backend Capabilities](../reference/header-backend-capabilities.md) for the full resolution contract (fallback triggers, the device-context exception, and how an env pin interacts with both). |
| `gcc-path` | — | Path to cross-compiler binary (dump mode, and compare mode with a single-pair operand) — see `ast-frontend` above for how this reaches every mode now that the underlying `--compiler` CLI flag is gone from all of them |
| `gcc-prefix` | — | Cross-toolchain prefix, e.g. `aarch64-linux-gnu-` (dump mode, and compare mode with a single-pair operand) — same note as gcc-path above; a full gcc-path wins if both are set, since the merged `compile.compiler` config key can only hold one |
| `gcc-options` | — | Extra flags for the header frontend (dump mode, and compare mode with a single-pair operand) — folds into the same synthesized `compile:` block `ast-frontend` above describes. A whitespace-containing flag (e.g. `-DMSG="hello world"`) is rejected with a clear error, since a raw CLI arg isn't subject to `compile.options`' own one-atom-per-entry, whitespace-free contract. |
| `sysroot` | — | Alternative system root (dump/deps-tree modes, and compare mode with a single-pair operand) — same note as gcc-path above for dump/compare; `deps-tree` keeps its own direct `--sysroot` forwarding, unaffected (it is not part of this compile-context group at all) |
| `nostdinc` | `false` | Skip standard include paths (dump mode, and compare mode with a single-pair operand) — same note as gcc-path above |

A directory/package (release/bundle) `compare` operand does not support
these six inputs — the per-library fan-out never threads this L2 compile
context to each pair's header dump, and the Action fails fast if any of
them is set for that shape. Compare libraries individually to use them;
see the [GitHub Action Inputs/Outputs
Reference](../reference/github-action-inputs.md) for the exact wording.

### Full-stack dependency validation (Linux ELF)

| Input | Default | Description |
|-------|---------|-------------|
| `follow-deps` | `false` | Include transitive dependency graph and symbol bindings in dump/compare output |
| `old-root` | — | Sysroot for the old (baseline) environment (required for `deps-compare` mode) |
| `new-root` | — | Sysroot for the new (candidate) environment (required for `deps-compare` mode) |
| `search-path` | — | Additional library search directories (space-separated) |
| `ld-library-path` | — | Simulated `LD_LIBRARY_PATH` (colon-separated) |

### Source-scan and build-source evidence (compare / dump modes)

These inputs drive [source intelligence](../learn/build-source-data.md) —
L3 build context, L4 source-ABI replay, and L5 source graphs — through
`compare` (both the two-sided and audit-only shapes), or fold the same
evidence into a `dump` snapshot. L4/L5 need `clang` (installed automatically
by `dependency-source: system` or `conda-forge-clang20` — plain
`conda-forge`, the default, doesn't provision clang); without it collection
degrades gracefully and L0–L2 stay authoritative.

| Input | Modes | Description |
|-------|-------|-------------|
| `sources` | compare (both shapes), dump | Source checkout/tree; drives L4 replay and graph collection. With a source-level depth and no compile DB, `abicheck` auto-detects the build system (CMake/Bazel) and runs the query itself to emit one — no flag, no manual build. For `compare`, this feeds the new (candidate) side only — the old side's evidence is expected to already be embedded in whatever `old-library` snapshot was resolved; not applicable to a directory/package operand. |
| `build-info` | compare (both shapes), dump | Out-of-tree L3 context: a build dir, a `compile_commands.json`, or a collected evidence pack. Same new-side-only note as `sources` above. |
| `compile-db` | compare (both shapes; dump folds into `build-info`) | Explicit `compile_commands.json` path. |
| `build-config` | compare (both shapes), dump | Trusted `.abicheck.yml`; its `build.query` runs automatically (operator-supplied = trusted). |
| `allow-build-query` | — | Deprecated and ignored (the `--allow-build-query` dump flag it fed was always a no-op and has since been removed outright). Kept registered only for back-compat with an existing workflow that still sets it. |
| `depth` | compare (both shapes), dump | Evidence-depth dial: `binary`, `headers`, `build`, or `source`. Maps to `--depth`. Omitting it means `headers` — pin `build`/`source` explicitly (there is no risk-driven `auto` escalation any more, ADR-068 (b)). |
| `since` | compare, two-sided shape only | Focus the run's source-evidence scope on files changed vs a git ref (e.g. `origin/main`). Rejected outright for the audit-only shape (`compare --no-baseline` does not implement revision-range evidence scoping, ADR-068 D2) and for a directory/package operand. |
| `changed-path` | compare, two-sided shape only | Changed path(s) to focus the run's source-evidence scope on (space-separated; alternative to `since`). Same audit-only/directory-package rejection as `since` above. |
| `budget` | compare, two-sided shape only | Time guard (e.g. `15m`). The step **fails** on overflow (`verdict: BUDGET_OVERFLOW`) — a budget never silently shrinks scope. Rejected outright for the audit-only shape (`compare --no-baseline`'s wall-clock guard isn't wired to that path yet, ADR-068 D2). |
| `against` | — | **Retired** (ADR-068's Action-input-lifecycle amendment, D8 hard removal): this applied only to the now-removed `mode: scan`. Set `old-library` (or `abi-baseline`) under `mode: compare` instead — setting `against` is now a hard error naming that replacement. |
| `crosscheck` | — | **Retired** (ADR-068 D8, hard removal): `scan --crosscheck`'s `KEY=LEVEL` promotion syntax is gone along with `mode: scan` itself. Every cross-source check it used to gate already reaches `compare` as an ordinary finding — use `policy`/`.abicheck.yml`'s `policy.overrides.<CHANGE_KIND>: error` to control one check's severity instead. Setting `crosscheck` is now a hard error naming that replacement. |
| `risk-rules` | — | **Retired** (ADR-068 (b)): `scan --risk-rules` and the risk-driven `auto` depth escalation it fed are gone. Pin `depth:` explicitly instead; setting this input is an error. |

!!! tip "Consuming build-emitted source facts (wrapper / Clang plugin)"
    If your **product build** emits its own `abicheck_inputs/` pack — via the
    `abicheck-cc` compiler wrapper or the optional
    [Clang plugin](../learn/build-source-data.md) (both write the identical
    schema) — there is no separate ingestion step. Pass the pack directory
    directly in `sources` or `build-info` (compare/dump mode); abicheck
    auto-detects it and folds it in with no re-parse. The Action does not run
    the wrapper/plugin itself (that happens in your build). The standalone
    `merge` CLI command that used to expose this is gone — see
    [Companion Commands](companion-commands.md).

### Output and policy

| Input | Default | Description |
|-------|---------|-------------|
| `format` | `markdown` | Output format: `markdown`, `json`, `sarif`, `html`, `junit`, `review`, or `oneline`. `sarif`/`html`/`review` are only available for a single-artifact `compare` (`old-library` or `abi-baseline`, plus `new-library`, as a single pair) — a directory/package comparison rejects them with a clear error (choose `markdown`, `json`, or `junit` instead). Within that single-artifact operand, `sarif` is available for **both** compare shapes (a two-sided compare and the audit-only shape alike), while `html`/`review` are two-sided-report renderers with no audit-only equivalent — `compare`'s audit-only shape (`old-library`/`abi-baseline` both omitted) narrows the supported set to `json`/`markdown`/`sarif`/`junit`/`oneline`. `html` is also available in `deps-tree`/`deps-compare` (a dependency-stack report). Requesting an unsupported format for the mode/shape is a **hard error**, raised before any dependency install — it used to silently fall back to a supported format with only a warning, which is unsafe for CI (see [Mode/input compatibility](#modeinput-compatibility)). |
| `output-file` | — | Path to write report (auto-set for SARIF) |
| `dry-run` | `false` | Resolve inputs/config and print what the run would do, without analyzing anything or writing output. Exits 0 for a resolvable preview — but an invalid flag combination or an unsatisfiable requested depth/evidence contract still exits nonzero, same as the real run would (see the [inputs reference](../reference/github-action-inputs.md) for the one deliberate exception, an unresolved baseline). Maps to `--dry-run`; supported by every mode, including compare's audit-only shape. |
| `estimate` | — | **Retired** (ADR-068's Action-input-lifecycle amendment, D8 hard removal): this applied only to the now-removed `mode: scan`, as a `dry-run` alias. Use `dry-run: 'true'` instead, which applies to every mode. Setting `estimate` is now a hard error naming that replacement. |
| `audit` | — | **Retired** (ADR-068's Action-input-lifecycle amendment, D8 hard removal): this applied only to the now-removed `mode: scan`, forcing a one-build audit-only run. Under `mode: compare`, simply omit `old-library`/`abi-baseline` to run an audit-only `compare --no-baseline`; set `severity-preset` (e.g. `default`) if this job should still gate on a `BREAKING`/`API_BREAK`-classified finding the way `mode: scan`'s own audit mode always did. Setting `audit` is now a hard error naming that replacement. |
| `policy` | `strict_abi` | Built-in policy: `strict_abi`, `sdk_vendor`, `plugin_abi` |
| `policy-file` | — | Custom YAML policy file |
| `suppress` | — | YAML suppression file (supports `label`, `source_location`, `expires`) |
| `verbose` | `false` | Enable debug output |

To enable suppression lifecycle enforcement, set it in the repository's own
`.abicheck.yml` — these are config keys, not flags, so they do not go through
`extra-args`:

```yaml
# .abicheck.yml, committed alongside the workflow
suppression:
  strict: true
  require_justification: true
```

### Action behavior

| Input | Default | Description |
|-------|---------|-------------|
| `python-version` | `3.13` | Python version for setup-python |
| `dependency-source` | *(unset — falls back to `install-deps`)* | How to install system dependencies: `conda-forge` (**default**), `conda-forge-gcc14`, `conda-forge-clang20` (the only conda-forge source that provisions clang — see the note above), `system`, or `none`. See the [GitHub Action Inputs/Outputs Reference](../reference/github-action-inputs.md) for the exact per-value breakdown. |
| `install-deps` | `true` | **Deprecated** — use `dependency-source` instead (kept for one release cycle; ignored if `dependency-source` is set). `true` (its own default too) maps to `dependency-source: conda-forge`, `false` maps to `dependency-source: none`. |
| `upload-sarif` | `false` | Upload SARIF to GitHub Code Scanning. Requires `format: sarif` and `mode: compare`; any other combination is a hard error raised before any dependency install. |
| `fail-on-breaking` | `true` | Fail step on binary ABI break |
| `fail-on-api-break` | `false` | Fail step on source-level API break |
| `severity-preset` | — | Severity preset: `default`, `strict`, or `info-only` (compare mode only) |
| `severity-addition` | — | Severity for additions: `error`, `warning`, or `info` (compare mode only) |
| `extra-args` | `''` | Additional CLI arguments passed to abicheck |
| `add-job-summary` | `true` | Write summary to Job Summary panel (ignored for dump mode) |
| `pr-comment` | `true` | Post a sticky ABI report comment on the PR (compare mode, including directory/package comparisons and the audit-only shape). No-op outside `pull_request` events. |
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
| `fail-on-removed-library` | `false` | Exit 8 when a library present in old is *proven* removed in new (NEW must be a stored `ProjectSnapshot` package or bundle-facts document whose capture asserted a complete inventory, `inventory_complete`, ADR-065); an unmatched library under an unproven inventory is an incomplete scope instead, governed by `.abicheck.yml`'s `scope.on_incomplete` key |

`keep-extracted` (the Action input mirroring `--keep-extracted`) is gone
outright (Phase 7d remainder, one-comparison-product.md §4.1, ADR-068 D5):
extraction cleanup is unconditional now, with no config replacement.

## Outputs

| Output | Description |
|--------|-------------|
| `verdict` | **compare, two-sided** (single pair or directory/package operands, including `--used-by`/`--required-symbol`-scoped runs): `COMPATIBLE`, `COMPATIBLE_WITH_RISK` (a real, exit-0 tier for a compatible-but-risky change), `SEVERITY_ERROR`, `COVERAGE_INCOMPLETE`, `ANALYSIS_INCOMPLETE` (single-pair only, with `.abicheck.yml`'s `assurance.require_complete: true`, when `analysis_assurance.status` is not `"complete"` — see the audit-only shape's own entry below for the full description; this orthogonal axis applies to both `compare` shapes), `SCOPE_INCOMPLETE` (directory/package operands only, ADR-065: an unchecked selected member under `.abicheck.yml`'s `scope.on_incomplete: block`, or no comparison completed at all — fails the step unconditionally), `API_BREAK`, `BREAKING`, `REMOVED_LIBRARY` (directory/package operands with `fail-on-removed-library` set, which since ADR-065 requires a proven-complete NEW inventory), `NOT_COMPARABLE`, `BUDGET_OVERFLOW`, `EVIDENCE_CONTRACT_ERROR` (ADR-037 D5), or `ERROR`. **compare, audit-only shape** (`old-library`/`abi-baseline` both omitted, ADR-068 D2): `COVERAGE_INCOMPLETE`/`EVIDENCE_CONTRACT_ERROR`/`ERROR` still apply (`COMPATIBLE_WITH_RISK`/`NOT_COMPARABLE`/`BUDGET_OVERFLOW` do NOT — an audit-only exit-0 run always resolves to `AUDIT_CLEAN`/`AUDIT_RISK`/`DRY_RUN` below instead, never the two-sided compatibility tier), plus `AUDIT_GATE` (a `BREAKING`/`API_BREAK`-classified finding against the candidate's own public surface while a severity preset other than `info-only` was in effect; ADR-068's 2026-09-10 amendment. Unlike legacy `mode: scan`, this Action never injects a preset on the caller's behalf — set `severity-preset` (e.g. `default`) explicitly to opt in to this gating; see "Migrating from mode: scan" below). Not a two-sided compatibility verdict: an audit reports no additions/removals/compatibility verdict at all, only its own candidate-side findings; exit code `3`, fails the step unconditionally), `AUDIT_CLEAN` (same audit-only shape, exit `0`, no candidate-side finding at all), `AUDIT_RISK` (same audit-only shape, exit `0`, a candidate-side finding was detected but this run did not gate on it — distinct from `AUDIT_CLEAN` so a reviewer can tell "nothing found" apart from "something found, not gating"), `DRY_RUN` (exit `0`, `dry-run` input true or an effective `--dry-run` via `extra-args`: no analysis was performed at all, so there is no candidate-side finding — or absence of one — to claim either way; distinct from `AUDIT_CLEAN`), `ANALYSIS_INCOMPLETE` (with `.abicheck.yml`'s `assurance.require_complete: true`, when `analysis_assurance.status` is not `"complete"` — the same orthogonal assurance axis as the two-sided shape, since that config key applies to both `compare` shapes; not a two-sided compatibility verdict either, since no baseline was compared). **dump:** `COMPATIBLE` or `ERROR`. **deps-compare:** `PASS`, `WARN`, `FAIL`, or `ERROR`. **deps-tree:** `PASS`, `FAIL`, or `ERROR`. This table is a summary — `action.yml`'s own `verdict` output description is the generated, canonical contract; consult it for the full detail on any value above. |
| `exit-code` | **compare, two-sided:** `0` (compatible), `1` (severity error, incomplete contract coverage, incomplete analysis assurance, or — directory/package operands, ADR-065 — an incompletely checked comparison scope under `.abicheck.yml`'s `scope.on_incomplete: block` or no comparison completed at all; the four share the code and are told apart by the report's pre-fold `severity.exit_code`, `contract_coverage_exit_contribution`, `analysis_assurance.status`, and the `exit` block's scope contributions), `2` (API break), `4` (ABI break), `8` (library proven removed, with `fail-on-removed-library` and a proven-complete NEW inventory), `16` (`NOT_COMPARABLE`, ADR-050 D2). **compare, audit-only shape:** `0` (`AUDIT_CLEAN`/`AUDIT_RISK`, or `DRY_RUN` under `dry-run: true`), `1` (incomplete contract coverage, incomplete analysis assurance, or — dry-run only — a preview of the exit-7 blocker; the three are told apart the same way as the two-sided shape's own exit 1), `3` (`AUDIT_GATE`), `7` (evidence-contract error, ADR-037 D5 — a pinned `--depth`/`--source-method` cause only; the `--abi3`-targeting-an-unrecognisable-binary cause is two-sided-shape only). **deps-compare:** `0` (pass), `1` (warn), `4` (fail). **deps-tree:** `0` (ok), `1` (missing). This table is a summary — `action.yml`'s own `exit-code` output description is the generated, canonical contract; consult it for the full detail on any value above. |
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

`mode: compare` is the **one-step entry point** for source intelligence: it
classifies the PR's changed paths, runs the always-on pattern and cross-source
checks plus the pinned evidence depth (L3 build context / L4 source-ABI replay
/ L5 source graph), and — with `old-library`/`abi-baseline` set — compares
against it. The full CI recipes — pinning `depth`, single-release audit, cost
estimation, cross-check gating, and the ways to feed build/source evidence
into a baseline (`dump --sources`, `build-info`, build-emitted packs) — live
on their own page:

➡️ **[GitHub Action: Source Scans & Build Evidence](github-action-source-scans.md)**

### Migrating from `mode: scan`

[ADR-068's Action-input-lifecycle amendment](../contribute/adr/068-one-comparison-product-and-scan-retirement.md#amendment-2026-09-11-the-action-input-lifecycle-mode-scan-retired-outright)
retired `mode: scan` outright — a step setting it now fails immediately,
before Python setup or any toolchain install, with an `::error::` naming the
replacement for your own shape. There is no deprecation window (ADR-068 D8).
`mode: scan` never collapsed to one spelling, so it does not migrate to one
either:

- **A baseline scan** (`against`/`abi-baseline` was set). Replacement:
  `mode: compare` with the identical value as `old-library` (or
  `abi-baseline`, unchanged) and the same `new-library` — the ordinary
  two-sided shape every example on this page already uses.
- **An audit-only scan** (no baseline, or `audit: true`). Replacement:
  `mode: compare` with **both** `old-library` and `abi-baseline` omitted.
  Omission is the trigger, not a separate flag — this runs a first-class
  `compare --no-baseline` against `new-library` alone. See
  [Source Scans § Single-release audit](github-action-source-scans.md#single-release-audit-no-baseline).

Two migration steps are required, not one:

**1. Add `severity-preset` to keep an audit-only step gating.** Legacy
`mode: scan` with no baseline gated a CI job on a `BREAKING`/
`API_BREAK`-classified finding **by default, unconditionally, no flag
needed**. `mode: compare`'s own audit-only shape reproduces the identical
partition at its own orthogonal exit code, `3`, published as the
`AUDIT_GATE` verdict — but that axis is **opt-in**, activated only by
`severity-preset` (any value except `info-only`). If your audit-only
`mode: scan` step relied on the default gating (i.e. you did not already pass
`severity-preset: info-only` to opt out), add `severity-preset: default` (or
`strict`) when you move it to `mode: compare` — without it, the migrated
step always exits `0`/passes regardless of what the audit finds. A step that
already set `severity-preset: info-only` needs no change. See the G20-corpus
verification transcript in the ADR-068 amendment linked above.

**2. Replace `format: text` with `format: markdown`, if set.** Legacy
`mode: scan` supported exactly two `format` values, `text` (its own
default) and `json` — `text` was rendered internally through compare's own
`markdown` renderer, never a distinct text format of its own.
`mode: compare` has no `text` format at all — `action/validate-inputs.sh`
rejects it outright, before Python/toolchain install, for both the
audit-only shape (`json`/`markdown`/`sarif`/`junit`/`oneline` only) and the
two-sided shape (`json`/`markdown`/`sarif`/`html`/`junit`/`review`/`oneline`
only). A step that explicitly set `format: text` under either scan shape
must change it to `format: markdown` (or drop the input entirely --
`markdown` is `mode: compare`'s own default too) when migrating; a step
that never set `format` (so relied on scan's own `text` default) needs no
change, since both defaults render the identical markdown output.

Three inputs have no `mode: compare` equivalent and are retired outright,
with no replacement: `new-library-set` (multi-library audit — compare each
library individually until ADR-065 S3's component inventories land),
`risk-rules` (the risk-driven `auto` depth escalation — pin `depth`
explicitly), and legacy `crosscheck`'s `KEY=error` promotion syntax (every
cross-source check already reaches `compare` as an ordinary finding; use
`.abicheck.yml`'s `policy.overrides.<CHANGE_KIND>: error` via `build-config`
instead).

## More usage recipes

Caching a baseline, SARIF upload, cross-compilation, multi-library/multi-platform
matrices, dependency/app-scoped checks, PR-comment tuning, and the
directory/package comparison recipes (RPM/Deb/tar/conda) are on their
own page:

➡️ **[GitHub Action: More Recipes](github-action-recipes.md)**

## Versioning

The action follows [semantic versioning](https://semver.org/). While abicheck
is pre-1.0, pin an exact release tag (the examples in this guide use the latest,
`v0.5.0`); a floating major tag is not published yet:

```yaml
uses: abicheck/abicheck@v0.5.0     # exact release tag (recommended, reproducible)
uses: abicheck/abicheck@abc123def  # exact commit SHA (most secure)
```

**Pin to the commit SHA, not just the tag, whenever the job grants an
elevated permission** — `security-events: write` (SARIF/Code Scanning
upload), `contents: write`, `id-token: write` (OIDC/publishing), or
similar. A mutable tag can be repointed to different code after you've
reviewed it once; every `uses:` step in that job (not only
`abicheck/abicheck` — `actions/checkout` and any upload step too) then runs
with that permission's token, so the same rule applies to all of them. Keep
the release tag in a trailing comment so the pin stays human-auditable:

```yaml
uses: abicheck/abicheck@<commit-sha>  # v0.5.0
```

Released tags are listed on the
[Releases page](https://github.com/abicheck/abicheck/releases). Once abicheck
reaches a stable `1.0`, a floating `v1` major tag updated on each patch/minor
release will become the recommended pin.
