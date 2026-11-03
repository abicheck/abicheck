### Fixed

- **A package captured from stored snapshots keeps their generation-drift
  notice** -- `project capture-variants` stamped the package *unstated*
  whenever a variant's input was a stored snapshot file, even when every
  snapshot stated the extractor/resolver generations it was dumped under. A
  gen-N snapshot reported generation drift when loaded on its own, but not
  once it had been wrapped in a package. The package now states the triple its
  facts agree on (a fresh dump contributes this build's, a stored snapshot its
  own stamp) and stays unstated only when they disagree.
