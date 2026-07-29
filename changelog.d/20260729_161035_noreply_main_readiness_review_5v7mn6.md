### Fixed

- **Apple/Xcode Clang's `compiler_version` no longer gets rejected in favor
  of its build identifier.** `_extract_version_token`'s "last dotted token
  on the first line" heuristic picked `1600.0.26.4` (the parenthetical
  build ID) instead of `16.0.0` (the real version) from a banner like
  `Apple clang version 16.0.0 (clang-1600.0.26.4)`, rejecting a valid
  `>=16,<17` constraint. Now tries the dotted number immediately following
  the literal word `version` first — both plain and Apple/Xcode Clang spell
  the real version that way — falling back to the previous last-token
  heuristic only when no such keyword is present (GCC's own banner has
  none).
- **`project plan`/`abi_project_plan`'s toolchain-identity check no longer
  probes profiles the generated run-plan never uses.** A committed
  toolchain-bindings file may legitimately be shared across runners (e.g.
  naming both a Linux and a macOS toolchain); a non-contract, unreferenced
  profile's binding can name a platform-specific compiler that's simply
  unavailable or mismatched on the current host. Probing it anyway aborted
  an otherwise-valid plan over a profile the plan never resolved a check
  for. Now restricted to the profile IDs the generated plan's checks
  actually reference — `project validate`/`abi_project_validate`
  intentionally keep checking every declared profile, since they validate
  the config itself, not one runner's resolved plan.
