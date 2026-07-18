<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `attach_clang_layout` only ran for a snapshot whose L2 backend was exactly
  `"clang"`, so `abicheck dump` on an ELF with `--ast-frontend hybrid` never
  got layout enrichment for its clang-only records: that path goes through
  `dumper.dump()`'s own `run_hybrid_dump` recursion, which never attaches
  layout facts to either sub-dump (doing so there would need importing this
  module from `dumper_hybrid.py`, closing a real cycle back through
  `dumper.py`). Now also runs for `"hybrid"` snapshots — safe because it only
  ever backfills a currently-empty layout field, so a hybrid snapshot's
  already-real castxml-sourced facts are left untouched and only the
  clang-only records the merge appended get enriched.
- The whole-snapshot disk cache's `_SNAPSHOT_CACHE_VERSION` wasn't bumped for
  this PR's castxml `CvQualifiedType` volatile-pointer-value spelling fix — an
  unconditional change to the *default* (`--ast-frontend auto`/`castxml`)
  cacheable dump path's output for the same cache-key inputs. A pre-existing
  `~/.cache/abi_check/snapshots` entry could otherwise still be served with
  the old (wrong) spelling instead of a fresh dump picking up the fix. Bumped
  1 → 2.
