<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3: shadow evaluator now confirms member-level and
  hidden-friend findings correctly** (no behavior change outside this
  still-unwired shadow module):
  - A member-level finding (e.g. `TYPE_FIELD_OFFSET_CHANGED` with
    `symbol="Point::x"`) was always downgraded to `UNKNOWN_UNRESOLVED`
    even when genuinely public, because confirmation checked the full
    `"Point::x"` string against `public_types` instead of the owner
    `"Point"` — `_type_identifiers("Point::x")` yields `{"Point::x", "x"}`,
    never `"Point"`. `_in_surface_result_is_confirmed()` now strips the
    member the same way `classify_change_surface()` does before checking
    owner-type membership.
  - A `hidden_friend_removed`/`hidden_friend_added` finding confirmed
    public by `surface._classify_hidden_friend_surface()`'s own
    origin-based provenance check (the befriending owner, or the friend
    function itself, confidently declared in a public header) was also
    always downgraded, since a hidden friend can never produce a real
    export and so never appears in `public_symbols`/`public_types` at
    all. A new `_hidden_friend_confirmed_public()` mirrors that
    classifier's own two provenance checks instead of consulting the
    universe-membership sets that don't apply to this kind.
