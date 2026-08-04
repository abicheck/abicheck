### Added

- `scripts/measure_contract_shadow.py` now measures *unresolved* public-break
  losses — a real break withheld from the gate because the contract decision
  could not resolve it — alongside the existing proven-loss metric, budgeted
  per contract domain and pinned by full finding identity.
