### Added

- **Required-symbol contracts on the typed Python API** —
  `CompareRequest(config_overrides={"consumers.required_symbols": [...]})`
  (and `run_compare(config_overrides=...)`) now applies a plugin-host
  required-symbol contract, as `compare --set` does: the same `@FILE` loader
  refuses an unreadable, missing, directory or non-UTF-8 file with the CLI's
  message, `plugin_abi` becomes the policy unless you chose one, and the
  result carries the same `required_symbol_contract` summary. It used to be
  refused with "no typed CompareRequest field applies this key".
