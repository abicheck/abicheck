### Fixed

- **ADR-063 T9's "legacy-hybrid backfill blocker" is closed.** Seven case-(a)
  facts (`RecordType.deprecated`, `EnumType.deprecated`/`is_scoped`,
  `Function.deprecated`, `Variable.deprecated`, `TypeField.deprecated`/
  `default`) are gated at compare time by `AbiSnapshot.fact_provenance`
  rather than a snapshot-level flag, because a hybrid (`--ast-frontend
  hybrid`) merge's two backends can disagree on which of them actually
  populated any one declaration's fact. `clang_deprecation_facts_reliable`
  reads `True` unconditionally for a hybrid producer, so loading a document
  that predates this fact family's own schema version previously
  reconstructed every one of these facts as a confirmed `PRESENT` —
  including for a declaration neither backend's merge ever actually
  recorded looking at, since the legacy JSON format always serializes some
  value. `storage/fact_backfill.py`'s legacy-load correction now consults
  the document's own per-declaration `fact_provenance` map on a hybrid
  document, downgrading a declaration with no recorded provenance entry to
  `NOT_COLLECTED` (namespace-qualified key first, falling back to a legacy
  bare key only when unambiguous) while leaving a real, non-resting legacy
  value untouched either way.
