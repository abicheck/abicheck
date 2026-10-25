### Changed

- **`run_compare` builds its classification request through a workflow function** — the field-for-field mapping of resolved settings onto `CompareRequest` moved from the native `compare` CLI into `workflows.compare.invocation.build_resolved_compare_request`, so the request a pair is classified under is built in one tested place rather than inline in the CLI. Behaviour is unchanged: the same values reach the same fields, and every flag, exit code and output is the same.
