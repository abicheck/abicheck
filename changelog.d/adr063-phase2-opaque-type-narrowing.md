### Fixed

- **Opaque-type suppression no longer collides on a bare type name when it
  doesn't have to** (ADR-063 Phase 2's closing slice). Two unrelated types
  sharing a bare leaf spelling in different scopes (one forward-declared/
  opaque, the other a real, visible declaration) could both have their
  structural changes suppressed, since a stable-identity *miss* always fell
  back to the collision-prone spelling tier. It still does, **except** when
  `OpaqueTypeIndex.complete` — a new, per-comparison completeness signal —
  proves every opaque declaration on *both* sides resolved a stable
  identity, in which case a miss is now trusted as proof the change is
  about a different, non-opaque declaration. Degrades safely to the
  previous (permissive) behavior whenever completeness can't be proven —
  a mixed header-AST/DWARF comparison, or one side loaded from an archived
  baseline predating this identity population. No change to any other
  case: a genuine hit is suppressed exactly as before, and a change
  carrying no resolvable identity at all still falls through to the
  spelling tier unconditionally.
