### Fixed

- **`compare --used-by` no longer scopes a consumer against a library it
  could not read** — the ELF/PE/Mach-O parsers skip what they cannot parse,
  so a library whose export table failed to read (or a stored snapshot whose
  platform block records no parse) used to look like one exporting nothing:
  every symbol the consumer needs then read as "missing", a break nobody
  observed. Such a library is now refused with `Error: --used-by library:
  ...` and exit 1; `scope_diff_to_app` and `check_against` in the Python API
  raise `LibraryExportsUnreadableError` (a `ValueError`).
