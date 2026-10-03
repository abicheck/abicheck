### Removed

- **`compare --scope-public-headers`/`--no-scope-public-headers` are gone**
  (one-comparison-product Phase 9b). `--contract` is now the one contract
  mechanism. Public-header scoping stays on for a run with no `--contract`,
  so an invocation that passed neither flag is unchanged. Drop
  `--scope-public-headers` (it was the default, and `--contract public` names
  that domain explicitly). Replace `--no-scope-public-headers` with
  `--contract all`, or set `scope.public: false` in `.abicheck.yml` to keep
  the unscoped reading without contract evaluation. The old spellings exit
  `64` (`No such option`). `--contract auto` now takes its domain from
  `scope.public`. The typed Python API's `CompareRequest.scope_public` is
  unchanged. Human-facing messages that named the flag now say
  "public-header scoping".
