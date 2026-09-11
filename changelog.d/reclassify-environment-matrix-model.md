### Fixed

- Internal only: `abicheck/buildsource/build_config.py`'s `deployment:`
  key validation (`EnvironmentMatrix.from_dict`) moved to the sibling
  `build_config_schema.py`, the module every other `.abicheck.yml` subkey
  type check already lives in. This was previously blocked by a real
  `extract -> workflows` layering violation
  (`architecture/modules.yaml`/ADR-061): `environment_matrix.py` was
  classified `workflows`, which `build_config_schema.py`'s `extract`
  classification may not import. Splitting the one bounded dotted-numeric-
  version parser `environment_matrix.py` depended on out of
  `diff_versioning.py` (`compare`-classified) into a new leaf,
  `abicheck/model/dotted_version.py`, removed `environment_matrix.py`'s
  only non-stdlib dependency, which let it be reclassified `model` — the
  innermost layer every other layer may import. This is what let
  `architecture/debt.yaml`'s `build_config.py` `no_growth` baseline come
  back down (911 → 900) instead of staying raised to admit the validation
  logic being stuck on that file permanently. No behavior change.
