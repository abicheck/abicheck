### Fixed

- **Scope-comparability gate: additive-only header-set changes no longer
  hard-fail** — `abicheck compare` no longer refuses a comparison
  (`ScopeMismatchError`, exit 16) just because the new side declares
  strictly more public headers or public-header directories than the old
  side, with nothing removed or renamed. `check_contracts_comparable`
  (`comparability.py`) now checks each `SCOPE_FIELD_KEYS` field
  independently for a pure superset relationship
  (`_scope_field_is_additive_superset`) before raising — found live during
  a full-version-matrix scan reaching epics-base/pvxs's current `master`,
  which had added exactly one new public header
  (`include/pvxs/json.h`) since its last tagged release (ADR-050 D2, see
  `validation/pvxs-main-scan-2026-07-26.md`'s F8). A removal, rename, or
  disjoint header set — even alongside a co-occurring addition — still
  hard-fails exactly as before; `--diagnostic-comparison` remains available
  unchanged for those cases.
