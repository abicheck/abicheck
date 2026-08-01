### Changed

- ADR-049 Phase 6's contract-mode measurement (`scripts/measure_contract_shadow.py`)
  now runs a platform/shape corpus alongside the labelled FP-rate corpus, and
  reports unresolved rate per lane. The FP-rate corpus carries no export
  tables, so the `exports` contract domain previously measured 100%
  unresolved — measuring the absence of evidence rather than the domain. The
  new lanes (ELF, PE, Mach-O, stripped, versioned, `extern "C"`) give it real
  evidence; the gate's four zero baselines are unchanged and still green.
  Lanes the measurement deliberately cannot reach are named with their reason
  rather than omitted.
