### Fixed

- The C++20 concept pre-scan no longer reads the body of an `#elif 0` that
  follows an `#if 0` as live code, so a header whose only concept sits in
  such a branch is no longer reported as having unobserved constraints.
  Extractor generation 58.
- Four text scanners (type-graph names, namespace spellings, the castxml
  out-of-line inline scan) no longer let a stray closing bracket drive their
  nesting depth negative.
