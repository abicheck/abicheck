### Added

- **`scan --against` now honors the severity gate** — `--severity-preset`,
  the per-category `--severity-*` overrides, and `--exit-code-scheme`
  (plus `.abicheck.yml`'s `severity:`/`exit_code_scheme` keys) now feed
  `scan --against`'s exit code exactly as they already do for `compare`:
  under the resolved `severity` scheme, the exit code is the worst
  error-level category among the baseline comparison's findings rather
  than a fixed mapping from the overall verdict, so e.g.
  `--severity-preset info-only` can leave a `BREAKING` verdict at exit `0`.
  Previously `scan --against` always used the legacy verdict→`{0,2,4}`
  mapping and silently ignored any severity setting. Under the `severity`
  scheme the report also states the gate that produced the exit code —
  a `severity` object in the JSON `diff` block
  (`config`/`categories`/`exit_code`/`blocking`/`blocking_categories`, the
  same shape and the same builder `compare`'s report uses;
  `scan_schema_version` 1.9) and a matching `severity gate: exit N —
  blocking: <categories>` line in the default text output — so a non-zero
  exit on an otherwise compatible diff names its own cause instead of being
  indistinguishable from the orthogonal contract-coverage exit `1`. Both
  are absent under the default legacy scheme, which runs no severity gate.
  The block is the scan's compatibility gate rather than the baseline
  diff's alone — a `--crosscheck KEY=error` promotion raises it too, adding
  a `promoted_crosscheck` blocking category (a floor: it never lowers the
  gate). `aggregate` reads that nested block as the target's compatibility
  gate when present (through the same fail-closed validator a `compare`
  gate goes through), which is what keeps the compatibility and
  contract-coverage axes separable now that a scan can natively exit `1`.
  `--pack` gate-severity folding is not yet extended to `scan`; pass
  severity settings directly. See `docs/reference/exit-codes.md`'s new
  "`scan --against` and severity" section.

