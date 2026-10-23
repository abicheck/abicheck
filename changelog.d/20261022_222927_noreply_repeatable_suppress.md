### Fixed

- **`--suppress` is now repeatable instead of silently keeping only the last
  file** — `--suppress shared.yml --suppress variant.yml` used to drop every
  rule of `shared.yml` without a message. All files are now merged in
  command-line order (first match wins; a later matching rule is recorded as
  shadowed, never dropped), each rule keeps its own file and in-file index in
  `disposition_audit.rules`, the `unused_rules` gate and strict-expiry errors
  name the file that holds the rule, and a one-file invocation is unchanged.
  Applies to two-sided, release/directory and `--no-baseline` compares and to
  the typed API's `suppress` field. The Action's `suppress` input takes one
  path per line, each forwarded as its own `--suppress`.
  The typed API treats an empty `CompareRequest.suppress` collection the same as `None`.
