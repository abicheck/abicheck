<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Security

- **Namespace-suppression globstar matching could stall on a pathological
  candidate name.** A `namespace`/`entity_namespace`/`cause_namespace`
  selector chaining several non-adjacent `**` segments (e.g.
  `"**::a::**::a::**::a::**::a::**::a::z"`, with or without an embedded
  `*`/`?`/`[...]` wildcard among them) compiled to a single regex with one
  independently-backtracking group per globstar — combinatorial for the
  `re` engine against a long, repetitive-content candidate name; a
  121-segment non-matching name took over 8 seconds to reject a single
  match, and suppression matching runs across every finding in a
  comparison. Fixed by splitting a namespace pattern into *runs* of
  consecutive non-globstar segments at every standalone `**` and matching
  them with a non-backtracking dynamic-programming walk over the
  candidate name's `::`-delimited segments instead: a run with no
  wildcard is matched by direct positional comparison, a wildcarded run
  is matched by one combined regex over its own segments (so a bare
  `*`/`?` still spans `::` within its own run exactly like plain
  `fnmatch` always could — this is not itself a new limitation), and only
  the ambiguity in how many segments each globstar absorbs gets the
  backtracking-safe treatment — always polynomial, and typically much
  closer to linear once a run whose own trailing wildcard is
  unconstrained short-circuits after its first successful match and a
  run with a required trailing literal segment skips straight past any
  span that can't possibly satisfy it. A pattern with at most one
  globstar — the overwhelming majority of real suppression rules, which
  can never combine into the combinatorial ambiguity above — keeps the
  plain compiled regex this module used before this fix, avoiding
  needless overhead on ordinary suppression matching.
