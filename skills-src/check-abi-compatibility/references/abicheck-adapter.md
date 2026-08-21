# abicheck adapter — CLI recipes for this workflow

Backend mechanics for the parent skill's decision procedure: the exact
commands, flag combinations, and report fields the workflow drives, kept out
of `SKILL.md` per this skill source's Layer A/B/C split. Getting the two
artifacts in the first place is
[getting the two sides](getting-the-two-sides.md); evidence-depth choice is
[evidence and depth](../../shared/evidence-and-depth.md); reading the result
is [report interpretation](../../shared/report-interpretation.md). This file
is only the recipes that connect them.

## Preflight

```bash
abicheck --version
```

Check the reported version against this skill's declared
`abicheck-version-range` (this skill's own frontmatter). Outside that range,
stop rather than proceed on an unvalidated surface.

## The canonical comparison

```bash
abicheck compare OLD NEW \
  --depth headers \
  --scope-public-headers \
  --report-mode root-cause \
  --format json \
  -o compare.json
```

Every other invocation below is this recipe plus one addition. `OLD`/`NEW`
are whatever [getting the two sides](getting-the-two-sides.md) produced —
binaries, JSON snapshots, or a mix.

| Add | When |
|---|---|
| `--header old=OLD_HEADER --header new=NEW_HEADER` (repeatable), `--include old=... --include new=...` | the public headers are not auto-discoverable. **Scope both per side** — a bare `--header PATH`/`--include PATH` applies to *both* artifacts, so when `OLD` and `NEW` come from different revisions it parses the old binary against the new checkout's API and a real signature/layout change cancels out, reporting compatible. Use the bare form only when one set of paths genuinely describes both sides. |
| `--policy plugin_abi` / `--policy sdk_vendor` / `--policy DOCUMENT` | the project has its own view of what a given change kind means — [policies and suppressions](../../shared/policies-and-suppressions.md) |
| `--contract public` | per-finding contract relevance and its coverage ledger are needed — [public surface and scoping](../../shared/public-surface-and-scoping.md) |
| `--suppress FILE` | the project already has a suppression file. Never author one here. |
| `--used-by CONSUMER` / `--required-symbol SYM` / `--required-symbols FILE` | the question is about one named consumer rather than the library globally — see the parent skill's named-consumer step and [consumer scoping](../../shared/consumer-scoping.md) |
| `--depth build` / `--depth source`, with `--sources`/`--build-info` | flags, macros, or reachability are in question — [evidence and depth](../../shared/evidence-and-depth.md) |

## Named-consumer invocations

Both branches start from the canonical comparison above; only the scoping
dial changes. Full model, failure modes, and the `verdict`/`full_verdict`
reading rule: [consumer scoping](../../shared/consumer-scoping.md).

### Application consumer — `--used-by`

```bash
abicheck compare OLD NEW \
  --used-by path/to/consumer-binary \
  --header old=../old-side/include/foo.h --header new=include/foo.h \
  --include old=../old-side/include/ --include new=include/ \
  --depth headers --report-mode root-cause --format json \
  -o consumer.json
```

One run per consumer when several must each be named against the findings
that reach them: `--used-by` is repeatable, and a single multi-app run does
answer every consumer, but only the *per-app summary* is per app — the
findings themselves are one deduplicated union across every named app, with
no app-to-finding association.

### Plugin / host boundary — `--required-symbol`

```bash
abicheck compare OLD NEW \
  --required-symbol plugin_init --required-symbol plugin_shutdown \
  --header old=../old-side/include/plugin_api.h \
  --header new=include/plugin_api.h \
  --depth headers --report-mode root-cause --format json
```

or, from a maintained list:

```bash
abicheck compare OLD NEW --required-symbols host-contract.txt \
  --header old=../old-side/include/plugin_api.h \
  --header new=include/plugin_api.h \
  --depth headers --report-mode root-cause --format json
```

Supply the entrypoint's headers on both branches, and require
`evidence_tier: header_aware` before accepting a pass — a stripped or
symbols-only run can keep exporting the required symbol while its signature
or a struct it passes changes incompatibly, invisible below that tier.

## Field paths this workflow reads, by step

Full reading order and every conditional block: [report interpretation](../../shared/report-interpretation.md).
This is only the map from workflow step to field:

| Step | Fields |
|---|---|
| Validate comparability | `verdict`, `reason.kind`, `reason.message` |
| Validate evidence coverage | `evidence_tier`, `evidence_tiers`, `coverage_warnings`, `scope.resolved`, `layer_coverage` (for `--depth build`/`source`) |
| The compatibility answer | `verdict`, `summary` |
| The grading of that answer | `severity.exit_code`, `severity.blocking`, `severity.blocking_categories` |
| Root causes | `root_causes`, `root_cause_count`, `changes[].caused_by_type`, `changes[].caused_count` |
| Named-consumer scope | `verdict` (the scoped answer), `full_verdict` (the library-wide answer), `changes[].affected_symbols`, `changes[].public_reachable`, `changes[].reachability_state`, `changes[].reachability_kind`, `changes[].impact_is_direct`, `changes[].affected_public_roots` |
| Orthogonal coverage axis | `contract_coverage_failures`, `contract_coverage_exit_contribution` |
