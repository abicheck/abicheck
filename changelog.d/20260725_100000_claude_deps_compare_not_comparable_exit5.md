<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck deps compare` now surfaces the ADR-050 D2 comparability gate
  instead of silently reporting "nothing to report" for the affected
  dependency.** `stack_checker.py`'s `_run_abi_diff` used to swallow every
  exception from `compare()` — including a genuine
  `ProfileMismatchError`/`ScopeMismatchError` — into the same bare
  `abi_diff=None` as an unreadable file or a real crash. It now re-raises
  the two comparability-gate exceptions; the caller attaches the reason to
  a new `StackChange.not_comparable_reason` field (surfaced in both the
  JSON and markdown reports), and `deps compare` exits **`5`** — a new
  code, dominating the existing `0`/`1`/`4` verdicts — when at least one
  dependency's before/after DSOs weren't extracted under a comparable
  contract.
