<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A single declared header's own filename was incorrectly load-bearing
  for the ADR-050 comparability gate** — a real regression discovered via
  a near-total-red CI run once `dumper.py`'s `contract` wiring went live
  at scale (this same PR): both `scope_fingerprint`'s `headers` field and
  `profile_fingerprint`'s `header_sequence` field encoded a declared
  header's own basename as identity, even when only *one* header was
  declared per side. Renaming a project's single main header between
  versions (`v1.h` → `v2.h`, or any other rename) is a common, legitimate
  practice — not the "manifest/CLI-flag drift" mistake this fingerprint
  exists to catch — and roughly half of `examples/`'s own case fixtures
  (plus every `abidiff`/`abi-compliance-checker` parity test, which name
  synthetic headers after each side's binary) use exactly this pattern,
  so essentially the entire CI matrix broke the moment real dumps started
  populating `contract`. With only one declared header there is nothing to
  disambiguate a name against anyway; the multi-header case (2+ co-located
  declared headers, where a name genuinely disambiguates one file from
  another) is unchanged and still catches a real declared-surface
  difference. The renamed header's actual API surface is still verified
  by the ordinary diff engine — this only concerned whether the extraction
  inputs counted as "the same declared surface" for the comparability
  gate.
