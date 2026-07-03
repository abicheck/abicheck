# CI Gating: How the Pieces Fit Together

Four mechanisms decide what fails your build: **baselines** (what you compare
against), **policies** (how each change is classified), **suppressions** (which
changes are waived), and **severity** (which categories set the exit code).
Each has its own reference page; this page is the map — what runs in what
order, and how the knobs interact.

```mermaid
flowchart LR
    B["Baseline<br/>(snapshot / registry)"] --> D["Detect changes<br/>(compare)"]
    N["New build"] --> D
    D --> P["1 · Policy classifies<br/>each change"]
    P --> S["2 · Suppressions<br/>waive changes"]
    S --> V["3 · Verdict + severity<br/>categories"]
    V --> E["4 · Exit code<br/>(legacy or severity scheme)"]
    V -.-> R["Report rendering<br/>(--show-only, --format)"]
```

## The order of operations

It starts with detection: `abicheck compare BASELINE NEW` diffs the two ABI
surfaces and produces raw changes. The baseline side is a snapshot, library,
or registry entry — see [Baseline Management](baseline-management.md). The
detected changes then flow through four stages (the numbers match the diagram
above):

1. **Classify (policy).** The active [policy profile](policies.md)
   (`--policy strict_abi|sdk_vendor|plugin_abi` or a custom
   `--policy-file`) maps each change kind to its impact — the same change can
   be `API_BREAK` under `strict_abi` but `COMPATIBLE` under `sdk_vendor`.
2. **Waive (suppressions).** [Suppression rules](suppressions.md)
   (`--suppress FILE`) remove matching changes **before** the verdict and
   severity counts are computed. A suppressed breaking change does not fail
   the build; it is tallied separately (`suppressed_count` in the JSON
   output).
3. **Score (verdict + severity).** The surviving changes produce the overall
   verdict (`NO_CHANGE` … `BREAKING`) and, when
   [severity](severity.md) is configured, per-category
   (`abi_breaking` / `potential_breaking` / `quality_issues` / `addition`)
   severity levels.
4. **Exit.** The exit code comes from one of the two schemes below.

**Display filtering is outside the pipeline.** `--show-only`, `--stat`,
`--report-mode`, and `--format` change what the report *renders*, never the
verdict or the exit code.

## The two exit-code schemes

`compare` has two exit-code regimes, and **passing any `--severity-*` flag
silently switches from the first to the second** — the most common source of
confusion when wiring up CI:

| Scheme | Active when | Codes |
|---|---|---|
| **Legacy (verdict-based)** | No `--severity-*` flag given | `0` compatible / `2` `API_BREAK` / `4` `BREAKING` |
| **Severity-based** | Any `--severity-*` flag given | `0` no error-level findings / `1` error in `addition`·`quality_issues` only / `2` error in `potential_breaking` / `4` error in `abi_breaking` |

In both schemes `0` passes and `4` is worst — but under the severity scheme
exit `1` means an error-level *finding*, whereas under the legacy scheme `1`
is not used for verdicts at all (invalid invocations exit `64`). Pin the
regime explicitly with `--exit-code-scheme legacy|severity` (or the
`exit_code_scheme` config key) so a later flag change can't silently flip it.
Full matrix, including `appcompat`/`deps`/`compat` and multi-library codes:
[Exit Codes](../reference/exit-codes.md).

## How the knobs interact

- **Policy → severity.** Severity categorizes changes *after* the policy has
  classified them. If `sdk_vendor` downgrades a kind from `potential_breaking`
  to `quality_issues`, the default preset then treats it as `warning`, not
  `error` — so `--policy sdk_vendor --severity-preset default` will not fail
  on it, while `--severity-preset strict` (everything `error`) still will.
  See [Severity → Policy interaction](severity.md#policy-interaction).
- **Policy → suppressions.** Independent: suppressions match on
  symbol/type/kind/location, regardless of how the policy classified the
  change. A suppression written under one policy keeps working if you switch
  policies.
- **Suppressions → verdict, severity, and exit code.** Suppressed changes are
  removed before scoring, so they affect *all* downstream outputs: the
  verdict, the severity category counts, and therefore the exit code — under
  either scheme. Guard the waiver list itself with `--strict-suppressions`
  (fail on unused/expired rules) and `--require-justification`.
- **Baselines → everything.** All of the above only gates what changed
  *relative to the baseline you chose*. Compare against the last release (not
  the previous commit) to catch cumulative drift; see
  [Baseline Management](baseline-management.md) for storage and registry
  workflows.

## Recipes

**Breakage-only gate** — report everything, fail only on binary ABI breaks:

```bash
abicheck compare baseline.json build/libfoo.so --new-header include/ \
  --severity-preset info-only --severity-abi-breaking error
```

**Fail on source-level breaks too** (the legacy default behaviour, pinned
explicitly):

```bash
abicheck compare baseline.json build/libfoo.so --new-header include/ \
  --exit-code-scheme legacy    # 0 / 2 (API_BREAK) / 4 (BREAKING)
```

**Strict API-surface governance** — also fail when new public API appears.
Note that any `--severity-*` flag switches to the severity scheme, where
`potential_breaking` (which covers `API_BREAK`) defaults to `warning` — raise
it to `error` too, or a source-level break that failed under the legacy
scheme would now exit `0`:

```bash
abicheck compare baseline.json build/libfoo.so --new-header include/ \
  --severity-potential-breaking error \
  --severity-addition error
```

**Vendor-friendly gate with audited waivers**:

```bash
abicheck compare baseline.json build/libfoo.so --new-header include/ \
  --policy sdk_vendor --suppress suppressions.yaml \
  --strict-suppressions --require-justification
```

More recipes: [Choose Your Workflow → How should CI behave](choose-your-workflow.md)
and the policy recipes in [Getting Started](../getting-started.md).

## Related pages

- [Baseline Management](baseline-management.md) — producing, storing, and
  pulling the comparison baseline
- [Policy Profiles](policies.md) — built-in profiles and custom YAML policies
- [Suppressions](suppressions.md) — schema, matching semantics, expiry,
  lifecycle
- [Severity Configuration](severity.md) — categories, presets, per-category
  flags
- [Exit Codes](../reference/exit-codes.md) — the canonical exit-code matrix
- [GitHub Action](github-action.md) — the same pipeline via `with:` inputs
