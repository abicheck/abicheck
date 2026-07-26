<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck compat check` now surfaces the ADR-050 D2 comparability gate
  instead of an unhandled traceback.** `compat/cli.py`'s `compare()` call had
  no exception handling of its own, unlike its sibling operations (dump,
  descriptor parsing, report writing), each already wrapped in its own
  `_compat_fail`. A `ProfileMismatchError`/`ScopeMismatchError` now routes
  through `_compat_fail` and exits **`9`** — the one code the documented 3-11
  extended error range left unused — distinct from native `compare`'s own
  `16`, since the two commands maintain independent exit-code schemes.
