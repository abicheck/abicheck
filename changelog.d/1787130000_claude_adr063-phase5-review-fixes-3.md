### Fixed

- **`Function.is_explicit_fact`/`is_override_fact` now distinguish "not
  applicable" from "not collected".** For an ordinary free function
  (`explicit` is conceptually inapplicable) or a non-virtual-eligible kind
  (`override` is inapplicable), both the castxml and direct-clang header
  backends now construct these two facts explicitly as
  `Fact.not_applicable()` at parse time, instead of letting the generic
  `bridge_legacy_and_fact` omission bridge collapse a confirmed non-gap
  into `NOT_COLLECTED` — the same distinction a successful header parse
  should be able to make (Codex review).
- **`tu_merge.py`'s function merge no longer fabricates
  `Fact.present(None)` for `contract_attributes`.** When both TUs being
  merged leave `contract_attributes` at `None` ("neither side captured
  this" — see `_merge_contract_attributes`'s own docstring),
  `replace_with_fact_sync`'s blanket "derive `Fact.present(value)`" rule
  previously turned that into a confirmed-absence status; the merge now
  passes the correct `Fact.not_collected()`/`Fact.present(...)` explicitly
  (Codex review).
