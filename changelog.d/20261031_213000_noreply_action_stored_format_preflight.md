### Fixed

- **The Action rejects an unsupported `format` for a stored BundleFacts baseline before installing anything** —
  with `old-library` (or a local `abi-baseline` file) naming a stored
  BundleFacts document, `format: sarif` or `review` now fails at input
  validation instead of after the toolchain install. The check uses the
  CLI's own document classifier from the Action's source tree, and on a
  runner whose pre-installed Python is older than 3.11 it defers to the CLI
  as before. The `format` input's description now lists the real stored set
  (json, markdown, junit, oneline, html).
