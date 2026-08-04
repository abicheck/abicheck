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
  kinds, rather than looking like two unrelated problems. Both report shapes
  are read: a `compare`/`scan` report's `changes`, and a `compare-release`
  report's `bundle_findings`/`matrix_findings` (what a `kind: bundle` check
  produces). A profile only counts as *unaffected* by a finding when it
  enumerated its findings in full — a report that is missing, unreadable,
  not-comparable, carries an unparseable entry, or is a release report
  (which lists bundle/matrix findings but only per-library counts) is
  `undetermined` instead, the per-finding form of the invariant `aggregate`
  is built on. Such a report's findings are still read, since seeing a
  finding proves it is there while not seeing one proves nothing. Purely a
  reporting view: the gate exit code is unchanged.
