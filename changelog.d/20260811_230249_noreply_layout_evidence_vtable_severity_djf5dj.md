### Fixed

- **A policy override on `type_vtable_changed` or `layout_unverifiable` is
  now respected correctly when the two findings are folded together.**
  `layout_unverifiable`'s fold into `redundant_changes` (see above) is now
  excluded from verdict computation only when the covering
  `type_vtable_changed` finding's own policy-resolved severity is at least
  as severe as `layout_unverifiable`'s own — so overriding
  `type_vtable_changed` to compatible while leaving `layout_unverifiable`
  at its RISK default still surfaces that RISK in the verdict, instead of
  silently dropping it.
