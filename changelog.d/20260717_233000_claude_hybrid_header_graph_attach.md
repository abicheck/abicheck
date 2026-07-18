<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `service.run_dump`'s `--ast-frontend hybrid` path forwarded `header_graph`
  to both recursive castxml/clang sub-dumps, so each attached its OWN
  header-only (L2) semantic graph seeded from only that one backend's
  declarations — the final merged snapshot's embedded graph could then miss
  a clang-only declaration the merge appended (one castxml never produced
  at all). `header_graph` attachment is no longer forwarded to either
  sub-dump; it now runs exactly once, on the already-merged snapshot.
