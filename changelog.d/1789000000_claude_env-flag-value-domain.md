### Fixed

- **Every `ABICHECK_*` boolean environment variable now reads one value
  domain.** `1`/`true`/`yes`/`on` turn a knob on and `0`/`false`/`no`/`off`
  turn it off (case- and whitespace-insensitive) whichever way the knob's own
  default points, and an unset, empty or unrecognized value resolves to that
  default rather than its opposite. Five hand-rolled parsers with four
  different token sets are replaced by one registered reader
  (`abicheck/env_flags.py`). The defect this closes: `ABICHECK_CC_DISABLE`
  was read as "any non-empty value", so `ABICHECK_CC_DISABLE=0` silently
  turned `abicheck-cc`'s source-fact capture **off**; it now leaves capture
  on. `ABICHECK_COLLECT_COMDAT` also accepts `on` and
  `ABICHECK_PARALLEL_EXTRACTION` also accepts `off`, which neither did.
