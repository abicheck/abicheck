### Removed

- **`--exit-code-scheme` is gone, along with every manual override of the
  gate algorithm it controlled (ADR-064, CLI cleanup phase two PR G2).**
  There is no `auto`/`legacy`/`severity` selector any more, on any surface:
  the `--exit-code-scheme` CLI flag (`compare`, `scan --against`),
  `.abicheck.yml`'s top-level `exit_code_scheme:` key, the `kind: gate` pack
  manifest schema's `gate.exit_code_scheme` assignable field, and
  `CompareRequest`/`ScanRequest`'s typed-API `exit_code_scheme` fields are
  all deleted outright. The gate algorithm is now **fully automatic**: with
  no severity setting in effect from any source (`--severity-preset`, a
  `.abicheck.yml` `severity:` block, a run profile, or a `kind: gate` pack),
  the plain compatibility verdict decides `0/2/4`; with one in effect, the
  resolved severity gate decides `0/1/2/4` — exactly what `auto` already did,
  now with no way to override it. A stale `.abicheck.yml` naming
  `exit_code_scheme:` now fails fast at parse time (an unknown top-level key
  is a hard error), and `--exit-code-scheme`/`kind: gate` packs asserting
  `gate.exit_code_scheme` are rejected the same way any other unassignable
  field is. The purely-derived, already-resolved report fields of the same
  name are unaffected and unrenamed — `gate.exit_code_scheme` in JSON
  reports, `effective_config_fields["gate.exit_code_scheme"]`, and the
  unrelated `scoped_exit_code_scheme` result field all keep working exactly
  as before; only the *settable* surface is gone. The `--profile ci-gate`
  bundle, which used to pin `exit_code_scheme: severity`, now states
  `severity_preset: default` instead — behavior-preserving, since stating a
  severity preset is what makes the scheme resolve to `severity` under the
  new rule anyway. See ADR-064 and
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s "PR 4/PR G2" section for
  the full design rationale and file-by-file account.
