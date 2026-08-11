<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`explain_use_case_impact()` could mis-attribute a versioned export**
  (G29 Phase 4, Codex review): when one declaration maps to more than one
  exported symbol (e.g. two versioned exports `foo@V1`/`foo@V2` of the
  same definition), attribution kept only the last symbol seen for that
  declaration — so a use case that named `foo@V1` specifically could be
  attributed a `foo@V2`-only change instead, or its own `foo@V1` change
  could vanish depending on graph edge iteration order. Fixed: a use case
  entrypoint naming one exact versioned symbol now stays pinned to that
  symbol alone; only an entrypoint naming the shared, undisambiguated
  declaration (or a label that coalesces onto it) legitimately covers
  every version it maps to.
