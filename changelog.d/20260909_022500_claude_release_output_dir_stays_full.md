<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- A directory/package `compare --output-dir ... --view show=...` no
  longer narrows each library's own `--output-dir` JSON report by the
  display filter. That per-library file is the "always full" source the
  release Markdown/JSON summary itself directs a reader to when its own
  capped findings list was truncated — a `--view show=...` filter that
  excluded the very finding a reader was told to go find there could make
  it disappear from that supposedly complete file too. `--output-dir`
  now behaves like a secondary `--write`: always full, never filtered by
  the primary render's display selection.
