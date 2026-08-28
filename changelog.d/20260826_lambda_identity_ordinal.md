### Fixed

- **Lambda-closure identity no longer embeds its source `:line:col`**: a
  castxml/clang closure-parameterized type/function (e.g.
  `raii_guard<(lambda at task_group.h:522:26)>`) previously compared as
  removed-plus-added whenever an *unrelated* edit earlier in the same header
  shifted the lambda to a new line -- reported against real oneTBB
  2021.13.0 -> 2022.3.0 binaries as a spurious `type_removed`/`type_added`
  pair, a paired `func_removed`/`func_added` on every ctor/dtor/method of
  the instantiation, and a `declaration_renamed` RISK finding whose entire
  content was the line-number text.
  `qualified_name_segments.renumber_anonymous_closure_identities()` now
  replaces the `:<line>:<col>` discriminator with a stable ordinal -- "the
  Nth lambda of this marker kind declared in this header" -- computed once
  per snapshot, mirroring GCC/DWARF's own per-scope `{lambda(...)#1}`
  numbering. As long as an edit doesn't reorder or add/remove same-header,
  same-kind lambdas relative to each other, both sides of a comparison now
  assign the identical ordinal to the identical closure, eliminating all
  three noise classes for that case. Snapshots loaded from disk are
  renumbered too (a no-op once already renumbered), so a baseline
  persisted before this fix compares correctly against a freshly-dumped
  snapshot instead of disagreeing on identity purely from the encoding
  change. A hybrid
  (`--ast-frontend hybrid`) snapshot's `fact_provenance` dict is renumbered
  alongside the ABI-surface fields too, since its keys embed the same
  closure-parameterized type-name spelling -- left un-renumbered, a
  provenance-gated detector would silently miss on every closure-
  parameterized declaration after this fix renamed the type it was keyed
  against.
- **Follow-up (Codex review, fresh evidence): `service.run_dump`'s own
  hybrid recursion -- the real Tier-2 entry point the CLI routes
  `--ast-frontend hybrid` through -- reproduced the identical
  per-leg-independent-ordinal bug this fix closed for
  `dumper_hybrid.run_hybrid_dump`.** It has its own separate castxml/
  clang recursion and merge, and was never wired to defer/re-renumber
  after the merge. Now wrapped in the same
  `qualified_name_segments.defer_closure_identity_renumbering()` context
  during both recursive dumps, with the merged snapshot renumbered
  exactly once afterward.
- **Second follow-up (Codex review): a header basename containing parens
  (`foo(test).hpp`) or a colon defeated the ordinal regex's basename
  capture, silently leaving such closures un-renumbered.** The basename
  group now accepts one level of balanced parens via an explicit
  alternation rather than a bare `[^:()]+` exclusion, while still
  refusing a bare, unparenthesized `:`/`)` -- which is also what keeps a
  match from ever bleeding across two separate markers in the same type
  name. A bare (non-parenthesized) colon in a basename, legal on POSIX,
  remains an accepted, documented limitation -- the same shape as the
  pre-existing same-basename-different-file limitation.
- **Third follow-up (Codex review): a free-text field could be
  corrupted if it happened to contain text matching the closure marker
  syntax.** The recursive dataclass walk that collects/rewrites closure
  markers previously reached every string field of every declaration
  dataclass, including `deprecated` (a human-written message) and
  `default` (a verbatim, unevaluated expression) -- so a deprecation
  message that happened to quote something shaped like
  `(lambda:x.h:10:2)` was silently rewritten to `(lambda:x.h#1)`, and
  could even consume an ordinal slot a real closure should have gotten.
  These two field names are now excluded from both collection and
  rewriting, by name, across every dataclass that has them.
- **Fourth follow-up (Codex review): a basename with two or more levels of
  nested parens (`foo(a(b)).hpp`) still fell through to no match at all.**
  A single regex alternation can only ever balance one level of nesting.
  The basename is now scanned manually (`_scan_anon_type_marker`), tracking
  real paren depth so any number of nested groups balance correctly; the
  fixed marker prefix (`(lambda:`/`(unnamed <kind>:`) stays a regex, and
  the first depth-0 `)` always ends the marker, matching a real compiler's
  own basename spelling (never unbalanced).
- **Fifth follow-up (Codex review): two more payload locations could be
  corrupted, beyond `deprecated`/`default`.** `Variable.value` (its
  compile-time constant initializer) is the identical free-text payload
  shape and is now excluded the same way. `AbiSnapshot.constants` (a
  `#define`/`constexpr` name -> value string dict) is payload too, but
  can't be excluded by field name alone -- the generic dict walk treats
  every dict's keys/values uniformly, and `fact_provenance` (a genuinely
  identity-bearing dict) needs that same walk to keep working. Removed
  `constants` from the set of fields renumbering ever reaches at all,
  since a constant's literal value can never legitimately be a
  closure-parameterized type spelling.
