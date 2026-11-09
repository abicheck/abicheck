### Fixed

- **A stored dump compared with the same binary is no longer `UNCONFIRMED`**
  (ADR-090 amendment). A dump of a binary now records the SHA-256 of the
  binary's bytes (the snapshot's `abicheck.binary_sha256` fact). So a stored
  dump compared with that same binary live, or two stored dumps of one binary,
  are recognized as one input: a stripped pair compared without headers reads
  `NO_CHANGE` with exit `0`, as `compare same.so same.so` already did, instead
  of `UNCONFIRMED` with exit `1`. A binary that differs in any byte (including
  a Windows rebuild that changed only the PE timestamp) is not one input and
  keeps `UNCONFIRMED`. Snapshots written before this release record no hash,
  so compared with a live binary they still read `UNCONFIRMED`; re-dump them
  to get the exemption. Extractor generation 50.
