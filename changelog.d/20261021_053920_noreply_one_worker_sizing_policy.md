<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **One worker-sizing rule for every per-unit pool** — the manifest dump, the
  L4 source replay and the L5 call-graph pass each carried its own copy of the
  CPU and RAM clamps. They now all call `process_resources.bounded_jobs`, with
  their own `ABICHECK_*_JOBS` override, RAM-budget variables and RAM probe
  passed in. Worker counts are unchanged. Log output and two edge cases differ:
  the L4 replay and call-graph pass now warn on an unparsable override (they
  fell back silently), the call-graph pass warns on an oversubscribed or
  RAM-exceeding override, and a whitespace-only override counts as unset on the
  manifest dump and L4 replay (it used to mean serial).
