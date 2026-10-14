### Fixed

- **Failed or interrupted dumps no longer wait for a running header-graph parse** — the header-graph clang prefetch that runs beside a primary dump is now cancelled when that dump fails or is interrupted: its clang child process group is killed and its thread-budget slot is released. Previously the child kept running to completion, so the process could not exit until the parse finished. Only that child's own process group is signalled. Successful runs are unchanged.
