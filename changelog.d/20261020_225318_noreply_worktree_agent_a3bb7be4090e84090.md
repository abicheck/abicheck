<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A selected baseline is no longer silently discarded when `abi-baseline` is a file path.** `abi-baseline` given as a local file path is used directly, so a `baseline-profile`/`baseline-target` selection beside it had nothing to select from and was dropped without a word. The root Action now fails with a usage error in `action/validate-inputs.sh` and again in `action/run.sh`, naming the conflict. A release tag or `latest-release` with the selection is unchanged.
