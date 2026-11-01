### Fixed

- `abicheck dump RELEASE --bundle-facts-out PATH` refuses a malformed project
  config discovered in the working directory with exit 64, as
  `compare --bundle-facts-out` does. It used to drop the config's
  `scope.public_header_dirs` silently and write the document anyway.
