### Removed

- **Compare options moved to `.abicheck.yml` are gone from the command line.**
  `--used-by-manifest`, `--required-symbol` and `--view` are removed from
  `compare`. Each now fails as an unknown option (exit 64). Set them with
  `.abicheck.yml` (`consumers.manifests`, `consumers.required_symbols`,
  `output.view`) or with `--set PATH=VALUE`, whose values are YAML
  (`--set 'consumers.manifests=[m.json]'`). `--define`/`-D`,
  `--severity-preset`, `--used-by`, `-H`/`-I`, `--build-info`, `--sources` and
  `--contract` stay on the command line.

### Changed

- `consumers.manifests`, `consumers.required_symbols` and `output.view` are
  refused, not ignored, under `--no-baseline` and on a stored BundleFacts
  operand, and `--set compile.defines=` is refused under `--no-baseline`.
