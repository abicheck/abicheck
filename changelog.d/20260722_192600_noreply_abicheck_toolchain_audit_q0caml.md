<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Hidden-friend surface classification now falls back to the friend
  function's own origin for a qualified owner too.** When a hidden friend's
  befriending class (`caused_by_type`) is a namespaced name (`ns::Foo`) and
  its origin is present but inconclusive (`UNKNOWN` on the only side that has
  it, or the two sides disagree), `_classify_hidden_friend_surface` in
  `abicheck/surface.py` now falls back to the friend function's own recorded
  origin (`change.symbol`) instead of keeping the finding immediately — the
  same fallback already applied when the owner cannot be resolved at all.
  Previously this fallback only ran for a bare (unqualified) owner, so a
  qualified owner in the same inconclusive state silently skipped it. The
  qualified- and bare-owner code paths are also consolidated into one, using
  `_hidden_friend_owner_effective_origin` uniformly for both.
</content>
