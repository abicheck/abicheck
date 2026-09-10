# GitHub Action: Source Scans & Build Evidence

The main [GitHub Action](github-action.md) page covers installation, inputs,
outputs, and the everyday compare recipes. This page is the
**source-intelligence companion**: running `mode: compare` with build/source
evidence from CI, pinning the `depth` dial, single-release audits, cost
estimation, cross-check gating, and the three ways to feed L3/L4/L5
build/source evidence into a baseline. For what the evidence layers *are*,
see [Evidence & Detectability](../learn/evidence-and-detectability.md); for
the underlying CLI flags, see [Evidence Depth](evidence-depth.md).

> **See also.** If this check is one of several a project-wide
> `.abicheck.yml` `targets:`/`profiles:` block declares (not a standalone
> root-Action step), see
> [S7: Source Scan via Compile-DB Replay](../integration/scenarios/source-replay.md)
> and
> [S8/S9: Source Facts From the Build Itself](../integration/scenarios/build-integrated-facts.md)
> for the `check-target`/`evidence-producer` composition this page's inputs
> map onto.

## Source-aware comparisons (build & source evidence)

`mode: compare` (the default) is the **recommended entry point** for source
intelligence against a real baseline. It always runs the compiler-free
pattern pre-scan and every intra-version cross-source check
(`CROSS_SOURCE_EVOLUTION_CHECKS`), and takes the identical
`depth`/`since`/`changed-path`/`sources`/`build-info` inputs
`mode: scan` does — running the pinned evidence level (L3 build context / L4
source-ABI replay / L5 source graph) and comparing against `old-library`. It
emits a single coverage-annotated report saying, per layer, what ran versus
what was skipped.

