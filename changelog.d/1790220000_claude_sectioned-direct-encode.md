### Changed

- Writing a sectioned snapshot no longer routes every section through a
  throwaway content-addressed object store (canonicalize, hash, deep copy,
  read back). Sections are encoded directly through the same validation and
  DTO codecs; output is byte-identical. On a 76-root Intel SVS snapshot this
  lowered the packaging step's peak from 424 to 283 MiB and its time from
  81 s to 28 s.
