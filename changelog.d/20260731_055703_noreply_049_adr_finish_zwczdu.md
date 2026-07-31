### Added

- **ADR-049 `contract=exports` is implemented in the shadow contract
  evaluator, backed by a new export-rooted evidence provider** —
  `abicheck/export_surface.py` (`compute_export_surface`) resolves a
  snapshot's ABI surface from the binary's *observed* export table (ELF
  `.dynsym`, the PE export directory, the Mach-O export trie) plus the
  closure over the raw record/enum/typedef graph. That is a different
  evidence domain than `surface.py`'s header-derived public surface, with no
  header-origin demotion anywhere: a private-header type reached from a real
  export is inside this contract, and an unexported public-header
  declaration is not. `evaluate_change_contract_relevance` now implements
  all three `ContractMode` values instead of raising `NotImplementedError`
  for `EXPORTS`, taking the new `exports_old`/`exports_new` surfaces —
  required for that mode rather than approximated from the header surface.

- **`compare --contract public|exports|all` selects that evidence domain**
  (ADR-049 Phase 6) — the domain `--contract-evaluation` judges each finding
  against, which until now was derived only from
  `--scope-public-headers`/`--no-scope-public-headers`. An explicit value
  outranks that legacy alias (ADR-049 D7's `explicit_cli` > `legacy_alias`
  precedence, resolved through Phase 1's own `resolve_legacy_contract_mode`
  rather than a second copy of the mapping). Available on the Python API and
  the typed request object too (`CompareRequest.contract_mode`,
  `service.run_compare`/`compare_snapshots`). Requires
  `--contract-evaluation`, and is advisory exactly like it: selecting a
  domain never changes a verdict, an exit code, or which findings appear.

- **The export surface now tracks unresolved type edges** — a root
  signature, or a reached record's field or base, whose type string names no
  record, enum, or typedef the snapshot carries leaves the closure
  incomplete, so `ExportSurface.exclusion_is_provable` (and with it any
  `PROVEN_OUT_OF_CONTRACT` decision) now requires there to be none. Spellings
  are resolved through `type_reachability.py`'s namespace-suffix and
  stdlib-stripping machinery, so a partially-qualified or bare standard-library
  spelling still resolves; toolchain-owned records' internals and dependent
  (`typename`/`template`) spellings are excluded as non-edges.
