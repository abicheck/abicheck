### Fixed

- **Release bundle analysis that cannot build its snapshot is now reported,
  not dropped.** A directory/package `compare` whose bundle snapshot failed
  to build used to omit cross-library analysis from the report, with only a
  stderr warning. The failure is now recorded in the JSON report's
  `bundle_analysis_errors` and the Markdown "Bundle Analysis Warnings"
  section, like a failure inside the analysis. The exit code is unchanged;
  see the known gap "A failed bundle snapshot build is reported but does not
  floor the exit code".
- **A corrupt stored-package `manifest.json` is an error, not "no
  manifest".** A `ProjectSnapshot` package operand whose manifest is
  truncated, malformed or unreadable by this build now fails the release
  comparison with a usage error. Before, it read as a package that embeds no
  instantiation manifest, which skipped the manifest-drift checks. A plain
  directory, or another format's `manifest.json`, still reads as absent.
- **A failed PDB lookup marks the PE `debug` section `failed`.** When
  locating the PDB raised, the dump treated it as "no PDB". It now records
  the lookup error the same way as a PDB parse error (ADR-062 D15).
- **`legacy_exit_code` raises for a verdict with no mapping.** It used to
  return `0`, a clean pass, for any unmapped value.
