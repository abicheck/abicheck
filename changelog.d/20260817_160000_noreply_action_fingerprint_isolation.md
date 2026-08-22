### Security

- **The composite Action now isolates the report-fingerprint Python helper
  from untrusted checkouts.** `_file_fingerprint` runs from the Action's
  private temporary directory with `PYTHONPATH` cleared, preventing checkout
  `sitecustomize.py` startup hooks from executing before a comparison.
