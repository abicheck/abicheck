### Changed

- **`ResolvedExecutionContext` now covers `dump`, and `compare`'s native CLI
  stops re-deriving an already-resolved `contract.mode`** — One Semantic
  Pipeline plan, sub-phase 4B. `resolve_dump_request` builds the same
  pre-execution `ResolvedExecutionContext` `compare`'s pipeline already
  attaches, and `execute_dump_request` is now a real post-execution
  `with_assurance()` caller, completing the context's evidence view from the
  dump's own achieved depth. `abicheck compare` also now hands
  `checker.compare` its already-resolved ADR-049 `contract.mode` instead of
  letting `contract_pipeline.build_contract_stage` re-derive the identical
  legacy-alias domain a second time — no observable behavior change, since
  the two computations always agreed, but the redundant resolution is gone.
