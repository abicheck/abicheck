### Added

- **A trusted toolchain-bindings file resolves `profiles.<id>.compile.binding`
  logical ids to executable paths** (P1 toolchain-profile audit). New
  `abicheck.buildsource.toolchain_bindings` (`load_bindings_file()`,
  `resolve_binding()`, `check_profile_bindings_resolve()`) loads a schema
  `abicheck.toolchain-bindings/v1` YAML document — `{schema, bindings: {id:
  path}}` — from an explicit path only, never auto-discovered, mirroring
  `buildsource.extractor_manifest`'s "trusted-by-operator" security model:
  an auto-discovered `.abicheck.yml` may *declare* a logical `binding` id
  (already whitespace-atom-validated), but resolving it to a real
  executable now requires this separately-trusted mapping. `abicheck
  project-targets validate` gains `--toolchain-bindings PATH`, which
  additionally checks every declared `profiles.<id>.compile.binding`
  resolves against the given file — omitting the flag skips the check
  entirely (backward compatible; a declared `binding` with no bindings
  file given is not itself a validation error). No dump/compare consumer
  resolves a binding into an actual frontend/compiler invocation yet — that
  is the larger, not-yet-built G30 P1.4 run-plan-to-invocation wiring; this
  is the trust-boundary-respecting resolution primitive that work will
  consume.
