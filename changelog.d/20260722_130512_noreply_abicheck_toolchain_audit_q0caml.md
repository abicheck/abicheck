### Documentation

- **`Cpp20Requirement.reason` is now a `Literal` of its four valid values**
  instead of a plain `str` with a stale comment enumerating only three of
  them, and the comment describing the standard-library concept list now
  accurately scopes it to `<concepts>`/`<iterator>` (bare `std::` names) —
  the `<ranges>` concepts live under the distinct `std::ranges::` namespace
  and are not covered.
