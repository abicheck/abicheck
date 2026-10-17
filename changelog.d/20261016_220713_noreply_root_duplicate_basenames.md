### Changed

- **The package extractors moved under their ADR-061 owner, `extract/`** — `abicheck.package` is now `abicheck.extract.packages`. It holds the RPM, Deb, tar, conda, wheel and directory extractors, `detect_extractor`, `is_package` and the shared-library discovery helpers. The root path is removed rather than re-exported, and `abicheck.storage.package` (the snapshot package model) is unchanged. **Migration:** import `detect_extractor`, `is_package` and the extractor classes from `abicheck.extract.packages`.
