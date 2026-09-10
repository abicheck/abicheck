### Fixed

- **A directory/package release run's own per-library receipt still missed
  a project-backed `.abicheck.yml` `policy.overrides` when no `--pack` was
  selected.** `resolve_release_pack_application(_from_ctx)` returned a bare
  `None` whenever no `--pack` was given, so `record_release_resolved_
  config` had nothing to stamp onto each library's own `DiffResult.
  evaluation_config` even when a project override genuinely changed that
  library's verdict. Fixed generally: both resolvers now still resolve a
  real, receipt-shaped `CompatibilityEvaluationConfig` with no pack
  selected too, returning an *inert* `PackApplication` (`is_empty()` still
  `True`, so every pre-existing no-`--pack` behavior is unaffected) instead
  of `None`.
- **`scan --against` could crash with an uncaught exception instead of a
  clean usage error on a malformed auto-discovered project-config
  override.** A `.abicheck.yml` with a well-shaped but invalid override
  (e.g. an unrecognized `ChangeKind` slug) raised `PolicyError` uncaught
  for a plain `scan ARTIFACT --against BASELINE` invocation (no `--contract`
  or `--pack`), exiting 1 instead of the clean exit-64 `BadParameter` both
  `compare` operand shapes already produce for the identical input. Fixed
  by catching and translating it the same way every other malformed-policy
  path in this codebase already does.
- **The canonical `St` Itanium substitution for `std::` dropped the
  qualified owner candidate during public-surface classification.**
  `itanium_special_name_owner_identifiers` advanced past the `St`
  substitution code without adding `"std"` to the owner's own scope-path
  components, so `_ZTVSt6vectorIiE` (`std::vector<int>`) produced only
  `{"vector"}` -- never `{"std::vector", "vector"}`, the pair the
  fully-spelled `_ZTVN3std6vectorIiEE` equivalent already produced. A
  snapshot modeling an internal `std::vector` record but no bare `vector`
  type read the substituted owner as unresolvable and kept its vtable
  churn in-surface instead of demoting it, purely depending on which of
  two equivalent mangled spellings a compiler happened to emit. Fixed by
  appending `"std"` to the parsed scope-path components for this shape too,
  matching the identical fix already applied to the sibling
  `itanium_scope_components_with_template_positions` parser.
- **The packaged JSON Schema still described `summary.compatible_additions`'s
  meaning-change as effective "as of 3.15"**, contradicting the real
  version history after that bump was corrected to the MAJOR `4.0` (see
  the third review round's own fix) -- a schema-driven consumer could read
  the description and wrongly conclude a 3.x report it accepted already
  carried the new, narrower semantics. Updated both field descriptions
  (`compatible_additions`/`quality_issues`) to place the transition at
  `4.0`, and republished the `docs/reference/schemas/v1` mirror to match.
