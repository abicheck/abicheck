### Changed

- **Both header-AST parsers now track scopes as typed segments, not bare
  names** — `dumper_clang.py`'s AST walk and `dumper_castxml.py`'s
  `context`-chain walk record each containing scope as a
  `model.identity.ScopeSegment` (`Namespace`/`InlineNamespace`/`Record`/
  `Anonymous`) at the point that scope is entered, keeping the node kind,
  a record's access specifier, an inline namespace's version tag, and a
  per-parent ordinal for an unnamed scope — all information the flat
  `"::"`-joined spelling discards, so a record nested in a record no longer
  looks identical to the same names nested in a namespace (ADR-063 Phase 2).
  Purely additive parser-internal state: every `qualified_name` and every
  snapshot field is byte-for-byte unchanged, and no `EntityId` is built from
  this yet. No user-visible behaviour change. (Fixed in review: the
  per-parent anonymous-ordinal counter is now shared across a named
  namespace's separate reopened blocks and across a transparent AST
  wrapper such as `extern "C" { ... }`, keyed by the logical scope path
  itself rather than by which AST node produced it — previously, either
  case could collide two unrelated anonymous scopes onto the same
  ordinal.)
