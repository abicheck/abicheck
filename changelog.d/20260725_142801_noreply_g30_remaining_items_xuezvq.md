### Added

- **TU → link-unit → DSO source-evidence attribution** ([ADR-053](https://github.com/abicheck/abicheck/blob/main/docs/contribute/adr/053-tu-link-unit-dso-attribution.md), G30 P2). A new pure module,
  `abicheck.buildsource.link_attribution`, derives which output binary/binaries
  a translation unit's compiled code ends up in, from either a build system's
  real target graph (CMake, Bazel) or real linker/archiver invocations (now
  also captured by the `make` adapter, previously compile-lines only).
  `build-output.json`'s `evidence.projection: "inferred"` is no longer an
  unconditional hard failure — it validates by re-deriving the attribution
  and confirming it genuinely ties evidence to the target, so one build-wide
  evidence pack can now be safely and automatically split across several
  targets. `abicheck.buildsource.inputs_pack.ingest_inputs_pack()` gained
  matching opt-in `attribution`/`expected_target_id` parameters to filter a
  shared pack down to one target's own translation units.

