<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`CudaConstraints.driver_range` is now frozen into a tuple, like
  `gpu_architectures`.** Constructing the public model with a YAML-shaped
  `CudaConstraints(driver_range=["525.0", "580.0"])` left `driver_range` as
  a mutable, unhashable `list`, so hashing that `CudaConstraints` (and any
  containing `EnvironmentMatrix`/`CompareRequest`) raised
  `TypeError: unhashable type: 'list'`. `__post_init__` now normalizes a
  non-`None` `driver_range` into a tuple the same way it already does for
  `gpu_architectures`.
- **A malformed or missing `env_matrix_path` on a typed `CompareRequest` is
  now rejected before any live extraction or build-query work runs.**
  `CompareRequest.effective_env_matrix()` used to be resolved for the first
  time only inside `classify_compare_pair`, which runs after
  `run_compare_request`'s `resolve_compare_request` phase has already
  completed its extraction work — so a bad `env_matrix_path` let that work
  run before failing. `run_compare_request` now resolves it once, early,
  and threads the already-resolved matrix into `classify_compare_pair` so
  the file is not read twice; `CompareRequest.__post_init__` itself still
  performs zero file I/O.

