<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **`TYPE_VTABLE_CHANGED` evidence-gating cluster: `FactStatus` pre-check
  formally investigated and declined.** ADR-063 Track 4's 5B closure
  re-examined whether consolidating the shared vtable-evidence predicate
  into `abicheck/compare/vtable_evidence.py` changed the safety of adding
  a direct `vtable_fact.status` check to `diff_types_vtable.py`'s
  `TYPE_VTABLE_CHANGED` cluster — it does not, since DWARF still reports a
  per-TU capture gap as genuinely `PRESENT`. A narrower `NOT_COLLECTED`/
  `FAILED`-only check was also investigated and declined: it would
  short-circuit two evidence streams independent of `vtable_fact`'s own
  status. No behavior change; the full reasoning is recorded once,
  canonically, in `diff_types_vtable.py`'s own module docstring, with
  short pointers from `compare/vtable_evidence.py`, the plan, and the
  status ledger, and a new regression test
  (`tests/test_vtable_evidence_guard.py::TestExplicitFactStatusWouldNotSafelyGateThisGuard`)
  proving the fallback streams still fire.
