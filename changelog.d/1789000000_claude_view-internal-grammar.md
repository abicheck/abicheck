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
