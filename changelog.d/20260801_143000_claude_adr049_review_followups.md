### Fixed

- A JUnit coverage suite now carries its library in the suite name and each
  testcase classname. In a multi-library document every suite was named
  alike and its cases identified only a provider and side, so two libraries
  failing the same provider produced two indistinguishable errors.
- `run_scan` raises `ValidationError` for a request the resolver rejects (a
  D7 same-tier conflict, a D8 pack conflict, an unknown `policy`) instead of
  letting the resolver's own `ValueError` escape. Every other
  request-validation failure there already raised `ValidationError`, so a
  caller guarding `run_scan` with it did not catch these.
- The `contract_coverage_failures` JSON Schema requires `status` and
  `completeness`, which `CoverageFailure.to_dict()` always emits — in the
  packaged schema and its published mirror.
- `scripts/contract_platform_corpus.py` fails at import if a `PLATFORM_CORPUS`
  case has no `CASE_LANE` entry. It would otherwise fall back to the
  FP-corpus lane and silently inflate that bucket's rate.
