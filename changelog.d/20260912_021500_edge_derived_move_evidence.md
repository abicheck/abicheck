### Fixed

- A reconciled rename-and-move whose declaring files come from
  `SOURCE_DECLARES` edges rather than the declarations' own recorded paths
  again describes itself as "both name and location evidence changed",
  instead of reporting the location as unestablished.
