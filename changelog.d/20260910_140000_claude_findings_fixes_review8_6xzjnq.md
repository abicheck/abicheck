### Fixed

- **Public-surface scoping's vtable/RTTI owner resolution over-scoped a
  round-1 host-independence fix, silently demoting the wrong things (or
  nothing at all) for an Itanium special-name shape outside the structural
  parser's subset.** A `_ZTV`/`_ZTI`/`_ZTT` owner's structural parsers
  return `None` for two genuinely distinct reasons: the shape is templated
  or namespace-qualified (which the parser *can* handle, and must resolve
  identically regardless of whether an external demangler happens to be
  installed) and a shape the parser flatly cannot handle at all (a standard
  `Ss`/`Sa`/... substitution, or a local-class owner). The round-1 fix
  removed the `demangle()` fallback for *both* cases, when the
  host-independence invariant only ever applied to the first — for the
  second, a demangler-equipped host now silently under-classified a known
  private owner as "unknown, keep" instead of correctly resolving and
  demoting it, the same conservative-but-imprecise regression the rest of
  the codebase's `demangle()`-fallback pattern (e.g.
  `FUNC_REMOVED_ELF_ONLY`'s `demangled_symbol`) already avoids elsewhere.
  Restored the fallback for exactly the `owner_scope is None` branch, with
  new regression tests for both directions (`_ZTVSs`/`_ZTVZ3foovE1A`
  resolve and demote with a demangler present, conservatively keep without
  one) and an explicit test pinning that this is *not* a regression of the
  round-1 invariant, which stays scoped to shapes the structural parser can
  parse.
- **A selected policy pack's provenance could mislabel a project-config
  (`.abicheck.yml`) `policy.overrides` kind as pack-sourced.** When a
  pack overrode one `ChangeKind` and the project config overrode a
  *different* one, `pack_application()` derived "pack-contributed" by
  subtracting only the explicit `--policy <file>`'s own kinds from the
  fully merged `overrides` mapping — which also contains the project's own
  contribution. Scoring stayed correct either way (both are real
  overrides), but `PackApplication.policy_overrides`, `is_empty()`, and the
  pack-manifest receipt provenance could all record the project-sourced
  kind as pack-sourced. Fixed by having the canonical resolver
  (`compatibility_evaluation_frontend.py`) capture the genuinely
  pack-contributed subset once, before the project-config fold, on a new
  `CompatibilityPolicyConfig.pack_overrides` field — `pack_application()`
  now reads it directly instead of re-deriving an approximation from the
  merged result.

### Documentation

- Corrected several places (`docs/reference/config-file.md` and matching
  code comments/docstrings) that wrongly called `scan`'s `--crosscheck
  KEY=LEVEL` flag "retired" while describing `.abicheck.yml`'s
  `policy.overrides` block — the flag is still live and parsed; the two are
  distinct, coexisting mechanisms.
- Corrected the `compare_report.schema.json` description of
  `compatible_additions`'s pre-4.0 meaning (both the packaged schema and
  its `docs/reference/schemas/v1/` copy): the old field already included
  `quality_issues`, so stating `compatible_additions + quality_issues
  equals that old total` double-counted them. Now states that a consumer
  needing the old genuine-growth count must *subtract* `quality_issues`.
