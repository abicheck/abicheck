<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set`: hard-linked binary aliases, DoS-bounded ELF
  classification, and PIE-executable-named-`.so` rejection.**
  `discover_artifact_set` and `run_scan_set` deduplicated candidate
  library paths via `Path.resolve()`, which only follows symlinks — two
  hard-linked paths to the same inode were treated as distinct libraries
  and could trip a spurious "colliding library identities" error. Both
  now dedupe on filesystem identity (`(st_dev, st_ino)`, falling back to
  the resolved path if `stat()` fails). Separately,
  `package._is_elf_shared_object`'s PIE-executable check
  (`_has_pie_executable_flag`, via the ELF `DT_FLAGS_1`/`DF_1_PIE`
  dynamic-table flag) now runs before, not after, the `PT_INTERP`
  filename fallback, so a real PIE executable built with a `.so`-shaped
  filename (`gcc -pie -o fake.so`) is correctly rejected instead of
  slipping through as a library. The `.dynamic`-table scan backing that
  check is now capped at 8192 entries, since `PT_DYNAMIC`'s `p_filesz` is
  an attacker/file-controlled field with no natural bound (unlike
  `e_phnum`), and an unbounded value could otherwise drive an effectively
  unbounded seek+read loop over an externally-supplied binary.
