<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`--set PATH=VALUE` on `compare` and a `config_overrides` argument on `CompareRequest`** (ADR-091 D4) — set one `.abicheck.yml` key for one run, e.g. `--set scope.show_redundant=true`. The value is YAML, so `true`, `3` and `[a, b]` have their file types. It goes through the same schema as the file, and an unknown key, a wrong type or a malformed assignment exits 64 naming the key. Precedence is explicit flag > `--set` > `.abicheck.yml` > default. A core option with its own flag (`--include`, `--define`, `--contract`, `--severity-preset`, ...) is refused and the error names that flag, so each option keeps one spelling. A directory or package comparison accepts only the keys its members receive (the severity levels, `scope.public`, `scope.collapse_versioned_symbols`, `deployment`) and refuses the rest. `compare` applies only the keys it resolves from the config; a key it does not yet apply (such as `compile.*`, `build.*`, `sources.*`) is refused with exit 64 rather than dropped. `--set` is not yet accepted by `dump`, `project plan` or `project capture-variants`, nor on a comparison of a stored BundleFacts document, which refuses it with exit 64.
