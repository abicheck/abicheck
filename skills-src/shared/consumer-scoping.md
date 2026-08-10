---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - impact-analysis
---

# Consumer scoping

A library-global verdict answers "is this change breaking for *someone*". A
consumer-scoped question is different: "does it break **this** consumer" —
and the two answers legitimately diverge in both directions.

- Globally `BREAKING`, but this consumer never touched the removed symbol →
  unaffected.
- Globally `COMPATIBLE`, but this consumer depends on a symbol the new build
  stopped exporting from *its* linked set → affected.

Neither direction is a filtered view of the other, which is why this is its
own decision tree rather than a display option.

## The dials

| Dial | Consumer shape | What it establishes |
|---|---|---|
| `--used-by CONSUMER` (repeatable) | an application or library that links the subject | the consumer's actually-imported symbol set, scanned from its own binary |
| `--required-symbol SYM` (repeatable) | a plugin/host ABI contract | the entrypoints the host requires the subject to provide |
| `--required-symbols FILE` | the same, from a file | a maintained required-entrypoint list |

`--used-by` is evidence-driven: the consumer binary is read, and its imports
become the scope. `--required-symbol(s)` is declaration-driven: the caller
states the contract, because a plugin host's requirement is not recoverable
from the plugin's own imports.

Deeper evidence sharpens the answer. At `--depth source`, reachability is
call-graph proven rather than surface-level; at shallower depths the answer
is "this symbol is imported", which is sound for removals but weaker for
"does the changed field actually reach this consumer". Say which you have —
see [evidence-and-depth.md](evidence-and-depth.md).

## Reading the result

**Read the verdict the right way round.** In a scoped run the top-level
`verdict` is the *scoped* answer — the CLI promotes it there because it is
what the gate acts on — and `full_verdict` carries the library-wide result.
Treating `verdict` as the global answer inverts the report contract and
discards the very thing a consumer-scoped run exists to produce.

`full_verdict` is written on every scoped run, equal or not, so **compare the
two values** rather than reading anything into the field being there. They
diverge when `full_verdict != verdict`.

Findings carry consumer-relevant fields when the run establishes them:

- `changes[].affected_symbols` — the symbols the finding touches.
- `changes[].public_reachable`, `reachability_state`, `reachability_kind`,
  `reachability_proof_path` — whether and how the finding reaches the
  scoped surface.
- `changes[].impact_is_direct`, `impact_proof_path`, `impact_assessment` —
  the impact-analysis view.
- `changes[].affected_public_roots` — the public entry points implicated.

The impact model behind these fields is owned by
[the impact analysis page](../../docs/learn/impact-analysis.md); the
application-consumer workflow is
[application compatibility](../../docs/use/appcompat.md), and the plugin/host
shape is [plugin systems](../../docs/use/plugin-systems.md).

## Failure modes to state, not paper over

- **The consumer's imports are unresolvable.** Then you cannot scope, and the
  honest answer is the global verdict plus a statement that consumer scoping
  was unavailable. Stripping alone does **not** put a binary here: scoping
  reads loader-visible imports (ELF `.dynsym`, the PE import directory,
  Mach-O undefined symbols), which survive `strip` — it removes `.symtab`.
  Reserve this for an undetectable format or genuinely absent loader
  metadata; treating every stripped consumer as unscopable throws away an
  answer the tool can give.
- **A required symbol is missing from the *old* side too.** The contract was
  already unsatisfied; that is a pre-existing defect, not a regression this
  change introduced. Report it as such.
- **Multiple consumers.** Answer per consumer — and note *how* the tool
  reports them: one run with several `--used-by` values yields a per-app
  summary (verdict, required-symbol count, missing symbols/versions,
  relevant-change count, coverage) but a single deduplicated union of the
  findings themselves, with no app-to-finding association. Scope one run per
  consumer when you need to name the findings that reach each one; a single
  merged verdict, or a merged finding list read as one app's, hides exactly
  the divergence this workflow exists to surface.
- **A consumer loads symbols dynamically** (`dlopen`/`dlsym`, plugin
  registries, `GetProcAddress`). Import scanning cannot see those. Say the
  scope is incomplete rather than claiming the consumer is unaffected —
  [safety-invariants.md](safety-invariants.md) item 1.
