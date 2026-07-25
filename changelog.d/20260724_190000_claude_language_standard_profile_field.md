<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`language_standard` now flows into the ADR-050 comparability profile
  fingerprint** (Codex review, PR #624 follow-up): `dump()`'s
  `compute_extraction_contract(...)` wiring previously left
  `language_standard` empty, so two dumps differing only by an explicit
  `-std=`/`--std=`/`/std:` flag (e.g. `-std=c++17` vs. `-std=c++20`)
  produced identical `profile_fingerprint`s even though the extracted AST
  can genuinely depend on the active standard (`__cplusplus`-gated
  declarations, etc.) — the comparability gate could silently allow a
  trusted verdict across two different language-standard extraction
  contexts. Added `_compiler_options.explicit_language_standard()`, which
  extracts the last explicitly forwarded standard value (last-wins,
  matching real compiler flag precedence and the same `gcc_options` →
  `gcc_option_tokens` ordering the frontend command lines themselves use),
  and wired it into `dump()`'s contract population. Deliberately does not
  reconstruct a frontend's own auto-injected default standard when the
  caller supplied none — only what was explicitly requested.
