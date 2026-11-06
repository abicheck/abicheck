### Added

- Catalog cases case230–case232 show C++ behavioral breaks: the declaration,
  symbol and layout stay identical (abicheck correctly reports `NO_CHANGE` at
  every evidence depth, including build data and sources), yet an unchanged
  consumer breaks at runtime: error reporting switched from a return code to
  an exception, a return value changed its unit, and a factory's ownership
  convention changed. A new subject page groups them with case208, and the
  learning docs gain a section on behavioral breaks as a separate kind of
  incompatibility.
