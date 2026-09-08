# Scenario S5: Single-Build Audit, No Baseline

You want abicheck to run — surfacing internal-noise checks, cross-source
findings, a public-surface report — but there is nothing to compare against
yet: no prior release, no `accepted-main` history, nothing. This is
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S5, and it is a **real, distinct** check kind, not a degraded form of
comparison — advisory by default, since there is no baseline-drift verdict
to gate CI on in the first place.

## The recommended CLI path: `scan` (no `--against`)

`abicheck scan CANDIDATE` with no `--against` is the way to run a
single-build audit from the CLI today — it runs the same eleven ADR-035
cross-source/single-release checks (`exported_not_public`,
`private_header_leak`, `unversioned_exported_symbol`,
`rtti_for_internal_type`, `public_not_exported`,
`public_to_internal_dependency`, and five more) and reports whatever it
finds, cleanly, whether the candidate is clean or not:

```bash
abicheck scan build/libfoo.so -H include/
```

**`compare --no-baseline` (ADR-068 D2) is not yet a safe replacement for
this scenario — verified broken, not just incomplete.** It takes one
operand and records the OLD side with
[ADR-065](../../contribute/adr/065-comparison-scope-selection-and-completeness.md)'s
`declared_absent` acquisition state, self-diffing the candidate through the
same cross-source-check pipeline in principle — but when the candidate
actually *has* one of the hygiene problems this scenario exists to catch
(confirmed live against `catalog/cases/case143_audit_accidental_export`'s
own accidental-export fixture), `workflows/no_baseline_compare.py`'s
`run_no_baseline_compare()` hits its own internal
`assert not diff.changes` guard and crashes with an unhandled
`AssertionError` instead of rendering the finding. It only ever "worked" for
a clean candidate — i.e. a self-diff that legitimately produces zero
changes, which never exercises the code path the assertion protects. This
CLI slice also doesn't yet accept `--sources`/`--build-info`/`--depth`/
cross-toolchain flags, a secondary `--write`, or `--dry-run`, and only
`--format json`/`markdown` render at all — but the crash is the reason to
avoid it here, not those. Use `scan` (above) until this is fixed.

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

- **You now have something to compare against** → any other scenario;
  `--no-baseline` (CLI) / `baseline-channel: none` (Action) is a starting
  point, not a permanent choice for a project that will eventually publish
  a release or track `main`.
- **`target-kind: app-consumer`/`plugin-contract`** — not supported with
  `baseline-channel: none`: `scan` has no `--used-by`/`--required-symbol`
  equivalent, so an app-consumer/plugin-contract audit with no baseline has
  no scope to check against. Use `kind: library` for a no-baseline audit.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [`check-target` Action Reference](../../reference/check-target.md) — the full bypass mechanics and report shape.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the one-step `mode: scan` equivalent.
