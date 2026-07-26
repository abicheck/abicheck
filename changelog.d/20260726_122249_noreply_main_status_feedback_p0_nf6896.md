### Added

- **`release_recommendation` gains `possible_impact`** (schema 2.22): a new,
  always-non-null string field carrying the bump abicheck would recommend if
  its evidence were sufficient to confirm one. `version_bump` stays `null`
  when `state` is `"unavailable"` (the honesty fix from earlier in this PR),
  but that previously left the still-plausible bump readable only from
  free-text `rationale` prose — `possible_impact` gives automation a
  machine-readable answer to "what would this be if confirmed?" without
  reintroducing the blind-trust risk `version_bump: null` exists to prevent
  (an automated release action must still gate on `version_bump`/`state`,
  never on `possible_impact` alone).
- **`aggregate --format json`/text output gains a `profile_matrix`** (schema
  1.1): a project running the same target under multiple toolchain profiles
  (`profiles:` in `.abicheck.yml`, `check_id` shaped
  `target@profile#baseline_channel@requested_depth` per ADR-047 §7) previously
  saw unrelated-looking `target_id` rows in `targets[]` with nothing tying
  them back to the same logical target. `TargetReport` gains `profile_id`/
  `base_target` (parsed from a `check_id`-shaped `target_id`, via the new
  `parse_check_id()`), and `AggregateResult.profile_matrix` groups every
  target sharing a `base_target` into one entry naming which profiles are
  `affected_profiles` (verdict neither `NO_CHANGE` nor `COMPATIBLE`) versus
  clean — e.g. "libfoo is broken under linux-gcc14/linux-clang20 but fine
  under windows-msvc" instead of three opaque rows. Empty (and every target's
  `profile_id` absent) for a `target_id` with no profile encoding — the
  common single-profile case — so this is purely additive. Grouping combines
  *all* of a profile's checks worst-verdict-wins rather than keying by
  `profile_id` alone, since a run plan can carry more than one check for the
  same target+profile at different baseline channels/requested depths — a
  naive last-write-wins would let a later, cleaner check silently overwrite
  an earlier, breaking one for that profile (Codex review). Each
  `profile_matrix` entry also gains `incomplete_profiles`: when one of a
  profile's checks is unavailable while another did report, the unavailable
  one is surfaced here instead of being silently dropped — a completed,
  compatible `headers`-depth check plus a missing required `build`-depth
  check for the same profile is an incomplete-coverage gap, not "this
  profile is clean" (Codex review).
- **`abicheck/type_reachability.py`**: a new, additive building block for
  status-review item 3 ("direct vs. transitive type reachability").
  `directly_referenced_stdlib_types()` computes, from a snapshot alone,
  which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by a
  non-stdlib function's signature or a non-stdlib type's own field — as
  opposed to only reachable via deep template-instantiation internals
  (`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that the existing
  whole-name-prefix filter (`is_non_abi_surface_type`) already correctly
  treats as toolchain-artifact churn either way. Not yet wired into any live
  detector — see `AGENTS.md`'s "Known gaps" for why retrofitting the ~15
  existing call sites needs its own scoped, individually-verified follow-up.
