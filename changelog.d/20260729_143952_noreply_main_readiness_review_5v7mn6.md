### Fixed

- **A comma-only or blank `compiler_version` constraint no longer vacuously
  matches every probed compiler.** `abicheck.buildsource.toolchain_probe`'s
  `parse_version_constraints`/`version_satisfies` now raise
  `ToolchainProbeError` when a spec like `","` parses to zero clauses,
  instead of silently reporting `project validate --toolchain-bindings` as
  passing regardless of what the resolved compiler actually is.

