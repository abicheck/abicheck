### Fixed

- Stored snapshots: the `declarations` section is now version 3, with a
  v2 -> v3 migration, because generation 85 added `Function.member_kind_fact`
  to its wire fields without a section bump.
