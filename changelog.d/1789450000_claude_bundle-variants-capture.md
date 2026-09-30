### Added

- **Multi-variant capture from `.abicheck.yml`.** A new `bundle_variants:`
  block declares a project's build variants (variant name → `target_triple`,
  `compiler_family`, `feature_toggles`, `required`, default `true`), strictly
  validated on every config load. The new `abicheck project capture-variants
  --variant NAME=PATH ... --package DIR` captures every declared variant into
  one `ProjectSnapshot` package, one variant record each, keeping what the
  config *declared* separate from what the capture *observed* (the DWARF
  producer's compiler family/version, target architecture, binary format):
  if the config says `clang` and the binary says GCC, both are recorded. A
  required variant with no input, a missing path, nothing to capture, or a
  failed capture is a usage error before anything is written; an optional
  one is skipped, reported, and simply absent from the package. `--dry-run`
  checks the plan without capturing.
- **Variant pairing in stored release comparisons.** Comparing two such
  packages with `compare` adds `comparison_scope.variant_pairing` to the
  JSON report (release schema 1.10): both packages' variants paired by id, a
  change to a variant's declared coordinates reported as a variant-boundary
  change distinct from captured drift such as a compiler-version bump, and a
  variant present on only one side reported as unmatched (never as a
  removal), with required ones listed under `unmatched_required`.
  Report-only: verdicts and exit codes are unchanged.
- Two variants of one stored package may now contain a library with the same
  name.
