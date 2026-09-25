### Fixed

- **A C release no longer reports every public function as missing from the
  bundle.** The directory/package release reconciliation acquired its public
  surface with `compare`'s default `--lang c++` treated as an explicit request,
  so a C header tree was parsed as C++ while every member was parsed as C: each
  declaration's obligation became its C++ mangling, which no member exports,
  and all of them were listed under `missing_exports`. The release surface now
  resolves the language exactly as the member dumps do.

### Changed

- The release contract reconciliation attributes each satisfied obligation
  through the release's `provided_by` relation (ADR-075 D5) instead of reading
  the export index directly; `BundleExportIndex.satisfies`, which nothing else
  used, is removed.
