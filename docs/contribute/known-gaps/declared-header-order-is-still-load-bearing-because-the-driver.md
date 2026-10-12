---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Declared-header order is still load-bearing because the driver TU is sequential

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): PARTIAL.** The scope-confirmed header growth waiver landed (comparability.py:845,1298 reference _header_sequence_is_scope_confirmed_growth). Still open: the sequential aggregate driver TU itself, so reordering an existing header still refuses. Canonical sorting is measured unsound and rejected; the remaining options are recorded in [ADR-079](../adr/079-declared-header-order-independent-extraction.md).


`profile_fingerprint`'s `header_sequence` field records declared-header
*order*, and it does so for a real reason: `dumper.py` generates one
aggregate driver translation unit that `#include`s the declared headers in
sequence, so a header's macros/pragmas can change how every header parsed
after it resolves.

The comparability gate originally waived only a strict *trailing* append
(`_header_sequence_is_additive_reorder_free`), which is the one shape that
proves no existing header's preprocessing context changed. That is correct
for a deliberately declared `-H a.h -H b.h` order and badly mis-fitted to a
directory-discovered one, where the order is an incidental product of
sorting whatever the sweep found. Real integration evidence: a project
adding one public header named `json.h` to a set sorting as
`data.h, log.h, ...` landed it in the *middle*, the waiver declined, and an
ordinary public-header addition refused the entire comparison —
`profile_fingerprint mismatch; differing fields: header_sequence`, no
verdict, and no findings on any axis, the binary's own exported-symbol
identity included.

**What was fixed, in two rounds.** The first round changed the
*disposition*: an order-preserving insertion of headers the scope
fingerprint independently confirms as new became a non-fatal
`ComparabilityMismatch` (`fatal=False`) whose residual risk was recorded as
a bounded assurance reduction on the `declaration`/`layout` dimensions. That
removed the refusal but kept pricing the insertion — and the price is real:
`AnalysisAssurance.status` reads `partial`, which `assurance.require_complete`
gates on, so a project running that (correct, unweakened) setting still
failed on an ordinary public-header addition, now with
"extraction contexts were not provably identical" instead of a refusal.

The second round removed the price, because the hazard it named is not one
this contract prices anywhere else:

* A declared header's **content** is not part of `profile_fingerprint` at
  all — `comparability_fields._header_identities` keys a header by its
  root-relative path, never its bytes. So an existing `data.h` that gains a
  `#define` or a `#pragma pack` between two versions changes the parse
  context of every header after it and is compared at **full** assurance,
  by design: the declared surface's content is the subject of the
  comparison, not evidence about the extraction environment. Adding those
  same declarations as a new file is the same fact in a different shape.
* The added header's position is an incidental product of the project's
  discovered, sorted public-header set. Pricing it makes assurance depend
  on the new header's spelling — `json.h` costs assurance, `zzz.h` does
  not, for the same library change.
* The scope axis already treats the identical addition as ordinary
  evolution at full assurance (`_scope_field_is_additive_superset`), and
  the trailing-append waiver already accepts the added header's own parse.

So the `header_sequence` carve-out is now stated over
`comparability_sequences._header_sequence_is_scope_confirmed_growth` — the
union of the append and insertion shapes — and scope-confirmed growth is
waived outright wherever the new header sorts.
`comparability_profile._declared_header_insertion_mismatch` and
`inserted_header_entries` were deleted rather than left as a second,
unreachable path. The non-fatal (`fatal=False`) machinery itself stays:
it expresses a general rule, has no producer today, and
`analysis_assurance_comparability.py` remains the (still correct) bridge
that stops a bounded run from reporting `complete`.

**What still refuses, unchanged:** a reorder of an *existing* declared
header, growth whose extra entries the declared surface does not confirm as
new (e.g. a header fed to the L2 frontend on the new side only, whose
content the old snapshot never parsed), a duplicate/sentinel/undecodable
sequence, and any other diverging profile field riding alongside the
addition — compiler family or version, target triple, pointer width,
endianness, ABI dialect, language standard, macro ops, pass-through flags,
or an unexplained `include_sequence` (a changed `-I` topology, which decides
which dependency header an `#include` resolves to). The carve-out only ever
removes `header_sequence` from the working set, so that safety property is
structural rather than a list to keep in sync.

**What remains open:** the sequential aggregate TU itself. Parsing each
declared header in an independently scoped TU would make declared order
non-load-bearing outright — a *reorder* would then be comparable too, and
this whole carve-out family could retire — but that is a dumper change
carrying a per-header TU cost, and it was deliberately not attempted
alongside either disposition fix. `--diagnostic-comparison` is explicitly
**not** the answer to any of this: it downgrades assurance wholesale
instead of resolving the extraction question.

## What the 2026-10-12 check established

The aggregate TU is built in declared order by both backends
(`extract/headers/ast_config.py::clang_aggregate_text`,
`extract/castxml_header_compat.py::castxml_aggregate_text`), so declared order
changes what is extracted. Measured with `z_base.h` (defines `Base`) and
`a_user.h` (declares two functions over `Base`, no include), built into one
shared library and dumped through `dumper.dump`:

* **Declared `z_base.h, a_user.h`:** both functions extracted, under castxml and
  clang.
* **Declared `a_user.h, z_base.h`:** castxml excludes `a_user.h` after its L2
  parse fails, logs "excluding them and retrying (reduced evidence)", and
  returns **zero** declarations. Clang raises `SnapshotError` (unknown type name
  `Base`).
* **Canonical sorting is unsound.** A sorted order puts `a_user.h` before
  `z_base.h`, which is the second row: it converts a valid declared order into
  an empty or failed extraction. Sorting is rejected.
* **Removing the refusal alone is unsound.** Comparing the same unchanged
  library dumped in the two orders, with `check_contracts_comparable` bypassed,
  reports `BREAKING`: `func_removed make_base`, `func_removed base_x`, plus
  `exported_not_public` for both. The refusal is what stops this false break.
* **A per-header TU is not output-neutral either.** A standalone dump of
  `a_user.h` fails (clang: unknown type `Base`), so one TU per header turns the
  order dependence into a loud failure or reduced evidence for every
  non-self-contained header. It also multiplies parse cost by the header count.

Pinned by `tests/test_driver_tu_declared_order.py`: the aggregate text equals
the declared order for every permutation (oracle: expected lines written from
the input list), declared order changes the extracted declarations under both
backends, and reordering existing headers on real dumps still refuses with
`header_sequence`. A change that makes extraction order-independent must update
these tests deliberately.

**What changed (ADR-079, accepted and implemented).** The declared order still governs
extraction. A dump now proves, per snapshot, whether its declarations equal the sorted
order's, by parsing both orders and comparing the extracted declarations record for record.
A proven snapshot records the canonical order in `ExtractionContract.header_canonical`
(schema v59). The gate accepts a reorder of an identical header set only when both sides
carry the same proof. Adding or removing headers keeps the existing `header_sequence` rules.

**Still refused, by design.** A reorder whose declarations are unproven (for example
`a_user.h` before `z_base.h`, where the sorted parse fails), any side without the proof
(including every pre-v59 snapshot), and a macro or pragma leak that changes no declaration:
the witness cannot see it, and the residual is recorded in ADR-079.
