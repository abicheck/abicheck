### Performance

- The L2 clang AST no longer keeps each source location's `includedFrom`
  dict once locations are materialized (`extract/headers/clang/locations.py`);
  no reader of the L2 tree consults it. On a oneDAL clang comparison the
  peak RSS fell from 1.338 to 1.147 GiB with identical findings.
