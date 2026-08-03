### Fixed

- `compare` and `scan` fold the `.abicheck.yml` `compile:` block in from the
  config they already parsed for the ADR-049 receipt, instead of re-reading
  the file. A second read could fold a different revision's compile settings
  in than the persisted digest describes, and a file deleted between the two
  reads dropped `compile.std`/`defines`/`sysroot` silently — no error, the
  headers just parsed without them.
