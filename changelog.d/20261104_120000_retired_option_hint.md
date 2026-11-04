### Fixed

- **A retired ADR-091 option now names its replacement.** `compare --version old=X` (and `dump`/`project history --version`, `compare --abi3`, `deps compare --old-root`/`--new-root`, `deps tree --sysroot`) still fails with exit 64, but the error now says to use `--label` (or `--abi3-floor`/`--root`) instead of Click's fuzzy "Did you mean `--verbose`/`--variant`" suggestion.
