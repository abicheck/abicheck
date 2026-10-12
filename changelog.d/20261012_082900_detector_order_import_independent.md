### Fixed

- **Detector execution order no longer depends on which module a process imports first** — detectors run, are listed in reports, and tie-break cross-detector dedup in the order `abicheck.detector_registry.DETECTOR_MODULE_ORDER` declares. Previously the order followed import order, so a program or script that imported a detector module (e.g. `abicheck.diff_symbols`) before `abicheck.checker` got a different `detectors`/`not_evaluated_detectors` order, and potentially a different surviving finding among equivalent duplicates, than the CLI. The CLI's own order is unchanged.
