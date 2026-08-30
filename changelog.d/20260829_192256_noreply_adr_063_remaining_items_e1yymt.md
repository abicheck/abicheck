### Fixed

- **A rewrite touching only a frozen dataclass's `init=False` field no
  longer mutates the caller's original object.** The closure-marker
  rewrite walk rebuilt a frozen dataclass via `dataclasses.replace` only
  when an `init=True` field changed; when the only change was to an
  `init=False` field, the rebuild was skipped and the subsequent
  `object.__setattr__` mutated the caller's original instance in place
  instead of a fresh copy.
