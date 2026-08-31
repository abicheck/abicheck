### Fixed

- **`--stat --format json` now includes `suppression_audit`/`old_evidence_depth`/`new_evidence_depth`** — `reporter.to_stat_json` was the one JSON builder still calling `render_json` directly instead of the shared `render_json_with_side_facts` tail the other three (`to_json`, the leaf and root-cause report modes) already use, so it silently omitted these two ADR-061 Phase 2 item 5 facts while every other JSON output mode emitted them. Caught by CodeRabbit review.
