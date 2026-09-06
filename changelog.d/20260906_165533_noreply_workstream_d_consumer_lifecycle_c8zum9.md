### Added

- **`compare --used-by-manifest`: consumer specification (digest, platform,
  profile, provider-baseline provenance, advisory/required)** — Workstream
  D-S1 (`vision-api-abi-evolution.md`, section "D. Optional prebuilt-consumer
  lifecycle"). A `--used-by`/`--required-symbol` consumer was previously a
  single binary path with no identity beyond "the file at this path", and an
  unreadable consumer was always a hard error. `--used-by-manifest PATH`
  (repeatable) names one or more consumer binaries via a small JSON document,
  each optionally carrying a `digest` (verified against the real file content
  before it is read), `platform`, `profile`, `provider_baseline`, and a
  `requirement` of `"required"` (default — an unreadable consumer aborts the
  run, same as a bare `--used-by <path>`) or `"advisory"` (an unreadable
  consumer is skipped and reported, never aborts the run). Manifest-named
  consumers merge into the same `--used-by` pipeline: they show up in the
  same `used_by[]`/`consumer_scope` report block and contribute to the same
  worst-wins scoped gate. `--format json` also gains a new
  `consumer_impact_summary` object ("N of M consumers affected") reporting
  `total`/`evaluated`/`affected`/`unreadable_advisory`/`unreadable_paths`
  across every supplied `--used-by`/`--used-by-manifest` consumer (report
  schema 3.3, additive). The GitHub Action gains a matching `used-by-manifest`
  input, mirroring `used-by`.

