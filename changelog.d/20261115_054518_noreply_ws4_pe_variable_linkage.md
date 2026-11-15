### Fixed

- **A C++ namespace-scope global binds to its MSVC data export** — a header-backed
  PE global with a plain spelling (`int g;`, exported as `?g@@3HA`) is now bound to
  its data symbol when that symbol decodes to the declared builtin type. A
  disagreeing type is refused, and a C global whose plain export is missing is still
  reported as `public_not_exported`. Declarations keep their castxml identity.
