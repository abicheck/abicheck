<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The ADR-050 profile fingerprint discarded the AST frontend's own
  identity** (Codex review, PR #624 follow-up): `compiler_version` read
  only `ast_toolchain["compiler_version"]` (falling back to bare
  `"version"`), which for a castxml-produced snapshot always resolves to
  the *host compiler's* identity, silently dropping castxml's own
  producer/version — two dumps made with different castxml binaries (or
  castxml vs. clang, when both happen to wrap the same host
  compiler/version) could share a `profile_fingerprint` despite castxml and
  clang's `-ast-dump=json` having materially different parsing
  capabilities. Added `dumper_contract._profile_compiler_version()`,
  combining `producer` + the frontend's own resolved version + the host
  compiler version into one hashed value.
- **PE/Mach-O header-scoped dumps didn't thread `public_headers`/
  `public_header_dirs` into the ADR-050 scope fingerprint** (Codex review,
  PR #624 follow-up): `service.run_dump` applies those inputs to a
  PE/Mach-O snapshot separately, via `_apply_native_provenance`, *after*
  `_try_header_scoped_dump` returns — so the same call that now attaches
  `contract` (added in the previous fix) hard-coded
  `public_headers=None`/`public_header_dirs=None`, meaning two saved
  snapshots differing only in declared public-header provenance could
  share a `scope_fingerprint`. Threaded both inputs through
  `_try_header_scoped_dump()` and the two `service._dump_pe`/
  `service._dump_macho` wrappers, from the same `run_dump` call sites that
  already forward them to `_apply_native_provenance`.
