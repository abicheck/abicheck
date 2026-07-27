<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 3 shadow evaluator is now reachable through the Tier-2
  service, not just `checker.compare` directly** (`service.py`,
  `api_types.py`): `checker.compare`'s `contract_evaluation` parameter was
  wired in an earlier fragment, but no front-end may call Tier-1
  `checker.compare` directly (`cli-contract` AI-readiness gate, ADR-037
  D10.1) -- every real caller (CLI, MCP, `compare-release` fan-out,
  `appcompat`) routes through `service.compare_snapshots`/
  `run_compare_request`/`run_compare`, none of which forwarded the flag, so
  the shadow evaluator was completely unreachable outside a test calling
  the core function directly. Added `contract_evaluation: bool = False` to
  `compare_snapshots`, to `CompareRequest` (forwarded by
  `run_compare_request`), and to `run_compare`'s keyword shim (appended
  last, after `diagnostic_comparison`, preserving positional-argument
  safety for existing callers per this file's established convention).
  Regenerated `docs/reference/python-api-reference.md` for the new
  parameter/field.
