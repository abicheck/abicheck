### Removed

- **`--ast-frontend`, `--sysroot`, and `--nostdinc`/`--no-nostdinc` are gone
  from `compare`, `dump`, and `scan`** — demoted to `.abicheck.yml`'s
  `compile:` block (`compile.frontend`, `compile.sysroot`,
  `compile.nostdinc`; Phase 7b of
  `docs/contribute/plans/one-comparison-product.md`). All three commands
  share one L2 compile context (ADR-037 D8.1), so removing the flag from the
  shared decorator retires it everywhere at once, including the per-side
  `--ast-frontend old=`/`new=` override. The old spellings now exit `64`
  (`No such option`), with no hidden alias. `--compiler`,
  `--compiler-prefix`, `--compiler-option`, `--frontend-context`,
  `--allow-ast-frontend-fallback`, and `--allow-unsupported-castxml` are
  **not** demoted — none has a working `compile:` config-parsing path today,
  and the cross-compile trio is documented as CLI-only per ADR-037 D4, a
  decision this change does not revisit. `--lang` is **not** demoted either:
  its explicit-vs-auto-detected disambiguation is a known, currently-fragile
  gap (see `dump_cmd`'s "G31 Phase C follow-up" comment), so forcing it to
  config-only now would regress correctness rather than just relocate a
  setting.
