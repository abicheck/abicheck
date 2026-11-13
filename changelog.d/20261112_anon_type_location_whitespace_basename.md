### Fixed

- An anonymous-type or lambda location whose declaring header's basename
  begins with whitespace now strips to the same identity whether or not a
  checkout root precedes it. The bare spelling's leading whitespace was
  consumed as the separator after `at`, so the identity depended on the
  checkout root, which the stripping exists to remove.
