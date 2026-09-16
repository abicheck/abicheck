### Fixed

- A `type_size_changed` row in the PR comment rendered its delta twice —
  `Size changed: Ctx (64 → 96 bits) (64 → 96)`. `report/value_delta.states_delta`
  suppresses a row's authoritative values only when the description already
  states that exact transition, and its trailing-completion rule demanded that
  nothing but closing punctuation follow the delta. A trailing **unit** left
  `"bits"` after the match, so the predicate answered "not stated" and the
  renderer appended a second copy. A unit is not more of the value, which is
  what that rule guards against; a closed unit vocabulary is now allowed to
  follow, and an unknown trailing word still falls through to the safe answer
  of reporting the values.
