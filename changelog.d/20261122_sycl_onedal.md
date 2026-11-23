### Fixed

- Replay the host AST on older Intel DPC++ drivers that reject the newer host-only flag but provide a validated device and host job pair.
- Recognize Intel DPC++ inline-namespace queue overloads when grouping public ABI removals. Exclude unrelated nested and wrapper types.
- Refresh the installed PI/UR inventory when a whole-snapshot cache hit reuses an ELF extraction.
- Include exported PI extension functions in ELF plugin inventories and their version landmarks. Mixed-generation comparisons omit extension differences that older snapshots could not observe.

### Added

- An optional abiforge campaign exercises host and queue consumers against the oneDAL SDK and checks grouped overload removals with the public CLI.
- Capture and compare one mixed-toolchain SDK baseline using profiles and per-library header roots in the main project config.
- Capture multiple consumer views of one shared object in one variant package by selecting a named header profile for each variant.
