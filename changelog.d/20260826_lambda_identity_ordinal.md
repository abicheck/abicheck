### Fixed

- **Lambda-closure identity no longer embeds its source `:line:col`**: a
  castxml/clang closure-parameterized type/function (e.g.
  `raii_guard<(lambda at task_group.h:522:26)>`) previously compared as
  removed-plus-added whenever an *unrelated* edit earlier in the same header
  shifted the lambda to a new line -- reported against real oneTBB
  2021.13.0 -> 2022.3.0 binaries as a spurious `type_removed`/`type_added`
  pair, a paired `func_removed`/`func_added` on every ctor/dtor/method of
  the instantiation, and a `declaration_renamed` RISK finding whose entire
  content was the line-number text. `qualified_name_segments.
  renumber_anonymous_closure_identities()` now replaces the
  `:<line>:<col>` discriminator with a stable ordinal -- "the Nth lambda of
  this marker kind declared in this header" -- computed once per snapshot,
  mirroring GCC/DWARF's own per-scope `{lambda(...)#1}` numbering. As long
  as an edit doesn't reorder or add/remove same-header, same-kind lambdas
  relative to each other, both sides of a comparison now assign the
  identical ordinal to the identical closure, eliminating all three noise
  classes for that case. Snapshots loaded from disk are renumbered too
  (a no-op once already renumbered), so a baseline persisted before this
  fix compares correctly against a freshly-dumped snapshot instead of
  disagreeing on identity purely from the encoding change. A hybrid
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
