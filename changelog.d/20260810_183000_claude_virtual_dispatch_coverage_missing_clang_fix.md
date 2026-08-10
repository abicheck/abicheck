### Fixed

- **`fold_virtual_dispatch_graph` still claimed full coverage when its
  prerequisites never ran at all**: the previous mutual-exclusion fix
  checked only "narrowed" or "degraded" — but when `clang`/`clang++` isn't
  on `PATH`, each of `fold_call_graph`/`fold_type_graph`/
  `fold_override_graph` returns early setting none of the three coverage
  dicts (there's no diagnostic to attach a degraded stamp to), so both
  checks read as false and the derived pass still claimed full coverage
  from zero prerequisite facts. Now requires every prerequisite to carry
  its own full-coverage stamp before claiming this pass's own, falling
  back to `degraded_passes` otherwise.
