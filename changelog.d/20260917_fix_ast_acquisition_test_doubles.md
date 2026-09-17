### Fixed

- The `group` parameter added to `run_ast_acquisition`/`AstAcquisitionScope.run`
  (the bounded AST-root retention) left two `integration`-marked test modules'
  doubles narrower than the real entry points, so every wrapped call raised
  `TypeError` and the observations those tests rest on came back empty rather
  than wrong. The doubles now forward `group`, and
  `tests/test_ast_acquisition_double_signatures.py` states the invariant
  structurally in the fast lane, reading the real signature with
  `inspect.signature` rather than restating it.
