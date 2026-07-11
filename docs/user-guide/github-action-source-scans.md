# GitHub Action: Source Scans & Build Evidence

The main [GitHub Action](github-action.md) page covers installation, inputs,
outputs, and the everyday compare recipes. This page is the
**source-intelligence companion**: running `mode: scan` from CI, pinning the
`depth` dial, single-release audits, cost estimation, cross-check gating, and
the three ways to feed L3/L4/L5 build/source evidence into a baseline. For
what the evidence layers *are*, see
[Evidence & Detectability](../concepts/evidence-and-detectability.md); for the
underlying CLI flags, see [Source-Scan Depth](scan-levels.md).

## Source scans (build & source evidence)

`mode: scan` is the **one-step entry point** for source intelligence. It
classifies the PR's changed paths, always runs the compiler-free pattern and
intra-version cross-source checks, then runs the pinned evidence level
(L3 build context / L4 source-ABI replay / L5 source graph) and — when a
`baseline` is given — compares against it. It emits a single
coverage-annotated report saying, per layer, what ran versus what was skipped.

> **New to what these layers see?** The concept-track
> [level-by-level walk-through](../concepts/what-each-level-sees.md)
> shows, on one running example, the concrete data each level (L0→L5) produces
> and where each goes blind — the "why" behind the inputs below.

The common case needs four inputs — the built binary, its public headers, the
source tree, and a baseline:

```yaml
permissions:
  contents: read
jobs:
  abi-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # needed for `since: origin/...` change focusing

      - name: Build
        run: cmake -B build -S . && cmake --build build

      - name: Source-intelligence scan
        uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          baseline: abi-baseline.json   # committed, or use abi-baseline: latest-release
          since: origin/${{ github.base_ref }}   # focus on changed files
          fail-on-api-break: true       # gate on source/API breaks too
```

`clang` is installed automatically (for L4/L5). On a `pull_request` run,
`since: origin/${{ github.base_ref }}` focuses the (expensive) source replay on
the files the PR touched — pair it with `fetch-depth: 0` in `checkout` so the
base ref is available.

### Pin the scan depth

The Action's `scan-mode` input still defaults to `pr` (a deprecated alias), so an
unset `depth` currently runs the `pr` preset **and emits a deprecation warning** —
it is not the CLI's `auto`. Pin the modern `depth` dial for reproducible CI (and to
silence the warning); the raw CLI resolves `auto` only when nothing is passed:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          baseline: abi-baseline.json
          depth: source         # source-ABI replay of changed TUs (deterministic)
          since: origin/main    # scope the L4 replay to the PR's changed TUs
          budget: 15m           # fail (BUDGET_OVERFLOW) rather than overrun
```

| Want… | Set |
|-------|-----|
| Cheap build-flag drift only (L3) | `depth: build` |
| Source semantics on changed TUs (+ L5 graph) | `depth: source` + `since:` |
| Full source-ABI replay of the whole library | `depth: full` |
| Risk-driven (dev/local, opt-in) | omit `depth` (→ `auto`) + `since:` |

!!! note "`scan-mode`/`source-method` are deprecated (ADR-037 D5)"
    The older `scan-mode` (`pr`/`pr-deep`/`baseline`/`audit`) and `source-method`
    (`s0…s6`) inputs still work but map onto `depth` and print a deprecation
    warning. `scan-mode` currently defaults to `pr`, so leaving both new inputs
    unset still emits that warning — set `depth` (or `audit: 'true'`) to pin the
    modern dial. The mapping is in the
    [Deprecated axes appendix](../concepts/evidence-and-detectability.md#appendix-deprecated-scan-axes-s0s6-and-mode).

### Single-release audit (no baseline)

Run the intra-version hygiene checks against one build — no old version needed.
Useful as a standing lint on the default branch:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          audit: 'true'
```

### Estimate cost before committing to a depth

`estimate: 'true'` is a dry run — it prints the projected per-layer cost (TU
count, seconds) and scans nothing, always exiting 0. Handy when sizing a budget
for a large repo:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          depth: source
          estimate: 'true'
```

### Gate CI on a specific cross-source check

Cross-source findings are advisory by default. Promoting one to `error` makes a
finding for it exit `2` (the API_BREAK tier); add `fail-on-api-break: true` so
that exit turns the step red:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          baseline: abi-baseline.json
          crosscheck: 'private_header_leak=error odr_type_variant=error'
          fail-on-api-break: true   # gate on the exit-2 (API_BREAK) tier
```

`fail-on-api-break` gates the whole API_BREAK tier (baseline/source breaks and
promoted cross-checks alike); the Action can't tell from the exit code which one
fired, so leave it `false` if you only want binary ABI breaks (exit 4) to gate.

## Passing sources into a baseline (build/source evidence)

There are three ways to feed L3/L4/L5 evidence into the comparison. Pick by
where your build produces facts.

### A. Inline at dump time (simplest)

`dump` with `sources`/`build-info` embeds the build/source facts **inline** in
the snapshot, so any later `compare` (including one run from this Action on two
such snapshots) carries the L3/L4/L5 findings — no out-of-band directories:

```yaml
      - name: Dump baseline with build + source evidence
        uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build/libfoo.so
          header: include/
          sources: .
          depth: full                   # whole-library L3+L4+L5 for a baseline (unseeded `source` falls back to a headers-only replay)
          output-file: abi-baseline.json
```

Compare two such snapshots later with the default `compare` mode — the embedded
evidence diffs automatically.

### B. Combine independently-produced dumps with `merge`

When the binary side and source side are produced in parallel (e.g. on
different runners), `mode: merge` folds them into one self-contained baseline:

```yaml
      # one job produces the artifact-side dump (L0/L1/L2)…
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build/libfoo.so
          header: include/
          output-file: libfoo.bin.json

      # …another produces the source-side dump (L3/L4/L5)…
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          sources: ./libfoo-src/
          output-file: libfoo.src.json

      # …then merge them into one baseline:
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: merge
          merge-inputs: 'libfoo.bin.json libfoo.src.json'
          on-conflict: error            # good for baseline generation
          output-file: libfoo.baseline.json
```

### C. Build-emitted facts (Flow-2 `abicheck_inputs/` pack)

A product build that emits a self-describing `abicheck_inputs/` pack (via
`abicheck-cc` — see [Build Info & Sources](../concepts/build-source-data.md))
needs no source replay in CI. `mode: merge` ingests the pack directly:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: merge
          merge-inputs: 'libfoo.bin.json ./abicheck_inputs/'
          output-file: libfoo.baseline.json
```

The resulting `libfoo.baseline.json` is a normal snapshot — pass it as
`old-library`/`baseline` to any later `compare` or `scan`.
