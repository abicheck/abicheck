<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action.yml`'s `since`/`changed-path` input descriptions still said
  "scan mode only" and "Maps to scan --since/--changed-path"** (Codex
  review, round 14), one commit after `action/run.sh` was fixed to actually
  forward both in `mode: compare`'s single-pair branch. Marketplace/IDE
  input documentation is generated from these descriptions
  (`docs/reference/github-action-inputs.md` via `gen_action_reference.py`),
  so a user reading only that surface would still believe the newly
  supported `compare`-mode scoping didn't exist and could unintentionally
  replay the whole source target instead of scoping to their PR's changed
  files. Updated both descriptions to name `compare` (single-pair operands)
  alongside `scan`, and regenerated the reference doc.
