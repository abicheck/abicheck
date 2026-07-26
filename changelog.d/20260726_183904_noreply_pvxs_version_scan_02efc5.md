### Fixed

- **A `public_header_paths` entry outside the declared headers' own common
  root gave the scope `"headers"` field and `header_sequence`/
  `include_sequence` two different identity strings for the same physical
  file, spuriously hard-failing a genuinely safe, purely additive header
  change.** `header_sequence`/`include_sequence` identities were computed
  relative to the common root of `declared_headers` alone, while the scope
  `"headers"` field's identities were computed relative to the common root
  of `declared_headers` *and* `public_header_paths` combined — so a
  provenance-only header outside that narrower root widened one root but
  not the other, and the sequence carve-outs' specific-correspondence check
  (comparing identities by exact string equality) could never match a
  newly-added header against its own scope-side identity, raising
  `ProfileMismatchError` even though the growth was genuinely additive.
  `_header_identities`'s own root computation now widens the same way the
  scope side already does.
- **The base `_scope_field_is_additive_superset` carve-out (the `"headers"`/
  `"public_header_dirs"` scope fields) accepted a duplicated identity as
  safe growth**, the same class of gap already closed for
  `header_sequence` and the owned-header pair lists: `set(new_list) >=
  set(old_list)` silently collapses a duplicated identity away before it
  can be detected, even though `compute_extraction_contract` always emits
  a sorted, deduplicated list for these fields.
- **The `include_sequence` owned-header carve-out still accepted a
  duplicated pair inside an *unchanged* `hdrs:` slot.** The duplicate-pair
  check added in the previous round lived inside the per-slot diff loop,
  which only reaches a slot whose payload actually differs between old and
  new — an unchanged, malformed slot with a duplicated pair rode alongside
  a genuinely-growing separate slot completely unexamined.
  `_slot_indices_match_position` (which validates every slot, including
  unchanged ones) now also rejects a duplicated pair within a single
  slot's payload.

`abicheck/comparability.py` crossed the file-size hard cap with these
fixes' regression tests — the `header_sequence`/`include_sequence`
additive-growth carve-outs and their shared shape validators are split out
into two new sibling modules, `abicheck/comparability_sequences.py` and
the leaf `abicheck/comparability_json.py`.
