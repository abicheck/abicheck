---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `Param.is_va_list` (G31 Phase C continued) inherits the toolchain- identity-probe gap above, rather than being a new problem

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** snapshot_reliability.py:182 and fact_registry_schema.py:273 have clang_va_list_facts_reliable as a plain flag without target identity, and no toolchain-identity probe exists yet (see entry 9).

Its
extraction predicate (`dumper_clang_qualifiers._clang_param_is_va_list`)
is deliberately scoped to the one ABI verified here — x86-64 System V —
and a snapshot from an unrecognized target already degrades to a
conservative `False` (see the function's own docstring). What's still
open (Codex review, fresh evidence): the snapshot-level reliability flag
(`AbiSnapshot.clang_va_list_facts_reliable`) records only "did the fixed
extractor run", not "against which resolved target" — so two
genuinely-different-target clang snapshots (x86-64 vs. AArch64, say) can
both read as reliable, and a real cross-architecture comparison (which
this tool's comparability layer permits in general) could read the
x86-64 side's real detections against the AArch64 side's uniform `False`
as a spurious `PARAM_BECAME_VA_LIST`/`PARAM_LOST_VA_LIST`. No header-AST
fact has resolved-target validation today, not just this one — closing
it here alone would be an inconsistent one-off fix for a structural gap;
it belongs with the toolchain-identity probe above once that lands.
