# Scenario S14: Multi-DSO Release Bundle

Your libraries ship together, as a set, with dependencies *between* them —
not as independent artifacts someone could reasonably compare one at a time.
Removing a symbol from one library that another library in the same release
still calls is a real break, but it's a **cross-library** finding no
single-library comparison can see.
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S14 is deliberately distinct from
[S15](multi-dso-project.md) (multiple *independent* targets, N separate
reports): a bundle is **one report**, with cross-library findings (soname
skew, provider-set changes, missing-library detection) as first-class
results, not N reports someone has to manually cross-reference.

This is the existing directory/`--instantiation-manifest` bundle-analysis capability
`abicheck compare` already provides — see
[Multi-Binary Releases](../../use/multi-binary.md) for the full
model (what counts as a bundle, `--instantiation-manifest` vs. plain directory mode, and
the cross-library findings it produces). This page covers only how a bundle
fits into the project-integration model above that.

## Declaring a bundle

`.abicheck.yml`'s `bundles:` block groups member targets:

```yaml
targets:
  libpvxs:
    binary_pattern: "lib/libpvxs.so*"
    bundle: pvxs-release
  libpvxsIoc:
    binary_pattern: "lib/libpvxsIoc.so*"
    bundle: pvxs-release

bundles:
  pvxs-release:
    targets: [libpvxs, libpvxsIoc]
    checks:
      - channel: accepted-main
        depth: binary   # a bundle check is binary-depth only -- see below
```

`check-project.yml`'s [run plan](../../reference/run-plan-schema.md) emits
one `kind: bundle` check for `pvxs-release` alongside (not instead of) any
per-target checks each member also declares on its own — a library can be
both a bundle member and independently checked (`bundle_only: false`, the
default).

## Depth is binary-only, by design

A bundle-scoped check is restricted to `requested-depth: binary` — its
baseline is always the members' raw staged binaries (no per-member historical
headers/build-context evidence exists in a bundle baseline today), so
`headers`/`build`/`source` are rejected outright rather than silently
comparing against the *current* checkout's headers on both sides. See the
["Bundle members: why `stage_binary` matters" section](../../reference/publish-baseline.md)
of the `publish-baseline`/`update-main-baseline` reference for the full
rationale and the `binaries/` staging this depends on.

## When to move past this scenario

- **Your libraries don't actually depend on one another** — you just build
  them together — → [S15: Multiple Independent Targets](multi-dso-project.md).
- **You want deeper (header/build/source) evidence for one specific member**
  → declare that member with its *own* `checks:` (independent of the bundle
  check) at whatever depth you need — see
  [S6](header-aware-check.md)/[S7](source-replay.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Multi-Binary Releases](../../use/multi-binary.md) — the canonical bundle-analysis reference.
- [`publish-baseline`/`update-main-baseline` Reference](../../reference/publish-baseline.md) — how a bundle baseline is staged.
