### Changed

- **`overload_added` declares where it is observable (ADR-093).** An overload added to an exported free function reads from the export table alone (the mangled names differ), so the binary observes it: declared `{B}`. A member overload needs the header, but that is a surface limit of the class not being exported, not evidence the binary cannot see the change. It leaves the UNCLASSIFIED baseline (133 -> 132).
