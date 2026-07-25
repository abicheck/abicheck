<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`profile_fingerprint` had no field for repeatable pass-through frontend
  flags** (Codex review, PR #624): a flag like `-include a.h -include b.h`
  forces preprocessing content whose *order* can change macro/pragma state
  before the rest of the TU is parsed — `-include a.h -include b.h` and
  `-include b.h -include a.h` can genuinely produce different ASTs, but
  neither fingerprint tracked this at all. The depfile-derived buckets
  (external `-I` slots, the system/toolchain bucket) pick up `a.h`/`b.h`'s
  *content*, but are deliberately order-independent by design, so a
  reordering of such flags went completely uncaught. Added a new
  `pass_through_flags` profile field — an ordered (not sorted), opaque list
  hashed in the given order, populated whenever an L2 frontend ran. This
  function doesn't parse or validate the flags themselves; the CLI/manifest
  glue that would collect them from a real `-include`/similar invocation is
  separate, not-yet-built work (`dumper.py` isn't wired to this module at
  all yet).
