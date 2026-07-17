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
(L3 build context / L4 source-ABI replay / L5 source graph) and — when
`against` is given — compares against it. It emits a single
coverage-annotated report saying, per layer, what ran versus what was skipped.

> **New to what these layers see?** The concept-track
> [level-by-level walk-through](../concepts/what-each-level-sees.md)
> shows, on one running example, the concrete data each level (L0→L5) produces
> and where each goes blind — the "why" behind the inputs below.

The common case needs four inputs — the built binary, its public headers, the
source tree, and a baseline to compare `against`:

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
          against: abi-baseline.json   # committed, or use abi-baseline: latest-release
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
          against: abi-baseline.json
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
    (omitting `against`/`abi-baseline` for an audit-only run); `full` collapsed into `source`, since the two only
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
          # No `against`/`abi-baseline` on this step -- scan already runs
          # audit-only whenever no baseline is given.
```

### Estimate cost before committing to a depth

`dry-run: 'true'` prints the resolved depth/scope and, in scan mode, the
projected per-layer cost (TU count, seconds) — without scanning anything,
always exiting 0. Handy when sizing a budget for a large repo:

```yaml
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          depth: source
          dry-run: 'true'
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
          against: abi-baseline.json
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

## Recommended flow: a multi-library release with one shared facts pack

