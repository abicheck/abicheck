<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Security

- **Namespace-suppression globstar matching, follow-up: a wildcarded
  segment beside a globstar chain was still exponential.** The prior fix
  in this same release routed only an all-literal-plus-globstar namespace
  pattern through the new non-backtracking matcher, falling back to the
  old regex for any pattern with an embedded per-segment `*`/`?`/`[...]`.
  A pattern combining both (e.g. `"**::a*::**::a::**::a::**::a::**::a::z"`)
  still took the old, still-exponential path — ~3.7 seconds to reject 61
  repeated segments and growing rapidly from there. `_SegmentGlobMatcher`
  now handles a wildcarded segment the same way as a literal one (matched
  against exactly one whole `::`-delimited name segment via a bounded
  per-segment regex), so every namespace/entity_namespace/cause_namespace
  pattern routes through the non-backtracking DP matcher — including the
  one shape (a wildcarded segment immediately bordering a trailing
  globstar) that still needs the existing combined-regex behavior to stay
  correct, now checked against only a small, bounded set of candidate
  segment-counts instead of the whole string via backtracking.
