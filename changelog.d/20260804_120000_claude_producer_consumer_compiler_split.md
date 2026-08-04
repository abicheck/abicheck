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
  kinds, rather than looking like two unrelated problems. Profiles on
  different C++ ABIs are handled too, conservatively: a declaration spelled
  `_ZN3lib3addEii` by an Itanium toolchain and `?add@lib@@YAHHH@Z` by MSVC is
  recognized as one declaration (reported as `cross_abi_declaration`), so
  neither profile is reported clean of the other's finding — but the two are
  never merged into a single entry, since no mangling parser recovers
  parameter types and nothing in a report distinguishes "both profiles lost
  the same overload" from "each lost a different one". Where the two
  spellings *are* comparable — both Itanium, as a GCC and a Clang profile
  are — their inequality proves distinct overloads and each profile is
  reported clean of the other's finding as it should be. The one platform
  difference that *is* provable is merged: a Mach-O toolchain's extra
  leading underscore (`__ZN3lib3addEii` vs `_ZN3lib3addEii`) leaves two
  byte-identical complete manglings once normalized, so a Linux and a macOS
  profile reporting one removal produce one `all_profiles` entry rather than
  two half-known ones. All three report shapes are read: a `compare`
  report's `changes`, a `compare-release` report's
  `bundle_findings`/`matrix_findings` (what a `kind: bundle` check
  produces), and a `scan --against` report's `diff.findings`. A profile only
  counts as *unaffected* by a finding when it enumerated its findings in
  full — a report that is missing, unreadable, not-comparable, carries an
  unparseable or non-conformant entry, lists only its gating buckets (a
  `scan` report, capped at 20), was narrowed for display with
  `compare --show-only`, or is a release report (which lists bundle/matrix
  findings but only per-library counts) is `undetermined` instead, the
  per-finding form of the invariant `aggregate` is built on. Such a report's
  findings are still read, since seeing a finding proves it is there while
  not seeing one proves nothing. Purely a reporting view: the gate exit code
  is unchanged. Two Windows profiles on different targets (x64 vs ARM64EC,
  whose `$$h` decoration marks the target rather than the declaration) are
  likewise never reported clean of each other's identical finding, while a
  GCC/Clang matrix keeps full precision — Itanium encodes nothing but the
  declaration, so there inequality really is proof. A `compare-release`
  report whose run hit an operational error still contributes the findings
  it did collect, marked incomplete.
