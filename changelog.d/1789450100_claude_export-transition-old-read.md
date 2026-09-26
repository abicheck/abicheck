### Fixed

- **An unread OLD export table no longer hides a lost export.**
  `export_transition.surface_exit_is_evidence_gap` suppressed a surface exit
  whenever OLD was "not exported", which it read as "not confirmed
  exported" -- so an OLD binary whose export table was never read
  suppressed a declaration's exit even when it had really exported it. The
  guard now requires OLD's absence to be established: the removal paths hand
  it the OLD table as `compare.edge_query.ObservedExportTable`, whose
  `answer()` is the I4 typed answer (`proven_absent` only when the
  `exports` coverage records cover the table), and a caller passing a bare
  name set still refuses when OLD's own export fact is `FAILED`. An OLD with
  no binary at all owes no table and keeps suppressing.
