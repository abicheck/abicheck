---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Destructive test-helper resets are guarded one helper at a time (2026-09-30)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** No gate guards against rmtree of a caller-supplied path: 40 rmtree call sites remain under tests/ and scripts/, and the entry says the /tmp canary detector is not wired into CI. Not further verified.


The "something prunes `/tmp` on these runners mid-job" behind `ci.yml`'s
`TMPDIR=$RUNNER_TEMP` step was a unit test: it handed
`scripts/check_l2_cli_perf.py`'s `_reset_cache` the path `/tmp`, and that
helper was a bare `rmtree`. It now empties only a cache root the harness
created and marked (`prepare_cache_root`), refusing anything else
(`tests/test_l2_cli_perf_cache_reset.py`; bug class
`test_harness.destructive_reset_of_a_caller_supplied_path`). What remains
open: no gate stops another helper under `tests/` or `scripts/` from
`rmtree`-ing a caller-supplied path, and the detector that found this one
(a canary file under `/tmp`, checked before and after every test by a
`pytest_runtest_protocol` hookwrapper, per-worker timestamps to name the
overlapping test) is not wired into CI. The `TMPDIR=$RUNNER_TEMP` step stays:
it is cheap isolation and would contain the next such helper.
