### Fixed

- Removed two unreachable, superseded copies of the not-collected-reason loop
  left stacked under the live one in `buildsource/evidence_report.py`. A new
  per-PR `static-dead-code` gate (`scripts/check_static_dead_code.py`) now fails
  on unreachable statements and orphan modules, and ratchets unread parameters.
