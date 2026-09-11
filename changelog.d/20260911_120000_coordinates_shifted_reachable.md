### Fixed

- **`declaration_coordinates_shifted` is reachable again.** ADR-048's
  coordinate-only reconciliation outcome required a `SOURCE_DECLARES`-edge
  declaring file on *both* sides, which no real header-graph pair carries:
  every otherwise-eligible pair fell through to
  `declaration_identity_reconciled`, whose prose asserts the strictly
  stronger "both name and location evidence changed" at `risk` severity —
  on *absent* location evidence. The declaring-file guard is dropped; the
  coordinate shift embedded in the qualified name is itself the location
  evidence, and `closure_location_free_identity` stripping it is what
  proves the non-coordinate part identical. Every other narrowing (both
  qualified names present and differing, an agreeing signature tail, a
  type-shaped node kind) is unchanged.
