# Scenario S5: Single-Build Audit, No Baseline

You want abicheck to run — surfacing internal-noise checks, cross-source
findings, a public-surface report — but there is nothing to compare against
yet: no prior release, no `accepted-main` history, nothing. This is
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S5, and it is a **real, distinct** check kind, not a degraded form of
comparison — advisory by default, since there is no baseline-drift verdict
to gate CI on in the first place.

## The declared target: `compare --no-baseline` (ADR-068 D2)

[ADR-068](../../contribute/adr/068-one-comparison-product-and-scan-retirement.md)
D2 makes this scenario a *scope* of the one comparison product rather than a
second command: `abicheck compare --no-baseline CANDIDATE`, with OLD
recorded in
[ADR-065](../../contribute/adr/065-comparison-scope-selection-and-completeness.md)'s
`declared_absent` acquisition state. `scan` is retired with **no deprecation
window** (ADR-068 D8) once that migration completes, so treat
`compare --no-baseline` as where this scenario is going.

**It does not get you there yet.** See the section below for the CLI path
that actually works today, and the
[known gap](../../contribute/known-gaps.md#compare-no-baseline-does-not-yet-reproduce-scans-audit-mode-findings)
for the exact defect and what closing it requires.

## The working CLI path today: `scan` (no `--against`)

`abicheck scan CANDIDATE` with no `--against` is the way to run a
single-build audit from the CLI today — it runs the same ADR-035
cross-source/single-release checks (`CROSS_SOURCE_EVOLUTION_CHECKS`, e.g.
`exported_not_public`, `private_header_leak`,
`unversioned_exported_symbol`, `rtti_for_internal_type`,
`public_not_exported`, `public_to_internal_dependency`, and the rest of the
registered set) and reports whatever it finds, cleanly, whether the
candidate is clean or not:

```bash
abicheck scan build/libfoo.so -H include/
```

**`compare --no-baseline` is verified broken for this scenario, not merely
incomplete**, in two distinct ways depending on the candidate's shape:

- **A stored `.abi.json` candidate crashes.** All eleven G20 audit fixtures
  (`catalog/cases/case14{3,4,5,6,7,8,9}_*`, `case15{0,1}_*`, `case181_*`)
  abort with an unhandled `AssertionError` from
  `workflows/no_baseline_compare.py`'s `assert not diff.changes` guard
  instead of rendering the finding, while `scan` reports each one's
  documented verdict.
- **A live binary plus `-H` renders an empty result.** Against
  `examples/workflows/audit-release`'s `libgreet.so`, `scan libgreet.so
  --header include` reports `exported_not_public`, while
  `compare --no-baseline libgreet.so --header include --format json` exits 0
  with `"changes": []` and no cross-source block at all.

The cause is structural: the audit is implemented as a self-diff that then
asserts the diff is empty, an invariant the per-side cross-source stages
`compare()` gained in ADR-068 Phase 2a/2b legitimately violate. This CLI
slice also doesn't yet accept `--sources`/`--build-info`/`--depth`,
a secondary `--write`, or `--dry-run` — but the missing findings are the
reason to avoid it here, not those. Use `scan` (above) until the
[known gap](../../contribute/known-gaps.md#compare-no-baseline-does-not-yet-reproduce-scans-audit-mode-findings)
is closed.

## The Action: `mode: scan`, no `against`

The GitHub Action does not yet expose `--no-baseline` as a `mode: compare`
input, and `mode: compare` requires both `old-library`/`new-library`. So at
the Action level, a no-baseline audit — including one that needs L3/L4
evidence — still goes through `mode: scan` with no `against`/`abi-baseline`
resolved:

```yaml
targets:
  libfoo:
    binary_pattern: "lib/libfoo.so*"
    checks:
      - channel: none
        depth: source   # or binary/headers/build -- an audit at any depth
```

```yaml
- uses: abicheck/abicheck/actions/check-target@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
  with:
    name: libfoo
    target-kind: library   # required -- app-consumer/plugin-contract have
                            # no scan-mode equivalent to --used-by/--required-symbol
    baseline-channel: none
    requested-depth: source
```

Set `baseline-channel: none`. `check-target` detects this and skips
[`resolve-baseline`](../../reference/resolve-baseline.md) entirely, routing
to a plain `scan` (no `--against`) instead of `compare` — it never even
attempts a baseline lookup, so this is not the same as an unresolved
`required: true` channel hitting `not_found` (a hard failure). A `.abicheck.yml`
`checks:` entry with `channel: none` defaults its own `gate_mode` to
`advisory`, matching this semantic.

See the [`check-target` reference](../../reference/check-target.md) for the
full bypass mechanics, and
[GitHub Action: Source Scans § Single-release audit](../../use/github-action-source-scans.md#single-release-audit-no-baseline)
for the equivalent one-step `mode: scan` (no `against:`) wiring.

## When to move past this scenario

- **You now have something to compare against** → any other scenario; a
  no-baseline audit (`scan CANDIDATE` with no `--against` on the CLI,
  `baseline-channel: none` on the Action — see "The working CLI path today"
  above for why `compare --no-baseline` is not the safe spelling here) is a
  starting point, not a permanent choice for a project that will eventually
  publish a release or track `main`.
- **`target-kind: app-consumer`/`plugin-contract`** — not supported with
  `baseline-channel: none`: `scan` has no `--used-by`/`--required-symbol`
  equivalent, so an app-consumer/plugin-contract audit with no baseline has
  no scope to check against. Use `kind: library` for a no-baseline audit.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [`check-target` Action Reference](../../reference/check-target.md) — the full bypass mechanics and report shape.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the one-step `mode: scan` equivalent.
