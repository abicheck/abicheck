### Fixed

- **`scan --artifact-set` now preserves and renders each member's own
  evidence-contract-abort reason**, instead of discarding it. Both the
  single-binary `scan`/`_run_scan_one_member` `_EvidenceContractError`
  catches used to drop `exc.message` outright, so an
  `EVIDENCE_CONTRACT_ERROR` member's own JSON report and the
  `--artifact-set` text renderer showed only the bare verdict — leaving the
  exit-`7` message's "see the JSON/text report's per_artifact entries for
  which member and why" pointing at a reason that was never actually
  written anywhere. `workflows/scan_abort_result.scan_abort_result_fields`
  now accepts the exception message and stores it under
  `report["evidence_contract_error_message"]`; `cli_scan.py`'s
  `--artifact-set` text renderer prints it per member (`  reason: ...`),
  and the JSON `per_artifact[].report` carries the same field.
  `SCAN_SCHEMA_VERSION` bumped to `1.27` for the new additive field. Found
  by Codex review on PR #1062.
- **Stale `--exit-code-scheme`/`exit_code_scheme:` mentions in agent
  instructions** (root `AGENTS.md`'s Exit codes section and its
  `pack_application.py` module-map narrative, and
  `abicheck/buildsource/CLAUDE.md`'s `.abicheck.yml` schema key list) were
  left unupdated when the flag/config-key/pack-field itself was deleted
  (CLI cleanup phase two PR G2), so a coding agent reading those files as
  the canonical source of truth would generate commands and configuration
  that now fail — `scripts/check_docs_contract.py`'s retired-surfaces sweep
  only scans `docs/`, not agent-guidance files, so it stayed green. Found
  by Codex review on PR #1062; fixed the content in this pass, extending
  the automated sweep's scope to agent-instruction files is left as a
  follow-up (see `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR G2
  section).
