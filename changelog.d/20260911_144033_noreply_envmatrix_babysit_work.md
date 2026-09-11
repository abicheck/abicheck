<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`CompareRequest.env_matrix_path` no longer loads its file at
  construction time** — `__post_init__` now only validates that
  `env_matrix`/`env_matrix_path` are not both given; the actual load
  happens lazily, via the new `CompareRequest.effective_env_matrix()`, at
  the point a comparison is classified. A caller may now build the request
  before the matrix file exists, and a queued request always classifies
  against the file's current contents rather than one cached at
  construction.
- **`EnvironmentMatrix`'s nested `sycl:`/`cuda:` deployment blocks now
  reject wrong-shape scalar and list-element values** — `sycl.implementation`,
  `sycl.min_pi_version`, and `cuda.toolkit_version` are validated the same
  way the outer `EnvironmentMatrix`'s own scalar fields already were, and
  `cuda.driver_range`'s two elements are checked individually, instead of
  a dict/list value being silently stringified into a meaningless literal.
- **`FrozenStrDict` (`EnvironmentMatrix.runtime_floors`'s backing type) is
  no longer a `dict` subclass** — it is now a `collections.abc.Mapping`
  backed by a private, never-exposed dict, closing a mutation vector no
  amount of instance-method overriding could close: calling `dict`'s own
  base-class method directly (e.g. `dict.__setitem__(instance, ...)`)
  against a `dict` subclass instance still mutated its shared storage
  regardless of which methods the subclass overrode. `dataclasses.asdict()`
  stays JSON-safe for this field via a dedicated `FrozenStrDict.__deepcopy__`,
  and a direct `copy.deepcopy()` of a containing `EnvironmentMatrix` keeps
  `runtime_floors` genuinely immutable via a new `EnvironmentMatrix.__deepcopy__`.
