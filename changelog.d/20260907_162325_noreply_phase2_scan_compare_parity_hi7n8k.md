### Added

- **Four more cross-source hygiene checks run automatically inside `compare()`** — `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`, and `public_to_internal_dependency` (ADR-068 plan §3 rows 3-5) join `unversioned_exported_symbol`/`private_header_leak` on the `cross_source_checks` pipeline stage (on by default, no new flag). `.abicheck.yml` gains a `scope.public_header_dirs` list so a project can declare its public/internal header boundary for `compare` even without a `-H` directory argument (ADR-068 plan §5 P4); a `-H` directory argument continues to work exactly as before.

