### Fixed

- **Binary-only package self-compare is clean again** — comparing a
  multi-library package against a byte-identical copy without headers no
  longer reports one `bundle_intra_dep_signature_unverified` per
  intra-package call edge (75 on oneDAL), which turned `NO_CHANGE` into
  `COMPATIBLE_WITH_RISK`. The finding is now skipped when the provider is
  proven unchanged — its file `sha256` matches on both sides, or the
  symbol's `ElfSymbol.code_hash` does — since a state identical on both
  sides is not a change.
