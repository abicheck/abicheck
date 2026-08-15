<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Performance

- **`dump`/`compare` no longer collect the same L3 build evidence twice per
  side.** `resolve_side_snapshot` now collects L3 `CompileUnit` evidence once
  via `buildsource.l2_seed.collect_l2_seed_evidence()` and shares it between
  L2 include-dir seeding and L2 compile-context derivation, instead of each
  independently re-resolving the same `--sources`/`--build-info` compile
  database. No behavior change — `derive_l2_include_dirs`/
  `derive_l2_compile_context` gained an additive `evidence=` parameter with a
  fully backward-compatible default, so every existing caller is unaffected.
</content>
