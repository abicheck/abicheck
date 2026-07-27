<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: public-root confirmation now respects
  D4's temporal side authority** (opt-in via
  `compare(..., contract_evaluation=True)`; no default-path behavior
  change): `_in_surface_result_is_confirmed` (and the
  hidden-friend confirmation path) previously checked the *union* of
  `surf_old`/`surf_new` regardless of the finding's direction, so new-side
  public evidence could retroactively confirm `IN_CONTRACT` for a
  modification/removal whose *old*-side obligation was never actually
  public -- confirmed empirically for a `FUNC_RETURN_CHANGED` finding on a
  function private in `surf_old` and public in `surf_new`. Fixed by adding
  `_authoritative_surface` (new side for `checker_policy.ADDITION_KINDS`,
  old side otherwise, per ADR-049 D4: "Removal and modification ... use
  old-side evidence. Addition ... use[s] new-side evidence") and checking
  only that side's `public_symbols`/`public_types`/`ambiguous_type_names`
  and resolvability throughout, replacing the earlier "either side
  resolvable" gate with an authoritative-side-specific one.

- **ADR-049 Phase 3 shadow evaluator: documented, not fixed, a known D5
  provider-completeness gap for terminal header exclusions**:
  `REASON_PRIVATE_HEADER`/`REASON_SYSTEM_HEADER` are treated as
  unconditionally terminal (`PROVEN_OUT_OF_CONTRACT` with `COMPLETE`
  assurance), but ADR-049 D5 requires every stronger-or-equal provider
  (an exact manifest, a required-symbol overlay, consumer-import evidence)
  to have completed first -- this module has no persisted
  provider-completeness ledger to consult (a known, already-documented
  Phase 3 gate: "every shadow delta has evidence" is Phase 3's own gate
  item, not yet built). The two provider signals this module *can* already
  see (`force_public_symbols`, `--post-manifest`) are both checked earlier
  and can never coexist with reaching this terminal branch for the same
  finding; the remaining gap is entirely providers not yet wired as inputs
  anywhere in the codebase. Documented in `_TERMINAL_SURFACE_REASONS`'s own
  comment and locked in with a regression test so building the real
  provider ledger is a deliberate, visible change to this behavior, not a
  silent drift.
