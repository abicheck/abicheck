### Fixed

- **The CastXML version gate no longer misreports a git-suffixed Superbuild
  release as unparseable.** The CastXML Superbuild's own version string uses
  a bare `-g<hash>` (or `-<n>-g<hash>`) suffix, e.g. `0.7.0-g9864b1e` — PEP
  440 only accepts that kind of build metadata after a `+` separator, so a
  direct `Version(...)` parse always failed regardless of whether the
  numeric release was actually in the supported range. `castxml_policy.py`
  now falls back to a `-`→`+`-normalized parse before giving up, so a
  git-suffixed build is judged on its numeric version like any other.
- **`Function.hidden_friend_owner` no longer risks silently corrupting
  positional `Function(...)` construction.** The new field was inserted
  ahead of several pre-existing fields (`source_header`, `origin`, ...) in
  this public, non-keyword-only dataclass; any external caller constructing
  a `Function` positionally past that point would have every later
  positional argument silently rebound one slot over. Moved to the end of
  the field list, after `is_override`, matching how every other previously
  added field was appended.
