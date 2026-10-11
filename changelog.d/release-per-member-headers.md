### Fixed

- **A directory/package `compare` parses each member with its own headers** —
  each member now gets the `-H` headers that declare one of its exported
  names, plus any header no member claims, instead of the union of every
  member's headers. A type change in library A's header is no longer a
  finding of library B, and no member walks the whole union. A member whose
  exports cannot be read (non-ELF today) or that claims no header keeps the
  union. After the fan-out, a type that a member's exports provably do not
  reach also leaves that member's verdict and exit contribution, but only
  when a sibling member reaches it. The finding stays recorded in the
  member's `surface_scope.out_of_surface_changes` with its reason, and as
  `out_of_contract` in the disposition audit.
