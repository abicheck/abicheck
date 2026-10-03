### Fixed

- `import abicheck.name_classification` (and `abicheck.finding_identity`,
  `abicheck.finding_identity_atomic`, `abicheck.compare.functions`, which
  import it before `abicheck.model`) no longer fails with a circular
  `ImportError` when it is the first abicheck import. `abicheck.model`'s
  re-exports of `name_classification` names are now resolved on first access,
  and the `model` modules that use them look them up at call time; behavior
  and the `from abicheck.model import canonicalize_type_name` spelling are
  unchanged.
