### Added

- **Explicit check identifiers (G42 phase 1).** `.abicheck.yml`'s
  `targets:`/`bundles:` `checks[]` entries gain an optional `id:` and an
  optional `analysis: {evidence, policy, assurance}` block. A project can
  now declare two checks for the same target/profile/channel/depth that
  differ only in analysis method/policy/assurance, each with a distinct
  `id:`, without colliding on the generated `check_id`: the id is appended
  as a `~<id>` tail (`target@profile#channel@depth~<id>`), and
  `abicheck project plan`'s duplicate-`check_id` guard now points at `id:`
  as the fix when two `checks[]` entries collide only because they declare
  different `analysis:`. `check_id`'s pattern also reserves a second,
  composable `!<environment_id>` tail segment for a later phase (named
  deployment environments); omitting both tails produces the unchanged,
  pre-existing `check_id` shape. Report schema bumped to 2.48.