**`mode: scan` remains the one to reach for** when this step also needs: a
single-release audit with no baseline at all (see [Single-release
audit](#single-release-audit-no-baseline) below — `mode: compare` has no
no-baseline input yet) or `crosscheck`'s `KEY=error` promotion syntax —
neither has a `compare` equivalent at all. Three inputs that used to be on
this list are **retired** rather than translated (ADR-068's second
2026-09-09 amendment, ruling (b)): `new-library-set` (the multi-library
audit mode — blocked on ADR-065 S3's component inventories; run one `scan`
per library meanwhile), `risk-rules` (and with it the risk-driven `auto`
depth escalation — pin `depth:` explicitly instead) and `build-target` (use
`mode: dump` to narrow a multi-target workspace). Setting any of them on a
`mode: scan` step is now an explicit `::error::`, not a silent downgrade. A
`budget` wall-clock guard is
different: `compare` itself now has `--budget`, but the Action's `mode:
compare` input still doesn't read it (a translation gap, not a missing CLI
capability — see the table below).

### `mode: scan` is being retired — and is already translated where it can be

[ADR-068](../contribute/adr/068-one-comparison-product-and-scan-retirement.md)
retires `scan` as a second analysis product. The CLI command is removed
outright, with **no deprecation window** (D8). Your workflow YAML is the one
thing the ADR protects: the Action absorbs the change, so `mode: scan` keeps
working across the CLI's removal and is itself dropped only in the Action's
own next major, per [ADR-047](../contribute/adr/047-github-actions-integration-model.md)'s
input lifecycle.

Concretely, `action/run.sh` already re-issues a **baseline** `mode: scan`
step as `abicheck compare` wherever the two commands are proven equivalent.
Its `_SCAN_NEEDS_LEGACY_CLI` predicate is the exhaustive list of request
shapes that still fall back to the legacy `scan` CLI — each is an
independently verified capability gap, not a stylistic preference, and each
is tracked in
[known gaps](../contribute/known-gaps.md#the-actions-mode-scan-still-routes-several-request-shapes-to-the-legacy-scan-cli):

| Your step sets… | Why it still runs legacy `scan` |
|---|---|
| no `against`/`abi-baseline` (audit-only) | `compare --no-baseline` does not reproduce the audit's findings yet |
| `budget` | `compare` has its own `--budget` flag now (exit `5` on overflow applies to both commands) — this is an Action-translation gap (`action/run.sh` still forces `INPUT_BUDGET` onto the legacy route), not a missing CLI capability, and closes once that translation is updated |
| `crosscheck` | no `compare` flag equivalent; `policy.overrides.<CHANGE_KIND>` in `.abicheck.yml` is the replacement spelling for the `KEY=error` half, and the engine axis behind it is ADR-064 surface a later slice retires |
| no `depth` at all | `compare` infers a deeper rung from `sources`/`build-info` if either is given, otherwise caps at `headers`; `scan`'s own `auto` now always resolves to `headers` (the risk-driven escalation is retired, ADR-068 (b)), so the two agree except when `sources`/`build-info` is given |
| `depth: build` or `depth: source` | `compare` now has its own evidence-contract floor (exit `7`), but it's narrower than `scan`'s: `scan`'s floor is unconditional, while `compare`'s is exempted whenever both operands are already-serialized snapshots (see [Evidence Depth](evidence-depth.md)'s "pinned depth is a contract" warning) — routing onto it could silently narrow the guarantee this Action's users rely on, so it stays on the legacy CLI |
| a shared `header:`/`include:` **and** a side-specific `old-header`/`new-header`/`public-header-dir`/`old-include`/`new-include` | `compare`'s side-aware flags don't cover that combination identically |
| an `against:` ending `.json`/`.json.gz`/`.json.zst`, or any file content-detected as a JSON snapshot | `compare` and `scan` disagree on snapshot-baseline handling |
| `output-file`, or an effective `format: json` (from the input or an `extra-args` override), or a `-o`/`--output` in `extra-args` | the two commands write different file shapes |
| `--write` in `extra-args` | `compare` accepts `--write` too, but the Action's own PR-comment JSON injection already manages a `--write json=…` slot itself, so a user-supplied one forces legacy routing to avoid a collision, not because the flag is missing on `compare` |
| `--abi3 FLOOR` in `extra-args` | `compare` accepts `--abi3` too (`cli_compare_helpers.fold_abi3_into_extra_changes`, ADR-068 Phase 2d) — but with genuinely different gating: `scan`'s own audit only ever lands the finding in the advisory `crosscheck` report, gated solely via `--crosscheck python_stable_abi_violation=error`, while `compare --abi3` folds it into the real diff that policy/suppression/verdict score like any other finding. Routing a baseline `--abi3` scan onto `compare` would silently change an existing workflow's verdict/exit code, so any value forces legacy CLI |
| a scan-only flag in `extra-args` (`--frontend-context`, `--allow-ast-frontend-fallback`, and the rest of the compile-context family) | no `compare` equivalent for the flag itself |
| anything other than an explicit bare `--pattern-verdicts` in `extra-args` | `compare`'s pattern-verdict modulation is unconditional (ADR-068 D4) with no flag left to disable it, while `scan --against` still defaults it off |

The routing is invisible in your YAML: the same inputs, outputs, and verdict
either way. It matters only when you read the step's log and see which
command actually ran. **The ~17 `mode: scan` branches in `action/run.sh`
disappear only when the last row above closes** — the predicate shrinks, it
does not vanish, until then.

!!! warning "One behavior change already landed for baseline `mode: scan`"
    ADR-068's 2026-09-09 amendment stopped `scan --against` from stripping
    cross-source findings to advisory-only. A baseline scan of a library
    with an accidental export, an unversioned exported symbol, or any other
    cross-source-only issue now reports that finding as a real, policy-gated
    result — the same way `compare` always did. It can raise the step's
    verdict, and under a severity preset that treats `RISK`/`API_BREAK` as
    error-level, its exit code. This is a documented breaking change to the
    `mode: scan` contract, not a regression.

> **New to what these layers see?** The concept-track
> [level-by-level walk-through](../learn/what-each-level-sees.md)
> shows, on one running example, the concrete data each level (L0→L5) produces
> and where each goes blind — the "why" behind the inputs below.

The common case needs four inputs — the built binary, its public headers, the
source tree, and a baseline (`old-library`) to compare against:

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

      - name: Source-aware comparison
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json   # committed, or use abi-baseline: latest-release
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          depth: source   # pin the source-ABI replay -- nothing escalates on its own (see below)
          since: origin/${{ github.base_ref }}   # focus on changed files
          fail-on-api-break: true       # gate on source/API breaks too
```

`clang` is installed automatically (for L4/L5). On a `pull_request` run,
`since: origin/${{ github.base_ref }}` focuses the (expensive) source replay on
the files the PR touched — pair it with `fetch-depth: 0` in `checkout` so the
base ref is available.

### Pin the depth

`depth` is the single evidence-depth dial, and `mode: compare` reads the same
values `mode: scan` does. **Pin it** — neither command escalates on its own
any more (ADR-068's second 2026-09-09 amendment retired `scan`'s risk-driven
`auto`, ruling (b), so a `mode: scan` step that relied on it now gets
`headers` and must pin the rung it needs). Omitting `depth` is not risk-based
selection either way: `compare` infers `source`/`build` from whichever of
`sources`/`build-info` is supplied, and bottoms out at `headers` when neither
is given; `scan` always resolves to `headers`.

**A pinned depth is a contract on `compare` too.** `scan --depth source`
with no `--sources`/`--build-info` given hard-fails before comparing (exit 7,
"pinned depth 'source' ... needs source evidence, but no --sources/--build-info
was given") — a pinned depth without the evidence to back it is an error, not
a silent downgrade. `compare --depth build|source` now enforces the same floor
and reports it through the same axis: an operand this run extracts live that
cannot reach the pinned rung records `evidence_contract_error` and exits `7`
(`policy/depth_evidence_contract.py`; the full per-command account is in
[`docs/use/evidence-depth.md`](evidence-depth.md)).

The one carve-out is a side that is *already* a serialized snapshot
(`old-library: abi-baseline.json`): that operand was not extracted by this run
at all, so there is no "reached a shallower rung than requested" failure to
report for it, and such a pair still exits `0`/`2`/`4` on its own contents.
So `sources:`/`build-info:` stay load-bearing under `mode: compare` in exactly
that case — a stored-snapshot operand pinned to `depth: build`/`source` is not
checked against the pin, while a live one is.

```yaml
      - uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          depth: source         # source-ABI replay of changed TUs (deterministic)
          since: origin/main    # scope the L4 replay to the PR's changed TUs
```

| Want… | Set |
|-------|-----|
| Cheap build-flag drift only (L3) | `depth: build` |
| Source semantics on changed TUs (+ L5 graph) | `depth: source` + `since:` |
| Full source-ABI replay of the whole library | `depth: source` with no `since:`/`changed-path` (an unseeded `depth: source` already analyses the whole current target — ADR-043) |
| Risk-driven depth selection (`auto`) | *Retired* (ADR-068's second 2026-09-09 amendment, ruling (b)). An omitted `depth` resolves to `headers` in every mode — pin `depth: build`/`source` for the rung the risk score used to escalate to. |
| A `budget:` wall-clock guard (`BUDGET_OVERFLOW` rather than overrun) | `mode: scan` only — `mode: compare` reads no `budget` input yet |

!!! note "The old `scan-mode`/`source-method` inputs and the `full` depth are gone"
    Earlier releases exposed `scan-mode` (`pr`/`pr-deep`/`baseline`/`audit`) and
    `source-method` (`s0…s6`) Action inputs, plus a fifth `depth: full` rung.
    As of the ADR-043 pre-1.0 CLI reset all three are removed outright, not
    deprecated — the CLI's `--depth` no longer accepts `full`/`--mode`/
    `--source-method`/`--max` at all (a plain usage error). Use `depth`
    (omitting `old-library`/`abi-baseline` for an audit-only `scan` run);
    `full` collapsed into `source`, since the two only ever differed in
    replay *scope*, and an unseeded `depth: source` already resolves to the
    whole target. The mapping from the old axes is in the
    [Removed scan axes appendix](companion-commands.md#removed-scan-axes-s0s6-mode-source-method-max).

### Single-release audit (no baseline)

Run the intra-version hygiene checks against one build — no old version needed.
Useful as a standing lint on the default branch. `mode: compare` has no
no-baseline input at the Action level yet, and the CLI's own
`compare --no-baseline` does not report these findings ([known
gap](../contribute/known-gaps.md#compare-no-baseline-does-not-yet-reproduce-scans-audit-mode-findings);
see [Scenario S5](../integration/scenarios/single-build-audit.md)), so this
stays `mode: scan` — and, per the routing table above, runs the legacy CLI:

```yaml
      - uses: abicheck/abicheck@v0.5.0
        with:
          mode: scan
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          # No `against`/`abi-baseline` on this step -- scan already runs
          # audit-only whenever no baseline is given.
```

### Estimate cost before committing to a depth

`dry-run: 'true'` prints the resolved depth/scope and the projected
per-layer cost (TU count, seconds) — without comparing anything, always
exiting 0. Works the same on `mode: compare` as on `mode: scan`. Handy when
sizing a budget for a large repo:

```yaml
      - uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/
          sources: .
          depth: source
          dry-run: 'true'
```

### Gate CI on a specific cross-source check

Cross-source findings are advisory by default. Promoting one to `error` makes a
finding for it exit `2` (the API_BREAK tier); add `fail-on-api-break: true` so
that exit turns the step red. `crosscheck`'s `KEY=error` promotion syntax has
no `compare`-mode equivalent yet (it's the `--crosscheck` CLI flag, still
`scan`-only), so this stays `mode: scan`:

```yaml
      - uses: abicheck/abicheck@v0.5.0
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
        uses: abicheck/abicheck@v0.5.0
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
[Build Info & Sources](../learn/build-source-data.md) for the underlying
CLI-level flows.

## Recommended flow: a multi-library release with one shared facts pack

**This is the canonical multi-DSO recipe** — [Action Reference](github-action.md)
and [More Recipes](github-action-recipes.md) link here rather than restating
it, so update this section (not a copy) when the recipe changes.

This is the concrete, Action-supported answer to a specific, recurring shape
of project: several libraries built from one source tree, one facts pack
collected **once** for the whole build (via [source
replay](producing-source-facts.md#full-source-scan-replay-from-a-compile-database),
the [`abicheck-cc` wrapper](producing-source-facts.md#wrapper-injection-the-abicheck-cc-compiler-wrapper),
or the [Clang plugin](producing-source-facts.md#plugin-injection-the-clang-facts-plugin)),
and no single ".so that represents the release" to hand `scan`/`dump` — which,
per [Mode/input compatibility](github-action.md#modeinput-compatibility), only
accept **one** artifact each; there is no `scan`/`dump` equivalent of
`compare`'s directory/package fan-out.

**Scope caveat:** every library in the recipe below points at the *same*
shared `abicheck_inputs/` pack, with no per-target projection check that the
pack's facts actually belong to that specific library rather than another
one built from the same tree. That's fine for what this recipe supports
today — a build-wide source audit, and a per-target *header*-depth check
(`-H`/`header` scopes each matrix row's L2 declared surface correctly, which
is independent of the shared pack). It is **not** enough to claim per-target
*source*-depth coverage: nothing here proves library A's embedded L3/L4/L5
facts didn't actually come from library B's translation units. Recording
that distinction (a `build-output.json` `evidence.projection: "declared"` vs.
`"inferred"` tag, per [ADR-047 §9](../contribute/adr/047-github-actions-integration-model.md))
needs the per-target projection validator tracked as G30 plan item P1.1,
not yet implemented — until then, treat this recipe's `depth: source` dumps
as build-wide source evidence applied uniformly, not as independently-proven
per-library source coverage.

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
3. **[Post-matrix ABI gate](github-action-recipes.md#post-matrix-abi-gate-fan-out-builds-fan-in-verdict)** —
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
      # Same SHA as the build job's collect-facts calls above -- this step
      # consumes the abicheck_inputs/ pack that produced, and a version tag
      # here could disagree with collect-facts' pinned SHA on the pack
      # schema/fact-set recipe, risking a mismatch or missing source facts
      # the same "pin both uses: lines" rule in producing-source-facts.md
      # exists to prevent (Codex review).
      - uses: abicheck/abicheck@<same-sha-as-above>
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
        # Same SHA as the build job's collect-facts calls above (Codex
        # review) -- see the note on the release workflow's equivalent step.
        uses: abicheck/abicheck@<same-sha-as-above>
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
        uses: abicheck/abicheck@<same-sha-as-above>
        with:
          old-library: baselines/${{ matrix.lib.name }}.abicheck.json
          new-library: candidate.json
          format: json
          output-file: report-${{ matrix.lib.name }}.json
          fail-on-breaking: false   # let the post-matrix gate job decide
          fail-on-api-break: false
      - name: Upload this library's report
        uses: actions/upload-artifact@v4
        with:
          name: report-${{ matrix.lib.name }}
          path: report-${{ matrix.lib.name }}.json

  abi-gate:
    needs: scan-candidates
    # same aggregation job as "Post-matrix ABI gate (unified verdict)" --
    # downloads with `pattern: report-*`, `merge-multiple: true`
```

**Layering onto an existing binary-ABI tool** (the common reason to reach for
this pattern at all): keep that tool's job exactly as-is for the binary ABI
gate, and add the above as a second, independent job for the source/API
surface — don't try to make one job do both. Start the second job **advisory**
(`fail-on-breaking: false`, `fail-on-api-break: false`, report only) while you
build confidence in the new source/API signal on real history; flip on
`fail-on-api-break: true` once it's been quiet for a burn-in period. This
mirrors [Choose Your Workflow](../start/choose-your-workflow.md)'s guidance to not make
one step prove more than its evidence actually supports.

This pattern produces one baseline *file* per library, which is a per-library
instance of the [release-contract baseline](baseline-management.md#two-kinds-of-baseline-release-contract-vs-accepted-main) —
apply that page's release-vs-accepted-main split and refresh discipline to
each file the same way you would to a single-library baseline.
