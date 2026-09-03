### Changed

- **`compare-release`'s severity/exit-code-scheme resolution now goes
  through one typed `GateOptions` object** (ADR-064's "`GateOptions` — the
  release fan-out's own prerequisite rewrite") instead of threading six raw
  preset/category/scheme strings independently through three functions,
  each re-deriving the same `SeverityConfig`. Purely internal: no CLI
  surface or externally observable exit code changes.
