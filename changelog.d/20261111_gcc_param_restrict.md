### Fixed

- **native GCC header backend: C++ parameter restrict** — the GCC plugin now
  reads a parameter's top-level `__restrict` from its declaration, since C++
  drops it from the function type. Removing or adding `restrict` on a C++
  parameter is now reported as `param_restrict_changed` with the gcc header
  backend. Plugin protocol schema 5 (rebuild the plugin).
  `EXTRACTOR_GENERATION` 58, snapshot cache salt v52.
