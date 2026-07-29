### Fixed

- **A declared target's optional vendor component no longer causes a false
  toolchain-target mismatch.** `arm-none-eabi` (vendor omitted) and
  `arm-unknown-none-eabi` (vendor explicitly `unknown`) name the same real
  bare-metal target — Clang's own triple parser normalizes both to the
  same canonical form — but the raw suffix-comparison fallback used when
  neither side's OS/environment marker is recognized compared the two
  spellings verbatim and rejected the equivalent one as a mismatch.

### Security

- **`abi_project_validate`/`abi_project_plan` no longer leak a probed
  toolchain binding's resolved filesystem path.** A `compiler_family`/
  `compiler_version`/`target` mismatch error from
  `check_profile_toolchain_identity()` embeds the bindings file's exact
  resolved executable path (e.g. `/opt/toolchains/gcc13/bin/gcc`) for a
  human CLI reader, but the MCP tools returned it unredacted — bypassing
  the module's own path-redaction boundary that every other domain error
  already goes through. Both tools now redact it to just the executable's
  basename before it reaches the report.