- **Sixth follow-up (Codex review): a basename with an *unmatched* closing
  paren (`foo)bar.hpp`, legal on POSIX) still fell through to no match.**
  The manual scanner treated the first depth-0 `)` as the marker's
  terminator unconditionally; a depth-0 `)` is now only accepted once the
  text immediately before it ends in `:<digits>:<digits>`, and otherwise
  treated as ordinary basename text so scanning continues to the real
  terminator.
- **Seventh follow-up (Codex review): a header basename that is itself a
  complete, marker-shaped substring (`(lambda:a.h:1:2).hpp`, a real if
  unusual filename) produced two overlapping matches instead of one.**
  `_anon_type_ordinal_matches()` scans for every marker *prefix* first and
  then balances parens from each one independently, so an outer marker
  containing a nested `"(lambda:"`-shaped basename let both the outer scan
  and the inner prefix match succeed, and `apply_anonymous_type_ordinals`'s
  splice-based rewrite then corrupted the string by rewriting both
  overlapping ranges. The scan now skips any prefix match that falls
  inside an already-accepted outer match's span, so nested marker-shaped
  text is left untouched as part of the outer marker's own basename.
- **Eighth follow-up (Codex review): a basename with an *unmatched
  opening* paren (`foo(bar.hpp`, legal on POSIX) still fell through to
  no match at all -- the mirror image of the unmatched-closing-paren case
  above.** There, depth never returns to 0 by the time the marker's real
  closing paren is reached, so the depth-tracking scan alone reaches the
  end of the string without ever finding a depth-0 terminator. The scanner
  now falls back to a second, depth-blind pass whenever the first one
  finds nothing: it looks for the first `)` whose immediately preceding
  text ends in `:<digits>:<digits>`, treating every paren in between as
  ordinary basename text -- correct precisely because depth tracking
  already had its chance and failed, meaning the string's parens don't
  balance within this marker to begin with.
- **Ninth follow-up (Codex review): a basename with coordinate-shaped text
  of its own before the real terminator (`foo:1:2)bar.hpp:10:2)`)
  corrupted the marker instead of assigning an ordinal to the real,
  trailing coordinates.** The depth-tracking scan returned on the FIRST
  depth-0 `)` whose preceding text matched `:<digits>:<digits>`, which for
  this basename shape is `foo:1:2)` -- not the true end of the marker. It
  now scans to the end of the string and prefers the LAST such candidate.
  This can never run past a genuinely separate, later marker: any such
  marker's own prefix always starts with `(`, which bumps depth before its
  own closing paren is reached, keeping it ineligible as a candidate for
  the current scan.
- **Tenth follow-up (Codex review): `service.run_dump`'s ELF/PE/Mach-O
  tails renumbered too early relative to `attach_clang_layout`.** That
  function runs the G28 layout tool AFTER `_dump_elf`/`_dump_pe`/
  `_dump_macho`'s own `dumper.dump()` call already renumbered the
  snapshot's closure markers to `#N` form -- but the tool independently
  derives a base class's name straight from clang's own (still
  `:line:col`-form) spelling and inserts it into `RecordType.base_offsets`,
  so a closure-parameterized base's offset landed keyed by the pre-renumber
  spelling while `RecordType.bases` itself already carried the
  post-renumber one. `_check_base_offsets()` does an exact key lookup, so
  old/new snapshots could never join on that key, silently missing a real
  base-offset ABI change. Fixed the same way the hybrid recursion already
  handles the analogous problem: `qualified_name_segments.
  defer_closure_identity_renumbering()` now wraps the whole
  `_dump_elf`/`_dump_pe`/`_dump_macho` + `attach_clang_layout` sequence,
  with renumbering applied exactly once at the very end.
- **Eleventh follow-up (Codex review): `source_location`/`source_header`
  (ADR-015 provenance -- a filesystem path, never a type/name spelling)
  were reachable by the same generic dataclass walk `deprecated`/
  `default`/`value` already needed excluding.** A legal path containing
  marker-shaped text of its own (`/tmp/(lambda:a.h:1:2)/api.h`) was
  rewritten even for a snapshot with no real closure at all, corrupting
  persisted declaration provenance and, transitively, any later
  header-origin/dependency-scoping decision that reads it. Added to
  `_PAYLOAD_FIELD_EXCLUSIONS`, the same way.
