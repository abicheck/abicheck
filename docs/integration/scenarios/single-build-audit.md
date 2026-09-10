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

**It gets you there now.** The two defects that made this section read
"use `scan` instead" until 2026-09-09 — a stored `.abi.json` candidate
aborting with an `AssertionError`, and a live binary plus `-H` rendering an
empty result — are both fixed, and
`tests/parity/test_no_baseline_audit_corpus_parity.py` pins that
`compare --no-baseline` reports at least every check `scan` does — counted
per finding kind, so no capability is lost — across all eleven G20 audit
fixtures, while manufacturing no comparison of its own. It is a
no-capability-loss floor, not an assertion that the two produce
byte-identical reports: `scan`'s per-check coverage rows (status/detail/
providers) have no audit-report equivalent yet, see
[`known-gaps.md`](../../contribute/known-gaps.md).

## The CLI path: `compare --no-baseline`

```bash
abicheck compare --no-baseline build/libfoo.so -H include/
```

This runs the same ADR-035 cross-source/single-release checks
(`CROSS_SOURCE_EVOLUTION_CHECKS`, e.g. `exported_not_public`,
`private_header_leak`, `unversioned_exported_symbol`,
`rtti_for_internal_type`, `public_not_exported`,
`public_to_internal_dependency`, and the rest of the registered set) that
`scan CANDIDATE` (no `--against`) runs, and reports whatever it finds under
`findings[]`.

Two things to know about the report shape, both direct consequences of
ADR-068 D2:

- **`changes[]` is always empty, and `verdict` is always `null`.** An audit
  reports no addition, no removal, and no compatibility verdict — the
  candidate-side findings live under `findings[]`. A consumer parsing
  `changes` off this report is reading the wrong key.
- **Every finding carries an evolution state**, which with OLD
  `declared_absent` is always `persistent` (the check fired) or
  `not_evaluated` (the check's evidence gate closed). `introduced` and
  `resolved` are unreachable here by construction: both assert something
  about a baseline this run was told does not exist (ADR-068 D3).

`--header`/`--include`, `--sources`/`--build-info`, `--depth`, `--contract`,
`--policy`/`--suppress`, `--dry-run` and `-o/--output` all work. `--format`
accepts `json`, `markdown`, `sarif`, `junit` and `oneline`; `html` and
`review` are a declared usage error, because both render a *comparison*
(verdict badge, OLD → NEW counts, release recommendation) and an audit has
none of those — `oneline` is the closest honest equivalent to a review
digest. An `old=`-prefixed evidence input (`--sources old=…`) is a usage
error too: there is no OLD side for it to describe.

Exit codes: hygiene findings are advisory and never gate on their own, so a
clean run and a run reporting several findings both exit `0`. The three
orthogonal axes still apply — see
[exit codes](../../reference/exit-codes.md#compare-no-baseline-adr-068-d2-single-artifact).

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
  no-baseline audit (`compare --no-baseline CANDIDATE` on the CLI,
  `baseline-channel: none` on the Action) is a starting point, not a
  permanent choice for a project that will eventually publish a release or
  track `main`.
- **`target-kind: app-consumer`/`plugin-contract`** — not supported with
  `baseline-channel: none`: `scan` has no `--used-by`/`--required-symbol`
  equivalent, so an app-consumer/plugin-contract audit with no baseline has
  no scope to check against. Use `kind: library` for a no-baseline audit.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [`check-target` Action Reference](../../reference/check-target.md) — the full bypass mechanics and report shape.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the one-step `mode: scan` equivalent.
