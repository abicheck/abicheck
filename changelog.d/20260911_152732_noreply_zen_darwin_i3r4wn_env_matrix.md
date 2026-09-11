### Fixed

- **`FrozenStrDict` no longer exposes a mutable backing container** — its
  private backing store is now a `tuple` of `(key, value)` pairs instead of
  a plain `dict`, closing the vector where `instance._data["GLIBC"] =
  "2.34"` reached straight through the (necessarily readable) private
  attribute and mutated `EnvironmentMatrix.runtime_floors`'s backing dict
  in place, silently changing an already-inserted `CompareRequest`'s hash.
  `__setattr__` already blocked *reassigning* the private attribute
  wholesale; this closes mutating what it already pointed to. See
  `abicheck/model/frozen_str_dict.py`'s module docstring for the full,
  now-two-round history and the precise (and deliberately not
  over-claimed) immutability guarantee this provides.
