### Fixed

- The castxml header backend's preprocessed-source table now keys the files
  it stores the same way it keys lookups. A table built from, or read back
  with, a different spelling of a path (POSIX names on Windows, `./` or `..`
  segments) no longer misses the file and reports a variable's natural
  alignment as certain where an alignment specifier could apply.
