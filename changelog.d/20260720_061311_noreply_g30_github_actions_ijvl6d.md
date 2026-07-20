<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--report-mode leaf` now carries the report-identity envelope fields
  too** — `check_id`/`profile_id`/`requested_depth`/`effective_depth`/
  `baseline_channel` (schema 2.12) were wired into the full and `--stat`
  JSON paths but not leaf mode's separate code path; code review on the
  introducing PR caught the gap.
- **`requested_depth`/`effective_depth` are now validated at the point
  they're set**, not only by the (opt-in, test-only) JSON Schema — an
  unknown depth spelling now raises `ValueError` immediately from
  `compare`'s/`scan`'s JSON builders, matching
  `mcp_server._validate_public_depth`'s existing check on the same public
  depth ladder (`abicheck/checker_types.py`'s new
  `EVIDENCE_DEPTH_VALUES`/`validate_evidence_depth`).
- **`check_id`'s JSON Schema now enforces its documented
  `target@profile#baseline_channel@requested_depth` shape** via a
  `pattern`, instead of describing it in prose only.
