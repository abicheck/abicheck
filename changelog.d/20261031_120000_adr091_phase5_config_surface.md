### Removed

- **Compare options moved to `.abicheck.yml` are gone from the command line.**
  `--used-by-manifest`, `--required-symbol` and `--view` are removed from
  `compare`. Each now fails as an unknown option (exit 64). Set them with
  `.abicheck.yml` (`consumers.manifests`, `consumers.required_symbols`,
  `output.view`) or with `--set PATH=VALUE`, whose values are YAML
  (`--set 'consumers.manifests=[m.json]'`). `--define`/`-D`,
  `--severity-preset`, `--used-by`, `-H`/`-I`, `--build-info`, `--sources` and
  `--contract` stay on the command line.
- **GitHub Action inputs moved to `set:` are retired.** `dso-only`,
  `include-private-dso`, `fail-on-removed-library`, `public-header-dirs`,
  `frontend`, `compile-options` and `nostdinc` fail the run before any
  analysis and name their `set:` replacement. `jobs` and `allow-build-query`
  fail the same way with no replacement. `compiler`, `lang`, `sysroot`,
  `severity-preset`, `build-info` and `compile-db` stay.

### Changed

- `consumers.manifests`, `consumers.required_symbols` and `output.view` are
  refused, not ignored, under `--no-baseline` and on a stored BundleFacts
  operand, and `--set compile.defines=` is refused under `--no-baseline`.

### Fixed

- The Action's PR comment no longer reuses or reruns a report filtered by an
  `output.view` `show=` token. A filtered JSON report is detected by its
  `show_only_filter` key, so a view from `.abicheck.yml` is caught too, and
  the comment rerun pins `output.view=[]` so it sees every gated change.
