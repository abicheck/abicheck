<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Markdown and review output keep symbol names intact** — a `<name>` in a symbol (a C++ template name such as `std::vector<int>`) was read as inline HTML and dropped, and a backtick inside a symbol closed the code span around it, in the Breaking Changes list, the review heading and Examples line, and every other Markdown symbol and type name outside table cells. One helper, `md_text`, now escapes prose, code spans and table cells, and the demangle pass escapes the names it inserts. The rendered bytes of template-named symbols change accordingly. The JUnit `testsuite` properties, the terminal `Audit:` line and the HTML disposition line now also state the acknowledged count (ADR-067 D5), the same value the JSON report and Markdown state.
