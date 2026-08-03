<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`compare --pack` / `scan --against ... --pack`: ADR-049 D8 pack manifests
  now configure the run.** A pack is a small versioned YAML document
  (`id`/`version`/`kind`/`assignments`) carrying one reusable piece of
  configuration: a `kind: policy` pack assigns `ChangeKind` slugs to
  `break`/`warn`/`risk`/`ignore` exactly as `--policy-file`'s overrides do, a
  `kind: contract` pack assigns `surface.internal_namespaces` (and, since the
  contract-coverage exit below, `contract.unresolved`), and a
  `kind: gate` pack assigns `gate.exit_code_scheme` and
  `gate.severity.<category>`. Selecting one really changes the verdict and the
  exit code — the pack loader, the D8 conflict rule, and the resolver that
  composes them all landed in earlier ADR-049 phases, but nothing applied the
  result, so a first `--pack` was reverted before merge rather than shipped as
  configuration that configures nothing. Composition is D8's: an explicitly
  stated value (`--policy-file`, `--exit-code-scheme`, `--severity-*`, a
  `--profile`, or `.abicheck.yml`) always outranks a pack, and two selected
  packs assigning different values to the same field are a usage error (exit
  64) unless something else already states it. The applied value is read back
  off the one canonically-resolved configuration by provenance, so no front end
  can apply a value D7 precedence ruled out.
  A manifest assigning a field this build resolves but does not yet act on
  (`contract.overlays`, `assurance.require_evidence`) is
  rejected with the field and the reason, as is a `kind: gate` pack on `scan`
  (whose exit code follows its verdict directly, so it has no gate to move),
  `--pack` without `--against` on `scan`, and `--pack` on a directory/package
  (release) `compare`.
