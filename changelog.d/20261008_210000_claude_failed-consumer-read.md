### Fixed

- **`compare --used-by` no longer passes a consumer it could not read** — a
  recognised ELF/PE/Mach-O consumer whose import table failed to parse used
  to be evaluated as requiring nothing, so it was reported `NO_CHANGE` with
  100% symbol coverage even when it needed a removed symbol. Such a consumer
  is now unreadable, exactly like an unrecognised file: a required consumer
  is an error, an advisory one is reported `unreadable` and excluded from the
  scoped gate. The same applies to `parse_app_requirements` and
  `check_against` in the Python API.
