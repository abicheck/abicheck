<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The `check-target` assurance overlay no longer treats a checkout-side
  `compile.sysroot: ""`/`compile.compiler: ""` as an explicit value that
  blocks a sources-root `compile.sysroot`/`compile.compiler` setting.**
  `_merge_compile_block`'s per-field `compile:` merge (now
  `action_config_overlay_compile.merge_compile_block`) previously decided
  "checkout already set this key" by raw key presence, which is right for
  an explicitly non-empty value but wrong for an empty-string
  `sysroot`/`compiler`: the real
  `cli_options.merge_compile_config` gates both on the checkout-side
  value's truthiness (`if bc.compile_sysroot`/`if ... bc.compile_compiler`),
  not `is not None`, so an empty-string checkout value is treated exactly
  like an absent key there. A checkout config validly spelling
  `compile.sysroot: ""` with a distinct sources-root config setting a real
  path now correctly resolves to the sources-root's value when
  `analysis-assurance-complete` enables the overlay's per-field `compile:`
  merge, instead of silently pinning "no sysroot" and changing header
  resolution, the extracted API surface, and findings.
