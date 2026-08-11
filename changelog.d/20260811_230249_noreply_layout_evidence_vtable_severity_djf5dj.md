### Fixed

- **A policy override on `type_vtable_changed` or `layout_unverifiable` is
  now respected correctly when the two findings are folded together, by
  every consumer that gates on findings — not just the legacy verdict.**
  `layout_unverifiable`'s fold into `redundant_changes` only happens when
  the covering `type_vtable_changed` finding's own policy-resolved
  severity is at least as severe as `layout_unverifiable`'s own; the
  comparison is now made *before* the fold (inside the pipeline step
  itself), so a non-subsumed finding is simply never removed from
  `DiffResult.changes` in the first place. This closes a gap the previous
  after-the-fact fix left open: the severity-scheme exit code and the
  JSON/SARIF severity gate all read `DiffResult.changes` directly and
  never saw a folded finding once it moved to `redundant_changes`,
  regardless of what the legacy verdict said.
