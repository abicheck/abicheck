### Added

- **Release HTML report.** A directory/package `compare` now accepts
  `-o html=PATH`. The page is rendered from the release JSON document alone,
  so it never changes the verdict or exit code: headline verdict and exit
  reasons, one row per member, the comparison scope (compared, unchecked,
  out of scope, proven removed/added, with reasons), release coherence
  findings and a dependency graph.
- **Dependency graph.** The release HTML draws members and the libraries
  they need as a layered SVG, with each node's status stated in text as well
  as colour, a title on every node and edge, and the same edges listed in a
  table; graphs over 60 libraries are capped with the omitted counts stated.
  The release JSON (schema 1.9) records the facts it is drawn from:
  `libraries[].dependencies` holds each side's ELF `DT_SONAME` and
  `DT_NEEDED` list.
- **Surface changes in the HTML report.** A single-pair HTML report now shows
  additions, removals and modifications with their old/new declarations,
  capped per group like the Markdown report, with the omitted count stated.
