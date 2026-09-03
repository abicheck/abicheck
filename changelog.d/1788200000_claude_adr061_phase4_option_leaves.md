### Changed

- **ADR-061 Phase 4 (thin CLI and Python API)**: four CLI-frontend modules
  moved into the `abicheck.frontends` package — `cli_profiles.py` →
  `frontends/cli/options/profiles.py`, `cli_contract_options.py` →
  `frontends/cli/options/contract.py`, `cli_options_contract.py` →
  `frontends/cli/options/inventory.py`, and `cli_help.py` →
  `frontends/cli/help.py` (1,249 lines). The two renames are deliberate:
  `cli_options_contract` (the `cli-contract` gate's flag inventory) and
  `cli_contract_options` (ADR-049's contract-evaluation options) were
  unrelated things whose names differed only by word order. No behaviour
  change; all option decorators, help panels and gate metadata are
  unchanged.
