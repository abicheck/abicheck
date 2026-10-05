### Fixed

- **One finding per undeclared export that appeared or disappeared.** An
  export no public header declares was reported twice when NEW gained it —
  `func_added_elf_only` and an `INTRODUCED` `exported_not_public` — and twice
  when OLD lost it (`*_removed_elf_only` plus a `RESOLVED`
  `exported_not_public`). The existence finding is kept (it carries the
  addition count and the MINOR bump); the hygiene finding moves to the
  redundant list with `caused_by_type: "export_existence:<kind>:<symbol>"`,
  so it is still recorded and still scores exactly as before. A `RESOLVED`
  hygiene finding now never scores the verdict from the redundant list either.
- **`*_elf_only` wording now agrees with `exported_not_public`.** Both read
  one per-export accounting decision
  (`buildsource.export_account_decision.account_export`), including the
  header-text evidence the existence detector could not see: an
  instantiation of a `template <...>` the public headers declare (oneCCL's
  `create_communicators`) now reads "instantiation of public template …"
  instead of "not declared in any public header"; a declaration inside an
  `#ifdef` region or an excluded header, a C++ ABI artifact, an
  external-dependency leak and an allocator interposer each say so. A removed
  export no public header promised now states whom it can break (a consumer
  that bound it outside the public headers — `dlsym()`, a hand-written
  prototype).
- **`func_added`/`var_added` no longer call every unexported addition
  "public header-only".** The description now names non-public access
  (`private member`, `protected member`) and says "header-only" only for a
  shape that is (inline, or internal linkage); a pure virtual or deleted
  function is named as such, and an out-of-line declaration with no export
  reads "declared without an exported symbol".
- **`--config` no longer makes two side-by-side install trees "different
  ownership rules".** Each side's own `-H`/`public_header_dirs` roots are now
  recorded relative to that side's anchor whether or not a project config is
  in play; only roots the config itself states stay relative to the config's
  directory. Previously a config stating nothing but `compile.std` turned
  `-H old=inst-1/include -H new=inst-2/include` into two rules (a coverage
  warning, or a refusal under ownership narrowing). Snapshots recorded under
  a config with operand roots fingerprint differently once re-dumped.
- **A warm run reports progress.** A snapshot-cache hit skips every
  extraction phase, so a redirected log of a warm run had no `abicheck:` line
  at all; the hit now writes one (`<binary>: snapshot cache hit, extraction
  skipped`).

### Performance

- **`compare` no longer re-reads both binaries for the L0 hard-removal
  probe.** When both snapshots were identity-checked against their binaries,
  each side's symbols-only view is built from the ELF table it already
  carries (1.1 s, 13% of a oneCCL comparison, for no finding).
