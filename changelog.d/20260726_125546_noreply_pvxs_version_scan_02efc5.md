### Fixed

- **F8 additive-only header-set carve-out no longer declines on an
  unchanged sentinel field** — the real CLI shape (`-H old=<dir> -H
  new=<dir>`, a single public-header directory per side) collapses
  `public_header_dirs` to the identical `"<single-header-dir>"` sentinel
  on both sides, but `_scope_field_is_additive_superset` declined on the
  sentinel unconditionally, even when the two sides were byte-identical —
  wrongly hard-failing before the carve-out ever reached the genuinely
  differing `headers` field. The helper now returns `True` immediately for
  an unchanged field, regardless of shape. Verified end-to-end against a
  direct repro of the real F8 scenario (declared via directories, not
  individual files).
