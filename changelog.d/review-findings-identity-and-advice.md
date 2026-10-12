### Fixed

- Bundle comparisons no longer drop `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` on equal per-symbol code hashes alone; only a byte-identical provider file proves the signature unchanged.
- Source-graph reconciliation no longer reports a declaration move when both sides declare the entity in a shared header but record different `def_file`s.
- The castxml unparseable-header fallback stamps the resolved compiler/language metadata of the final full-set parse, not of a concurrent probe.
- `compare --used-by`/`--required-symbol` release advice no longer recommends a major bump for a suppressed consumer-only finding the full-library gate never scored.
