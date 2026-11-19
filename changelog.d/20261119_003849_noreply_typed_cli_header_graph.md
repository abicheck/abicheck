<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Typed-API header graph no longer drops re-exported `std::` names** — header-proven public-surface declarations now include `std_reexports` entries, so a removed `using`-declaration (function, type or variable) scores as an API break instead of `PROVEN_OUT_OF_CONTRACT`. Affects `dump` with public headers and `compare` on the typed API.
