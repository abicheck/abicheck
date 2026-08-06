---
doc_type: how-to
audience:
  - ci-owner
  - library-maintainer
level: intermediate
summarizes:
  - contract-relevance-and-coverage
depends_on:
  - abicheck/cli_contract_options.py
  - abicheck/contract_pipeline.py
  - abicheck/pack_application.py
lifecycle: active
generated: false
---

# Contract Evaluation

Practical guide to turning on contract-aware gating. For the mental model
(what a "contract" is, the three modes, why this can't hide a break), see
[Contract-Aware Compatibility](../learn/contract-aware-compatibility.md).
For the exact field vocabulary and precedence, see [Compatibility
Evaluation Config](../reference/compatibility-evaluation-config.md).

## Turning it on

```bash
abicheck compare old.so new.so -H include/ --contract-evaluation
```

With no `--contract`, the domain follows the legacy
`--scope-public-headers`/`--no-scope-public-headers` flag (default: public
headers). To select a domain explicitly:

```bash
abicheck compare old.so new.so -H include/ \
  --contract-evaluation --contract public   # or: exports | all
```

`--contract` **requires** `--contract-evaluation` — passing it alone is a
usage error (exit `64`), since without the flag nothing consumes the
selected domain.

`scan --against` accepts the identical pair of flags, for the same reason:

```bash
abicheck scan build/libfoo.so --against baseline.json \
  --contract-evaluation --contract exports
```

## Reading the result

```bash
abicheck compare old.so new.so -H include/ \
  --contract-evaluation --format json -o report.json
```

Two things to check in the JSON:

- Per finding: `contract_relevance`, `compatibility_evaluation_status`,
  `compatibility_decision` (`null` for anything `NOT_EVALUATED`).
- Run level: `contract_coverage_failures` (a list, empty when evidence was
  complete) and `contract_coverage_exit_contribution` (`0`/`1`).

Markdown/text output includes a `"Not evaluated (contract)"` count in the
headline summary, and SARIF annotates an excluded finding with a
`contractRelevance` property (severity `note`, not `error`) rather than
dropping it.

## Accepting incomplete evidence deliberately

If a domain's evidence is *known* to be incomplete for some legs of your
matrix (e.g. one lane never gets header input), accept that explicitly
rather than leaving CI red or turning the feature off:

```yaml
# packs/accept-unresolved.yml
id: accept_unresolved
version: 1
kind: contract
assignments:
  contract.unresolved: warn
```

```bash
abicheck compare old.so new.so -H include/ \
  --contract-evaluation --contract exports \
  --pack packs/accept-unresolved.yml
```

This zeroes the contract-coverage exit contribution *only* — the failures
stay listed in `contract_coverage_failures`, and it changes nothing about
per-finding compatibility decisions. `contract.unresolved` requires
`--contract-evaluation`; setting it in a pack without the flag is a usage
error naming the field and the reason.

## Consumer/entrypoint evidence outranks header/export inference

`compare --used-by APP` or `--required-symbol(s)` layered on top of
`--contract-evaluation` promotes a finding to `IN_CONTRACT` whenever it
matches the app's actual imports or the plugin host's required entrypoints
— stronger evidence than anything a header/export scan alone can infer,
per ADR-049 §4.3:

```bash
abicheck compare old.so new.so --used-by ./myapp \
  --contract-evaluation --contract public
```

This promotion only ever raises a finding toward `IN_CONTRACT` — it never
demotes one, and it recomputes the scoped verdict/gate afterward if it
changed anything. See [Application Compatibility → Why does this consumer
depend on the changed
declaration?](appcompat.md#why-does-this-consumer-depend-on-the-changed-declaration)
for the deeper "why", not just "whether."

## CI recipe: gate only the declared contract, don't hide the rest

```yaml
- name: ABI contract gate
  run: |
    abicheck compare baseline.json build/libfoo.so \
      -H include/ \
      --contract-evaluation --contract public \
      --pack packs/accept-unresolved.yml \
      --severity-preset default \
      --format json -o report.json
```

Read `report.json`'s `verdict` for the gated compatibility result and
`contract_coverage_exit_contribution` separately if your pipeline needs to
distinguish "a real break" from "we couldn't prove enough" in its own
messaging — the process exit code already folds both, but a human-facing
summary usually wants to say which one fired.

## Common mistakes

- **Expecting `--contract` alone to do anything.** It needs
  `--contract-evaluation`.
- **Suppressing to fix a coverage gap.** `--suppress` cannot reach a
  `CoverageFailure` — give the evaluator the missing evidence (headers,
  build info) or accept the gap explicitly with `contract.unresolved: warn`.
- **Reading `compatibility_decision: null` as "compatible."** It means
  policy never scored the finding — check `contract_relevance` for why.
- **Assuming `exports` mode never benefits from headers.** Root selection
  really is export-table-only — headers/publicness play no part in
  deciding *which declarations are roots*. But the closure walk from those
  roots still needs typed declaration data to resolve, so a header-only (or
  debug-info-only) snapshot with *no* observed export table at all will
  generally land in `UNKNOWN_UNRESOLVED`/coverage failure — not because
  headers are useless under `exports`, but because there was no export
  table to root the closure on in the first place. Passing headers/debug
  info alongside a real export table can still turn an otherwise-unresolved
  type edge into a provable exclusion.

## See also

- [Contract-Aware Compatibility](../learn/contract-aware-compatibility.md) — the mental model
- [Compatibility Evaluation Config](../reference/compatibility-evaluation-config.md) — field reference
- [CI Gating](ci-gating.md) — the full pipeline this stage is one part of
- [Exit Codes](../reference/exit-codes.md) — the exhaustive exit-code contract
- [Aggregate Reports](aggregate-reports.md) — the `contract_coverage` axis at the multi-target level
