### Changed

- **`abicheck/bundle.py` split into three, closing another G38 Phase 15
  file-split prerequisite**: `bundle.py` was sitting at the AI-readiness
  2000-line hard cap. Its individual `_detect_*` bundle-finding producers
  and the heuristic primitives they depend on moved into two new sibling
  modules — `abicheck/bundle_detectors.py` (structural, intra-dependency,
  provider-changed, and version-drift detectors) and
  `abicheck/bundle_detector_heuristics.py` (the manifest-drift/SONAME-skew
  detectors plus the system-provider/system-symbol/system-version/
  ELF-magic/namespace-stripping primitives several detectors share) — each
  well clear of the 800-line cap a brand-new file is held to.
  `bundle.py` re-exports every name an existing test or caller imports
  directly (`from abicheck.bundle import ...`) for back-compat
  (`bundle.py`: 1999 -> 877 lines), unblocking future work that needs new
  bundle-analysis surface without hitting the hard cap.
