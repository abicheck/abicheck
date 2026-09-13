### Changed

- **`compare --view` now carries two decisions instead of six.** The
  `patterns`, `filtered` and `suppressions` tokens are gone and the three
  ledgers they gated — pattern-verdict modulation, the scope/reconciliation
  ledger, and the `--suppress` audit — are disclosed unconditionally
  (ADR-067's record-before-disposing rule: an accepted break, an exclusion or
  a suppressed finding may not be invisible because a token was not typed).
  `demangle`/`no-demangle` are gone too: human output always demangles C++
  symbols and keeps the exact mangled spelling beside the readable name
  (`lib::gone(int) [_ZN3lib4goneEi]`), so there is nothing left to choose.
  Every retired spelling exits 64 with no alias, naming the behavior that
  replaced it.

- **`--view`'s display filter resolves through the change catalog.** The
  severity/element/action dimensions now read `ChangeKindMeta.entity`/
  `.operation` — declared once per kind, beside its verdict — instead of
  re-deriving them from the kind's *name*. The superseded name-prefix table
  matched no element at all for 238 of the 407 kinds, so
  `--view show=functions,variables,types,enums,elf` hid 58% of the catalog;
  the element vocabulary also gains `build`, `source` and `analysis` tokens
  for the dimensions that table could not express. A kind a detector emits
  for more than one entity type resolves its entity per *finding*
  (`ChangeKindMeta.entity_from_field`, declared on the same single
  registration), so a graduated experimental **function** is no longer
  reported and filtered as a type.

- **An attribute transition is a modification, not an addition.** A kind
  reporting that a persisting declaration gained or lost `[[deprecated]]`,
  an `override` specifier or a field default initializer now carries
  `operation: modified` -- `--view show=added` no longer lists functions
  that were merely annotated, and `show=changed` no longer hides them.
  `func_export_added`/`var_export_added` stay additions: there a symbol
  genuinely appears in the export table.

- **Stored-bundle-facts Markdown demangles too.** It was the one human
  Markdown output still rendering raw mangled names, which the retired
  `--view demangle` token left with no override.

- **A directory/package comparison discloses its scope exclusions too.** It
  reported only a *count* of findings scoped out of the public surface, so a
  release whose whole breaking set was excluded could pass while explaining
  nothing. The rows come from the same builder a single comparison uses.

- **A directory/package comparison discloses its suppression audit.** The
  release fan-out rendered no audit section at all, so a passing release
  report could hide which breaking findings a rule disposed of — ADR-067
  makes that part of the result, not a display preference.

- **The two unconditional stderr ledgers demangle** (filtered-surface and
  build-context-reconciled), so a human ledger no longer reads raw while
  the report beside it reads `Readable [mangled]`.

- **The aggregate stored-bundle JSON carries both symbol names too**, and
  `typedef_version_sentinel` is a removal rather than a modification, as its
  own description always said.

- **Every public rendering entry point rejects the retired `leaf` mode.**
  `reporter.to_json`, `report.to_markdown`, `sarif.to_sarif` and
  `junit_report.to_junit_xml` shared none of `render_output`'s check, so a
  caller passing `report_mode="leaf"` to any of them silently received a
  *full* report instead of an error — as did `service_render.render_envelope`,
  whose mode arrives on the envelope rather than as an argument. One owner
  now (`report/report_modes.py`), consulted by all five.

### Removed

- **`--view leaf` is retired in favour of `--view root-cause`.** Measured
  rather than argued: over the 129 catalog library pairs that build in this
  environment, the two modes exposed the *identical* finding set in every one
  of the 93 cases that had findings, and `leaf`'s own headline section was
  empty in 40 of them. The JSON `leaf_changes`/`non_type_changes` keys go with
  it; `root_causes`/`root_cause_count` is the supported grouping.

### Added

- **Every machine projection carries both symbol names.**
  `demangled_symbol` is now resolved for any Itanium-mangled finding, not
  only an `ELF_ONLY`-visibility one, and each finding carries its declared
  `entity` beside the `operation` field it already had (report schema 5.0).

- **A release report now carries its disposition ledgers, not just its
  counts.** A directory/package `compare` captured each library's
  suppression and public-surface-scope ledgers as text and echoed them to
  stderr, so the requested JSON artifact named neither the rule that fired
  nor the finding it disposed of — a passing release report could hide every
  break in it, and a terminal log is not a report. Each `libraries[]` entry
  now carries the `suppression` and `surface_scope` blocks a single-pair
  report already had, built by the same two functions so both cardinalities
  read one shape (`release_schema_version` 1.4; present only when the
  setting was in effect, so a document produced without them is unchanged).

### Fixed

- **`to_junit_xml_multi` validates its report mode.** The multi-library JUnit
  entry point reached the testsuite builder directly, so a retired or unknown
  `report_mode` silently rendered a full document instead of erroring.

- **Both JUnit entry points batch-demangle once.** Neither prewarmed the
  demangle cache, so on a host without the in-process `cxxfilt` package a
  large report forked a `c++filt` subprocess per distinct C++ symbol. SARIF
  and the shared document builder already prewarmed.

- **`consumer_required_symbol_removed` is a binary-surface finding, not a
  function one.** The evidence is a consumer's dynamic-symbol table, which
  carries no function/variable discriminator, so a required *data* symbol was
  hidden from `--view show=variables` and serialized as `entity: "function"`.

- **`python_api_parameter_removed` is a modification.** The function persists
  with a changed signature — a parameter is not an entity — so the finding
  belongs to `--view show=changed`, matching every sibling parameter-level
  kind. It was the only one declared as a removal.

- **A symbol is never corrupted by demangling.** The mangled-token scan had no
  left boundary, so a C or assembler export merely *containing* a mangled-looking
  suffix was rewritten into text that no longer contained it — `my_Z3foov`
  rendered as `myfoo() [_Z3foov]`. Retiring `no-demangle` is what made this
  unavoidable rather than latent, since every human format now demangles.

- **JUnit classnames come from the change catalog.** JUnit kept a fourth
  name-prefix taxonomy after every other projection moved to the catalog, so it
  answered `metadata` for kinds the catalog declares as real elements
  (`constant_added` is a variable, `calling_convention_changed` a function) and
  could not classify a polymorphic kind at all — JUnit disagreed with JSON and
  with `--view show=` about the same finding.

- **A release's disposed findings are named in Markdown too, not only JSON.**
  The Markdown artifact carried aggregate disposition *counts* but never said
  which findings a rule or scoping decision disposed of, leaving that detail
  only in a transient stderr echo.

- **`internal_symbol_required_by_public_api` takes its entity from what
  triggered it.** The detector fires on any breaking change whose subject is the
  internal declaration — `var_removed` included — so a public inline function
  referencing a removed internal global produced a *variable* finding that a
  fixed entity hid from `--view show=variables`.

- **A release's ledger blocks survive lockstep SONAME suppression.** They were
  snapshotted before that release-wide pass ran, so a superseded member named
  neither the `lockstep_soname_bump` rule nor the finding it hid.
