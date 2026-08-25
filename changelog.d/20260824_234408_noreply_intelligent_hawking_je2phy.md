<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Namespace-move roll-up now recognizes header-tier (non-mangled) symbol
  keys.** `find_namespace_move_groups` only parsed scope components from
  mangled Itanium/MSVC symbols, so a real namespace move reported through a
  castxml-synthesized constructor/destructor key (`__abicheck_ctor__...`,
  `~Qualified::Name`) or a plain qualified display name never joined the
  batch roll-up — leaving most of a real move's findings unpaired. A new
  qualified-name fallback (`diff_cxx_rules.qualified_name_scope_components`)
  closes the gap generically for any such key.
- **A lambda-closure-parameterized function-level finding is now demoted
  when confirmed never exported on either side.** A `func_removed`/
  `func_params_changed`/`template_param_type_changed`/
  `template_return_type_changed` finding whose subject is a template
  instantiated over a local lambda closure type — spurious churn from an
  unrelated source-line shift — is now demoted via the existing
  `effective_verdict`/`modulation_reason` hook (ADR-025) when the reported
  symbol is confirmed absent from both binaries' real exported symbol
  table, mirroring `diff_versioning.demote_internal_version_node_findings`.
  A genuinely-exported symbol, or a castxml-synthesized ctor/dtor key
  (never a real export by construction), is left untouched.
- **Cross-tier enum findings now dedupe correctly.** The L2 header-tier
  enum detector (`diff_types._diff_enums`, bare `EnumType.name`) and the L1
  DWARF-tier detector (`diff_platform._diff_enum_layouts`, fully-qualified
  DWARF key) could both report the identical `ENUM_MEMBER_REMOVED`/
  `ENUM_MEMBER_VALUE_CHANGED`/`ENUM_LAST_MEMBER_VALUE_CHANGED`/
  `ENUM_UNDERLYING_SIZE_CHANGED` change with two different `canonical_finding_id`
  values, so cross-detector dedup never recognized them as one finding. Fixed
  with a bare/qualified name bridge in `diff_filtering` plus adding the four
  enum kinds to `_deduplicate_cross_detector`'s own dedup-category table,
  which had never attempted identity resolution for them at all.
</content>
