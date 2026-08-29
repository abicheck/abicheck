### Fixed

- **ADR-063 Phase 1: the legacy compile-database match's derived flags no
  longer risk corruption from an embedded space** — `dump`'s typed
  pipeline threads the `-p`/`--compile-db` legacy auto-match's
  already-split castxml flags (e.g. `("-I", "/opt/SDK Files/include")`)
  through `CompileContext`. The merge used to `" ".join()` those tokens
  into the free-form `gcc_options` string, which every consumer later
  re-splits — silently corrupting a token with embedded whitespace (a
  Windows SDK include path with a space, or a compile-db `-DNAME=a b`
  define) into the wrong number of tokens. Fixed by routing the tokens
  through `CompileContext.gcc_option_tokens` instead, a field that carries
  verbatim argv entries and is never re-parsed, with the exact same
  precedence preserved (an explicit, caller-supplied compile context still
  wins over the legacy match for a conflicting flag). The same
  `" ".join()` pattern is pre-existing, shared debt in the real CLI's own
  `_merge_gcc_options` legacy-match path — not fixed in this change; see
  `docs/contribute/known-gaps.md`'s "ADR-063 Phase 1" entry for the
  precise scope and the recorded follow-up.
