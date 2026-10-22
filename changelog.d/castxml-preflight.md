### Fixed

- **A missing `castxml` now fails before any extraction** — when an ELF `compare`, directory/package `compare` member, or `dump` will parse headers with the castxml or hybrid frontend and `castxml` is not on `PATH`, the run fails up front with the same "castxml not found in PATH" error, install hint and exit status, instead of after the binary and debug-info work (#1710). Runs without headers, with `--dwarf-only`/symbols-only, with the clang frontend, or on PE/Mach-O (which degrade to export-table mode) are unchanged.
