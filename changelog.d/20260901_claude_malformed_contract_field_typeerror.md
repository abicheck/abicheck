### Fixed

- **A malformed `contract.profile_fields`/`scope_fields` in a JSON snapshot
  could crash `compare`/`compat check` with a raw, unhandled `TypeError`**
  instead of a clean error message. A prior fix made
  `extraction_contract_from_dict` reject (rather than silently coerce) a
  wrong-shaped `profile_fields`/`scope_fields` value, raising `TypeError` —
  but the two CLI boundaries that load a JSON snapshot only caught
  `ValueError`/`KeyError`/`UnicodeDecodeError`/`OSError` around it, so the
  new `TypeError` escaped uncaught. Both `compare`'s snapshot loader
  (`workflows/input_resolution.py`) and `compat check`'s descriptor loader
  (`compat/cli.py`) now catch `TypeError` alongside the others, producing
  the same clean `SnapshotError`/classified compat-mode exit every other
  malformed-snapshot failure already gets.
