<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->

### Fixed

- The full-CLI L2 perf harness now validates **every** repetition rather than
  only the last, requires every file an invocation declares to write (not just
  the first), checks each cold/warm repetition individually instead of reducing
  the batch to its most favourable one, and requires the include-graph pass to
  have run on a live scenario. Each of these was a way for a *faster, wrong* run
  to be accepted: a degraded earlier repetition keeping its timing in the median,
  a stale export standing in for one a repetition never rendered, one lucky cache
  hit certifying a scenario, and a skipped `clang -M` pass reading as a speedup.
- The header-graph PR gate now passes `--require-all-metrics`, so a missing or
  non-gateable `dump_ms`/`attach_ms`/`total_ms` fails the job instead of
  degrading to a printed note. The comment previously justifying its absence was
  wrong about this job: the base measurement is taken with *head's* harness
  against base's installed package, so the base report always carries all three.
- Real-integration profile tool requirements are per header/compile context, so a
  host missing `icpx` reports oneDAL as `PARTIAL` with its three host libraries
  measurable rather than blocking the whole profile.
