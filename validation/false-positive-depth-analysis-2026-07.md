# False positives by evidence depth — can source/build clear what headers can't? (2026-07)

**Question posed.** The per-depth accuracy matrix looks like this:

| Depth   | Comparable | Eval | Correct | Coverage | FP | FN |
|---------|-----------:|-----:|--------:|---------:|---:|---:|
| binary  | 141 | 134 | 73  | 54.5%  | 1 | 60 |
| headers | 141 | 134 | 109 | 81.3%  | 0 | 25 |
| build   | 141 | 134 | 109 | 81.3%  | 0 | 25 |
| source  | 141 | 134 | 134 | 100.0% | 0 | 0  |
| full    | 141 | 134 | 134 | 100.0% | 0 | 0  |

The ask: *build example cases that show a **false positive** at a lower depth
(`binary`/`headers`) that only **higher** data (`build`/`source`) can clear —
specifically cases where headers are **not** enough and only source/build reveal
the finding is a false positive.*

This note answers whether that is possible, where it is possible, and where it
is **not** — grounded in the tool's real behaviour, not assertion. Every verdict
quoted here is produced by `abicheck.checker.compare` and reproduced by the
runnable demo `validation/scripts/fp_depth_demo.py` (pure Python, no toolchain).

---

## TL;DR

