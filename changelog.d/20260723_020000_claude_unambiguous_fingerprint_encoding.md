<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`abicheck.comparability`'s fingerprint fields used unescaped `|`/`:`/`,`
  delimiters to join user-controlled strings** (Codex review, PR #624): a
  macro value or `-I` label containing one of these characters could make
  two genuinely different inputs serialize to the identical joined string —
  for example `macro_ops=[("D", "A|U:B")]` and `[("D", "A"), ("U", "B")]`
  both produced the literal string `"D:A|U:B"`, letting a real
  `profile_fingerprint` drift silently pass `check_contracts_comparable`'s
  gate. `macro_ops`, `include_sequence`, `headers`, `public_header_dirs`,
  and the ancestor-derived slot token's owned-header list now all use
  `json.dumps` instead of a raw delimiter join, which length-delimits every
  element unambiguously regardless of its content.
