### Fixed

- ADR-049: an ambiguous type identity whose colliding entries are *all*
  public-header origin no longer withholds a contract decision. Both readings
  place the finding in the contract, so the ambiguity cannot change the
  answer; withholding there dropped genuine breaking findings from the gate.
  The proof counts public-origin qualified siblings against a newly recorded
  `PublicSurface.ambiguous_type_name_arity` and fails closed, so a collision
  hiding a private sibling stays unresolved as before.

### Added

- `scripts/measure_contract_shadow.py` now measures *unresolved* public-break
  losses — a real break withheld from the gate because the contract decision
  could not resolve it — alongside the existing proven-loss metric, budgeted
  per contract domain.
