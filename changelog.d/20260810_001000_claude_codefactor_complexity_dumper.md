### Changed

- **The heaviest dumper-side functions were restructured to cut cyclomatic
  complexity** — `dumper_scoping._directly_referenced_dependency_names` (the
  dependency-header direct-reference filter), `build_context._expand_response_files`
  (GNU `@response-file` inlining), and `dumper.py`'s own `_header_ast_parser`
  and `_castxml_dump` were each split into named helpers, one per phase.
  `_expand_response_files` keeps its exact signature, budgets and cache
  semantics; the scoping filter additionally folds two owner maps that were
  built in separate passes from identical data into one. No behaviour, output,
  or public signature changes.
- `dumper_ast_config_cpp20_chains`'s two preprocessor-chain classifiers
  (`_frame_for_chain_open`, `_line_for_open_arm`) are now table-driven: each
  arm of their long, order-sensitive `if`-chains became a named predicate
  carrying its own review-derived rationale as a docstring, consulted through
  an ordered rule tuple. The load-bearing ordering (every inverted-polarity
  spelling tested ahead of the general guard rule that would otherwise also
  match it) is now stated by the table rather than implied by statement order.
  Verified behaviour-identical against the previous implementation across
  every (directive spelling × policy flags × stack frame) combination.
- Relocated `_build_clang_header_command` from `dumper.py` to
  `dumper_ast_config.py` (next to its castxml counterpart, whose flag handling
  it deliberately mirrors) and the ELF export-symbol readers
  (`_HIDDEN_VIS`, `_is_abi_relevant_symbol`, `_pyelftools_exported_symbols`)
  to `dumper_elf_symbols.py`, alongside the visibility helpers already living
  there. Both are pure relocations with re-export shims: `dumper.py` sits at
  the AI-readiness 2000-line hard cap, so splitting its two most complex
  functions in place needed line budget first. Every caller stays in
  `dumper`, so bare-name calls still resolve through `dumper`'s namespace and
  existing `monkeypatch.setattr(dumper, ...)` targets keep working.
