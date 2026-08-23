### Fixed

- **`--bundle-facts-out`'s help text referenced a `compare --against-bundle-facts`
  flag that doesn't exist (Codex review, fresh evidence).** This phase only
  ships the Python consumer API (`abicheck.bundle_facts.compare_bundle_from_facts()`);
  a CLI consumer is deferred, and `docs/use/multi-binary.md` already
  documents this correctly — but the option's own `--help` text pointed
  users at a command Click would reject outright. Reworded to point at the
  real Python API and note there's no CLI consumer yet.
