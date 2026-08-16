### Fixed

- **`abicheck dump` now folds real L3 build context into its own L2 header
  parse, on both the ELF and PE/Mach-O paths.** A `dump --sources ...
  --build-info compile_commands.json` snapshot's `parsed_with_build_context`
  read `false` and `language_standard` read `""` even though real build
  evidence was supplied and embedded in the snapshot — the evidence was
  collected and stored, but never routed to the header-AST invocation,
  because `dump`'s CLI path never called the same L3→L2 fold `compare`'s
  implicit-dump operand and `scan --against` already apply via
  `resolve_side_snapshot`. Concretely, a `dump`-produced baseline and a
  `scan --against`/`compare` candidate of the *same* project, given the
  *same* build evidence, resolved to genuinely different `CompileContext`s
  (a `profile_fingerprint` mismatch on `include_sequence`/
  `language_standard`), so `scan`/`compare` correctly refused the comparison
  as `NOT_COMPARABLE` — not because evidence was missing, but because the
  two commands extracted under non-comparable recipes for reasons neither
  command's own diagnostics named. Fixed with a new, shared
  `buildsource.l2_seed.fold_l3_compile_context()`, called from both
  `perform_elf_dump` (ELF) and `handle_non_elf_dump` (PE/Mach-O, which
  shared the identical gap) — folding the real L3 `CompileUnit`-derived
  context (`-std=`, ABI-relevant `-D`/`-U`, target, sysroot) into the same
  explicit context CLI flags/`.abicheck.yml` already resolved, the exact
  fold `resolve_side_snapshot` already applies elsewhere. `dump`'s two
  independent second-pass clang re-parses (the header-graph attach and the
  clang-layout-tool attach) now receive this same fully-merged context too,
  closing a narrower sibling gap where their own re-derivation never looked
  at `gcc_option_tokens`/`sysroot`/`nostdinc`/deferred include roots at all.
  `AbiSnapshot.parsed_with_build_context` is now stamped from either this
  fold or the older `-p`/`--compile-db` mechanism.
