<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`abicheck project validate INPUT` is now one command over all three
  project-integration documents**, replacing `project validate` /
  `project validate-build` / `project validate-use-cases`. Which validation
  runs is decided by INPUT's own shape — a directory (or a
  `build-output.json`) declaring `schema: abicheck.build-output/v1` is a
  build output, a YAML list is an `impact-use-cases.yaml` manifest, and a
  YAML mapping is a project config — never by its filename, so a document
  held under any name is routed correctly and one held under a *misleading*
  name is not routed confidently wrong. `--toolchain-bindings` still applies
  only to a project config and is a usage error elsewhere: recognizing a
  document authorizes nothing. The two retired spellings exit `64` with no
  alias.
