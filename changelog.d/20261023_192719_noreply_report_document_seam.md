<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **JUnit's scoped-gate properties read a shared fact** — `--used-by`/`--required-symbol(s)` runs now compute their consumer-gate facts once in the report builder (`ReportEnvelope.scoped_gate`) and JUnit formats them, instead of deriving the counts from the `DiffResult` inside the JUnit renderer. The emitted `<properties>` are byte-identical. JSON, Markdown, HTML and SARIF do not carry these facts yet; that parity is tracked as remaining work.
