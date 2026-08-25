<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Namespace-move roll-up now recognizes header-tier (non-mangled) symbol
  keys.** `find_namespace_move_groups` only parsed scope components from
  mangled Itanium/MSVC symbols, so a real namespace move reported through a
  castxml-synthesized constructor/destructor key (`__abicheck_ctor__...`,
  `~Qualified::Name`) or a plain qualified display name never joined the
  batch roll-up — leaving most of a real move's findings unpaired. A new
  qualified-name fallback (`diff_cxx_rules.qualified_name_scope_components`)
  closes the gap generically for any such key. `find_namespace_move_groups`
  also now rejects an ambiguous pairing in either direction: one removed
  symbol matching several distinct added targets (one-to-many), and several
  distinct removed namespaces converging on the identical added target
  (many-to-one, e.g. `old1::{f,g}`/`old2::{f,g}` removed with only
  `new::{f,g}` added) — and no longer double-counts the same declaration
  toward the roll-up's 2+-pairs threshold when it's reported under two
  different string identities (a real mangled symbol and a header-tier
  synthetic key for the same move). `qualified_name_scope_components`'s
  balanced-nesting check (and `strip_trailing_top_level_parameter_list`'s
  identical concern) now track angle-bracket and paren nesting as two
  independent counters rather than one shared depth — a real, demangled
  non-type template argument can legitimately contain a parenthesized
  `<`/`>` comparison (`operator std::integral_constant<bool, (sizeof(T) >
  1)>`), which a single shared counter miscounted as closing the enclosing
  template and rejected as malformed. A bare, unparenthesized `<`
  comparison (e.g. `operator B<N < M>`, legal C++ and confirmed produced
  verbatim by clang's own AST dump for an uninstantiated class template
  member) is also now correctly accepted — unlike `>`, C++'s grammar does
  not require a `<` comparison to be parenthesized as a non-type template
  argument, so a real template-opening `<` is now distinguished from a
  comparison via the same spacing convention every compiler pretty-printer
  uses (a template opener is never preceded by whitespace; a binary
  operator always is, on both sides). A multi-character `<`-led expression
  operator (`<<`, `<=`, `<<=`, `<=>`, e.g. `operator B<N << M>`) is now
  also correctly accepted — the per-character spacing signal alone
  misclassified the second `<` of `<<` (preceded by the first `<`, not
  whitespace) as its own template opener; these tokens are now recognized
  and skipped atomically before either character is considered
  individually, which is structurally sound with no whitespace check
  needed (a template-argument-list can never begin with a bare `<` or
  `=`, so two adjacent `<`s, or a `<` immediately followed by `=`, can
  only be this operator's own spelling). Brace (`{`/`}`) nesting is now
  also tracked as a third independent counter: C++20 allows a captureless
  lambda closure as a non-type template argument, and its body is a full,
  self-contained statement grammar where a `<`/`>` comparison needs no
  parenthesization the way one directly in the template-argument-list
  does (`operator B<[]{ return N > M; }>`, confirmed to compile and
  pretty-print verbatim). Once a brace opens, every character up to its
  matching close is now treated as fully opaque — unlike the angle-bracket
  cases, this needs no heuristic at all, since braces always balance
  unconditionally in valid C++. Bracket (`[`/`]`) nesting is now tracked
  the same way, for the same reason: a subscript expression as (or within)
  a non-type template argument (`operator B<A[N > M]>`, confirmed to
  compile) carries a `>` needing no parenthesization either, since `]` —
  not `>` — closes it. Both fixes are string/character-count-based, not a
  real tokenizer: a string/char literal inside a lambda body containing a
  brace/bracket/paren/angle-bracket character can still desynchronize the
  count, and closing that would need real lexical scanning (quoting,
  escape sequences, raw string literals, comments) — documented as a
  known, accepted limitation in `qualified_name_scope_components`'s own
  docstring rather than attempted, since it is a materially larger piece
  of work than the bounded fixes above and the shape it would guard
  against (a string literal inside a lambda body used as a conversion
  operator's own non-type template argument) is far into
  adversarially-constructed territory rather than real-world C++. A
  lambda's trailing-return-type arrow (`[]() -> bool { ... }`, confirmed
  to compile and pretty-print verbatim as a non-type template argument) is
  now also correctly accepted — the `>` in `->` sits in the lambda's own
  declarator, not inside any brace/bracket the earlier fixes already
  track as opaque, so it needs its own check. Unlike every other `>` case
  here, this one needs no heuristic at all: a `-` immediately adjacent to
  a `>` can only ever tokenize as the single `->` token by the C++
  lexical grammar's own maximal-munch rule, never as two separate tokens.
- **A lambda-closure-parameterized function-level finding is now demoted
  when confirmed never exported on either side.** A `func_removed`/
  `func_params_changed`/`template_param_type_changed`/
  `template_return_type_changed` finding whose subject is a template
  instantiated over a local lambda closure type — spurious churn from an
  unrelated source-line shift — is now demoted via the existing
  `effective_verdict`/`modulation_reason` hook (ADR-025) when the reported
  symbol is confirmed absent from both binaries' real exported symbol
  table, mirroring `diff_versioning.demote_internal_version_node_findings`.
  A genuinely-exported symbol, or a castxml-synthesized ctor/dtor key
  (never a real export by construction), is left untouched.
- **`find_namespace_move_groups` now also rejects a many-to-one collision
  spanning DIFFERENT masking positions**, not just the same one. The
  existing many-to-one guard only caught two removed segment values
  competing for the identical masked context at the SAME component
  position; when `p1::old::{f,g}` (differing from the sole added
  `new::old::{f,g}` at position 0) and `new::p2::{f,g}` (differing from the
  same added declaration at position 1) are both removed, their masked
  contexts differ, so the position-scoped check saw no collision and both
  `p1 -> new` and `p2 -> old` independently cleared the 2+-pairs threshold
  as contradictory `SYMBOL_RENAMED_BATCH` findings over the identical
  added symbols. Fixed by also tracking, per added declaration's own full
  scope-chain identity, the distinct `(old_segment, new_segment)`
  substitution keys it has been claimed under, and rejecting any entry
  whose added declaration was claimed under more than one. That guard was
  itself refined once more: tracking distinct *substitution key text*
  under-rejected a further collision where two genuinely different removed
  originals happen to spell the identical `(old_segment, new_segment)`
  text from different masking positions (removing `old::new::f` and
  `new::old::f` while adding only `new::new::f`: both claims spell
  `old -> new`). Fixed by tracking the set of distinct *claiming
  removed-symbol identities* per added declaration instead of key text —
  an added declaration can only actually be the result of one historical
  move, regardless of whether two different claims happen to describe that
  move with the same substitution text. A symmetric gap was found and
  closed too: the same removed symbol can resolve to *different* added
  declarations at its different masking positions (removing
  `p1::old::{f,g}` while adding `new::old::{f,g}` and `p1::new::{f,g}`
  makes each removed symbol match both `p1 -> new` and `old -> new`,
  mutually exclusive substitutions, simultaneously) — now tracked the
  mirror-image way, per removed symbol's own identity, the set of distinct
  added declarations it resolves to. Building both directions' tracking
  from the same entry set turned out to be unsound either way it was tried
  (a same-position collision on one removed symbol's entry either leaked
  into an unrelated symbol's own clean, different-position candidacy and
  wrongly discredited it, or — filtered the other way — lost real evidence
  that a target is contested). A third round found that "filtering the
  other way" fix was itself unsound in the mirror-image shape: a removed
  symbol whose one candidacy is contested by an unrelated third symbol
  (unconfirmable, not disproven) had its OTHER, merely-locally-clean
  candidacy wrongly treated as confirmed and reported — the identical
  unsound "resolve ambiguity by preferring whichever half looks cleaner"
  move, just with the collision on the other position. Both tracking dicts
  are now built from every raw candidacy, unfiltered, matching this
  function's own stated false-negative-over-false-positive default: a
  removed symbol with any second raw candidacy at all — confirmed-contested
  or not — is now treated as genuinely undecidable and reported nowhere.
  A fourth round found one more gap in the SAME construction: a candidacy
  a removed symbol's own masking position discarded via the pre-existing
  LOCAL one-to-many check (that position's masked context matching more
  than one distinct added target) never entered the shared `entries` list
  at all, so it never contributed to either cross-position tracking dict
  either — even though discarding it as unusable evidence for one specific
  pairing doesn't mean the added declaration it ambiguously matched stops
  being a real, live alternative explanation for a DIFFERENT removed
  symbol's own claim on it. Fixed by moving both tracking dicts' construction
  earlier, into the same loop that computes each masking position's raw
  candidate list, so every raw candidacy is registered before the local
  one-to-many filter ever discards it.
- **Cross-tier enum findings now dedupe correctly.** The L2 header-tier
  enum detector (`diff_types._diff_enums`, bare `EnumType.name`) and the L1
  DWARF-tier detector (`diff_platform._diff_enum_layouts`, fully-qualified
  DWARF key) could both report the identical `ENUM_MEMBER_REMOVED`/
  `ENUM_MEMBER_VALUE_CHANGED`/`ENUM_LAST_MEMBER_VALUE_CHANGED`/
  `ENUM_UNDERLYING_SIZE_CHANGED` change with two different `canonical_finding_id`
  values, so cross-detector dedup never recognized them as one finding. Fixed
  with a bare/qualified name bridge in `diff_filtering` plus adding the four
  enum kinds to `_deduplicate_cross_detector`'s own dedup-category table,
  which had never attempted identity resolution for them at all.
