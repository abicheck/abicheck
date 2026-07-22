### Fixed

- **Hidden-friend surface classification no longer conflates an
  unclassified-but-present bare owner with a genuinely absent one.** When a
  hidden friend's owning class is indexed exactly (e.g. `ns::Foo`) with a
  private/system origin on one snapshot, but the other snapshot only has the
  same owner under its bare name with an `UNKNOWN` origin (present, just
  never classified against a `--public-header` set), that side neither
  confirms nor refutes private/system — it must not be silently treated the
  same as an owner that's truly missing. `classify_change_surface` now keeps
  such findings rather than demoting them on the strength of a single
  confident side.
