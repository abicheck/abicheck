### Removed

- **A `--policy` document's top-level `acknowledgment:` key is renamed to `acknowledgments:` (ADR-091 R3, breaking).** This matches the `.abicheck.yml` block renamed in 0.7 (`acknowledgments:`), so the same concept has one spelling. There is no alias. A policy file that still says `acknowledgment:` now fails before analysis with an error naming `acknowledgments:`, instead of being read as an unknown key. Rename the block in the policy document; the keys inside it (`unacknowledged_additions`, `breaks`) are unchanged.

### Changed

- **ADR-091 is Accepted and implemented.** The option-vocabulary migration (`--label`, `--root`, `--set`, the Action input renames and tombstones, the typed API renames, and the config renames) is complete; the remaining spelling exceptions are recorded in ADR-091 and `architecture/option-vocabulary.yaml`.
