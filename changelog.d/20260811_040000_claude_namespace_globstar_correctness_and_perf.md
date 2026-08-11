<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Namespace-suppression backtracking-safe rewrite, follow-up: two real
  regressions in the previous fix, found via review before merge.**
  Extending the new non-backtracking matcher to *every* namespace pattern
  (routing every non-globstar segment through per-segment matching)
  silently changed real, pre-existing `fnmatch` behavior that predates the
  globstar rewrite entirely: a bare `*`/`?` used to be able to span `::`
  freely (`namespace: "oneapi::*::detail"` legitimately matched
  `"oneapi::x::y::detail"`), and per-segment matching quietly broke that.
  Fixed by splitting a namespace pattern into *runs* of consecutive
  non-globstar segments at every standalone `**`: each run is compiled as
  one combined regex over its own segments (so a wildcard still spans its
  own run's internal `::` joiners, exactly like real `fnmatch` always
  could), while only the *globstar* boundaries between runs get the
  backtracking-safe treatment — because a chain of standalone globstars,
  not an ordinary wildcard, was always the actual source of the
  exponential blowup. Separately, always routing namespace matching
  through the general DP matcher (even for the overwhelmingly common case
  of zero or one globstar, which can never combine into combinatorial
  backtracking) measurably slowed down ordinary suppression auditing — a
  pattern with at most one globstar now reuses the plain compiled regex
  this module used before the rewrite, closing a real +94% regression on
  a `suppression_audit`-shaped benchmark. Also: the namespace-ancestor walk
  (checking a symbol and each of its parent namespaces against a rule) no
  longer calls the matcher once per ancestor level — it now computes the
  backtracking-safe match once and reuses the result, closing a further
  multiplicative slowdown the ancestor walk caused for a wildcarded
  pattern beside several globstars.
