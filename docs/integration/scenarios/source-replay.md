# Scenario S7: Source Scan via Compile-DB Replay

You have a compile database (or something abicheck can derive one from) and
want PR-scoped source-level checks — inline function bodies, template
instantiations, macro values, default arguments, `constexpr` — not just the
binary/header surface. [ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S7: on a pull request, scope the (expensive) replay to just the changed
translation units; on a release/nightly run, replay the whole target.

This is the **replay** evidence producer — abicheck derives source facts
itself from your existing compile database, with no build-time integration
step. Compare with [S8/S9: Source Facts From the Build Itself](build-integrated-facts.md),
where the *build* emits source facts as it compiles instead.

## What you need

- A source checkout (`sources:`).
- A compile database, or something abicheck can derive one from
  zero-config (CMake configure-only, a Bazel `aquery`, or a Make dry-run
  transcript) — see
  [Evidence Depth § Obtaining a compile database](../../use/evidence-depth.md#obtaining-a-compile-database-without-a-full-build).
- `clang` on the runner. With the latest published release (`@v0.6.0`, used
  below), the default `dependency-source: conda-forge` installs castxml but
  does **not** install clang on Linux or macOS. Pin
  `dependency-source: conda-forge-clang20`, choose `system` when clang is
  already provisioned by the runner, or install clang yourself; Windows also
  needs its compatible tools installed explicitly. Without clang, L3
  build-context evidence (this scenario's compile database) is still
  collected, and the structural L5 target/compile-unit/source graph still
  builds from that L3 evidence alone — only L4 replay and the clang-backed
  call/type/include-graph edges of L5 are skipped, not the whole source scan
  or the whole L5 graph; see [Evidence Depth](../../use/evidence-depth.md)
  for what each layer needs. Check the `layers`/coverage output to confirm L4
  actually ran. See [GitHub Action: dependency-source](../../use/github-action.md).
- For a PR run: `fetch-depth: 0` on checkout, so the base ref is available to
  seed the diff scope.

## Two entry points, same evidence

**One-step, root-Action `mode: compare`** — the simplest wiring, classifies
changed paths, runs the pinned evidence level, and compares in one step
([ADR-068's Action-input-lifecycle amendment](../../contribute/adr/068-one-comparison-product-and-scan-retirement.md#amendment-2026-09-11-the-action-input-lifecycle-mode-scan-retired-outright)
retired the once-separate `mode: scan` outright — this is now the same
`mode: compare` the rest of this page's two-sided examples use, with
`old-library` as the baseline):

```yaml
- uses: abicheck/abicheck@v0.6.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/
    sources: .
    since: origin/${{ github.base_ref }}
```

See [GitHub Action: Source Scans](../../use/github-action-source-scans.md)
for every input this mode accepts, cost estimation, and gating on a specific
cross-source check.

**Composed via `check-target`/`check-project.yml`** — when this check is one
of several a `.abicheck.yml` `targets:`/`profiles:` block declares,
`evidence-producer: replay` is the bridge. `actions/check-target` is included
in `v0.6.0`, so pin that release tag for a reproducible workflow:

```yaml
- uses: abicheck/abicheck/actions/check-target@v0.6.0
  with:
    name: libfoo
    requested-depth: source
    evidence-producer: replay
    sources: .   # replay's own default when omitted -- a bare pointer, no build step needed
    # ... baseline/candidate inputs per your baseline channel ...
```

See the [`check-target` reference](../../reference/check-target.md) for the
full `evidence-producer` contract and how it composes with `collect-facts`.

## When to move past this scenario

- **Your build can emit source facts itself, avoiding a second replay pass**
  → [S8/S9: Source Facts From the Build Itself](build-integrated-facts.md)
  (the `abicheck-cc` wrapper or the Clang plugin).
- **You have several DSOs sharing one facts pack** → S16,
  [Build Info & Sources](../../learn/build-source-data.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the canonical source-intelligence `mode: compare` reference.
- [Evidence Depth](../../use/evidence-depth.md) — the depth ladder and compile-DB acquisition options.
- [What Each Level Sees](../../learn/what-each-level-sees.md) — a worked example of what L3/L4/L5 actually produce.
