---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Evidence-provider model — investigated, found not to reproduce as described; no fix applied

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Investigated and found not to reproduce. The modules it relies on still exist (dumper_layout_backfill.py, policy/classification.py:619 evidence_status_for_result).

A status-review follow-up asked whether
`evidence_status_for_result`'s report-level downgrade (kind-level
`ARTIFACT_PROVEN` → `UNATTRIBUTED` only when `DiffResult.evidence_tiers`
is header-only for the *whole* comparison) can let an individual
header-derived `BREAKING_KINDS` finding read as artifact-proven merely
because *some other, unrelated* part of the same report had binary
evidence. Traced this for the highest-stakes family it could apply to —
layout findings (`TYPE_SIZE_CHANGED`/`TYPE_ALIGNMENT_CHANGED`,
`diff_types.py`) — and it does not hold up: (1) the direct-clang L2
backend's `RecordType.size_bits`/`alignment_bits` are populated **only**
when `dumper_layout_backfill.backfill_dwarf_layout()` actually
corroborates them against real DWARF (`model.py`'s own
`dwarf_layout_coherence` docstring) — with no DWARF to backfill against,
those fields stay `None` and `_append_type_size_and_alignment_changes`'s
own `is not None` guard means no finding is even emitted, so an
"unconfirmed clang-derived layout finding" cannot occur; (2) the castxml
backend computes struct layout itself, via its own bundled real compiler
targeting the resolved ABI — `model.py` already documents this as
deliberately treated as sufficient L2 evidence ("trivially self-consistent
by construction", not needing DWARF corroboration), a prior, intentional
design decision this pass would have to *overturn*, not merely patch.
The one place this class of risk is genuinely live is exactly the
already-tracked toolchain-identity-probe gap above (castxml/clang invoked
with compiler/ABI flags that don't match the real build) — not a separate
evidence-status bug. A **real** per-finding provider model (recording,
per `Change`, which of L0–L5 actually produced/corroborated it) would
need new provenance plumbing through all ~45 `Change(...)` construction
sites across `diff_*.py`/`buildsource/*.py`, each individually verified
against the FP-rate/mutation-score gates — a multi-day project on its
own, not attempted here.