- **Yes, but only one class.** The single genuine case where a depth *above*
  headers clears a false positive is **build-context / preprocessor divergence**
  — a public struct field guarded by `#ifdef`, where the header parsed
  *context-free* computes a different layout than what was actually built. The
  `headers` depth false-positives; the `build` depth (real `-D` flags from the
  compile database) clears it. The `binary` depth is *blind* to the conditional
  layout (exported symbols only) and is therefore correct — it does **not**
  observe the shrink, so the transition is `headers → build`, not
  `binary → build`. See
  [`preproc_conditional_field`](#the-one-real-class-build-context-divergence).
- **A *pure source-only* clear does not exist** for a shipped-ABI **compare**
  verdict. Build and source evidence is *corroborating*: it **adds** breaks
  (cuts false negatives — the 25 → 0 drop from `headers` to `source` in the
  table) and refines reachability, but by the **authority rule** (ADR-028 D3) it
  never silently overturns an artifact-proven layout break. So there is no
  "headers say breaking, only source proves it safe" for the compare path.
- **Most false positives are cleared at `headers`, not above it.** That is the
  `internal_struct_churn` case and it is why the table already reads `FP = 0` at
  `headers`. The scoping that removes internal-type churn is a *header-depth*
  capability.
- **Why the table shows `FP = 0` at `headers`.** The `--depth` dial folds the
  debug layer (`L1`) and the header layer (`L2`) into one `headers` collection.
  The classic false positive — layout churn seen with debug info but before
  scoping — lives *inside* that fold, so it never surfaces as a standalone
  column. `scripts/check_tier_accuracy.py` (which separates `L1` from `L2`) is
  where you can see that `L1→L2` transition explicitly.

---

## The matrix this note produces

```text
case                         truth         binary headers   build  source    full
---------------------------------------------------------------------------------
preproc_conditional_field    non-breaking       ✓      FP       ✓       ✓       ✓
internal_struct_churn        non-breaking       ✓       ✓       ✓       ✓       ✓
detail_type_via_pointer      non-breaking       ✓       ✓       ✓       ✓       ✓
real_public_break            breaking          FN       ✓       ✓       ✓       ✓

  headers->build: cleared 1 FP (preproc_conditional_field)
```

Run it yourself:

```bash
python validation/scripts/fp_depth_demo.py            # text matrix above
python validation/scripts/fp_depth_demo.py --markdown # report table
```

Each case supplies, **per depth**, the `(old, new)` pair that depth actually
observes, and the pair is run through the real `compare`. This is deliberately
*not* the "project one full snapshot down" model of
`check_tier_accuracy.py`: a lower depth does not merely see *less*, it can see a
**distorted** picture (the phantom `#ifdef` layout), and modelling that
distortion is the whole point.

---

## The one real class: build-context divergence

`preproc_conditional_field` — a public struct whose field is compiled
conditionally, shipped with the macro fixed on both releases:

```c
// v1 public header                 // v2 public header (b becomes opt-out)
struct S {                          struct S {
    int a;                              int a;
    int b;                          #ifdef KEEP_B   // project ships -DKEEP_B
};                                      int b;
S *use(void);                       #endif
                                    };
                                    S *use(void);
```

Both releases are built with `-DKEEP_B`, so the **true, shipped ABI is identical
(`{a, b}` in both)** — the correct verdict is *non-breaking*. The false positive
comes only from parsing the v2 header *without* that define, where `b` vanishes
while v1 still declares it.

| Depth | What it observes | Verdict | Why |
|-------|------------------|---------|-----|
| `binary` | exported symbols only; both builds identical, no type layout | **✓ NO_CHANGE** | L0 is *blind* to the conditional field — it cannot see a shrink, so it does not (and must not) raise one |
| `headers` | castxml parses **context-free** (`KEEP_B` undefined) → v2 drops `b` while v1 keeps it | **FP** `type_field_removed`, `type_size_changed` | the header AST models a layout that was never built |
| `build` | compile DB carries `-DKEEP_B` → both sides are `{a, b}` | **✓ NO_CHANGE** | build context resolves the real preprocessor branch; corrects the parse, does not overturn an artifact-proven break |
| `source` / `full` | strictly more evidence than build | **✓** | cannot lose a verdict build already reached |

Because the binary is blind rather than wrong, `build` clearing the header
false positive does **not** violate the authority rule — there was no
artifact-proven break to overturn, only a context-free header-parse artifact.

This is the honest shape of "a false positive only higher-than-headers data can
clear". It is exactly the mismatch class `header_build_context_mismatch` (L3)
exists to flag — the header AST is captured *context-free*, so the declared API
surface can disagree with what the shipped translation units compiled (see
`abicheck/buildsource/crosscheck.py`). Feed abicheck the compile database
(`abicheck scan --depth build -p build/`, or `compare -p`) and the divergence
resolves.

---

## Why a *pure source-only* clear does not exist (for compare)

We looked for the stronger case the question also asks about — *headers **and**
build both false-positive, only source proves it safe* — and it does not occur
in the current compare model. Two structural reasons:

1. **The authority rule (ADR-028 D3).** Artifact evidence (binary/debug/headers)
   is *authoritative* for the shipped-ABI verdict; build/source evidence is
   *corroborating*. It explains, localizes, scopes, adds confidence, and raises
   its **own** source-/API-level findings — but it never silently deletes an
   artifact-proven break. A "source clears a header break" transition is exactly
   the deletion the rule forbids. `check_tier_accuracy.py` gates this
   (`under_call_monotonicity`).

2. **Header scoping is already good.** The false positives that *could* be
   cleared by better reachability are already declined at the `headers` depth.
   `detail_type_via_pointer` in the demo is the near-miss: an internal `detail`
   type reached only through a public pointer return, growing by an appended
   field. One might expect a layout-only depth to over-call it — but abicheck's
   header depth already classifies an appended field behind a pointer as
   *compatible* (`type_field_added_compatible`) and an unreachable type's change
   as `NO_CHANGE`. There is no residual false positive for source to clear.

Where source/build genuinely move the needle is the **other** axis — false
*negatives*. The table's `headers 25 → source 0` FN drop is real and is the
whole reason the source depth exists: macro/`constexpr`/inline/default-argument
and uninstantiated-template changes leave **no artifact footprint** and only
source replay (L4) sees them (`public_macro_removed`, `concept_tightened`,
`odr_type_variant`, …; see `scripts/evidence_tiers.py`). Those are breaks lower
depths *miss*, not false positives they *raise*.

---

## Where each depth clears a false positive (summary)

| False positive | Raised by | Cleared by | Mechanism |
|----------------|-----------|------------|-----------|
| internal-type layout churn | debug layer (folded into `headers`) | `headers` | public/internal **scoping** |
| build-conditional (`#ifdef`) layout | `headers` (binary is blind → correct) | **`build`** | real `-D` flags from the compile DB |
| (hypothetical) source-only clear | — | — | **does not occur** — authority rule |

The practical guidance that falls out of this: **give abicheck the compile
database** whenever a public header uses preprocessor-conditional layout. Without
it, `headers` is parsing a branch the shipped binary may never have compiled,
and that — not a tool bug — is the origin of the residual `headers`
false positive this analysis reproduces.

---

## Reproduce / extend

- Demo: `validation/scripts/fp_depth_demo.py` (this note's matrix).
- Separated `L0…L3` view with the `L1→L2` scoping transition made explicit:
  `python scripts/check_tier_accuracy.py --markdown`.
- Per-case minimum evidence for the real catalog: `examples/ground_truth.json`
  (`min_evidence`), computed by `scripts/evidence_tiers.py`.
- Conceptual write-up: `docs/concepts/evidence-and-detectability.md`
  §"What each layer buys".
