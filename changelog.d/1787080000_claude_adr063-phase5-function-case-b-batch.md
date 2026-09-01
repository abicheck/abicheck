### Added

- **ADR-063 Phase 5's fact/capability registry: `Function`'s ten case-(b)
  fields converted to `Fact[T]`** (schema v34) — `contract_attributes`,
  `is_explicit`, `is_hidden_friend`, `source_header`, `is_variadic`,
  `exception_spec`, `is_override`, `hidden_friend_owner`, `elf_binding`,
  and `is_compiler_generated` now carry `Fact[...]` siblings, the same
  case-(b) "`None` already unambiguously means not captured" pattern
  already applied to `RecordType`/`EnumType`/`Variable`'s twin fields.
  `elf_binding_fact`'s decoded value is reconstructed as a real
  `SymbolBinding` enum member, mirroring `Variable.elf_binding_fact`.

  This closes out every case-(b) field the fact/capability registry's
  design section named for the four declaration dataclasses
  (`RecordType`, `EnumType`, `Variable`, `Function`).

  Fixed two real post-construction mutation traps this batch's
  `contract_attributes`/`is_override`/`elf_binding` conversions would
  otherwise have silently reverted: `dumper_elf_symbols.
  _populate_elf_visibility`'s `func.elf_binding = ...` attribute
  assignment now also updates `elf_binding_fact` explicitly, and
  `dumper_hybrid.py`/`tu_merge.py`'s raw `dataclasses.replace()` calls
  touching `contract_attributes`/`is_override`/`elf_binding` now route
  through `replace_with_fact_sync` instead.

### Changed

- **`abicheck/model/fact_registry.py` split into three modules**
  (`fact_registry_schema.py` for the `FactLifecycle`/`FactDefinition`/
  `FactRegistry` vocabulary and unconverted-field allowlists,
  `fact_registry_entries.py` for the `FACT_REGISTRY` entry list itself,
  `fact_registry.py` now a thin re-exporting facade) once the combined
  content crossed this repo's 800-line new-file cap. One-directional:
  both siblings import from `fact_registry_schema.py`, avoiding a real
  import cycle. `abicheck/dumper_hybrid.py`'s own
  `architecture/debt.yaml` no-growth budget freed up by moving
  `_backfill_fact` (a small, self-contained per-fact merge helper) to
  `fact_provenance.py` as public `backfill_fact`, a natural fit for
  that module's own "which backend's value the merge used" concern.
  Both are purely internal reorganizations; the public surface of
  either module is unchanged.
