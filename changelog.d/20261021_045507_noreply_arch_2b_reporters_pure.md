### Changed

- **HTML report compute no longer reaches policy directly** — `html_report.py` now resolves its impact text, active `reclassify:` rules, effective kind sets, disposition audit (reusing the envelope's shared document when given) and versioning-policy acceptance through `report/` owners (`report/finding.py`, `report/disposition_audit.py`, `report/versioning_policy_section.py`). The ratchet test covers `html_report.py` and records only its two remaining legacy root imports, so the allowlist can only shrink. Rendered HTML, exit codes and the public `html_report` names are unchanged.
