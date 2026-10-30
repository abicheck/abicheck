<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **The retired CLI spellings are gone, with no transition window (ADR-091 D7)** — `compare --version`, `dump --version` and `project history --version` (use `--label`), `compare --abi3` (use `--abi3-floor`), `deps compare --old-root`/`--new-root` (use `--root old=PATH`/`--root new=PATH`) and `deps tree --sysroot` (use `--root PATH`) are no longer accepted. Each now fails as an unknown option with exit 64. The root `abicheck --version` is unchanged. Migration table: [Upgrading to 0.7](https://abicheck.github.io/abicheck/start/upgrading-to-0.7/).
