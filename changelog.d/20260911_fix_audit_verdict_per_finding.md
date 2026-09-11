### Fixed

- The audit lane's finding-verdict check now validates every `findings[]` row
  individually, keyed by kind: a row with a missing or empty `verdict` is its
  own error rather than being silently filtered out, an unrecognised verdict
  is rejected instead of reading as hygiene, and the highest observed severity
  band must *equal* the band the catalog's flags claim — catching both a
  demoted break case and an escalated hygiene case.
- The out-of-band snapshot-reader AST guard now traces the unwrap's *result*:
  calling the unwrap and then indexing the raw document no longer satisfies it.
