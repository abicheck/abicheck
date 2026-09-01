### Fixed

- **A late `scan` budget overflow no longer discards a decision the run
  already resolved** (ADR-064 stage 1b follow-up; PR review finding on the
  fix above). `run_scan_core`'s *final* `_check_scan_budget` call — the one
  after a baseline compare may already have resolved a full gate/coverage/
  assurance `ExitDecision` — used to raise `_BudgetOverflow` with nothing
  attached, so `scan_abort_result_fields` persisted a budget-only decision
  and every other axis's already-computed contribution was silently lost.
  `scan_engine.py` now wraps that one call site in
  `abicheck.workflows.scan_abort_result.attach_prior_on_budget_overflow`,
  which gives the raised `_BudgetOverflow` a `prior_decision` (the prior
  `ExitDecision.to_dict()`, duck-typed via `hasattr` rather than an
  `isinstance` import of the private exception class); `service_scan.py`'s
  two catch sites now forward `exc.prior_decision` through to
  `scan_abort_result_fields`, which reconstructs it via
  `ExitDecision.from_dict` and threads it into
  `resolve_scan_exit_decision`'s own `prior_decision` parameter — matching
  that resolver's existing "budget discards, but preserves" contract. The
  earlier, first-stage `_BudgetOverflow` raise (before a baseline compare
  ever runs) has nothing to preserve and is unaffected.
