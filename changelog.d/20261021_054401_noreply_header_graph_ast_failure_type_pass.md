### Fixed

- **A failed clang header AST no longer reads the type pass as complete** —
  when the header AST fails, the header-only graph's type pass is now recorded
  as degraded and not collected, instead of complete. Before, its structural
  edges came from the snapshot's own declarations and read as a full pass, so
  a `DECL_HAS_TYPE` or `DECL_REFERENCES_DECL` change the AST would have shown
  could be reported as clean. Type-edge coverage on such a run is now reported
  as not collected, which can make a result report fewer confirmed type-edge
  findings than before. A run with no AST failure is unchanged.
