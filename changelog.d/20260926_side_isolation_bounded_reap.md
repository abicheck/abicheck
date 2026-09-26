### Fixed

- A failed side resolution no longer hangs `compare` waiting on a forked
  sibling that cannot act on SIGTERM (for example one blocked on a lock
  inherited held from the parent's SIGTERM handler): an abandoned child is
  now given a short grace period and then killed.
