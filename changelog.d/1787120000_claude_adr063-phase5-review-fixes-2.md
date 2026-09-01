### Fixed

- **`Function.source_header_fact`/`Variable.source_header_fact` no longer
  claim `dwarf`/`pdb` as producers.** Unlike `RecordType`/`EnumType`,
  `dwarf_snapshot.py` constructs every `Function`/`Variable` without
  `source_location`, and no `pdb` module constructs either dataclass at
  all — the fact stays `NOT_COLLECTED` on a debug-only snapshot, so
  claiming those two backends overstated available evidence in the
  registry and its generated documentation (Codex review).
- **`AbiSnapshot.ast_resolved_standard_fact` moved to the end of the
  dataclass**, after `typedefs_qualified` (the last field before the
  runtime-only/private cache fields), instead of sitting beside the
  legacy field it bridges — this package's own "append new fields at the
  end" convention for public dataclasses (Codex review).