This is the concrete, Action-supported answer to a specific, recurring shape
of project: several libraries built from one source tree, one facts pack
collected **once** for the whole build (via [source
replay](producing-source-facts.md#full-source-scan--replay-from-a-compile-database),
the [`abicheck-cc` wrapper](producing-source-facts.md#wrapper-injection--the-abicheck-cc-compiler-wrapper),
or the [Clang plugin](producing-source-facts.md#plugin-injection--the-clang-facts-plugin)),
and no single ".so that represents the release" to hand `scan`/`dump` — which,
per [Mode/input compatibility](github-action.md#modeinput-compatibility), only
accept **one** artifact each; there is no `scan`/`dump` equivalent of
`compare`'s directory/package fan-out.

The fix is not a new Action feature — it's composing three recipes this page
and [More Recipes](github-action-recipes.md) already document individually,
which is easy to miss without seeing them chained together:

1. **[Matrix over libraries](github-action-recipes.md#matrix-multiple-libraries)** —
   one matrix row per library, not per platform.
2. **[Inline embedding at dump time](#a-inline-at-dump-time-simplest)** — each
   matrix row's `dump` step points `build-info` at the *same* shared facts
   pack; `-H`/`header` scopes the L2 declared surface to that row's own public
   headers, and the embedded L3/L4/L5 facts are matched against it — the pack
   is collected once per build, not once per library.
3. **[Post-matrix ABI gate](github-action-recipes.md#post-matrix-abi-gate-unified-verdict)** —
   aggregates the per-library verdicts into one exit code, since there is no
   single combined verdict from a fan-out this page's `dump`/`scan` don't do
   natively.

The `abicheck_inputs/` pack itself is produced by whichever [producer](producing-source-facts.md#which-producer-pick-one)
fits your build; the [`collect-facts` Action](producing-source-facts.md#github-actions-the-collect-facts-action)
wires that up (`phase: prepare` before the build, `phase: verify` after)
instead of a hand-rolled build script.

This recipe specifically needs a pack it can `upload-artifact` from the
`build` job and `download-artifact` into separate `dump-baselines` matrix
jobs, so pin `producer` to `wrapper` or `clang-plugin` rather than `auto`:
for a CMake/Bazel/compile-DB project, `auto` resolves to `replay`, whose
`phase: prepare` returns `mode: inline` with an empty `pack-path` and never
creates an `abicheck_inputs/` directory at all — there is nothing to upload,
and every matrix row's `build-info: abicheck_inputs/` would point at a
directory that doesn't exist. (Replay's inline mode is for the single-job
case in [Section A](#a-inline-at-dump-time-simplest), where `dump` runs
right after the build with the checked-out `sources:` tree still on disk —
not for reuse across separate jobs.)

```yaml
# Release workflow — build once, produce a per-library baseline set from the
# one shared facts pack (matches "Recipe A" in Baseline Management, but with a
# manifest row per library instead of a single baseline file).
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # collect-facts pinned to a commit SHA, not a tag: see "Pin both uses:
      # lines" in producing-source-facts.md -- a version tag old enough to
      # predate this sub-action's own introduction can't resolve it at all.
      - uses: abicheck/abicheck/actions/collect-facts@<same-sha-as-below>
        id: facts
        with: { phase: prepare, producer: wrapper, public-roots: "include" }
      - name: Build
        # phase: prepare only *exports* the ABICHECK_CC_* env vars
        # abicheck-cc reads (see its own ::notice::) -- nothing invokes
        # abicheck-cc for you, so front every compile with it explicitly via
        # CMake's compiler-launcher hooks. Swap this line for
        # `-DCMAKE_CXX_FLAGS="$ABICHECK_PLUGIN_FLAGS"` if you pin
        # `producer: clang-plugin` instead.
        run: |
          cmake -DCMAKE_CXX_COMPILER_LAUNCHER=abicheck-cc \
                -DCMAKE_C_COMPILER_LAUNCHER=abicheck-cc -S . -B build
          cmake --build build
      - uses: abicheck/abicheck/actions/collect-facts@<same-sha-as-below>
        id: facts-verify
        with: { phase: verify, producer: ${{ steps.facts.outputs.producer }} }
      - uses: actions/upload-artifact@v4
        with:
          name: release-build
          path: |
            build/lib*.so
            include/
            abicheck_inputs/

  dump-baselines:
    needs: build
    strategy:
      matrix:
        lib:
          - { name: libfoo, so: build/libfoo.so, header: include/foo.h }
          - { name: libbar, so: build/libbar.so, header: include/bar.h }
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: release-build }
      - uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          new-library: ${{ matrix.lib.so }}
          header: ${{ matrix.lib.header }}
          build-info: abicheck_inputs/       # the one shared pack, every row
          depth: source
          new-version: ${{ github.ref_name }}
          output-file: ${{ matrix.lib.name }}.abicheck.json
      - uses: actions/upload-artifact@v4
        with:
          name: baseline-${{ matrix.lib.name }}
          path: ${{ matrix.lib.name }}.abicheck.json

  publish-baselines:
    needs: dump-baselines
    runs-on: ubuntu-latest
    permissions: { contents: write }
    steps:
      - uses: actions/download-artifact@v4
        with: { pattern: baseline-*, merge-multiple: true, path: baselines/ }
      # -R is required here: gh normally infers the repo from a local git
      # checkout, but this job only downloads artifacts, never checks out
      # the repo, so gh has no repository context to infer from.
      - run: gh release upload ${{ github.ref_name }} baselines/*.abicheck.json --clobber -R ${{ github.repository }}
        env: { GH_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
```

```yaml
# PR workflow — same matrix, dumping the candidate build instead of publishing,
# then comparing two JSON snapshots per library (no headers/build-info needed
# at compare time — both sides already have their facts embedded).
jobs:
  build:
    # Identical to the release workflow's `build` job above, just without
    # its `publish-baselines` job at the end -- repeated in full here (not
    # abbreviated) so this block is copy-pasteable on its own.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: abicheck/abicheck/actions/collect-facts@<same-sha-as-below>
        id: facts
        with: { phase: prepare, producer: wrapper, public-roots: "include" }
      - name: Build
        run: |
          cmake -DCMAKE_CXX_COMPILER_LAUNCHER=abicheck-cc \
                -DCMAKE_C_COMPILER_LAUNCHER=abicheck-cc -S . -B build
          cmake --build build
      - uses: abicheck/abicheck/actions/collect-facts@<same-sha-as-below>
        id: facts-verify
        with: { phase: verify, producer: ${{ steps.facts.outputs.producer }} }
      - uses: actions/upload-artifact@v4
        with:
          name: release-build
          path: |
            build/lib*.so
            include/
            abicheck_inputs/

  scan-candidates:
    needs: build
    strategy:
      matrix:
        lib:
          - { name: libfoo, so: build/libfoo.so, header: include/foo.h }
          - { name: libbar, so: build/libbar.so, header: include/bar.h }
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: release-build }
      - name: Dump candidate with build/source evidence
        uses: abicheck/abicheck@v0.3.0
        with:
          mode: dump
          new-library: ${{ matrix.lib.so }}
          header: ${{ matrix.lib.header }}
          build-info: abicheck_inputs/
          depth: source
          output-file: candidate.json
      - name: Download this library's baseline
        # -R is required: this job never checks out the repo either, so gh
        # has no local repository context to infer from (same reason as the
        # release-workflow's gh release upload above).
        run: gh release download --pattern '${{ matrix.lib.name }}.abicheck.json' -D baselines/ -R ${{ github.repository }}
        env: { GH_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
      - name: Compare two snapshots (source/API + binary evidence together)
        uses: abicheck/abicheck@v0.3.0
        with:
          old-library: baselines/${{ matrix.lib.name }}.abicheck.json
          new-library: candidate.json
          format: json
          output-file: report-${{ matrix.lib.name }}.json
          fail-on-breaking: false   # let the post-matrix gate job decide
          fail-on-api-break: false

  abi-gate:
    needs: scan-candidates
    # same aggregation job as "Post-matrix ABI gate (unified verdict)"
```

**Layering onto an existing binary-ABI tool** (the common reason to reach for
this pattern at all): keep that tool's job exactly as-is for the binary ABI
gate, and add the above as a second, independent job for the source/API
surface — don't try to make one job do both. Start the second job **advisory**
(`fail-on-breaking: false`, `fail-on-api-break: false`, report only) while you
build confidence in the new source/API signal on real history; flip on
`fail-on-api-break: true` once it's been quiet for a burn-in period. This
mirrors [Choose Your Workflow](choose-your-workflow.md)'s guidance to not make
one step prove more than its evidence actually supports.

This pattern produces one baseline *file* per library, which is a per-library
instance of the [release-contract baseline](baseline-management.md#two-kinds-of-baseline-release-contract-vs-accepted-main) —
apply that page's release-vs-accepted-main split and refresh discipline to
each file the same way you would to a single-library baseline.
