### Added

- **A removed symbol in a static archive can now be localized to the exact
  object member that defined it.** `archive_member`/`ARCHIVE_CONTAINS_OBJECT`/
  `OBJECT_DEFINES_SYMBOL` — registered in the L5 graph schema since ADR-041
  P1 #2 but left schema-only ("no archive/`nm`-equivalent introspection
  extractor") — are now populated by a real `ar`-index introspection pass
  (`abicheck.buildsource.archive_graph`). It reads an archive's own
  linker-written symbol index (GNU `/`/`/SYM64/` and BSD/Mach-O
  `__.SYMDEF`/`__.SYMDEF_64`, plain and thin-archive flavors alike) over
  every `static_library` node a `dump --sources`/`--build-info` run's
  `BuildEvidence.link_units` already produced, so a proof path can name
  "`cache_dispatch.o` in `libinternal_dispatch.a`" instead of only the
  owning target. Needs no compiler — it runs whenever an archive link input
  is present, independent of clang availability — and only ever joins a
  discovered symbol onto a `binary_symbol` node the graph already carries,
  so an archive's internal-only indexed symbols mint no new node. Degrades
  to a diagnostic (`degraded_passes["archive_graph"]`), never an exception,
  for a missing/unreadable/malformed archive or one built without a symbol
  index (`ar rc` with no `s`). This closes the last of G29 Phase 5's five
  named open graph families (object/archive link provenance); the other
  four (template instantiation, virtual dispatch, macro/config,
  callback/function-pointer) remain open. No new `ChangeKind`, no report
  schema change, no verdict/exit-code effect.

  Hardened against four real-world parsing/resolution gaps found in review
  (real macOS CI evidence for two of them): a standard COFF `.lib`'s second
  linker member (a little-endian layout distinct from its first, GNU-
  compatible one, both sharing the name `/`) is now recognized and skipped
  instead of mis-parsed as GNU format; a real macOS `ar`/`ranlib` writes its
  `__.SYMDEF SORTED` index member's name via the BSD `#1/<len>`
  self-referential extended-name form rather than the raw header field,
  which is now resolved before classification instead of silently folding
  the index into `archive_member` nodes as a fake object file; a redacted
  (`~`-prefixed) archive label or search root (ADR-032 D7) is now expanded
  before disk lookup; and a relative archive label that resolves to more
  than one distinct file across the search roots (two subprojects each
  linking their own same-named archive) is now a diagnostic, never a
  guessed match.

  Three more fixes from a second review round: the symbol-count sanity
  check on a GNU `/`/`/SYM64/` index no longer reuses the member-count cap —
  a real C++ archive can legitimately index far more global symbols
  (templates, inline functions) than it has object-file members, and the
  reused cap discarded a large legitimate archive's provenance outright; a
  symbol-index member whose body is too short to even hold its own count
  field now raises instead of silently returning zero symbols (which read
  identically to "confirmed, genuinely empty" downstream); and an
  "ambiguous archive" diagnostic's candidate paths are re-redacted before
  being embedded in `ExtractorRecord.detail`, since the un-redacted absolute
  path is otherwise persisted straight into build evidence.

  A fourth review round (a live Codex webhook comment) found that the BSD
  `__.SYMDEF` string table's own declared byte-length field — documented in
  this function's docstring as part of the format — was never actually read:
  every byte remaining in the member body after the ranlib entry table was
  taken as the whole string table unconditionally, so a member truncated
  right after the entry table would still parse "successfully" (as zero
  symbols, reading identically to "confirmed, genuinely empty") instead of
  raising. The string-table length is now read and validated, and the string
  table is bounded by it rather than by whatever bytes happen to follow.

  A third review round found three more real gaps: a GNU index whose offset
  array is complete but whose trailing name table has fewer than the
  declared count of NUL-terminated names now raises instead of silently
  returning however many names `str.split` happened to find; a Mach-O/BSD
  archive's own ranlib index keeps the platform's C-ABI leading underscore
  (`_foo`), while `macho_metadata.py` strips exactly one before a
  `binary_symbol` node is minted, so the exact-name join now falls back to
  the stripped form (exact match still tried first) instead of missing
  every Mach-O archive symbol; and an "unreadable archive" `OSError`
  diagnostic (permissions, a TOCTOU race) is now re-redacted the same way
  the ambiguous-archive one already was, since `OSError.__str__` typically
  embeds the real, resolved filename. Also fixes two real-toolchain
  integration tests that only surfaced on non-Linux CI runners: a redacted-
  home-placeholder test now sets both `HOME` and `USERPROFILE` (Windows'
  `os.path.expanduser` doesn't read `HOME`), and the real-`ar` round-trip
  tests account for Darwin's own leading-underscore symbol convention in
  their expected symbol sets (the archive's raw index correctly carries the
  underscore on that platform; only the test's fixed expectation needed to
  know that).
