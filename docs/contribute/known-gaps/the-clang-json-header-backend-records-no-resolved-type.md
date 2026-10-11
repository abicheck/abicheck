---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The clang JSON header backend records no resolved type identities, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public` (2026-10-01)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for type_identities under abicheck/extract/headers/clang/ finds nothing, so the clang backend still records no identities.


Schema v54 closed `public-contract-default.md` Phase 6's
`ambiguous_namespaced_leaf` defect at extraction. castxml resolves every
type slot to one element of its type graph, and
`extract/headers/castxml/type_resolution.type_identities` records that
element's qualified name (`Function.return_type_identities`,
`Param`/`Variable`/`TypeField.type_identities`). The exact public-surface
walk and the contract evaluator then know that `api()` returning the bare
`Cache *` reaches `ns1::Cache`, not `ns2::Cache`.

**What is still open:** the clang backend (`--ast-frontend clang`) writes
those fields as `None`, meaning not captured. `clang -ast-dump=json` gives a
declaration's type only as a `qualType` string (`"Cache *"`), with no
reference to the declaration it names, so the JSON carries no identity to
record. Reconstructing one from C++ name lookup (enclosing namespaces plus
using-directives) would be a heuristic. That is exactly the kind of guess
the exact walk exists to refuse, so it was not attempted. DWARF-sourced
snapshots and every pre-v54 baseline are in the same position. For all of
them the answer is the pre-v54 one: a break on a record whose leaf another
record shares stays `UNKNOWN_UNRESOLVED` under `--contract public`. It
raises the coverage floor (exit 1), not the ABI gate.
`scripts/check_fp_rate.py`'s `ambiguous_namespaced_leaf_spelling_only` case
pins that shape as the one explained `public` loss in
`scripts/measure_contract_shadow.py`.

**What would close it:** a clang-side source of declaration references. The
optional facts plugin (`contrib/abicheck-clang-plugin`) runs inside clang's
semantic analysis and could emit the same per-slot identity. DWARF's
`DW_AT_type` references a DIE whose scope chain is known, so the DWARF
snapshot path could populate the fields too. Either is additive: the
consumer side already reads the fields from any producer.
