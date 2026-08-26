### Changed

- **`abicheck.cli` is now a registration facade** (ADR-061 Phase 4): 1959 lines
  down to 128. The `dump` and `compare` command bodies, the shared CLI runtime
  (verbosity, output writing, provenance stamping, the process-exit decision)
  and the historical-import map moved into `abicheck.frontends.cli`. Every name
  `abicheck.cli` used to define stays importable from there, resolved lazily,
  so no call site changed.
- **A frontend now reaches the engine's decisions through one workflow surface
  rather than reaching past it.** `abicheck.workflows.gate` is the single place
  a frontend gets its process response — the compatibility verdict, the ADR-049
  contract-coverage floor and the assurance floor — so a new frontend inherits
  the whole decision or none of it, instead of being free to fold two axes and
  forget the third. `workflows.extraction`, `workflows.findings` and
  `workflows.scan_config` cover the input-side operations, finding identity and
  scan configuration. `workflows.scan_config` also becomes the real owner of
  the risk-rules loader, the public-provenance rule and the scan-config
  resolver, which the engine previously had to import back out of the CLI.

### Fixed

- **A malformed `--risk-rules` profile no longer reaches a typed API caller as
  a click exception**, on the paths that resolve it through the engine: the
  loader now raises `SnapshotError`, which `service_scan` translates to
  `ValueError` at its own boundary. The CLI's exit code is unchanged (**1**,
  operational).

### Notes

- Two things worth knowing when writing tests against the new layout: a
  `monkeypatch.setattr` on a name resolved through `abicheck.cli`'s lazy shim
  rebinds nothing the real caller reads, and a re-export surface binds its
  names at import time, so patching the original module does not reach a caller
  coming through the facade. Patch the owner.
