### Fixed

- **Typedef findings no longer change order between a live and a stored
  operand** — `typedef_removed`, `typedef_base_changed` and
  `typedef_version_sentinel` were emitted in the typedef map's insertion
  order, which a saved snapshot (written key-sorted) does not keep, so the
  same comparison listed them in a different order in `changes` depending on
  whether an operand was freshly dumped or loaded from a baseline file. They
  are now emitted in alias order, so the report is identical in both cases.
