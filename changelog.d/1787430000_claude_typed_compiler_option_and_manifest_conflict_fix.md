### Fixed

- **The typed `DumpRequest`/`CompareRequest` API never folded an
  `InputSpec.compile.gcc_option_tokens` include-search operand (the typed
  equivalent of `--compiler-option -I<dir>`) into `public_include_search_
  dirs`.** A directory reached only through such an operand stayed
  `PRIVATE_HEADER` even though the caller had named it explicitly, just not
  via `InputSpec.includes`. Fixed by folding `side.compile`'s (the
  pre-fold, caller-supplied `CompileContext`) include-search operands into
  the same shared `resolve_side_snapshot` primitive `compare`'s
  implicit-dump operand and `dump`'s typed API both already use.
- **Fixed a regression the CLI's own `--compiler-option`-folding fix
  introduced: `dump --dump-manifest ... --compiler-option -I<dir>` (a
  previously-working combination) started failing as a usage error.**
  `--compiler-option` is a *global* flag applied to every translation unit
  regardless of the manifest, but folding its include-search operands into
  `public_include_search_dirs` unconditionally collided with `dump()`'s own
  manifest mutual-exclusivity check (added in this same review round). Both
  the CLI (`perform_elf_dump`) and the typed-API primitive
  (`resolve_side_snapshot`) now suppress `public_include_search_dirs`
  entirely for a manifest dump — matching the manifest's own
  translation-unit-scoped provenance — while `gcc_option_tokens` still
  reaches every TU's parse unaffected.
