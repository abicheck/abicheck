### Fixed

- **CLI cleanup phase two, PR B follow-up (Codex review, real reproduction)**:
  `scan --against --dry-run --pack <gate-pack>` now previews the pack-folded
  exit-code scheme and severity levels, not a stale snapshot computed before
  the pack was applied. The preview values were being derived from
  `resolved_cfg` before `_resolve_scan_evaluation_config` folded a selected
  gate pack into it, so a pack that changed the scheme for the real run left
  `--dry-run` describing the legacy/pre-pack scheme instead.
