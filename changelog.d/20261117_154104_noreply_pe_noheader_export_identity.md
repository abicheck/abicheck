<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Headerless PE dumps name MinGW C++ exports as C++** — `extract_pe` marked every export whose name did not start with `?` as `extern "C"`, so a MinGW C++ export (`_Z...`) was read as C. Its export-table functions now take `is_extern_c` and `entity_id` from the PE identity function that the legacy PE route already uses, so the two routes agree on identity. Snapshots of headerless PE binaries change only in these two fields; names, return types and visibility are unchanged.
