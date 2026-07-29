### Fixed

- **`project plan` (CLI and MCP) now enforces the same toolchain-identity
  check `project validate` does.** Previously only `project validate
  --toolchain-bindings` checked a resolved binding's real
  `compiler_family`/`compiler_version`/`target` — `project plan` (and the
  MCP `abi_project_plan` tool), the command that actually produces the
  `run-plan.json` CI consumes, only resolved the binding path and never
  checked its identity, letting a run-plan silently emit the wrong
  compiler's path.
- **A declared `target` architecture match no longer masks an OS/ABI
  mismatch.** `target: x86_64-w64-mingw32` (Windows) bound to a real
  `x86_64-linux-gnu` compiler shares the same leading architecture
  component, so the earlier architecture-only check passed it silently.
  `abicheck.buildsource.toolchain_probe` now also compares a coarse OS
  family (Windows/Linux/Darwin/Android/*BSD) parsed from both the declared
  and probed target triples.

