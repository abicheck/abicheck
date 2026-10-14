<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Header-backend environment decisions now have one reader** — the AST-fallback, unsupported-castxml and `ABICHECK_AST_FRONTEND` decisions are resolved by `extract/headers/backend_plan.py` (`BackendPlan`), which the dump, the L2 cache key, the preflight and the L4 frontend choice all share. Behaviour is unchanged; the three `dumper` fallback helpers now take a plan instead of re-reading the environment.
