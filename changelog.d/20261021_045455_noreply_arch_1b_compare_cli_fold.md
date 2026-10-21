<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **One owner for the compare operand refusals** -- the rule that a forced ELF debug format cannot meet a PE or Mach-O side, and the two `--dump-manifest` rules (a manifest side that also names headers, and a manifest on a non-ELF input), are now decided once in `abicheck/workflows/operand_rules.py`. The `compare` CLI keeps its own `.abicheck.yml`/flag wording and exit code (exit 64 for the manifest rules, the same bad-parameter error for the debug format), and the typed API keeps its request-field wording. The duplicate CLI copy of the debug-format rule is removed, and `service_compare_evidence` no longer owns it. Behaviour is unchanged: every refusal prints and exits as before.
