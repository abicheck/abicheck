### Added

- Compare two Linux deployment directories with `--release-consumer bin/client` to record the old ELF client's loadability, versioned direct imports and candidate providers alongside the release verdict in JSON or Markdown. Place the identical client at that relative path in each root. The existing dependency resolver models the loader search. The release exit code still assesses the full interface.
