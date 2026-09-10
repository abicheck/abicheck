<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **License headers on internal compatibility facades and package
  entry points** — `aggregate.py`, `aggregate_findings.py`,
  `aggregate_manifest.py`, `workflows/aggregate/__init__.py`,
  `workflows/artifact/__init__.py`, `frontends/cli/options/__init__.py`,
  and `frontends/cli/commands/__init__.py` now carry the standard
  Apache-2.0 header, closing `check_ai_readiness.py`'s `license-header`
  warnings. No behavior change.
