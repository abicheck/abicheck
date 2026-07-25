<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Two nested project-owned `-I` roots owning the same declared header
  collapsed to identical slot tokens** (Codex review, PR #624): `-I work -I
  work/include` with a declared header at `work/include/foo.h` made both
  slots project-owned (each is an ancestor-or-equal of the header), and
  `_slot_token_for_ancestor` tokenized each slot purely from the header's
  global root-relative identity — identical regardless of which of the two
  dirs owned it. Swapping to `-I work/include -I work` therefore produced
  the same `include_sequence` and `profile_fingerprint` either way, silently
  losing the order-sensitivity a genuine search-path reorder is supposed to
  preserve. Each owned header now also contributes its identity *relative to
  that specific include dir* (always safe to compute: ownership already
  guarantees the dir is an ancestor-or-equal of the header) alongside its
  existing global identity, so two nested roots owning the same header now
  tokenize distinctly. The deeper case the same review raised — an
  ambiguous `#include <config.h>` resolving to a *different*,
  non-declared-header dependency file depending on search order — remains a
  known scope boundary: project-owned directories are position+logical-token
  only and never content-hash their non-declared-header contents, by this
  algorithm's original design (see `compute_extraction_contract`'s
  docstring); order becoming a hashed input again is the fix this review
  round covers, not full content-coverage for project-owned dependencies.
