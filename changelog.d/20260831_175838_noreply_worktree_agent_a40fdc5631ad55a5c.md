### Changed

- **`msvc_scope_components` moved to `abicheck.model.mangled_name`**
  alongside `itanium_scope_components` (ADR-061 Phase 2's "fourth,
  pre-existing tension" closure). Both are validated Itanium/MSVC mangled-
  name scope-component parsers, pure string decoding with no I/O, now
  living in one shared inward-facing `model`-classified leaf both `extract`
  and `compare` may import — `diff_cxx_rules.py` re-exports both by value
  for back-compat, so every existing `from .diff_cxx_rules import
  msvc_scope_components` call site is unaffected.
  `abicheck.buildsource.ctor_export_match` and
  `abicheck.buildsource.virtual_dispatch_graph` now import directly from
  `abicheck.model.mangled_name` instead of through the `compare`-classified
  `diff_cxx_rules.py`, closing the real `extract -> compare` edge that kept
  `ctor_export_match.py` unclassified. `ctor_export_match.py` is now
  classified `extract` in `architecture/modules.yaml` (the family
  `source_link.py` already lives in); `reclassify.py`, `contract_gating.py`,
  and `contract_evidence.py` remain unclassified for their own, unrelated
  reasons.

### Notes

- Pure relocation: no behavior or public-API change.
  `python scripts/check_architecture.py` reports 0 findings, and
  `tests/test_mangled_name.py` pins the back-compat re-export (`diff_cxx_
  rules.msvc_scope_components is model.mangled_name.msvc_scope_components`)
  the same way it already pinned the Itanium half.
