### Fixed

- **`public_api_exposes_stl_by_value` sees a `std::` type spelled by its
  bare alias** — castxml spells a `std::string` parameter as `string` (the
  typedef's unqualified name), so the anti-pattern's `std::` test never
  matched and a newly added `int f(std::string)` reported no risk. The check
  now falls back to the slot's resolved type identity
  (`std::basic_string<char>`) when the spelling carries no namespace.
