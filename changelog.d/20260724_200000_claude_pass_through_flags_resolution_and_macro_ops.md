<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A forced-include (`-include`) operand resolvable only via `-I` search
  paths could crash an otherwise-valid `dump()`** (Codex review, PR #624
  follow-up): the ADR-050 `pass_through_flags` wiring stored a bare
  `Path(operand)` for `-include <path>`, which `compute_extraction_contract`
  then content-hashes relative to abicheck's current working directory —
  not the frontend's own include search path. That could raise on a dump
  that had otherwise succeeded (no such CWD-relative file), or silently
  hash an unrelated file that happened to share the name. Added
  `header_conditionals.resolve_pass_through_paths()`, run by `dump()` with
  the known `-I` directories (`extra_includes`) plus CWD as candidate
  resolution roots; an operand that still can't be resolved anywhere known
  now falls back to opaque string hashing (the same treatment already
  given any unclassified flag) instead of raising or mis-hashing. System
  default include directories, sysroot, and `-I`/`-isystem` passed as raw
  text inside `gcc_options`/`gcc_option_tokens` are still not modeled —
  those degrade safely to the same string fallback; full resolution is
  deferred to the depfile mechanism (ADR-050 D1's still-deferred
  `depfile_resolved_paths`/`generated_driver_path`).
- **Function-like macro definitions (e.g. `-DAPI(x)=x`) were silently
  dropped from the ADR-050 profile fingerprint** (Codex review, PR #624
  follow-up): `ordered_macro_ops()` shared its token walk with
  `defines_from_flags()`, which intentionally only tracks bare-identifier
  macro names for its own (unrelated) build-context reconciliation
  purpose. That filter was incorrectly also applied to the fingerprint
  path, so two extractions differing only in a function-like macro's
  expansion could produce identical `profile_fingerprint`s. The filter now
  applies only inside `defines_from_flags()`; `ordered_macro_ops()` keeps
  every `-D`/`-U` token verbatim, including non-identifier bodies.
