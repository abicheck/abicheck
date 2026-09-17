### Fixed

- **`verify-tag`'s own tests used API fixtures no API produces.** They omitted
  the `ref` field and used short symbolic object names, so neither the
  exact-`refs/tags/<name>` selection nor the full-object-name rule the shared
  peeling owner applies was reachable by them. Fixtures are now shaped like
  the API's; every claim is unchanged.
