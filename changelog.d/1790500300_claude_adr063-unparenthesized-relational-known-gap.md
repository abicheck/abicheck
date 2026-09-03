### Documentation

- **Documented, as a pinned, executable test, an accepted residual in the
  qualified-name bracket scanners: an unparenthesized relational `<`/`>`
  used as a non-type template argument (`S<N < 2, &h>`) is still mistaken
  for a nested template open/close.** `iter_top_level_chars` and
  `skip_template_arguments` already handle the parenthesized form
  (`S<(N < 2), &h>`) correctly; the unparenthesized form is the same
  residual `extract.semantic_normalizer_artifacts.has_unresolved_component`
  already documents for the identical ambiguity -- C++ itself requires
  this disambiguating wrap in real, compilable source (an unparenthesized
  `<` there parses as opening a nested template-argument-list), and
  resolving the case where a pretty-printer drops a source
  parenthesization it judges redundant needs real expression parsing, not
  a textual bracket stack. Left as-is, matching that sibling module's own
  documented boundary, rather than attempting a heuristic third fix for
  an ambiguity the language does not resolve without parentheses (Codex
  review on PR #1041, follow-up round).
