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

`depth` is the single evidence-depth dial. Pin it for reproducible CI, or omit it
for `auto` (risk-driven, best paired with `since:`):

```yaml
      - uses: abicheck/abicheck@v0.4.0
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
| Full source-ABI replay of the whole library | `depth: source` with no `since:`/`changed-path` (an unseeded `depth: source` already analyses the whole current target — ADR-043) |
| Risk-driven (dev/local, opt-in) | omit `depth` (→ `auto`) + `since:` |

!!! note "The old `scan-mode`/`source-method` inputs and the `full` depth are gone"
    Earlier releases exposed `scan-mode` (`pr`/`pr-deep`/`baseline`/`audit`) and
    `source-method` (`s0…s6`) Action inputs, plus a fifth `depth: full` rung.
    As of the ADR-043 pre-1.0 CLI reset all three are removed outright, not
    deprecated — the CLI's `--depth` no longer accepts `full`/`--mode`/
    `--source-method`/`--max` at all (a plain usage error). Use `depth`
    (or `audit: 'true'`); `full` collapsed into `source`, since the two only
    ever differed in replay *scope*, and an unseeded `depth: source` already
    resolves to the whole target. The mapping from the old axes is in the
    [Removed scan axes appendix](../concepts/evidence-and-detectability.md#appendix-removed-scan-axes-s0s6-mode-source-method-max).

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
          depth: source                 # whole-library L3+L4+L5 for a baseline (unseeded `source` already analyses the whole target — ADR-043)
          output-file: abi-baseline.json
```

Compare two such snapshots later with the default `compare` mode — the embedded
evidence diffs automatically.

### B. Independently-produced dumps or a build-emitted facts pack

The `collect`/`merge` commands that used to combine a binary-side dump with a
separately-produced source-side dump (or an `abicheck-cc`-emitted
`abicheck_inputs/` Flow-2 pack) were removed from the public CLI in the
ADR-043 reset with no replacement command, and the Action's `mode: merge`
dispatch went with them. Section A (inline embedding) above is the only
Action-supported flow today.

For a build that genuinely produces the binary and source sides on separate
runners (or emits a Flow-2 pack), `compare`'s own out-of-band
`--old-build-info`/`--new-build-info` flags accept a pack directory per side —
including auto-detecting an `abicheck_inputs/` pack — but the Action does not
currently expose per-side build-info inputs for `mode: compare`. Run that step
directly with the CLI (`pip install abicheck`) instead of through this Action,
or embed inline at dump time as in Section A. See
[Build Info & Sources](../concepts/build-source-data.md) for the underlying
CLI-level flows.
