### Fixed

- **Consumer-scoped disposition ledger.** Closing a comparison's ledger a
  second time with a *different* consumer scope used to intersect the scopes,
  silently counting a real break out of `effective_total`; it now raises.
  Repeating the same scope stays a no-op. A contract promotion recorded after
  the scope was applied is now narrowed by it too.

### Changed

- The ledger's disposition moves live in `abicheck.policy.disposition_transitions`.
