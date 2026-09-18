### Fixed

- **Public-header provenance now matches a `-H`/`--header` root through a symlink.**
  When an include tree was named through a symlinked path (`-H /localdisk/.../inc`)
  but the header parser recorded the canonical one (`/mnt/cached_oses/.../inc/...`),
  every declaration classified as non-public and the run reported false
  `exported_not_public` findings; naming the canonical path was clean. A declared
  public root and a declaration's source location are now matched over path
  *aliases* — always the lexical spelling, plus a canonical (symlink-resolved) one
  when it is safe to obtain — so either spelling of one local tree reaches the same
  ownership answer. Canonicalization stays conservative: it applies only to a path
  rooted in the running platform's own syntax that resolves without error, so a
  POSIX-rooted path on Windows is never drive-anchored, a drive/UNC spelling on
  POSIX is never reinterpreted, a stored cross-machine path is never required to
  exist, and a resolution failure degrades to lexical matching rather than failing
  extraction. The lexical spelling remains the persisted configuration evidence —
  canonical spellings are runtime matching aids only and never change a saved
  baseline's identity. The same alias behavior applies wherever the ownership
  decision is made: public-header provenance, dependency-header classification,
  public directory roots, explicit public header files, and dump scoping.
