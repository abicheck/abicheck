### Fixed

- The ABICC-compatible HTML report no longer emits a stray blank line inside
  its "Test Info" table when no `deployment:` contract governs the run. The
  optional Deployment Floor Digest row was interpolated onto a line of its
  own, so the empty case still emitted that line's newline — contradicting
  the code's own stated "omitted entirely (not an empty row)".
