<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`aggregate` reconciles the *same finding* across compiler profiles**
  (G34 Phase D): a project checking one library under several toolchain
  profiles (`linux-gcc14`, `linux-clang20`, `windows-msvc`, ...) previously
  got per-profile verdicts (`profile_matrix`) but had to cross-reference the
  individual reports by hand to tell "the same removal shows up on every
  profile" from "each profile broke differently". A new `finding_matrix`
  block (JSON schema `1.2`, plus a `Cross-profile findings:` section in the
  text output) emits one entry per distinct logical finding with its own
  `affected_profiles`/`unaffected_profiles`/`undetermined_profiles` lists and
  a `scope` of `all_profiles`/`profile_specific`/`partial`/`undetermined`.
  Findings are matched with the same tiered identity model
  `diff_filtering.py` already uses as its cross-detector dedup key (ADR-049
  Phase 2), so one event two profiles report under different kinds because
  they had different evidence available (`func_removed` with DWARF,
  `func_removed_elf_only` without) reconciles to a single entry carrying both
  kinds, rather than looking like two unrelated problems. A profile whose
  report is missing, unreadable, not-comparable, or carries no `changes`
  array is `undetermined` for every finding — never listed as proven
  unaffected, the per-finding form of the invariant `aggregate` is built on.
  Purely a reporting view: the gate exit code is unchanged.
