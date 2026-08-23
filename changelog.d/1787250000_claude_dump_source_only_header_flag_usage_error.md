### Fixed

- **`dump --sources TREE -H hdr` (no binary) silently ignored `-H`/`--header`
  for the *written snapshot* — it embeds only L3/L4/L5 build/source facts,
  never an L2 header-AST pass, so the resulting snapshot has an empty
  (0 functions/enums), `depth="binary"` shape with no trace the flag had no
  effect there. A CLI warning now names the ignored flag on the real run
  (`dump`'s own `--dry-run` preview is unaffected — it genuinely resolves
  and reports on the given headers via the typed `DumpRequest` pipeline, so
  it was already correct). An initial fix rejected the combination outright
  as a usage error; that was reverted after it broke a wide, pre-existing
  test suite that legitimately combines `-H` with a source-only
  `--dry-run` — `-H` is not dead code for this invocation shape in general,
  only for what the non-dry-run path actually writes.
