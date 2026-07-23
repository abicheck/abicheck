<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **Clarified what schema v12's hard-rejection guard actually protects**
  (Codex review, PR #624): the comments around `SCHEMA_VERSION`/
  `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` in `abicheck/serialization.py`
  previously implied that any reader whose `SCHEMA_VERSION` predates the
  threshold hard-rejects a v12+ snapshot. That's only true for readers built
  from this commit onward — an already-released, pre-v12 install simply
  doesn't contain this guard's code, so it falls through to the ordinary
  warn-and-continue path and silently drops the unrecognized `contract`
  field, exactly the failure mode the comment claimed was closed. No in-band
  schema-version change can retroactively patch already-shipped code; the
  comments now say so explicitly and point at `contract_coverage="partial"`
  as the actual mitigation for a contract dropped this way. No behavior
  change — `contract` isn't populated by any real producer yet.
