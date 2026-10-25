### Fixed

- **An old castxml header baseline now compares clean instead of "with risk"
  for every array** — castxml used to spell `double items[2]` as `double[1]`
  (its last index); generation 15 spells the element count. Loading a castxml
  snapshot from before generation 15 now rewrites those stored array counts by
  provenance and says so in a load notice, so it compares clean against a
  fresh dump of the same headers, and a genuine one-element resize against it
  is still reported. Snapshots without castxml provenance keep the
  comparison-time extent-shift demotion.
