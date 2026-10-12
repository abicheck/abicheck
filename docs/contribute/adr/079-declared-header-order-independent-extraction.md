# ADR-079: Declared Header Order in the Aggregate Driver Translation Unit

**Date:** 2026-10-12
**Status:** Accepted — implemented. Adds a dump-time witness (`extract/header_order_witness.py`,
`header_order_proof`) and the optional stored field `ExtractionContract.header_canonical`
(schema v59). A reorder of an identical header set is comparable only when both dumps
carry the same proof; everything unproven keeps refusing. The declared order still governs
extraction and every add/remove rule.
**Decision maker:** Nikolay Petrov (repository maintainer), accepted in session 2026-10-12.

## Context

The castxml and clang backends parse every declared public header in one
aggregate translation unit, `#include`-ing them in the caller's declared order
(`extract/headers/ast_config.py::clang_aggregate_text`,
`extract/castxml_header_compat.py::castxml_aggregate_text`). A header's macros,
pragmas and typedefs therefore affect how every later header parses, so the
declared order is part of the extraction. `comparability_fields._declared_header_sequence`
records it in `profile_fingerprint`, and the gate refuses a reorder of existing
headers.

Measured on 2026-10-12 with two headers, `z_base.h` (defines `Base`) and
`a_user.h` (declares two functions over `Base`, no `#include`), built into one
shared library:

| Declared order | castxml | clang |
|---|---|---|
| `z_base.h, a_user.h` | both functions extracted | both functions extracted |
| `a_user.h, z_base.h` | zero declarations (reduced evidence) | `SnapshotError` (unknown type `Base`) |

`a_user.h` has no `#include` edge to `z_base.h` (`clang -M` confirms it), so
the include graph cannot order these headers. Two consequences:

1. **Sorting the header set is not output-neutral.** A sorted order can be the
   failing order (it is here), so it breaks a declared order that works.
2. **Removing the refusal alone is unsound.** The same unchanged library dumped
   in both orders, with the gate bypassed, reports `BREAKING` with
   `func_removed make_base` and `func_removed base_x`.

An include-graph ordering (the earlier option D) inherits the first problem: it
cannot see use-without-include, so it can rewrite a working order into the
failing one. It is withdrawn.

## Decision (option 2: witness, with a separately recorded proof)

The extraction stays in declared order. Order-independence is proved per dump,
not assumed from a rule.

**At dump time**, for a declared header set `H` with canonical order `C` (headers
sorted by their root-relative identity):

1. If `H` is already in canonical order, the declared extraction *is* the
   canonical extraction. Nothing is parsed twice.
2. Otherwise parse `H` (as today) and parse `C`. If the two extractions agree on
   every declaration and type (the comparison in "Witness equivalence" below), the
   declared extraction is proven equal to the canonical extraction.
3. If the orders disagree, or the canonical parse fails, nothing is proven.

**Recorded in the snapshot's extraction contract:**

- `profile_fields["header_sequence"]` is unchanged: it is still the declared order.
  Every existing rule for adding and removing headers keeps working on it, which
  is what preserves the required behaviour for growth and removal.
- A new field, `header_canonical`, carries the canonical identity sequence, and is
  present **only** when step 1 or 2 proved it. Absent means unproven, which is
  exactly today's behaviour.

**Comparison rule** (added to `comparability.py`'s carve-outs, checked after the
existing `header_sequence` rules):

- If `header_sequence` differs only by a permutation, the pair is comparable
  **iff** both sides carry `header_canonical` and the two values are equal. Equal
  canonical sequences mean both extractions are the canonical extraction of the
  same header set, so their difference is only the order the user declared.
- If both sides carry `header_canonical` and the header sets differ, the
  canonical sequences are checked by the existing insertion/removal waiver
  (`comparability_sequences.header_sequence_is_scope_confirmed_evolution`), which
  already accepts a scope-confirmed insertion in sorted order. Canonical sequences
  are sorted, so this is the case the waiver was written for.
- If either side lacks `header_canonical`, the rules are exactly today's.

**Witness equivalence.** Two extractions agree when their declaration and type
sets are equal as multisets of canonical JSON records (sorted keys), with
provenance fields excluded. A record that carries an order-dependent identifier
makes the witness fail closed: the result is "unproven", which keeps today's
refusal. This is a conservative test, and it has a known gap: a macro or pragma
leak that changes no declaration is invisible to it. That is recorded as a
residual, not claimed as proven.

## Consequences

- **Schema:** `header_canonical` is a new field in the stored extraction contract.
  It is optional, so snapshots without it stay valid, but it is a schema addition
  under the snapshot contract. The maintainer must decide whether that needs a
  schema version bump (ADR-050 D2 governs the contract's compatibility) before any
  code is written.
- **Behaviour:** a reorder of existing headers stops refusing only when both sides
  proved the canonical order. A declared order that is unsorted and does not
  prove, or any reorder where either side is unproven, keeps refusing. Adding or
  removing headers keeps today's rules, because `header_sequence` is unchanged.
- **Cost:** a second parse only when the declared order is unsorted. The canonical
  parse goes through the existing header-AST cache, since the aggregate text is
  part of its key.
- **Pin tests:** `tests/test_driver_tu_declared_order.py` keeps its extraction pins
  (declared order still changes the declarations). The refusal test on
  self-contained headers becomes comparable on proof. A new test covers an unproven
  pair refusing, and a proven pair with a flipped order comparing equal.
- **Known gap:** the known-gap entry stays OPEN until this ADR is Accepted and
  implemented.
