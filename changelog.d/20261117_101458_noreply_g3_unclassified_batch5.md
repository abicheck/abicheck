### Changed

- **`visibility_leak` declares where it is observable (ADR-093).** The leak is one-sided on the old snapshot's export table (ELF dynamic), so the binary alone observes it: declared `{B:ELF}`. It leaves the UNCLASSIFIED baseline (129 -> 128).
