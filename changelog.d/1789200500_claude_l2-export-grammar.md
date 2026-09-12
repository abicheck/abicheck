### Changed

- **The full-CLI L2 harness follows the new export grammar, and now verifies the
  promise it makes.** ADR-068 slices 7m/7n replaced `--format F -o PATH` with a
  repeatable `-o FORMAT=DESTINATION`, where every export renders the one
  completed analysis. The two-format scenario is therefore a *single* `compare`
  producing both artifacts, and it checks that the second renderer does not
  re-run the analysis — over a stored-old/live-new pair the invocation performs
  exactly one side's worth of header extraction. (Stored/stored would have been
  easier and useless: the count is zero either way, so it could not distinguish
  one analysis from two.) A logic-level grammar guard now fails in milliseconds
  if the harness and the CLI drift apart again, instead of a real subprocess
  exiting 64 partway through a lane.

### Fixed

- Removed an `if`/`else` in the new `l2-cli-perf` CI job whose two branches ran
  byte-identical commands, and the dead loop variable, unspecified text
  encodings, implicit `subprocess.run(check=...)`, outer-scope shadowing and
  over-broad `except Exception` that a lint sweep of the new scripts found.
  `classify_invocation` now consults the tool name as well as the argv, so a
  coincidental argv match on a non-AST tool cannot inflate the one count the
  stored-snapshot scenarios assert is zero.
- A live-measurement test still asserted the pre-rename `baseline_ms` after the
  three-metric split. It only runs in the `integration` lane, so a
  `-m "not integration"` sweep could not see it; it now asserts every gated
  metric plus the invariant that `total_ms` is never smaller than a phase it
  contains.
