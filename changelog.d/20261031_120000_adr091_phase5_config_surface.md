### Removed

- **Compare options moved to `.abicheck.yml` are gone from the command line.**
  `--define`/`-D` (compile `defines`), `--severity-preset`, `--view`,
  `--used-by-manifest` and `--required-symbol` are removed from `compare`
  (`--define`/`-D` also from `dump`, and `--severity-preset` from
  `compare-release`). Each now fails as an unknown option (exit 64). Set them
  with `.abicheck.yml` (`compile.defines`, `severity.preset`, `output.view`,
  `consumers.manifests`, `consumers.required_symbols`) or with
  `--set PATH=VALUE`, whose values are YAML (`--set 'compile.defines=[A, B=2]'`).
  `--used-by` and `-H`/`-I`, `--build-info`, `--sources`, `--contract` stay on
  the command line.

### Changed

- A `--set compile.defines=[...]` or a `compile.defines:` list replaces the
  configured macro list. The removed `-D` folded CLI macros into the config
  list by name; a list-valued override replaces it instead.
- `consumers.manifests`, `consumers.required_symbols` and `output.view` are
  refused, not ignored, under `--no-baseline` and on a stored BundleFacts
  operand, and `--set compile.defines=` is refused under `--no-baseline`.
