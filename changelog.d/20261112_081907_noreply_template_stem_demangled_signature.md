### Fixed

- **`overload_set_rerouted` and `instantiation_missing_from_binary` now fire on header snapshots** — both checks grouped functions by their demangled signature, which keeps the parameter list (and, for a function template, the return type). As a result no two overloads ever shared a name, and `getv<int>`/`getv<long>` never matched as instantiations of one template. Swapping one overload for another next to a surviving one now reports the re-route. Dropping one explicit instantiation while another survives now reports it as missing at headers depth, not only from DWARF.
