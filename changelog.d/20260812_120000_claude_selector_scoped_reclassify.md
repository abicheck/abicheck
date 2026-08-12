<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Selector-scoped reclassification (`reclassify:`), a third `--policy-file`
  primitive.** `suppress:`'s `Suppression` rules already had a rich
  per-symbol/pattern/namespace selector grammar but only one action —
  deleting the finding — while `overrides:` could change a finding's
  verdict but only per `ChangeKind`, globally, with no selector. Neither let
  a project say "every `func_visibility_changed` on *this* symbol family is
  a known, accepted risk" without either downgrading the whole kind
  project-wide or throwing the finding away entirely — the motivating case
  is a COMDAT-inline-heavy library like oneDAL, where dozens of symbols need
  the same downgrade while an unrelated visibility regression elsewhere
  must still break. `reclassify:` closes that gap: each rule reuses
  `Suppression`'s exact selector grammar (`symbol`/`symbol_pattern`/
  `type_pattern`/`member_name`/`namespace`/`entity_namespace`/
  `cause_namespace`/`source_location`/`change_kind`/`expires`) plus a
  required `to:` (`break`/`warn`/`risk`/`ignore`, the same vocabulary
  `overrides:` already uses), and keeps the finding visible at the new
  verdict instead of deleting it. Consulted ahead of the kind-global
  `overrides:` entry for the same kind (a selector-scoped rule is strictly
  more specific), and still respects the existing frozen-namespace verdict
  floor. See `abicheck/reclassify.py`'s module docstring and
  `policy_file.py`'s format example.
