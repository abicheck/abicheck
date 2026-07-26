# Scenario S5: Single-Build Audit, No Baseline

You want abicheck to run — surfacing internal-noise checks, cross-source
findings, a public-surface report — but there is nothing to compare against
yet: no prior release, no `accepted-main` history, nothing. This is
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S5, and it is a **real, distinct** check kind, not a degraded form of
comparison — advisory by default, since there is no baseline-drift verdict
to gate CI on in the first place.

## The bypass, not a `not_found` failure

Set `baseline-channel: none`. `check-target` detects this and skips
[`resolve-baseline`](../../reference/resolve-baseline.md) entirely, routing
to a plain `scan` (no `--against`) instead of `compare` — it never even
attempts a baseline lookup, so this is not the same as an unresolved
`required: true` channel hitting `not_found` (a hard failure). A `.abicheck.yml`
`checks:` entry with `channel: none` defaults its own `gate_mode` to
`advisory`, matching this semantic:

```yaml
targets:
  libfoo:
    binary_pattern: "lib/libfoo.so*"
    checks:
      - channel: none
        depth: source   # or binary/headers/build -- an audit at any depth
```

```yaml
- uses: abicheck/abicheck/actions/check-target@v0.5.0
  with:
    name: libfoo
    target-kind: library   # required -- app-consumer/plugin-contract have
                            # no scan-mode equivalent to --used-by/--required-symbols
    baseline-channel: none
    requested-depth: source
```

See the [`check-target` reference](../../reference/check-target.md) for the
full bypass mechanics, and
[GitHub Action: Source Scans § Single-release audit](../../use/github-action-source-scans.md#single-release-audit-no-baseline)
for the equivalent one-step `mode: scan` (no `against:`) wiring.

## When to move past this scenario

- **You now have something to compare against** → any other scenario;
  `baseline-channel: none` is a starting point, not a permanent choice for a
  project that will eventually publish a release or track `main`.
- **`target-kind: app-consumer`/`plugin-contract`** — not supported with
  `baseline-channel: none`: `scan` has no `--used-by`/`--required-symbols`
  equivalent, so an app-consumer/plugin-contract audit with no baseline has
  no scope to check against. Use `kind: library` for a no-baseline audit.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [`check-target` Action Reference](../../reference/check-target.md) — the full bypass mechanics and report shape.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the one-step `mode: scan` equivalent.
