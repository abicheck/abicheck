### Changed

- **Internal: `classify_compare_pair` splits at the classification boundary.** Everything it does after `compare_snapshots` and the evidence fold now lives in `abicheck.workflows.compare_finalize.finalize_classified_pair`: operand metadata and the same-binary note, `requested_depth`, analysis assurance, evidence depths, the suppression audit, the exit decision and the resolved gate receipt. `classify_compare_pair` still calls it, so the typed API's results are unchanged. This is the first step of routing the native `compare` CLI through the same core.
