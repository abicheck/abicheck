<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The rendered "Filtered by" hint for `--view show=...` no longer embeds
  the internal `;`-separated transport form** — with multiple `--view
  show=...` occurrences active, the Markdown/HTML report's suggested
  re-run invocation used to read `--view show=breaking;functions`, which
  `--view` itself rejects as one malformed value and an unquoted shell
  would split on as a command separator. It now renders one `--view
  show=...` token per group (e.g. `--view show=breaking --view
  show=functions`), a literally valid, re-runnable invocation.
