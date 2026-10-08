### Changed

- **`DiffResult` is pure data; classification moved to `abicheck.policy.evaluate`.** The `breaking`, `source_breaks`, `compatible` and `risk` properties are removed from `DiffResult` (Python API break). Call `abicheck.policy.evaluate.evaluate(diff)`, which returns a `ClassifiedDiff` with the same four lists plus `not_evaluated`: `diff.breaking` becomes `evaluate(diff).breaking`. `DiffResult.not_evaluated` stays. CLI output, exit codes and report schemas are unchanged.
