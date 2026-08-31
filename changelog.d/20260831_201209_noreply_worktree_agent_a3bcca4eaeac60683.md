### Changed

- **`abicheck/policy_file.py` is now classified `policy` in ADR-061's
  responsibility-package architecture** (internal only; no user-facing
  behavior change). `checker_types.py`'s `DiffResult.policy_file` field is
  now typed against a new structural `PolicyFileProtocol`/
  `ReclassifyRuleProtocol` pair (`abicheck/model/policy_file_protocol.py`)
  instead of the concrete `PolicyFile` class, closing the `model -> policy`
  import edge that previously blocked this classification; `PolicyFile`/
  `ReclassifyRule` themselves are unchanged. `service.py`'s
  `compare_snapshots`/`load_suppression_and_policy`/
  `_validate_contract_mode`/`dedup_policy_override_warnings` moved into a
  new leaf module, `abicheck/workflows/compare_policy.py`, re-exported from
  `service.py` unchanged (`service.py`: 451 -> 283 lines). Eight CLI helper
  modules that imported `PolicyFile`/its loaders directly now route through
  a new facade, `abicheck/workflows/policy_file.py`.
