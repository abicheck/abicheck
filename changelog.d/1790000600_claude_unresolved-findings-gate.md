### Fixed

- `compare --contract public` (or `exports`) no longer exits 0 when a
  finding's contract membership stays `UNKNOWN_UNRESOLVED` although every
  evidence provider closed -- for example a removed export whose only
  declaration sits in an `#ifdef` branch the parse did not take. Each such
  finding is listed in `contract_coverage_failures` (provider
  `finding_relevance`) and floors the exit code to 1;
  `contract.unresolved=warn` accepts it as before.
- Whole-surface metric findings (`public_surface_grew`,
  `public_surface_shrank`, `undocumented_export_ratio_increased`) are
  `NOT_APPLICABLE` under `--contract` instead of `UNKNOWN_UNRESOLVED`: they
  name no contract entity.
- Header identifier scanning treats a C++14 digit separator (`1'000`) as part
  of a number, not as the start of a character literal that swallowed the
  identifiers after it.
- Pointer-only internal-type leniency also requires proof for a record whose
  qualified name was never resolved.
