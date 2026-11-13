### Fixed

- The castxml header backend's preprocessed-source table now keys the paths
  it stores with the same normalisation it applies to lookups, so a table
  built or read back with another spelling of a path (another host's
  separators, `./` or `..` segments) still finds the file instead of
  treating it as one that spells no alignment specifier. Tables the backend
  builds itself were already normalised, so extraction output on a single
  host is unchanged (the extractor generation is not bumped).
