### Added

- **`project validate --toolchain-bindings` now enforces declared toolchain
  identity, not just binding resolution (G34 Phase A).** A
  `profiles.<id>.compile`/`consumer_compile` overlay's `compiler_family`/
  `compiler_version` is now checked against the real executable its
  `binding` resolves to, via a cheap probe reusing the existing cached
  `--version`-capture plumbing (no new subprocess handling). A mismatch —
  wrong family, or a version outside a declared constraint like
  `">=14.2,<15"` — is reported as a validation error alongside the existing
  unresolved-binding check. MSVC bindings are skipped (`cl.exe` has no
  `--version` flag), a documented limitation rather than a silent guess.
  See `abicheck.buildsource.toolchain_probe`.

