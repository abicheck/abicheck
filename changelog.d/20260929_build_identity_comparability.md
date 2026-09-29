### Added

- `build-output.json` accepts an optional `profile.build_system: {name, generator}`
  (e.g. `{"name": "cmake", "generator": "Ninja"}`); the validator rejects a
  malformed value and flags one that disagrees with a target's attribution
  build evidence.

### Changed

- The comparability gate now checks build identity: two snapshots whose
  embedded L3 build evidence names different build systems/generators, or
  different requested root targets, are refused as not comparable
  (`ProfileMismatchError`) instead of being diffed. When only one side
  records its build system (e.g. an older compile-DB-only baseline) the
  comparison runs but is bounded: a coverage warning and per-dimension
  `unverified` assurance, never a clean pass. See ADR-050's 2026-09-29
  amendment.
