### Fixed

- **A hidden-friend finding is no longer demoted when either snapshot lacks
  a resolvable public surface.** `classify_change_surface` dispatched
  hidden-friend findings before the resolvable-surface guard that protects
  every other kind of finding, so a mixed-evidence comparison (e.g. an
  ELF-only baseline against a header-resolvable dump) could demote a hidden
  friend from the one resolvable side's confidently-private owner, with
  nothing to cross-check on the other side. Hidden-friend findings now go
  through the same guard.
