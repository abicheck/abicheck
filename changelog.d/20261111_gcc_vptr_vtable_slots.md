### Fixed

- **native GCC header backend: vptr and vtable slots** — the gcc header
  backend now records a dynamic class's vptr offset (0, Itanium), so a first
  virtual is reported as `vptr_introduced`, and names vtable slots as castxml
  does: a pure virtual by its method (not `__cxa_pure_virtual`) and a virtual
  destructor's slot pair once. Defining a pure virtual is no longer a
  BREAKING `type_vtable_changed`. Plugin protocol schema 7 (rebuild the
  plugin). `EXTRACTOR_GENERATION` 60, snapshot cache salt v54.
