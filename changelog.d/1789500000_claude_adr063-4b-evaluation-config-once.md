### Changed

- `CompareResult` (typed Python API) now carries `resolved_execution_context`,
  with its `evaluation_config` filled in: the ADR-049 compatibility
  evaluation config is resolved once per classification and the same object
  is what `DiffResult.evaluation_config` and the contract-context receipt
  read. Previously the pair's context always reported `evaluation_config=None`
  and the receipt re-derived its own copy (ADR-063 sub-phase 4B).
