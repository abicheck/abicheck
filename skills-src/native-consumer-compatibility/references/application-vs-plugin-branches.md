# Application consumers vs. plugin/host boundaries

Both branches answer "will this consumer keep working". They differ in how
the consumer's required surface is *established*, which changes the evidence,
the failure modes, and the remediation. The general model is
[consumer scoping](../../shared/consumer-scoping.md); this file is the
side-by-side.

## Side by side

| | Application consumer | Plugin / host boundary |
|---|---|---|
| Direction | the consumer **calls into** the subject | the host **requires** the subject to provide entrypoints |
| Contract source | discovered by scanning the consumer's imports | declared by the caller |
| Dial | `--used-by CONSUMER` (repeatable) | `--required-symbol SYM` (repeatable) or `--required-symbols FILE` |
| Evidence | the consumer binary's own import table | the caller's assertion |
| Typical break | an imported symbol removed or its signature changed | a required entrypoint missing, or its signature/struct contract changed |
| Silent failure mode | dynamically-resolved symbols invisible to the scan | the host loading the plugin and failing later, not at load |
| Remediation | rebuild the consumer, or pin the old library | restore the entrypoint, or version the plugin interface |

## Why one skill, two branches

Both ask the same user-visible question and produce the same shape of
answer — a yes/no for one named consumer, with the findings that reach it.
They differ only at the level of which CLI dial establishes the scope, which
is exactly the level `compare` already folds them into one verb. Splitting
them into separate skills would publish an internal mechanism as if it were
a different user job.

## Application branch, in practice

```bash
# one run per consumer, into its own report
abicheck compare OLD NEW \
  --used-by build/bin/myapp \
  --depth headers --report-mode root-cause --format json \
  -o myapp.json

abicheck compare OLD NEW \
  --used-by build/lib/libplugin_host.so \
  --depth headers --report-mode root-cause --format json \
  -o plugin-host.json
```

- **One run per consumer.** `--used-by` is repeatable and a single run does
  answer every consumer, but only the *per-app summary* is per app; the
  findings are one deduplicated union with no app-to-finding association.
  Reading that merged list as one app's would misattribute another's break —
  see the parent skill's step 2A, which owns this rule.
- Answer **per consumer**. A merged verdict hides the divergence this
  workflow exists to surface.
- Deepen to `--depth source` when reachability, not just import presence, is
  the question.
- A consumer built against a *different* old version than the project's last
  release is the common real case — the "old" side is what that consumer was
  built against.

## Plugin branch, in practice

```bash
abicheck compare OLD NEW --required-symbols host-contract.txt --format json
```

- The required list is the host's contract. If the user cannot produce one,
  that itself is the finding: the boundary is undocumented, and no tool can
  verify an undeclared contract.
- Distinguish the two failure shapes explicitly:
  - required symbol present in old, **missing in new** → a regression this
    change caused;
  - required symbol missing from **both** → a pre-existing unsatisfied
    contract, not caused by this change.
- A plugin ABI is often better served by a policy profile that reflects its
  stricter rules — `--policy plugin_abi`, see
  [policies and suppressions](../../shared/policies-and-suppressions.md).
- Entrypoint presence is necessary, not sufficient: a struct passed across
  the boundary can change layout while every required symbol still exists.
  Check the layout findings too, and prefer explicit capability negotiation
  over a shared layout — see the `native-api-evolution` skill's design
  pattern catalogue.

## When both apply

A plugin that also links the library normally has both contracts. Run both
branches and report both; they can disagree, and the union is the answer.
