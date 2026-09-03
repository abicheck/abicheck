### Fixed

- **A dump of a library containing a lambda-bearing header no longer aborts
  with `FrozenInstanceError`.** The closure-identity renumbering pass
  (`renumber_anonymous_closure_identities`, which replaces a lambda marker's
  raw `:line:col` with a snapshot-stable ordinal so unrelated line drift
  cannot change a finding's identity) walked every dataclass reachable from
  a snapshot's functions/variables/types/enums and assigned rewritten strings
  back with `setattr` — which raises outright on a *frozen* dataclass. Such a
  value is now rebuilt via `dataclasses.replace` instead, so its own strings
  are renumbered like every other spelling rather than crashing the dump or,
  worse, silently surviving in the raw `:line:col` form this pass exists to
  remove.
