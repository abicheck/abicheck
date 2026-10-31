### Fixed

- **`compare --bundle-facts-out` no longer records a library twice** — on a
  directory of JSON snapshots, each compared library was stored under both its
  file name and its snapshot's library name. Every stored key is now one
  release member.
