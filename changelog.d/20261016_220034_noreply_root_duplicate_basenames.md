### Changed

- **The suppression rule model moved under its ADR-061 owner, `policy/`** — `abicheck.suppression` is now `abicheck.policy.suppression.rules`, and its YAML scalar helpers `abicheck.suppression_yaml` are now `abicheck.policy.suppression.yaml_scalars`. The root paths are removed rather than re-exported. **Migration:** import `Suppression` and `SuppressionList` from `abicheck.policy.suppression.rules`; the `abicheck.service` and `abicheck.policy_file` entry points are unchanged.
