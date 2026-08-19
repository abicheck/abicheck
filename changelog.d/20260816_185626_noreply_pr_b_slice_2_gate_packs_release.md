<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`--pack`'s `kind: gate` manifests now apply to a directory/package
  (release) `compare`** — previously a `kind: gate` pack's
  `gate.exit_code_scheme`/`gate.severity.<category>` assignments were
  rejected outright on the release fan-out, even though a `kind: policy`/
  `kind: contract` pack's `policy.overrides`/`surface.internal_namespaces`
  already applied uniformly to every library (CLI cleanup phase two, "PR B"
  slice 1). A selected gate pack's contribution is now folded into the
  release fan-out's own severity/exit-code-scheme resolution the same way
  it already is for a single-pair `compare`, so every library in the
  release sees the identical pack-resolved gate policy. `scan --against`'s
  own `kind: gate` rejection is unchanged.
