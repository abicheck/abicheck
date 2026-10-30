<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **One spelling for the version label, the Python floor and the dependency roots (ADR-091 D5)** — the per-side version label is `--label` on `compare`, `dump` and `project history` (was `--version`; the root `abicheck --version` still prints the tool version). `compare --abi3-floor` replaces `compare --abi3`. `deps compare --root [old=|new=]PATH` replaces `--old-root`/`--new-root`, and `deps tree --root PATH` replaces `--sysroot`. See [Upgrading to 0.7](https://abicheck.github.io/abicheck/start/upgrading-to-0.7/).

### Deprecated

- The retired spellings `compare --version`, `dump --version`, `project history --version`, `compare --abi3`, `deps compare --old-root`/`--new-root` and `deps tree --sysroot` still work for one release. They are hidden, behave identically, and print one `DeprecationWarning` on stderr that names the new spelling. Passing an old and a new spelling for the same option is a usage error (exit 64). They are removed in the next minor release.
