### Fixed

- **`service_scan.run_scan` (the Python API) now rejects `ScanRequest`'s
  `policy`/`suppression`/`policy_file`/`scope_to_public_surface`/
  `force_public_symbols`/`pattern_verdicts`/`env_matrix` fields when no
  baseline comparison actually runs** (`baseline=None`, or `mode="audit"`
  despite a baseline) — mirroring the CLI's identical `scan_cmd` guard.
  Without this, a caller could set e.g. a `policy_file` requiring evidence
  and have it silently discarded rather than surfaced as a
  `ValidationError` (Codex review, PR #657).
