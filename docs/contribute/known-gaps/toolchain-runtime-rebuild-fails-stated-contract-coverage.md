---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A toolchain-only rebuild still fails a stated contract's coverage

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-23 at `94d5467`): OPEN.** Found by the toolchain-drift
fixtures added after the SVS field report (#1751 fixed the import-edge and
leaked-dependency half). Each remaining cell is a strict expected failure in a
shrink-only list; deleting the entry is part of fixing it.

A library rebuilt from unchanged source with a different toolchain setup must
not exit 1 under a stated `--contract public|exports`: nothing it declares
changed. The evidence-adaptive default hides these cases, because its `all`
fallback (`policy/contract_default_mode.py::unresolved_default_fallback_applies`)
re-scores whatever the domain left `UNKNOWN_UNRESOLVED`. A stated mode keeps
them, and each one becomes an unsuppressible `contract_coverage_failures`
record.

Three open shapes:

1. **`tm_clone`: transactional-memory clones of libstdc++ symbols.**
   `-static-libstdc++` re-exports libstdc++'s `_ZGTt...` clones (for
   example `_ZGTtNKSt11logic_error4whatEv`). The toolchain-runtime name test
   (`buildsource/export_accounting.py::toolchain_runtime_name_predicate`)
   does not recognise the `_ZGTt` prefix, so under a stated `public` 69
   `func_added_elf_only` findings stay `UNKNOWN_UNRESOLVED`
   (`required_evidence_incomplete`).
2. **`unmatched_runtime_exports`: the export domain cannot close over
   runtime exports.** An exported symbol with no declaration makes the
   `export_table` record `unmatched_exports` (`PARTIAL`), so a stated
   `exports` fails coverage on the whole domain. A static libstdc++, or
   clang's default-visibility `std::` template instantiations at `-O0`, is
   enough. This fires even for a byte-identical pair with zero findings.
   Toolchain-owned exports should not count as unmatched.
3. **Non-declaration kinds left unresolved by a stated `public`.** The
   catalog corpus shows `toolchain_flag_drift`, `integer_model_changed`,
   `glibcxx_dual_abi_flip_detected`, `inline_namespace_moved`,
   `symbol_binding_became_unique` and `symbol_binding_lost_unique`
   `UNKNOWN_UNRESOLVED` only in the stated mode. Their subject is a binary or
   toolchain fact that no header can place, so they should be
   `NOT_APPLICABLE` to the contract domain, as the import-edge kinds became in
   #1751. The fix belongs in a per-kind subject field on the change catalog,
   not in a list of names.

Tests that pin each cell:

- `tests/test_toolchain_drift_stated_contract_integration.py`:
  `KNOWN_GAP_CELLS` and `KNOWN_GAP_IDENTICAL`, by (compiler, drift, mode),
  for shapes 1 and 2.
- `tests/test_contract_mode_differential_corpus.py`: `KNOWN_OFFENDERS`, by
  catalog case, for shape 3. Its subject oracle is the catalog family
  (`platform`/`build` = not a declaration) until the catalog has a
  per-kind subject field.
- `tests/test_toolchain_drift_stated_contract.py`: the stored SVS replay,
  which already passes under every mode.
